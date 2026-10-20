"""
app/services/live/recording_audio.py
====================================
Audio persistence for LiveKit recordings (Phase 4.2).

Layout under storage/<meeting_id>/recording/:

    segments/<seq>_<segment_id>/
        tracks.json            per-track metadata (identity, track sid, offset)
        track_<id>_<sid>.pcm   raw int16 mono while capturing (crash-safe)
        track_<id>_<sid>.wav   per-participant track after finalization
        segment.wav            time-aligned mix of every track in the segment
    recording.wav              all finalized segments in chronological order
    recording_manifest.json    segment offsets and participant/track metadata

Every track of a segment shares one clock that starts when capture is armed.
A track's first frame fixes its offset on that clock, and gaps (mute, network
stalls) are padded with silence, so concurrent speakers stay aligned in the
mix. Segments are sequential in time and are therefore concatenated.
"""
from __future__ import annotations

import json
import os
import re
import wave
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

TARGET_SAMPLE_RATE = 16000
# A track that falls further behind the shared clock than this is padded.
GAP_TOLERANCE_SECONDS = 0.2
# Silence inserted between consecutive segments in recording.wav.
SEGMENT_GAP_SECONDS = 1.0
_MIX_CHUNK_SAMPLES = TARGET_SAMPLE_RATE * 30
_FLUSH_EVERY_FRAMES = 50

RECORDING_FILENAME = "recording.wav"
MANIFEST_FILENAME = "recording_manifest.json"
TRACKS_FILENAME = "tracks.json"
SEGMENT_MIX_FILENAME = "segment.wav"


def safe_component(value: str, limit: int = 40) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", value or "")
    return (cleaned or "unknown")[:limit]


def normalize_frame(
    data: Any,
    sample_rate: int,
    num_channels: int,
    target_rate: int = TARGET_SAMPLE_RATE,
) -> np.ndarray:
    """Convert an interleaved int16 frame to mono int16 at target_rate."""
    pcm = np.frombuffer(bytes(data), dtype=np.int16)
    channels = max(1, int(num_channels or 1))
    if channels > 1:
        usable = len(pcm) - (len(pcm) % channels)
        pcm = pcm[:usable].reshape(-1, channels).astype(np.int32).mean(axis=1)
    if sample_rate and sample_rate != target_rate and len(pcm):
        n_out = max(1, int(round(len(pcm) * target_rate / sample_rate)))
        x_old = np.arange(len(pcm), dtype=np.float64)
        x_new = np.linspace(0, len(pcm) - 1, n_out)
        pcm = np.interp(x_new, x_old, pcm.astype(np.float64))
    return np.clip(np.rint(pcm), -32768, 32767).astype(np.int16)


class TrackWriter:
    """Appends one remote audio track to a raw PCM file on the segment clock."""

    def __init__(
        self,
        path: Path,
        *,
        track_sid: str,
        identity: str,
        name: str,
        sample_rate: int = TARGET_SAMPLE_RATE,
    ):
        self.path = Path(path)
        self.track_sid = track_sid
        self.identity = identity
        self.name = name
        self.sample_rate = sample_rate
        self.start_offset: Optional[int] = None
        self.samples = 0
        self.padded_samples = 0
        self.frames = 0
        self.closed = False
        self._fh = self.path.open("ab")

    def write(self, pcm: np.ndarray, t_rel: float) -> None:
        """Write a frame that finished arriving t_rel seconds after capture start."""
        if self.closed:
            return
        n = len(pcm)
        arrival_end = int(round(t_rel * self.sample_rate))
        if self.start_offset is None:
            self.start_offset = max(0, arrival_end - n)
        else:
            lag = arrival_end - (self.start_offset + self.samples + n)
            if lag > GAP_TOLERANCE_SECONDS * self.sample_rate:
                self._fh.write(np.zeros(lag, dtype=np.int16).tobytes())
                self.samples += lag
                self.padded_samples += lag
        self._fh.write(pcm.astype(np.int16, copy=False).tobytes())
        self.samples += n
        self.frames += 1
        if self.frames % _FLUSH_EVERY_FRAMES == 0:
            self._fh.flush()

    def close(self) -> None:
        if not self.closed:
            self._fh.flush()
            self._fh.close()
            self.closed = True

    def to_meta(self) -> Dict[str, Any]:
        return {
            "track_sid": self.track_sid,
            "identity": self.identity,
            "name": self.name,
            "pcm_file": self.path.name,
            "start_offset_samples": self.start_offset or 0,
            "sample_rate": self.sample_rate,
        }


