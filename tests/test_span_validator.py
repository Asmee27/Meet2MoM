from __future__ import annotations

"""
tests/test_span_validator.py
============================
Unit tests for span_validator.py proving deterministic invariant enforcement.
"""

from pathlib import Path
import pytest

from app.core import db as _db
from app.schemas.utterance import Utterance
from app.services.intelligence.context_builder import ExtractionWindow
from app.services.intelligence.span_validator import (
    DropReason,
    DroppedClaim,
    validate_candidates,
)


def _setup_test_meeting(mid: str, db_path: Path):
    _db.init_db(db_path)
    _db.insert_meeting(mid, filename="test.mp4", status="indexed", db_path=db_path)

    utts = [
        Utterance(
            meeting_id=mid,
            utt_id=f"u{i:05d}",
            start=float(i * 2),
            end=float(i * 2 + 1.5),
            speaker_id="SPEAKER_00",
            text=f"Utterance text number {i}",
            asr_conf=0.9,
            spk_conf=0.9,
        )
        for i in range(6)
    ]
    _db.bulk_insert_utterances(mid, utts, db_path=db_path)


def test_drop_unresolvable_utt_id(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test.db"
    mid = "meeting_unresolvable_test"
    _setup_test_meeting(mid, test_db)
    monkeypatch.setattr("app.core.db.DB_PATH", test_db)

    window = ExtractionWindow(
        window_id="w000",
        meeting_id=mid,
        utt_ids=["u00000", "u00001"],
        start_time=0.0,
        end_time=3.5,
        text_block="text",
        topic_id=None,
    )

    candidates = [
        {
            "meeting_id": mid,
            "window_id": "w000",
            "claim_type": "decision",
            "text": "Decision citing unresolvable id",
            "state": "accepted",
            "evidence": ["u00000", "u99999"],
        }
    ]

    accepted, dropped = validate_candidates(mid, candidates, [window])
    assert len(accepted) == 0
    assert len(dropped) == 1
    assert dropped[0].reason == DropReason.UNRESOLVABLE_UTT_ID
    assert "u99999" in dropped[0].detail


def test_drop_empty_evidence(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test.db"
    mid = "meeting_empty_ev_test"
    _setup_test_meeting(mid, test_db)
    monkeypatch.setattr("app.core.db.DB_PATH", test_db)

    window = ExtractionWindow(
        window_id="w000",
        meeting_id=mid,
        utt_ids=["u00000", "u00001"],
        start_time=0.0,
        end_time=3.5,
        text_block="text",
        topic_id=None,
    )

    candidates = [
        {
            "meeting_id": mid,
            "window_id": "w000",
            "claim_type": "decision",
            "text": "Decision with empty evidence",
            "state": "accepted",
            "evidence": [],
        },
        {
            "meeting_id": mid,
            "window_id": "w000",
            "claim_type": "action",
            "text": "Action with None evidence",
            "owner_type": "unassigned",
            "evidence": None,
        },
    ]

    accepted, dropped = validate_candidates(mid, candidates, [window])
    assert len(accepted) == 0
    assert len(dropped) == 2
    assert dropped[0].reason == DropReason.EMPTY_EVIDENCE
    assert dropped[1].reason == DropReason.EMPTY_EVIDENCE


def test_drop_out_of_window(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test.db"
    mid = "meeting_out_of_window_test"
    _setup_test_meeting(mid, test_db)
    monkeypatch.setattr("app.core.db.DB_PATH", test_db)

    w0 = ExtractionWindow(
        window_id="w000",
        meeting_id=mid,
        utt_ids=["u00000", "u00001"],
        start_time=0.0,
        end_time=3.5,
        text_block="text 0",
        topic_id=None,
    )
    w1 = ExtractionWindow(
        window_id="w001",
        meeting_id=mid,
        utt_ids=["u00004", "u00005"],
        start_time=8.0,
        end_time=11.5,
        text_block="text 1",
        topic_id=None,
    )

    # u00004 exists in meeting (w1), but candidate was produced by w0!
    candidates = [
        {
            "meeting_id": mid,
            "window_id": "w000",
            "claim_type": "action",
            "text": "Action citing id from different window",
            "owner_type": "unassigned",
            "evidence": ["u00004"],
        }
    ]

    accepted, dropped = validate_candidates(mid, candidates, [w0, w1])
    assert len(accepted) == 0
    assert len(dropped) == 1
    assert dropped[0].reason == DropReason.OUT_OF_WINDOW
    assert "u00004" in dropped[0].detail


def test_drop_duplicate_claim_overlapping_windows(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test.db"
    mid = "meeting_dup_test"
    _setup_test_meeting(mid, test_db)
    monkeypatch.setattr("app.core.db.DB_PATH", test_db)

    # Overlapping windows sharing u00002
    w0 = ExtractionWindow(
        window_id="w000",
        meeting_id=mid,
        utt_ids=["u00000", "u00001", "u00002"],
        start_time=0.0,
        end_time=5.5,
        text_block="block 0",
        topic_id=None,
    )
    w1 = ExtractionWindow(
        window_id="w001",
        meeting_id=mid,
        utt_ids=["u00002", "u00003", "u00004"],
        start_time=4.0,
        end_time=9.5,
        text_block="block 1",
        topic_id=None,
    )

    candidates = [
        {
            "meeting_id": mid,
            "window_id": "w000",
            "claim_type": "decision",
            "text": "Deploy the service on Friday.",
            "state": "accepted",
            "evidence": ["u00001", "u00002"],
        },
        {
            "meeting_id": mid,
            "window_id": "w001",
            "claim_type": "decision",
            "text": "   deploy the service on friday!   ",
            "state": "accepted",
            "evidence": ["u00002", "u00003"],
        },
    ]

    accepted, dropped = validate_candidates(mid, candidates, [w0, w1])
    assert len(accepted) == 1
    assert len(dropped) == 1
    assert accepted[0].text == "Deploy the service on Friday."
    assert dropped[0].reason == DropReason.DUPLICATE_CLAIM


def test_valid_claim_accepted_with_sorted_deduped_evidence_and_id(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test.db"
    mid = "meeting_valid_test"
    _setup_test_meeting(mid, test_db)
    monkeypatch.setattr("app.core.db.DB_PATH", test_db)

    window = ExtractionWindow(
        window_id="w000",
        meeting_id=mid,
        utt_ids=["u00000", "u00001", "u00002"],
        start_time=0.0,
        end_time=5.5,
        text_block="text",
        topic_id=None,
    )

    candidates = [
        {
            "meeting_id": mid,
            "window_id": "w000",
            "claim_type": "decision",
            "text": "Use PostgreSQL database",
            "state": "accepted",
            "evidence": ["u00002", "u00000", "u00002"],  # out of order & duplicate
        },
        {
            "meeting_id": mid,
            "window_id": "w000",
            "claim_type": "action",
            "text": "Set up database connection",
            "owner": "Alice",
            "owner_type": "explicit_third_person",
            "evidence": ["u00001"],
        },
    ]

    accepted, dropped = validate_candidates(mid, candidates, [window])
    assert len(dropped) == 0
    assert len(accepted) == 2

    dec = accepted[0]
    act = accepted[1]

    assert dec.claim_id == f"{mid}_d00000"
    # u00000 has start 0.0, u00002 has start 4.0 -> sorted!
    assert dec.evidence == ["u00000", "u00002"]

    assert act.claim_id == f"{mid}_a00000"
    assert act.evidence == ["u00001"]


def test_accepted_plus_dropped_counts_match_input_count(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test.db"
    mid = "meeting_counts_test"
    _setup_test_meeting(mid, test_db)
    monkeypatch.setattr("app.core.db.DB_PATH", test_db)

    w0 = ExtractionWindow(
        window_id="w000",
        meeting_id=mid,
        utt_ids=["u00000", "u00001", "u00002"],
        start_time=0.0,
        end_time=5.5,
        text_block="text 0",
        topic_id=None,
    )
    w1 = ExtractionWindow(
        window_id="w001",
        meeting_id=mid,
        utt_ids=["u00002", "u00003"],
        start_time=4.0,
        end_time=7.5,
        text_block="text 1",
        topic_id=None,
    )

    candidates = [
        # 1. Empty evidence -> EMPTY_EVIDENCE
        {"meeting_id": mid, "window_id": "w000", "claim_type": "decision", "text": "c1", "evidence": []},
        # 2. Invalid schema (invalid regex id) -> SCHEMA_INVALID
        {"meeting_id": mid, "window_id": "w000", "claim_type": "decision", "text": "c2", "evidence": ["u123"]},
        # 3. Unresolvable id -> UNRESOLVABLE_UTT_ID
        {"meeting_id": mid, "window_id": "w000", "claim_type": "decision", "text": "c3", "state": "accepted", "evidence": ["u99999"]},
        # 4. Out of window -> OUT_OF_WINDOW
        {"meeting_id": mid, "window_id": "w000", "claim_type": "decision", "text": "c4", "state": "accepted", "evidence": ["u00003"]},
        # 5. Valid claim -> ACCEPTED
        {"meeting_id": mid, "window_id": "w000", "claim_type": "decision", "text": "Valid decision", "state": "accepted", "evidence": ["u00000"]},
        # 6. Duplicate of valid claim -> DUPLICATE_CLAIM
        {"meeting_id": mid, "window_id": "w000", "claim_type": "decision", "text": "valid decision!", "state": "accepted", "evidence": ["u00000"]},
    ]

    accepted, dropped = validate_candidates(mid, candidates, [w0, w1])
    assert len(accepted) + len(dropped) == len(candidates)
    assert len(accepted) == 1
    assert len(dropped) == 5
