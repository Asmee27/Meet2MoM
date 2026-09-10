"""
tests/test_db.py
================
Proves that insert_claim_with_evidence enforces the foreign-key constraint:
when ANY evidence utt_id is missing from the utterances table the ENTIRE
claim insert rolls back and IntegrityError is raised.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.db import (
    get_conn,
    get_meeting,
    init_db,
    insert_meeting,
    bulk_insert_utterances,
    insert_claim_with_evidence,
    get_utterances,
    get_claims,
    upsert_job_status,
    get_job_status,
)
from app.schemas.utterance import Utterance
from app.schemas.claim import Decision
from app.schemas.job import JobStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    init_db(db)
    return db


def _utt(meeting_id: str, utt_id: str, start: float = 0.0) -> Utterance:
    return Utterance(
        meeting_id=meeting_id,
        utt_id=utt_id,
        start=start,
        end=start + 1.0,
        speaker_id="SPEAKER_00",
        text="Hello world",
        lang_tags=["en"],
        script="latin",
        asr_conf=0.9,
        spk_conf=0.85,
    )


def _decision(meeting_id: str, claim_id: str, evidence: list[str]) -> Decision:
    return Decision(
        claim_id=claim_id,
        meeting_id=meeting_id,
        text="We decided to ship next week.",
        evidence=evidence,
        state="accepted",
        confidence=0.9,
        rationale="Agreed unanimously.",
    )


# ---------------------------------------------------------------------------
# FK enforcement
# ---------------------------------------------------------------------------

def test_insert_claim_missing_evidence_raises_and_rolls_back(tmp_path: Path):
    db = _fresh_db(tmp_path)
    mid = "aa" * 16  # 32 hex chars

    insert_meeting(mid, filename="test.mp4", status="created", db_path=db)
    bulk_insert_utterances(mid, [_utt(mid, "u00000", 1.0)], db_path=db)

    decision = _decision(mid, "claim-001", ["u00000", "u99999"])  # u99999 missing

    with pytest.raises(sqlite3.IntegrityError):
        insert_claim_with_evidence(decision, db_path=db)

    # Full rollback: no claim row should exist
    assert get_claims(mid, db_path=db) == []


def test_insert_claim_valid_evidence_succeeds(tmp_path: Path):
    db = _fresh_db(tmp_path)
    mid = "bb" * 16

    insert_meeting(mid, filename="ok.mp4", status="created", db_path=db)
    bulk_insert_utterances(mid, [_utt(mid, "u00000"), _utt(mid, "u00001", 2.0)], db_path=db)

    decision = _decision(mid, "claim-002", ["u00000", "u00001"])
    insert_claim_with_evidence(decision, db_path=db)

    claims = get_claims(mid, db_path=db)
    assert len(claims) == 1
    assert claims[0]["claim_id"] == "claim-002"
    assert set(claims[0]["evidence"]) == {"u00000", "u00001"}


def test_get_utterances_ordered_by_start(tmp_path: Path):
    db = _fresh_db(tmp_path)
    mid = "cc" * 16

    insert_meeting(mid, db_path=db)
    bulk_insert_utterances(
        mid,
        [_utt(mid, "u00002", 5.0), _utt(mid, "u00000", 0.0), _utt(mid, "u00001", 2.5)],
        db_path=db,
    )

    result = get_utterances(mid, db_path=db)
    starts = [u.start for u in result]
    assert starts == sorted(starts)


def test_upsert_job_status_roundtrip(tmp_path: Path):
    db = _fresh_db(tmp_path)
    mid = "dd" * 16

    job = JobStatus(
        meeting_id=mid,
        stage="asr",
        progress=0.5,
        state="running",
        updated_at=datetime.now(timezone.utc),
    )
    upsert_job_status(job, db_path=db)

    r = get_job_status(mid, db_path=db)
    assert r is not None and r.stage == "asr"

    job2 = JobStatus(
        meeting_id=mid, stage="done", progress=1.0, state="done",
        updated_at=datetime.now(timezone.utc),
    )
    upsert_job_status(job2, db_path=db)
    r2 = get_job_status(mid, db_path=db)
    assert r2.stage == "done" and r2.progress == 1.0


def test_bulk_insert_recreates_missing_meeting_parent(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    mid = "ee" * 16

    bulk_insert_utterances(mid, [_utt(mid, "u00000")], db_path=db_path)

    assert get_meeting(mid, db_path=db_path)["meeting_id"] == mid
    assert len(get_utterances(mid, db_path=db_path)) == 1


def test_repeated_utterance_persistence_is_idempotent(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    mid = "ab" * 16
    utterances = [_utt(mid, "u00000"), _utt(mid, "u00001", 2.0)]

    bulk_insert_utterances(mid, utterances, db_path=db_path)
    bulk_insert_utterances(mid, utterances, db_path=db_path)

    assert len(get_utterances(mid, db_path=db_path)) == 2


def test_insert_meeting_upsert_preserves_parent_row(tmp_path: Path):
    db_path = _fresh_db(tmp_path)
    mid = "ff" * 16

    insert_meeting(mid, filename="original.mp4", duration=12.0, status="created", db_path=db_path)
    insert_meeting(mid, filename="replacement.mp4", duration=99.0, status="indexed", db_path=db_path)

    meeting = get_meeting(mid, db_path=db_path)
    assert meeting["meeting_id"] == mid
    assert meeting["filename"] == "original.mp4"
    assert meeting["duration"] == 12.0
    assert meeting["status"] == "indexed"


def test_foreign_keys_remain_enabled(tmp_path: Path):
    db_path = _fresh_db(tmp_path)

    with get_conn(db_path) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
