from __future__ import annotations

"""
tests/test_claim_writer.py
==========================
Tests for atomic persistence, foreign key enforcement, orphan check,
and idempotency in claim_writer.py.
"""

from pathlib import Path
import sqlite3
import pytest

from app.core import db as _db
from app.schemas.claim import Decision, ActionItem
from app.schemas.utterance import Utterance
from app.services.intelligence.claim_writer import persist_extraction
from app.services.intelligence.span_validator import DroppedClaim, DropReason


def _setup_meeting_and_utts(mid: str, db_path: Path):
    _db.init_db(db_path)
    _db.insert_meeting(mid, filename="test.mp4", status="indexed", db_path=db_path)
    utts = [
        Utterance(
            meeting_id=mid,
            utt_id=f"u{i:05d}",
            start=float(i * 2),
            end=float(i * 2 + 1.5),
            speaker_id="SPEAKER_00",
            text=f"Utterance text {i}",
            asr_conf=0.9,
            spk_conf=0.9,
        )
        for i in range(5)
    ]
    _db.bulk_insert_utterances(mid, utts, db_path=db_path)


def test_happy_path_persists_claims_and_evidence(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr("app.core.db.DB_PATH", test_db)
    monkeypatch.setattr("app.core.config.STORAGE_ROOT", tmp_path / "storage")
    monkeypatch.setattr("app.services.intelligence.claim_writer.STORAGE_ROOT", tmp_path / "storage")

    mid = "meeting_happy_path"
    _setup_meeting_and_utts(mid, test_db)

    accepted = [
        Decision(
            claim_id=f"{mid}_d00000",
            meeting_id=mid,
            text="Adopt React and TypeScript",
            evidence=["u00000", "u00001"],
            state="accepted",
        ),
        ActionItem(
            claim_id=f"{mid}_a00000",
            meeting_id=mid,
            text="Build API routes",
            evidence=["u00002"],
            owner="Alice",
            owner_type="explicit_third_person",
        ),
    ]

    dropped = [
        DroppedClaim(
            meeting_id=mid,
            window_id="w000",
            claim_type="decision",
            text="Dropped decision with empty evidence",
            cited_evidence=[],
            reason=DropReason.EMPTY_EVIDENCE,
            detail="empty evidence",
        )
    ]

    stats = persist_extraction(mid, accepted, dropped, db_path=test_db)
    assert stats["accepted"] == 2
    assert stats["dropped"] == 1
    assert stats["by_reason"] == {"empty_evidence": 1}
    assert stats["drop_rate"] == pytest.approx(1 / 3)

    # Check evidence_links row count == total evidence ids (2 + 1 = 3)
    with _db.get_conn(test_db) as conn:
        ev_count = conn.execute("SELECT COUNT(*) FROM evidence_links WHERE meeting_id = ?", (mid,)).fetchone()[0]
        claim_count = conn.execute("SELECT COUNT(*) FROM claims WHERE meeting_id = ?", (mid,)).fetchone()[0]
        assert claim_count == 2
        assert ev_count == 3

    # Check mirror file
    mirror_file = tmp_path / "storage" / mid / "intelligence" / "claims.json"
    assert mirror_file.exists()


def test_atomic_rollback_on_missing_evidence_invariant(tmp_path: Path, monkeypatch):
    """
    INVARIANT TEST: a batch where one claim cites a non-existent utt_id ->
    IntegrityError raised AND SELECT COUNT(*) FROM claims == 0 (whole batch rolled back)
    """
    test_db = tmp_path / "test.db"
    monkeypatch.setattr("app.core.db.DB_PATH", test_db)
    monkeypatch.setattr("app.core.config.STORAGE_ROOT", tmp_path / "storage")
    monkeypatch.setattr("app.services.intelligence.claim_writer.STORAGE_ROOT", tmp_path / "storage")

    mid = "meeting_rollback_test"
    _setup_meeting_and_utts(mid, test_db)

    accepted = [
        Decision(
            claim_id=f"{mid}_d00000",
            meeting_id=mid,
            text="Valid decision with real utterance",
            evidence=["u00000"],
            state="accepted",
        ),
        ActionItem(
            claim_id=f"{mid}_a00000",
            meeting_id=mid,
            text="Invalid action citing ghost utterance",
            evidence=["u99999"],  # Does not exist in utterances!
            owner="Bob",
            owner_type="explicit_third_person",
        ),
    ]

    with pytest.raises(sqlite3.IntegrityError):
        persist_extraction(mid, accepted, [], db_path=test_db)

    # Full batch rolled back: zero claims in table!
    with _db.get_conn(test_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM claims WHERE meeting_id = ?", (mid,)).fetchone()[0]
        assert count == 0
        ev_count = conn.execute("SELECT COUNT(*) FROM evidence_links WHERE meeting_id = ?", (mid,)).fetchone()[0]
        assert ev_count == 0


def test_orphan_check_query_returns_zero_rows(tmp_path: Path, monkeypatch):
    """
    ORPHAN CHECK: after a successful run, this query returns zero rows:
      SELECT c.claim_id FROM claims c
      LEFT JOIN evidence_links e ON c.claim_id = e.claim_id
      WHERE e.claim_id IS NULL
    """
    test_db = tmp_path / "test.db"
    monkeypatch.setattr("app.core.db.DB_PATH", test_db)
    monkeypatch.setattr("app.core.config.STORAGE_ROOT", tmp_path / "storage")
    monkeypatch.setattr("app.services.intelligence.claim_writer.STORAGE_ROOT", tmp_path / "storage")

    mid = "meeting_orphan_test"
    _setup_meeting_and_utts(mid, test_db)

    accepted = [
        Decision(
            claim_id=f"{mid}_d00000",
            meeting_id=mid,
            text="Decision 1",
            evidence=["u00001"],
            state="accepted",
        ),
        ActionItem(
            claim_id=f"{mid}_a00000",
            meeting_id=mid,
            text="Action 1",
            evidence=["u00002", "u00003"],
            owner_type="unassigned",
        ),
    ]

    persist_extraction(mid, accepted, [], db_path=test_db)

    with _db.get_conn(test_db) as conn:
        orphans = conn.execute(
            """
            SELECT c.claim_id FROM claims c
            LEFT JOIN evidence_links e ON c.claim_id = e.claim_id
            WHERE e.claim_id IS NULL
            """
        ).fetchall()
        assert len(orphans) == 0


def test_idempotency_persisting_twice_yields_identical_counts(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr("app.core.db.DB_PATH", test_db)
    monkeypatch.setattr("app.core.config.STORAGE_ROOT", tmp_path / "storage")
    monkeypatch.setattr("app.services.intelligence.claim_writer.STORAGE_ROOT", tmp_path / "storage")

    mid = "meeting_idempotency_test"
    _setup_meeting_and_utts(mid, test_db)

    accepted = [
        Decision(
            claim_id=f"{mid}_d00000",
            meeting_id=mid,
            text="Idempotent Decision",
            evidence=["u00000"],
            state="accepted",
        )
    ]
    dropped = [
        DroppedClaim(
            meeting_id=mid,
            window_id="w000",
            claim_type="decision",
            text="dropped text",
            cited_evidence=["u99999"],
            reason=DropReason.UNRESOLVABLE_UTT_ID,
            detail="unresolvable",
        )
    ]

    stats1 = persist_extraction(mid, accepted, dropped, db_path=test_db)
    stats2 = persist_extraction(mid, accepted, dropped, db_path=test_db)

    assert stats1 == stats2

    with _db.get_conn(test_db) as conn:
        claim_count = conn.execute("SELECT COUNT(*) FROM claims WHERE meeting_id = ?", (mid,)).fetchone()[0]
        ev_count = conn.execute("SELECT COUNT(*) FROM evidence_links WHERE meeting_id = ?", (mid,)).fetchone()[0]
        drop_count = conn.execute("SELECT COUNT(*) FROM dropped_claims WHERE meeting_id = ?", (mid,)).fetchone()[0]

        assert claim_count == 1
        assert ev_count == 1
        assert drop_count == 1


def test_dropped_claims_queryable_by_reason(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr("app.core.db.DB_PATH", test_db)
    monkeypatch.setattr("app.core.config.STORAGE_ROOT", tmp_path / "storage")
    monkeypatch.setattr("app.services.intelligence.claim_writer.STORAGE_ROOT", tmp_path / "storage")

    mid = "meeting_dropped_query_test"
    _setup_meeting_and_utts(mid, test_db)

    dropped = [
        DroppedClaim(
            meeting_id=mid,
            window_id="w000",
            claim_type="decision",
            text="drop 1",
            cited_evidence=[],
            reason=DropReason.EMPTY_EVIDENCE,
            detail="empty",
        ),
        DroppedClaim(
            meeting_id=mid,
            window_id="w001",
            claim_type="action",
            text="drop 2",
            cited_evidence=["u00004"],
            reason=DropReason.OUT_OF_WINDOW,
            detail="out of window",
        ),
    ]

    persist_extraction(mid, [], dropped, db_path=test_db)

    retrieved = _db.get_dropped_claims(mid, db_path=test_db)
    assert len(retrieved) == 2
    reasons = {r["reason"] for r in retrieved}
    assert reasons == {"empty_evidence", "out_of_window"}
