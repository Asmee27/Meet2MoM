"""
app/services/live/track_transcription.py
========================================
Track-aware transcription for LiveKit recordings.

A LiveKit recording carries one WAV per participant track plus a manifest
(recording_manifest.json) with each track's identity, display name and
offset in the meeting recording. Each track holds only that participant's
microphone, so it is the authoritative source for *who* spoke. Diarizing
the mixed recording can merge distinct voices (pyannote needs enough speech
per voice to separate them), so it is only used when tracks are unusable.

    plan = load_track_plan(storage.recording)
    if plan.usable:
        segments, stats = transcribe_tracks(plan, transcriber.transcribe_track)
    else:
        ... existing mixed-recording ASR + diarization ...

Every track must be valid for track mode; one missing or corrupt track sends
the whole meeting down the mixed path, so no participant's speech is lost.
"""
from __future__ import annotations

import hashlib
import json
import re
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from rapidfuzz import fuzz

from app.services.live.recording_audio import MANIFEST_FILENAME, RECORDING_FILENAME, wav_info

ATTRIBUTION_SOURCE = "livekit_track"
# Bump when track-mode output changes, so cached results are not reused.
TRACK_MODE_VERSION = "livekit-tracks-v1"
# Allowed slack between manifest, track and recording durations (seconds).
DURATION_TOLERANCE = 1.0
# Same words captured by two microphones at the same time (acoustic bleed).
BLEED_TEXT_SIMILARITY = 85
# Minimum shared time for two participants' segments to count as overlap.
MIN_OVERLAP_SECONDS = 0.15


@dataclass
class TrackSource:
    index: int
    identity: str
    name: str
    track_sid: str
    path: Path
    offset_seconds: float
    duration_seconds: float
    silent: bool = False

    @property
    def end_seconds(self) -> float:
        return self.offset_seconds + self.duration_seconds


@dataclass
class TrackPlan:
    usable: bool
    reason: Optional[str] = None
    tracks: List[TrackSource] = field(default_factory=list)
    problems: List[Dict[str, Any]] = field(default_factory=list)
    recording_duration: Optional[float] = None
    manifest_sha256: Optional[str] = None

    def summary(self) -> Dict[str, Any]:
        return {
            "attribution_source": ATTRIBUTION_SOURCE if self.usable else "diarization",
            "track_mode": self.usable,
            "fallback_reason": None if self.usable else self.reason,
            "recording_duration_seconds": self.recording_duration,
            "tracks": [
                {
                    "identity": t.identity,
                    "name": t.name,
                    "track_sid": t.track_sid,
                    "offset_seconds": t.offset_seconds,
                    "duration_seconds": t.duration_seconds,
                    "silent": t.silent,
                }
                for t in self.tracks
            ],
            "problems": self.problems,
        }


def _resolve_track_path(recording_dir: Path, rel: Any) -> Optional[Path]:
    """Resolve a manifest path strictly inside the recording directory."""
    if not isinstance(rel, str) or not rel.strip():
        return None
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    root = recording_dir.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def _peak(path: Path) -> int:
    peak = 0
    with wave.open(str(path), "rb") as wav:
        while True:
            block = wav.readframes(16000 * 30)
            if not block:
                break
            samples = np.frombuffer(block[: len(block) - len(block) % 2], dtype=np.int16)
            if len(samples):
                peak = max(peak, int(np.abs(samples.astype(np.int32)).max()))
    return peak