def write_tracks_meta(segment_dir: Path, tracks: Iterable[Dict[str, Any]]) -> None:
    path = Path(segment_dir) / TRACKS_FILENAME
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(list(tracks), indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_tracks_meta(segment_dir: Path) -> List[Dict[str, Any]]:
    path = Path(segment_dir) / TRACKS_FILENAME
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _open_wav_writer(path: Path, sample_rate: int) -> wave.Wave_write:
    wav = wave.open(str(path), "wb")
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(sample_rate)
    return wav


def _pcm_samples(path: Path) -> int:
    # A killed process can leave half a sample at the end; ignore it.
    return path.stat().st_size // 2 if path.exists() else 0


def _read_pcm(path: Path, start: int, count: int) -> np.ndarray:
    with path.open("rb") as fh:
        fh.seek(start * 2)
        raw = fh.read(count * 2)
    return np.frombuffer(raw[: len(raw) - len(raw) % 2], dtype=np.int16)


def _pcm_to_wav(pcm_path: Path, wav_path: Path, sample_rate: int) -> None:
    total = _pcm_samples(pcm_path)
    wav = _open_wav_writer(wav_path, sample_rate)
    try:
        pos = 0
        while pos < total:
            chunk = _read_pcm(pcm_path, pos, min(_MIX_CHUNK_SAMPLES, total - pos))
            wav.writeframes(chunk.tobytes())
            pos += len(chunk)
            if not len(chunk):
                break
    finally:
        wav.close()


def finalize_segment(segment_dir: Path, sample_rate: int = TARGET_SAMPLE_RATE) -> Dict[str, Any]:
    """
    Turn a segment's raw PCM tracks into per-track WAVs plus a time-aligned mix.

    Safe to call on a segment left behind by a crashed process: it only needs
    tracks.json and whatever PCM bytes reached the disk.
    """
    segment_dir = Path(segment_dir)
    tracks: List[Dict[str, Any]] = []
    for meta in read_tracks_meta(segment_dir):
        pcm_path = segment_dir / meta["pcm_file"]
        samples = _pcm_samples(pcm_path)
        if samples == 0:
            pcm_path.unlink(missing_ok=True)
            continue
        tracks.append({**meta, "samples": samples, "_pcm": pcm_path})

    total = max((t["start_offset_samples"] + t["samples"] for t in tracks), default=0)
    peak = 0
    mix_path = segment_dir / SEGMENT_MIX_FILENAME
    if total > 0:
        wav = _open_wav_writer(mix_path, sample_rate)
        try:
            pos = 0
            while pos < total:
                end = min(total, pos + _MIX_CHUNK_SAMPLES)
                acc = np.zeros(end - pos, dtype=np.int32)
                for t in tracks:
                    t_start = t["start_offset_samples"]
                    t_end = t_start + t["samples"]
                    lo, hi = max(pos, t_start), min(end, t_end)
                    if lo >= hi:
                        continue
                    chunk = _read_pcm(t["_pcm"], lo - t_start, hi - lo)
                    acc[lo - pos: lo - pos + len(chunk)] += chunk
                mixed = np.clip(acc, -32768, 32767).astype(np.int16)
                if len(mixed):
                    peak = max(peak, int(np.abs(mixed.astype(np.int32)).max()))
                wav.writeframes(mixed.tobytes())
                pos = end
        finally:
            wav.close()

    result_tracks = []
    for t in tracks:
        wav_name = Path(t["pcm_file"]).with_suffix(".wav").name
        _pcm_to_wav(t["_pcm"], segment_dir / wav_name, sample_rate)
        t["_pcm"].unlink(missing_ok=True)
        result_tracks.append({
            "track_sid": t["track_sid"],
            "identity": t["identity"],
            "name": t.get("name") or t["identity"],
            "file": wav_name,
            "start_offset_seconds": round(t["start_offset_samples"] / sample_rate, 3),
            "duration_seconds": round(t["samples"] / sample_rate, 3),
        })

    empty = total == 0 or peak == 0
    if empty:
        mix_path.unlink(missing_ok=True)
    return {
        "segment_file": None if empty else SEGMENT_MIX_FILENAME,
        "duration_seconds": round(total / sample_rate, 3),
        "peak": peak,
        "empty": empty,
        "tracks": result_tracks,
    }


def discard_segment_audio(segment_dir: Path) -> None:
    """Delete every audio file of a segment; tracks.json is kept for the audit trail."""
    segment_dir = Path(segment_dir)
    if not segment_dir.exists():
        return
    for path in segment_dir.iterdir():
        if path.suffix.lower() in (".pcm", ".wav"):
            path.unlink(missing_ok=True)


def wav_info(path: Path) -> Optional[Dict[str, Any]]:
    """Return basic properties of a readable WAV, or None if it is invalid."""
    try:
        with wave.open(str(path), "rb") as wav:
            frames = wav.getnframes()
            rate = wav.getframerate()
            return {
                "frames": frames,
                "sample_rate": rate,
                "channels": wav.getnchannels(),
                "duration_seconds": round(frames / rate, 3) if rate else 0.0,
            }
    except (OSError, EOFError, wave.Error):
        return None


def build_meeting_recording(
    recording_dir: Path,
    segments: List[Dict[str, Any]],
    meeting_id: str,
    sample_rate: int = TARGET_SAMPLE_RATE,
) -> Optional[Dict[str, Any]]:
    """
    Concatenate finalized segments (sorted by seq) into recording.wav and write
    the manifest. Writes through temp files that do not match "recording.*",
    so the worker never picks up a half-written artifact.
    """
    recording_dir = Path(recording_dir)
    final_path = recording_dir / RECORDING_FILENAME
    manifest_path = recording_dir / MANIFEST_FILENAME
    usable = [
        s for s in sorted(segments, key=lambda s: s["seq"])
        if s.get("segment_file") and (Path(s["dir"]) / s["segment_file"]).exists()
    ]
    if not usable:
        final_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        return None

    tmp_path = recording_dir / "tmp_assembling.wav"
    gap = np.zeros(int(SEGMENT_GAP_SECONDS * sample_rate), dtype=np.int16).tobytes()
    manifest_segments = []
    offset = 0
    out = _open_wav_writer(tmp_path, sample_rate)
    try:
        for index, seg in enumerate(usable):
            if index:
                out.writeframes(gap)
                offset += len(gap) // 2
            seg_offset = offset
            with wave.open(str(Path(seg["dir"]) / seg["segment_file"]), "rb") as src:
                if src.getframerate() != sample_rate or src.getnchannels() != 1:
                    raise ValueError("Segment audio is not 16 kHz mono")
                while True:
                    block = src.readframes(_MIX_CHUNK_SAMPLES)
                    if not block:
                        break
                    out.writeframes(block)
                    offset += len(block) // 2
            seg_dir_rel = Path(seg["dir"]).relative_to(recording_dir).as_posix()
            manifest_segments.append({
                "segment_id": seg["segment_id"],
                "seq": seg["seq"],
                "offset_seconds": round(seg_offset / sample_rate, 3),
                "duration_seconds": round((offset - seg_offset) / sample_rate, 3),
                "started_at": seg.get("capture_started_at") or seg.get("started_at"),
                "stopped_at": seg.get("stopped_at"),
                "stop_reason": seg.get("stop_reason"),
                "tracks": [
                    {
                        **track,
                        "file": f"{seg_dir_rel}/{track['file']}",
                        "recording_offset_seconds": round(
                            seg_offset / sample_rate + track["start_offset_seconds"], 3
                        ),
                    }
                    for track in seg.get("tracks", [])
                ],
            })
    finally:
        out.close()

    os.replace(tmp_path, final_path)
    manifest = {
        "meeting_id": meeting_id,
        "source": "livekit",
        "file": RECORDING_FILENAME,
        "sample_rate": sample_rate,
        "channels": 1,
        "duration_seconds": round(offset / sample_rate, 3),
        "segments": manifest_segments,
    }
    tmp_manifest = recording_dir / "tmp_manifest.json"
    tmp_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(tmp_manifest, manifest_path)
    return manifest


def _rms(path: Path, start_s: float, end_s: float) -> float:
    try:
        with wave.open(str(path), "rb") as wav:
            rate = wav.getframerate()
            first = max(0, int(start_s * rate))
            if first >= wav.getnframes() or end_s <= start_s:
                return 0.0
            wav.setpos(first)
            raw = wav.readframes(max(0, int((end_s - start_s) * rate)))
    except (OSError, EOFError, wave.Error):
        return 0.0
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    return float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0


def attribute_utterances(
    recording_dir: Path,
    manifest: Dict[str, Any],
    utterances: List[Dict[str, Any]],
    dominance: float = 2.0,
) -> List[Dict[str, Any]]:
    """
    Map each utterance (start/end in recording seconds) to the participant whose
    own track carried the most energy over that span. Returns None for the
    participant when no track is clearly dominant (crosstalk or silence).
    """
    recording_dir = Path(recording_dir)
    tracks = [t for seg in manifest.get("segments", []) for t in seg.get("tracks", [])]
    results = []
    for utt in utterances:
        start, end = float(utt.get("start") or 0.0), float(utt.get("end") or 0.0)
        scores: Dict[str, float] = {}
        names: Dict[str, str] = {}
        for t in tracks:
            t_start = t["recording_offset_seconds"]
            lo, hi = max(start, t_start), min(end, t_start + t["duration_seconds"])
            if hi <= lo:
                continue
            energy = _rms(recording_dir / t["file"], lo - t_start, hi - t_start)
            scores[t["identity"]] = max(scores.get(t["identity"], 0.0), energy)
            names[t["identity"]] = t.get("name") or t["identity"]
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best = None
        if ranked and ranked[0][1] > 0:
            runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
            if runner_up == 0 or ranked[0][1] >= dominance * runner_up:
                best = ranked[0][0]
        results.append({
            "utt_id": utt.get("utt_id"),
            "start": start,
            "end": end,
            "speaker_id": utt.get("speaker_id") or utt.get("speaker"),
            "participant_identity": best,
            "participant_name": names.get(best) if best else None,
        })
    return results
