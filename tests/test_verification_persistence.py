"""
Phase 1 persistence tests: versioned migration, cascade/idempotency of
verification rows, and FK-safe evidence corrections.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.core import db
from app.schemas.claim import Decision
from app.schemas.utterance import Utterance

NEW_TABLES = {"extraction_runs", "verification_results", "verification_runs", "evidence_corrections"}


def _tables(path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _seed(path, meeting_id="m1"):
    db.insert_meeting(meeting_id, filename="real.wav", db_path=path)
    db.bulk_insert_utterances(meeting_id, [
        Utterance(meeting_id=meeting_id, utt_id="u00000", start=0.0, end=2.0, speaker_id="S0",
                  text="We decided to ship on Friday."),
        Utterance(meeting_id=meeting_id, utt_id="u00001", start=2.0, end=4.0, speaker_id="S1",
                  text="Agreed."),
    ], db_path=path)
    db.insert_claim_with_evidence(
        Decision(claim_id=f"{meeting_id}_d00000", meeting_id=meeting_id, text="Ship on Friday",
                 evidence=["u00000"], state="accepted"),
        db_path=path,
    )


def _verification_row(claim_id, method="fusion", label="verified"):
    return {"claim_id": claim_id, "method": method, "label": label, "cited_utt_ids": ["u00000"],
            "reasons": [{"code": "x", "severity": "info", "message": "m", "utt_ids": []}]}


def test_migration_upgrades_a_v0_database_without_touching_existing_rows(tmp_path):
    path = tmp_path / "legacy.db"
    db.init_db(path)
    _seed(path)
    # Recreate the pre-Phase-1 state: no new tables, user_version 0.
    with sqlite3.connect(path) as conn:
        for table in NEW_TABLES:
            conn.execute(f"DROP TABLE {table}")
        conn.execute("PRAGMA user_version = 0")
    with sqlite3.connect(path) as conn:
        before = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                  for t in ("meetings", "utterances", "claims", "evidence_links")}

    db.init_db(path)
    db.init_db(path)  # idempotent

    assert NEW_TABLES <= _tables(path)
    assert db.get_schema_version(path) == db.SCHEMA_VERSION == 1
    with sqlite3.connect(path) as conn:
        after = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in before}
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert after == before


def test_verification_rows_cascade_when_claims_are_re_extracted(tmp_path):
    path = tmp_path / "t.db"
    db.init_db(path)
    _seed(path)
    db.replace_verification_results("m1", [_verification_row("m1_d00000")], db_path=path)
    db.record_evidence_correction("m1_d00000", "m1", "rejected", ["u00000"], ["u00001"], db_path=path)

    db.delete_claims_for_meeting("m1", db_path=path)  # what a re-extraction does first

    assert db.get_verification_results("m1", db_path=path) == {}
    assert db.get_evidence_corrections("m1", db_path=path) == {}


def test_replacing_results_never_duplicates(tmp_path):
    path = tmp_path / "t.db"
    db.init_db(path)
    _seed(path)
    rows = [_verification_row("m1_d00000", m) for m in ("deterministic", "semantic_nli", "fusion")]
    for _ in range(3):
        db.replace_verification_results("m1", rows, db_path=path)
    with db.get_conn(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM verification_results").fetchone()[0] == 3


def test_verification_row_requires_an_existing_claim(tmp_path):
    path = tmp_path / "t.db"
    db.init_db(path)
    _seed(path)
    with pytest.raises(sqlite3.IntegrityError):
        db.replace_verification_results("m1", [_verification_row("m1_ghost")], db_path=path)


def test_accepted_correction_with_unknown_utterance_rolls_back(tmp_path):
    path = tmp_path / "t.db"
    db.init_db(path)
    _seed(path)

    with pytest.raises(sqlite3.IntegrityError):
        db.record_evidence_correction("m1_d00000", "m1", "accepted", ["u00000"], ["u99999"], db_path=path)

    claim = db.get_claims("m1", db_path=path)[0]
    assert claim["evidence"] == ["u00000"]           # evidence unchanged
    assert db.get_evidence_corrections("m1", db_path=path) == {}  # nothing recorded


def test_accepted_correction_replaces_evidence_links_and_payload(tmp_path):
    path = tmp_path / "t.db"
    db.init_db(path)
    _seed(path)

    db.record_evidence_correction("m1_d00000", "m1", "accepted", ["u00000"], ["u00000", "u00001"], db_path=path)

    claim = db.get_claims("m1", db_path=path)[0]
    assert claim["evidence"] == ["u00000", "u00001"]
    assert claim["payload"]["evidence"] == ["u00000", "u00001"]
    correction = db.get_evidence_corrections("m1", db_path=path)["m1_d00000"]
    assert correction["original_utt_ids"] == ["u00000"]


def test_extraction_run_status_round_trip(tmp_path):
    path = tmp_path / "t.db"
    db.init_db(path)
    db.upsert_extraction_run("m2", status="no_claims", mode_requested="llm", mode_used="rules",
                             fallback_reason="stub", accepted=0, dropped=0, db_path=path)
    db.upsert_extraction_run("m2", status="completed", mode_requested="rules", mode_used="rules",
                             accepted=3, dropped=1, db_path=path)
    run = db.get_extraction_run("m2", db_path=path)
    assert run["status"] == "completed" and run["accepted"] == 3  # latest attempt only