def load_track_plan(recording_dir: Path) -> TrackPlan:
    """Validate the LiveKit manifest and every participant track it lists."""
    recording_dir = Path(recording_dir)
    manifest_path = recording_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        return TrackPlan(usable=False, reason="no_manifest")
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return TrackPlan(usable=False, reason="invalid_manifest")
    if not isinstance(manifest, dict) or manifest.get("source") != "livekit":
        return TrackPlan(usable=False, reason="not_livekit")

    recording_info = wav_info(recording_dir / RECORDING_FILENAME)
    if recording_info is None or recording_info["frames"] == 0:
        return TrackPlan(usable=False, reason="invalid_recording")
    recording_duration = recording_info["duration_seconds"]

    plan = TrackPlan(
        usable=False,
        recording_duration=recording_duration,
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
    )
    for segment in manifest.get("segments") or []:
        for track in segment.get("tracks") or []:
            index = len(plan.tracks) + len(plan.problems) + 1
            label = track.get("identity") or f"track {index}"

            def problem(kind: str) -> None:
                plan.problems.append({"track": label, "file": track.get("file"), "problem": kind})

            identity = str(track.get("identity") or "").strip()
            if not identity:
                problem("missing_identity")
                continue
            path = _resolve_track_path(recording_dir, track.get("file"))
            if path is None:
                problem("unsafe_or_missing_path")
                continue
            if not path.is_file():
                problem("file_missing")
                continue
            info = wav_info(path)
            if info is None:
                problem("unreadable_audio")
                continue
            if info["frames"] == 0 or info["sample_rate"] <= 0:
                problem("empty_audio")
                continue

            offset = track.get("recording_offset_seconds")
            if offset is None:
                offset = float(segment.get("offset_seconds") or 0.0) + float(
                    track.get("start_offset_seconds") or 0.0
                )
            try:
                offset = float(offset)
            except (TypeError, ValueError):
                problem("invalid_offset")
                continue
            duration = info["duration_seconds"]
            if offset < 0 or offset + duration > recording_duration + DURATION_TOLERANCE:
                problem("outside_recording")
                continue
            try:
                silent = _peak(path) == 0
            except (OSError, EOFError, wave.Error):
                problem("unreadable_audio")
                continue
            plan.tracks.append(TrackSource(
                index=index,
                identity=identity,
                name=str(track.get("name") or identity),
                track_sid=str(track.get("track_sid") or ""),
                path=path,
                offset_seconds=round(offset, 3),
                duration_seconds=duration,
                silent=silent,
            ))

    if plan.problems:
        plan.reason = "unusable_tracks"
    elif not plan.tracks:
        plan.reason = "no_tracks"
    else:
        plan.usable = True
    return plan


def track_cache_key(recording_dir: Path) -> Optional[str]:
    """
    Extra cache-key material for track-aware results. A recording processed
    with participant tracks must never reuse mixed-recording artifacts (or
    the reverse), and must re-run when its tracks change.
    """
    plan = load_track_plan(recording_dir)
    if not plan.usable:
        return None
    digest = hashlib.sha256(f"{TRACK_MODE_VERSION}|{plan.manifest_sha256}".encode())
    for track in plan.tracks:
        with open(track.path, "rb") as fh:
            for block in iter(lambda: fh.read(8 * 1024 * 1024), b""):
                digest.update(block)
    return f"{TRACK_MODE_VERSION}:{digest.hexdigest()}"


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", (text or "").lower(), flags=re.UNICODE)).strip()


def _overlap(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    return max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))


def _track_rms(track: TrackSource, start: float, end: float) -> float:
    try:
        with wave.open(str(track.path), "rb") as wav:
            rate = wav.getframerate()
            first = max(0, int((start - track.offset_seconds) * rate))
            count = max(0, int((end - start) * rate))
            if first >= wav.getnframes() or count == 0:
                return 0.0
            wav.setpos(first)
            samples = np.frombuffer(wav.readframes(count), dtype=np.int16).astype(np.float64)
    except (OSError, EOFError, wave.Error):
        return 0.0
    return float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0


TranscribeTrack = Callable[[str, float, int], Dict[str, Any]]


