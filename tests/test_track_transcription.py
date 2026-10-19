"""
Track-aware transcription for LiveKit recordings.

Regression for meeting 750ae9fd...: two participants (host from 0.569 s,
guest from 22.194 s) were diarized from the mixed recording into a single
SPEAKER_00. Speaker identity now comes from each participant's own track.

Whisper is never loaded: MeetingTranscriber.run_whisper is replaced by a fake
that returns scripted segments in track-local time, so the real offset and
confidence code (transcribe_track -> build_chunk_results) is exercised. All
audio is synthetic.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
import pytest

from app.core import db
from app.core.storage import MeetingStorage
from app.services.diarization import speaker_merger
from app.services.diarization.speaker_merger import SpeakerTranscriptMerger
from app.services.live import track_transcription as tt
from app.services.live.track_transcription import (
    load_track_plan,
    speaker_turns,
    track_cache_key,
    transcribe_tracks,
)
from app.services.transcription.transcriber import MeetingTranscriber
from app.services.transcript.transcript_index import build_transcript_index, persist_transcript_index

SR = 16000
HOST = {"identity": "user_8zvyy6va", "name": "Arya", "sid": "TR_AMbMHsEu2PFycw", "offset": 0.569}
GUEST = {"identity": "user_8k2qzyep", "name": "Guest Participant", "sid": "TR_AMB7iTYHAbbg9t", "offset": 22.194}
RECORDING_SECONDS = 62.737


def write_wav(path: Path, seconds: float, speech=(), freq=440.0, amp=6000):
    """Silence with tone bursts at the given track-local (start, end) spans."""
    samples = np.zeros(int(seconds * SR), dtype=np.float64)
    t = np.arange(len(samples)) / SR
    for start, end in speech:
        lo, hi = int(start * SR), int(end * SR)
        samples[lo:hi] = amp * np.sin(2 * np.pi * freq * t[lo:hi])
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples.astype(np.int16).tobytes())
    return path


def track_entry(p, seconds, folder="segments/001_322916865cf5"):
    return {
        "track_sid": p["sid"],
        "identity": p["identity"],
        "name": p["name"],
        "file": f"{folder}/track_{p['identity']}_{p['sid']}.wav",
        "start_offset_seconds": p["offset"],
        "duration_seconds": seconds,
        "recording_offset_seconds": p["offset"],
    }


def make_livekit_recording(root: Path, tracks, recording_seconds=RECORDING_SECONDS, write=True):
    """tracks: list of (participant, seconds, speech_spans, freq)."""
    root.mkdir(parents=True, exist_ok=True)
    write_wav(root / "recording.wav", recording_seconds, speech=[(36.1, 40.1)])
    entries = []
    for participant, seconds, speech, freq in tracks:
        entry = track_entry(participant, seconds)
        if write:
            write_wav(root / entry["file"], seconds, speech=speech, freq=freq)
        entries.append(entry)
    manifest = {
        "meeting_id": "m-test",
        "source": "livekit",
        "file": "recording.wav",
        "sample_rate": SR,
        "channels": 1,
        "duration_seconds": recording_seconds,
        "segments": [{"segment_id": "s1", "seq": 1, "offset_seconds": 0.0, "tracks": entries}],
    }
    (root / "recording_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def bug_meeting(root: Path) -> Path:
    """Shape of meeting 750ae9fd...: host speaks at 36.1 s, guest at 50.2 s (global)."""
    return make_livekit_recording(root, [
        (HOST, 62.116, [(36.1 - HOST["offset"], 40.1 - HOST["offset"])], 220.0),
        (GUEST, 40.543, [(50.2 - GUEST["offset"], 55.7 - GUEST["offset"])], 660.0),
    ])


def fake_segment(start, end, text, avg_logprob=-0.3, no_speech_prob=0.05):
    return SimpleNamespace(
        start=start, end=end, text=text, avg_logprob=avg_logprob,
        no_speech_prob=no_speech_prob, compression_ratio=1.2,
    )


def scripted_transcriber(script):
    """
    A MeetingTranscriber whose Whisper call returns script[track file name]
    (segments in track-local seconds). Nothing else is faked.
    """
    transcriber = object.__new__(MeetingTranscriber)
    calls = []

    def run_whisper(path, beam_size):
        calls.append((Path(path).name, beam_size))
        segments = script.get(Path(path).name, [])
        return list(segments), SimpleNamespace(language="en", language_probability=0.97)

    transcriber.run_whisper = run_whisper
    transcriber.calls = calls
    return transcriber


def file_of(p):
    return f"track_{p['identity']}_{p['sid']}.wav"


BUG_SCRIPT = {
    # Track-local times: host 35.531 s + 0.569 = 36.1 global; guest 28.0 + 22.194 = 50.194.
    file_of(HOST): [fake_segment(35.531, 39.531, "Hello. The quarterly budget review is scheduled for Tuesday.")],
    file_of(GUEST): [fake_segment(28.0, 33.4, "Good evening, sir. I will submit the marketing report by Friday. Thank you.")],
}


# ---------------------------------------------------------------------------
# 10 / 1 / 2: the original one-speaker bug and its fix
# ---------------------------------------------------------------------------

def test_regression_mixed_diarization_collapses_two_participants_into_one_speaker():
    """The evidence of meeting 750ae9fd... through the unchanged diarization merge."""
    transcript = [
        {"segment_id": 1, "start": 35.99, "end": 39.99, "text": "Hello. ...", "confidence_flag": "normal"},
        {"segment_id": 2, "start": 39.99, "end": 53.95, "text": "Good evening, sir. ...", "confidence_flag": "normal"},
    ]
    turns = [
        {"start": 36.16, "end": 39.637, "speaker": "SPEAKER_00"},
        {"start": 50.183, "end": 54.284, "speaker": "SPEAKER_00"},
    ]
    merger = SpeakerTranscriptMerger()
    labels = [merger.analyze_speakers(seg, turns)["speaker"] for seg in transcript]
    assert labels == ["SPEAKER_00", "SPEAKER_00"]            # the bug: one speaker


def test_two_participant_tracks_are_attributed_with_names_and_global_times(tmp_path):
    plan = load_track_plan(bug_meeting(tmp_path / "recording"))
    assert plan.usable, plan.problems

    segments, stats = transcribe_tracks(plan, scripted_transcriber(BUG_SCRIPT).transcribe_track)

    assert [(s["speaker"], s["speaker_name"]) for s in segments] == [
        (HOST["identity"], "Arya"),
        (GUEST["identity"], "Guest Participant"),
    ]
    # Offsets applied exactly once: local + track offset.
    assert segments[0]["start"] == pytest.approx(36.1, abs=0.001)
    assert segments[0]["end"] == pytest.approx(40.1, abs=0.001)
    assert segments[1]["start"] == pytest.approx(50.194, abs=0.001)
    assert segments[1]["end"] == pytest.approx(55.594, abs=0.001)
    assert all(s["attribution_source"] == "livekit_track" for s in segments)
    assert all(s["requires_review"] is False and not s["possible_overlap"] for s in segments)
    assert [s["segment_id"] for s in segments] == [1, 2]
    assert stats["speakers"] == sorted([HOST["identity"], GUEST["identity"]])
    assert stats["track_mode"] is True


def test_offset_is_applied_once_and_validated_against_track_and_recording(tmp_path):
    plan = load_track_plan(bug_meeting(tmp_path / "recording"))
    script = {
        file_of(GUEST): [
            fake_segment(0.0, 1.0, "First words."),                 # -> 22.194 global
            fake_segment(40.0, 45.0, "Runs past the track end."),   # clipped at track end
            fake_segment(41.0, 42.0, "Starts after the track."),    # impossible: dropped
        ],
    }
    segments, stats = transcribe_tracks(plan, scripted_transcriber(script).transcribe_track)
    assert [s["start"] for s in segments] == [pytest.approx(22.194), pytest.approx(62.194)]
    track_end = GUEST["offset"] + 40.543
    assert segments[1]["end"] == pytest.approx(min(track_end, RECORDING_SECONDS), abs=0.001)
    assert stats["dropped_out_of_range"] == 1


# ---------------------------------------------------------------------------
# 3 / 4: ordering, overlap and bleed
# ---------------------------------------------------------------------------

def test_out_of_order_track_output_is_merged_chronologically(tmp_path):
    root = make_livekit_recording(tmp_path / "recording", [
        (GUEST, 40.543, [(2, 4), (20, 22)], 660.0),              # guest listed first
        (HOST, 62.116, [(10, 12), (40, 42)], 220.0),
    ])
    script = {
        file_of(GUEST): [fake_segment(20, 22, "Guest late."), fake_segment(2, 4, "Guest early.")],
        file_of(HOST): [fake_segment(40, 42, "Host late."), fake_segment(10, 12, "Host early.")],
    }
    segments, _ = transcribe_tracks(load_track_plan(root), scripted_transcriber(script).transcribe_track)
    # Global: host 10.569, guest 24.194, host 40.569, guest 42.194.
    assert [s["text"] for s in segments] == ["Host early.", "Guest early.", "Host late.", "Guest late."]
    assert [s["start"] for s in segments] == pytest.approx([10.569, 24.194, 40.569, 42.194])
    assert [s["segment_id"] for s in segments] == [1, 2, 3, 4]


def test_simultaneous_speech_is_kept_for_both_participants_and_flagged(tmp_path):
    root = make_livekit_recording(tmp_path / "recording", [
        (HOST, 62.116, [(30 - HOST["offset"], 34 - HOST["offset"])], 220.0),
        (GUEST, 40.543, [(31 - GUEST["offset"], 35 - GUEST["offset"])], 660.0),
    ])
    script = {
        file_of(HOST): [fake_segment(30 - HOST["offset"], 34 - HOST["offset"], "Let us ship on Monday.")],
        file_of(GUEST): [fake_segment(31 - GUEST["offset"], 35 - GUEST["offset"], "I disagree, it needs testing.")],
    }
    segments, stats = transcribe_tracks(load_track_plan(root), scripted_transcriber(script).transcribe_track)
    assert {s["speaker"] for s in segments} == {HOST["identity"], GUEST["identity"]}
    host, guest = segments
    assert host["overlapping_speakers"] == [GUEST["identity"]]
    assert guest["overlapping_speakers"] == [HOST["identity"]]
    for seg in segments:
        assert seg["possible_overlap"] is True
        assert seg["speaker_confidence_flag"] == "medium"
        assert "possible_cross_talk" in seg["review_reasons"]
    assert stats["overlapping_segments"] == 2


def test_same_words_on_two_microphones_keep_only_the_speakers_own_track(tmp_path):
    # Host speaks loudly; a quiet copy leaks into the guest's microphone.
    root = make_livekit_recording(tmp_path / "recording", [
        (HOST, 62.116, [(30 - HOST["offset"], 34 - HOST["offset"])], 220.0),
        (GUEST, 40.543, [], 660.0),
    ])
    write_wav(root / track_entry(GUEST, 40.543)["file"], 40.543,
              speech=[(30 - GUEST["offset"], 34 - GUEST["offset"])], freq=220.0, amp=600)
    text = "The release is approved."
    script = {
        file_of(HOST): [fake_segment(30 - HOST["offset"], 34 - HOST["offset"], text)],
        file_of(GUEST): [fake_segment(30.1 - GUEST["offset"], 33.9 - GUEST["offset"], text)],
    }
    segments, stats = transcribe_tracks(load_track_plan(root), scripted_transcriber(script).transcribe_track)
    assert [s["speaker"] for s in segments] == [HOST["identity"]]
    assert segments[0]["possible_overlap"] is False
    assert stats["bleed_removed"][0]["removed"] == GUEST["identity"]


# ---------------------------------------------------------------------------
# 5: unusable tracks fall back safely; silent tracks are valid
# ---------------------------------------------------------------------------

def _problems(plan):
    return {p["problem"] for p in plan.problems}


def test_missing_track_file_disables_track_mode(tmp_path):
    root = bug_meeting(tmp_path / "recording")
    (root / track_entry(GUEST, 40.543)["file"]).unlink()
    plan = load_track_plan(root)
    assert not plan.usable and plan.reason == "unusable_tracks"
    assert _problems(plan) == {"file_missing"}


def test_empty_and_corrupt_tracks_disable_track_mode(tmp_path):
    root = bug_meeting(tmp_path / "recording")
    write_wav(root / track_entry(HOST, 62.116)["file"], 0)                        # zero frames
    (root / track_entry(GUEST, 40.543)["file"]).write_bytes(b"RIFF\x00garbage")   # not a WAV
    plan = load_track_plan(root)
    assert not plan.usable
    assert _problems(plan) == {"empty_audio", "unreadable_audio"}


@pytest.mark.parametrize("bad_path", ["../../secrets.wav", "C:/Windows/win.ini", "/etc/passwd", "", None])
def test_unsafe_track_paths_are_rejected(tmp_path, bad_path):
    root = bug_meeting(tmp_path / "recording")
    manifest = json.loads((root / "recording_manifest.json").read_text())
    manifest["segments"][0]["tracks"][1]["file"] = bad_path
    (root / "recording_manifest.json").write_text(json.dumps(manifest))
    plan = load_track_plan(root)
    assert not plan.usable
    assert _problems(plan) == {"unsafe_or_missing_path"}


def test_track_outside_the_recording_is_rejected(tmp_path):
    root = bug_meeting(tmp_path / "recording")
    manifest = json.loads((root / "recording_manifest.json").read_text())
    manifest["segments"][0]["tracks"][1]["recording_offset_seconds"] = 55.0     # 55 + 40.5 > 62.7
    (root / "recording_manifest.json").write_text(json.dumps(manifest))
    assert _problems(load_track_plan(root)) == {"outside_recording"}


def test_manifest_problems_fall_back_to_mixed_recording(tmp_path):
    root = tmp_path / "recording"
    root.mkdir()
    assert load_track_plan(root).reason == "no_manifest"
    (root / "recording_manifest.json").write_text("{not json")
    assert load_track_plan(root).reason == "invalid_manifest"
    (root / "recording_manifest.json").write_text(json.dumps({"source": "upload"}))
    assert load_track_plan(root).reason == "not_livekit"


def test_silent_track_is_valid_but_never_transcribed(tmp_path):
    root = make_livekit_recording(tmp_path / "recording", [
        (HOST, 62.116, [(35.5, 39.5)], 220.0),
        (GUEST, 40.543, [], 660.0),                                  # muted the whole time
    ])
    plan = load_track_plan(root)
    assert plan.usable
    assert [t.silent for t in plan.tracks] == [False, True]
    transcriber = scripted_transcriber({file_of(HOST): [fake_segment(35.5, 39.5, "Only the host spoke.")]})
    segments, stats = transcribe_tracks(plan, transcriber.transcribe_track)
    assert [s["speaker"] for s in segments] == [HOST["identity"]]
    assert [name for name, _ in transcriber.calls] == [file_of(HOST)]
    assert stats["per_track"][1]["skipped"] == "silent"


# ---------------------------------------------------------------------------
# 9: uncertain audio stays marked for review
# ---------------------------------------------------------------------------

def test_low_confidence_track_speech_still_requires_review(tmp_path):
    plan = load_track_plan(bug_meeting(tmp_path / "recording"))
    script = {
        file_of(HOST): [fake_segment(35.531, 39.531, "mumbled words", avg_logprob=-1.6, no_speech_prob=0.7)],
        file_of(GUEST): [fake_segment(28.0, 33.4, "Clear sentence.")],
    }
    transcriber = scripted_transcriber(script)
    segments, stats = transcribe_tracks(plan, transcriber.transcribe_track)
    host = next(s for s in segments if s["speaker"] == HOST["identity"])
    assert host["confidence_flag"] == "low"
    assert host["requires_review"] is True
    assert "low_asr_confidence" in host["review_reasons"]
    # The selective retry with a wider beam ran for the low-confidence track only.
    assert (file_of(HOST), 8) in transcriber.calls and (file_of(GUEST), 8) not in transcriber.calls
    assert stats["per_track"][0]["low_confidence_segments"] == 1


# ---------------------------------------------------------------------------
# 6: uploads / legacy recordings keep the original pipeline and cache key
# ---------------------------------------------------------------------------

def test_cache_key_only_changes_for_usable_livekit_tracks(tmp_path):
    upload = tmp_path / "upload"
    upload.mkdir()
    write_wav(upload / "recording.wav", 5)
    assert track_cache_key(upload) is None                       # upload: original hash

    root = bug_meeting(tmp_path / "recording")
    key = track_cache_key(root)
    assert key and key.startswith(tt.TRACK_MODE_VERSION)
    write_wav(root / track_entry(GUEST, 40.543)["file"], 40.543, speech=[(1, 2)], freq=500)
    assert track_cache_key(root) != key                          # tracks changed -> new key
    (root / track_entry(GUEST, 40.543)["file"]).unlink()
    assert track_cache_key(root) is None                         # unusable -> mixed path


def test_pipeline_script_uses_tracks_only_for_the_meetings_own_livekit_recording():
    source = Path("scripts/run_recorded_pipeline.py").read_text(encoding="utf-8")
    # Track mode is gated on the meeting's own recording and a usable plan;
    # the original ASR -> diarization -> merge path is kept for everything else.
    assert 'input_file == (storage.recording / "recording.wav").resolve()' in source
    assert "if track_plan is not None and track_plan.usable:" in source
    for original in ("transcriber.transcribe(", "diarizer.diarize(", "merger.merge("):
        assert original in source


# ---------------------------------------------------------------------------
# 7 / 8: consumers agree on identity; reprocessing does not duplicate
# ---------------------------------------------------------------------------

@pytest.fixture
def merger_outputs(tmp_path, monkeypatch):
    out = tmp_path / "transcript"
    out.mkdir()
    monkeypatch.setattr(speaker_merger, "TRANSCRIPT_DIR", out)
    monkeypatch.setattr(speaker_merger, "FINAL_HANDOFF_FILE", out / "meeting_transcript_ready.json")
    return out


def test_diarization_handoff_format_is_unchanged(merger_outputs):
    (merger_outputs / "t.json").write_text(json.dumps([
        {"segment_id": 1, "start": 0.0, "end": 2.0, "text": "Hi.", "confidence_flag": "normal"},
    ]))
    (merger_outputs / "d.json").write_text(json.dumps([{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"}]))
    SpeakerTranscriptMerger().merge(merger_outputs / "t.json", merger_outputs / "d.json")
    handoff = json.loads((merger_outputs / "meeting_transcript_ready.json").read_text())
    assert set(handoff[0]) == {
        "segment_id", "start", "end", "speaker", "text", "language", "asr_confidence",
        "speaker_confidence", "speaker_attribution_confidence", "possible_overlap",
        "requires_review", "review_reasons", "avg_logprob", "no_speech_prob", "no_speech_probability",
    }


def test_track_identity_reaches_index_database_and_minutes(tmp_path, merger_outputs, monkeypatch):
    from app.api.meeting_routes import create_mom
    from app.services.pipeline.intelligence_service import ensure_verification
    from tests.semantic_fakes import install_semantic_fakes

    install_semantic_fakes(monkeypatch)

    plan = load_track_plan(bug_meeting(tmp_path / "recording"))
    segments, _ = transcribe_tracks(plan, scripted_transcriber(BUG_SCRIPT).transcribe_track)
    SpeakerTranscriptMerger().write_outputs(segments)
    handoff = json.loads((merger_outputs / "meeting_transcript_ready.json").read_text())
    assert [h["speaker_name"] for h in handoff] == ["Arya", "Guest Participant"]
    assert {h["attribution_source"] for h in handoff} == {"livekit_track"}

    meeting_id = uuid4().hex
    MeetingStorage(meeting_id).ensure_dirs()
    db.insert_meeting(meeting_id, filename="live-test.wav")
    for _ in range(2):                                             # reprocessing
        persist_transcript_index(build_transcript_index({"segments": handoff}, meeting_id), meeting_id)
    utterances = db.get_utterances(meeting_id)
    assert len(utterances) == 2                                    # no duplicates
    assert [(u.speaker_id, u.speaker_name) for u in utterances] == [
        (HOST["identity"], "Arya"),
        (GUEST["identity"], "Guest Participant"),
    ]
    assert utterances[1].text.endswith("by Friday. Thank you.")

    db.upsert_extraction_run(meeting_id, status="completed", mode_used="rules", accepted=0, dropped=0)
    ensure_verification(meeting_id, force=True)
    mom = create_mom(meeting_id)
    assert mom["participants"] == ["Arya", "Guest Participant"]   # PDF: "2 speakers"


def test_speaker_turns_mirror_track_segments(tmp_path):
    plan = load_track_plan(bug_meeting(tmp_path / "recording"))
    segments, _ = transcribe_tracks(plan, scripted_transcriber(BUG_SCRIPT).transcribe_track)
    turns = speaker_turns(segments)
    assert [(t["speaker"], t["speaker_name"], t["source"]) for t in turns] == [
        (HOST["identity"], "Arya", "livekit_track"),
        (GUEST["identity"], "Guest Participant", "livekit_track"),
    ]
