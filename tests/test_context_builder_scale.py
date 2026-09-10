from __future__ import annotations

from uuid import uuid4

from app.core import db
from app.schemas.utterance import Utterance
from app.services.intelligence.context_builder import build_windows
from app.services.intelligence.span_validator import DropReason, validate_candidates


def _populate(meeting_id: str) -> None:
    db.insert_meeting(meeting_id, filename="synthetic.wav")
    rows = [
        Utterance(
            meeting_id=meeting_id,
            utt_id=f"u{i:05d}",
            start=i * 8.0,
            end=i * 8.0 + 4.0,
            speaker_id=f"SPK_{i % 4:02d}",
            text=f"Synthetic utterance {i}",
            asr_conf=0.8,
            spk_conf=0.8,
            conf_source="numeric",
        )
        for i in range(300)
    ]
    db.bulk_insert_utterances(meeting_id, rows)


def test_large_windows_and_validation_paths():
    db.init_db()
    meeting_id = uuid4().hex
    _populate(meeting_id)
    windows = build_windows(meeting_id, max_utterances=60, overlap_utterances=10)

    assert len(windows) == 6
    assert set().union(*(set(w.utt_ids) for w in windows)) == {
        f"u{i:05d}" for i in range(300)
    }
    for first, second in zip(windows, windows[1:]):
        assert len(set(first.utt_ids) & set(second.utt_ids)) == 10

    wrong_window = {
        "claim_id": "wrong-window",
        "meeting_id": meeting_id,
        "claim_type": "decision",
        "text": "A decision",
        "state": "accepted",
        "evidence": [windows[0].utt_ids[0]],
        "window_id": windows[3].window_id,
    }
    _, dropped = validate_candidates(meeting_id, [wrong_window], windows)
    assert dropped[0].reason == DropReason.OUT_OF_WINDOW

    duplicate = {
        "claim_id": "duplicate",
        "meeting_id": meeting_id,
        "claim_type": "decision",
        "text": "An overlapping decision",
        "state": "accepted",
        "evidence": [windows[0].utt_ids[-1]],
        "window_id": windows[0].window_id,
    }
    duplicate_again = dict(duplicate)
    duplicate_again["claim_id"] = "duplicate-again"
    duplicate_again["window_id"] = windows[1].window_id
    accepted, dropped = validate_candidates(
        meeting_id, [duplicate, duplicate_again], windows
    )
    assert len(accepted) == 1
    assert len(dropped) == 1
    assert dropped[0].reason == DropReason.DUPLICATE_CLAIM