def transcribe_tracks(
    plan: TrackPlan,
    transcribe_track: TranscribeTrack,
    review_status: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Transcribe every participant track and merge the results into one
    chronological, speaker-attributed transcript in the merger's format.

    transcribe_track(path, offset_seconds, index) must return
    {"segments": [...]} with timestamps already shifted by offset_seconds
    (MeetingTranscriber.transcribe_track does this exactly once).
    """
    if review_status is None:
        from app.services.diarization.speaker_merger import SpeakerTranscriptMerger

        review_status = SpeakerTranscriptMerger.determine_review_status

    if not plan.usable:
        raise ValueError(f"Track plan is not usable: {plan.reason}")

    by_identity = {t.identity: t for t in plan.tracks}
    stats: Dict[str, Any] = {
        **plan.summary(),
        "per_track": [],
        "dropped_out_of_range": 0,
        "bleed_removed": [],
        "overlapping_segments": 0,
    }
    segments: List[Dict[str, Any]] = []

    for track in plan.tracks:
        if track.silent:
            stats["per_track"].append({"identity": track.identity, "segments": 0, "skipped": "silent"})
            continue
        result = transcribe_track(str(track.path), track.offset_seconds, track.index)
        kept = 0
        # A segment must lie inside its own track and the recording.
        limit = min(track.end_seconds, plan.recording_duration or track.end_seconds)
        for raw in result.get("segments") or []:
            start, end = float(raw["start"]), min(float(raw["end"]), limit)
            if not (track.offset_seconds - 0.05 <= start < limit) or end <= start:
                stats["dropped_out_of_range"] += 1
                continue
            segments.append({
                **raw,
                "start": round(start, 3),
                "end": round(end, 3),
                "speaker": track.identity,
                "speaker_name": track.name,
                "track_sid": track.track_sid,
                "attribution_source": ATTRIBUTION_SOURCE,
                # Identity comes from the participant's own microphone track.
                "speaker_attribution_confidence": 1.0,
                "speaker_confidence_flag": "high",
                "possible_overlap": False,
                "overlapping_speakers": [],
                "speaker_distribution": {track.identity: 1.0},
            })
            kept += 1
        stats["per_track"].append({
            "identity": track.identity,
            "segments": kept,
            "detected_language": result.get("detected_language"),
            "language_probability": result.get("language_probability"),
            "low_confidence_segments": result.get("low_confidence_segments"),
            "retry_used": result.get("retry_used"),
        })

    segments.sort(key=lambda s: (s["start"], s["end"], s["speaker"]))

    # Acoustic bleed: identical words on two participants' tracks at the same
    # time. Keep the copy from the louder (own) microphone.
    dropped = set()
    for i, a in enumerate(segments):
        for j in range(i + 1, len(segments)):
            b = segments[j]
            if b["start"] >= a["end"]:
                break
            if i in dropped or j in dropped or a["speaker"] == b["speaker"]:
                continue
            if _overlap(a, b) < MIN_OVERLAP_SECONDS:
                continue
            if fuzz.ratio(_normalized(a["text"]), _normalized(b["text"])) < BLEED_TEXT_SIMILARITY:
                continue
            span = (max(a["start"], b["start"]), min(a["end"], b["end"]))
            energy_a = _track_rms(by_identity[a["speaker"]], *span)
            energy_b = _track_rms(by_identity[b["speaker"]], *span)
            loser = j if energy_a >= energy_b else i
            dropped.add(loser)
            stats["bleed_removed"].append({
                "kept": segments[i if loser == j else j]["speaker"],
                "removed": segments[loser]["speaker"],
                "start": segments[loser]["start"],
                "end": segments[loser]["end"],
            })
    segments = [s for k, s in enumerate(segments) if k not in dropped]

    # Genuine simultaneous speech by different participants is kept and flagged.
    for i, a in enumerate(segments):
        for b in segments[i + 1:]:
            if b["start"] >= a["end"]:
                break
            if a["speaker"] != b["speaker"] and _overlap(a, b) >= MIN_OVERLAP_SECONDS:
                for x, y in ((a, b), (b, a)):
                    x["possible_overlap"] = True
                    if y["speaker"] not in x["overlapping_speakers"]:
                        x["overlapping_speakers"].append(y["speaker"])
                    x["speaker_confidence_flag"] = "medium"

    for number, seg in enumerate(segments, start=1):
        seg["segment_id"] = number
        seg["overlapping_speakers"] = sorted(seg["overlapping_speakers"])
        seg.update(review_status(seg))
    stats["overlapping_segments"] = sum(1 for s in segments if s["possible_overlap"])
    stats["segments"] = len(segments)
    stats["speakers"] = sorted({s["speaker"] for s in segments})
    return segments, stats


def speaker_turns(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """speaker_turns.json equivalent derived from track-attributed segments."""
    return [
        {
            "start": s["start"],
            "end": s["end"],
            "speaker": s["speaker"],
            "speaker_name": s.get("speaker_name"),
            "source": ATTRIBUTION_SOURCE,
        }
        for s in segments
    ]
