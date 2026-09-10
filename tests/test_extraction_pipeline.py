from __future__ import annotations

"""
tests/test_extraction_pipeline.py
=================================
Tests for extractor selection, rule-based baseline adapter, and LLM fallback.
"""

import json
from pathlib import Path
import pytest

from app.core import db as _db
from app.core.llm_client import LLMClient, LLMClientError
from app.core.storage import MeetingStorage
from app.schemas.utterance import Utterance
from app.services.intelligence.extraction_pipeline import run_extraction


def _setup_fixture_meeting(mid: str, test_db: Path, storage_root: Path):
    _db.init_db(test_db)
    _db.insert_meeting(mid, filename="test.mp4", status="indexed", db_path=test_db)

    # Insert utterances designed to trigger both rule-based extractors
    utts = [
        Utterance(
            meeting_id=mid,
            utt_id="u00000",
            start=0.0,
            end=3.0,
            speaker_id="SPEAKER_00",
            speaker_name="Alice",
            text="We decided to migrate our primary database to PostgreSQL.",
            asr_conf=0.95,
            spk_conf=0.90,
        ),
        Utterance(
            meeting_id=mid,
            utt_id="u00001",
            start=3.5,
            end=7.0,
            speaker_id="SPEAKER_01",
            speaker_name="Bob",
            text="Bob will configure the database server by Friday.",
            asr_conf=0.92,
            spk_conf=0.88,
        ),
    ]
    _db.bulk_insert_utterances(mid, utts, db_path=test_db)

    # Create storage artifacts (topics and transcript)
    storage = MeetingStorage(mid)
    (storage.root / "transcript").mkdir(parents=True, exist_ok=True)
    (storage.root / "intelligence").mkdir(parents=True, exist_ok=True)

    topics_data = [
        {
            "topic_id": "topic_0",
            "topic_label": "Database Migration",
            "start": 0.0,
            "end": 7.0,
            "text": "We decided to migrate our primary database to PostgreSQL. Bob will configure the database server by Friday.",
            "speakers": ["Alice", "Bob"],
        }
    ]
    (storage.intelligence / "topics.json").write_text(
        json.dumps(topics_data, indent=2), encoding="utf-8"
    )

    transcript_data = {
        "segments": [
            {
                "start": 0.0,
                "end": 3.0,
                "speaker": "Alice",
                "text": "We decided to migrate our primary database to PostgreSQL.",
                "speaker_attribution_confidence": 0.90,
            },
            {
                "start": 3.5,
                "end": 7.0,
                "speaker": "Bob",
                "text": "Bob will configure the database server by Friday.",
                "speaker_attribution_confidence": 0.88,
            },
        ]
    }
    (storage.transcript / "frontend_transcript.json").write_text(
        json.dumps(transcript_data, indent=2), encoding="utf-8"
    )


def test_extractor_rules_end_to_end(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test.db"
    storage_root = tmp_path / "storage"
    monkeypatch.setattr("app.core.db.DB_PATH", test_db)
    monkeypatch.setattr("app.core.config.STORAGE_ROOT", storage_root)
    monkeypatch.setattr("app.services.intelligence.claim_writer.STORAGE_ROOT", storage_root)
    monkeypatch.setattr("app.core.storage.STORAGE_ROOT", storage_root)

    mid = "meeting_rules_e2e"
    _setup_fixture_meeting(mid, test_db, storage_root)

    stats = run_extraction(mid, mode="rules", db_path=test_db)

    assert stats["mode_used"] == "rules"
    assert stats["accepted"] >= 1

    claims = _db.get_claims_with_evidence(mid, db_path=test_db)
    assert len(claims) == stats["accepted"]
    for c in claims:
        assert len(c["evidence"]) >= 1
        for eid in c["evidence"]:
            assert eid.startswith("u000")


def test_extractor_llm_stub_end_to_end(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test.db"
    storage_root = tmp_path / "storage"
    monkeypatch.setattr("app.core.db.DB_PATH", test_db)
    monkeypatch.setattr("app.core.config.STORAGE_ROOT", storage_root)
    monkeypatch.setattr("app.services.intelligence.claim_writer.STORAGE_ROOT", storage_root)
    monkeypatch.setattr("app.core.storage.STORAGE_ROOT", storage_root)

    mid = "meeting_llm_e2e"
    _setup_fixture_meeting(mid, test_db, storage_root)

    client = LLMClient(provider="stub")
    client.set_stub_response({
        "decisions": [
            {
                "text": "Migrate database to PostgreSQL",
                "state": "accepted",
                "proposer": "Alice",
                "evidence": ["u00000"],
            }
        ],
        "actions": [
            {
                "text": "Configure the database server",
                "owner": "Bob",
                "owner_type": "explicit_third_person",
                "deadline_text": "by Friday",
                "evidence": ["u00001"],
            }
        ],
    })

    stats = run_extraction(mid, mode="llm", client=client, db_path=test_db)

    assert stats["mode_used"] == "llm"
    assert stats["accepted"] == 2

    claims = _db.get_claims_with_evidence(mid, db_path=test_db)
    assert len(claims) == 2
    for c in claims:
        assert len(c["evidence"]) >= 1


def test_both_modes_write_to_same_tables_via_same_codepath(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test.db"
    storage_root = tmp_path / "storage"
    monkeypatch.setattr("app.core.db.DB_PATH", test_db)
    monkeypatch.setattr("app.core.config.STORAGE_ROOT", storage_root)
    monkeypatch.setattr("app.services.intelligence.claim_writer.STORAGE_ROOT", storage_root)
    monkeypatch.setattr("app.core.storage.STORAGE_ROOT", storage_root)

    mid = "meeting_both_modes"
    _setup_fixture_meeting(mid, test_db, storage_root)

    # 1. Run rules
    stats_rules = run_extraction(mid, mode="rules", db_path=test_db)
    claims_rules = _db.get_claims_with_evidence(mid, db_path=test_db)

    # 2. Run LLM with stub
    client = LLMClient(provider="stub")
    client.set_stub_response({
        "decisions": [
            {"text": "Migrate database to PostgreSQL", "state": "accepted", "evidence": ["u00000"]}
        ],
        "actions": [
            {"text": "Configure database server", "owner": "Bob", "owner_type": "explicit_third_person", "deadline_text": "by Friday", "evidence": ["u00001"]}
        ],
    })
    stats_llm = run_extraction(mid, mode="llm", client=client, db_path=test_db)
    claims_llm = _db.get_claims_with_evidence(mid, db_path=test_db)

    # Both populated the same schema and tables
    assert set(claims_rules[0].keys()) == set(claims_llm[0].keys())
    assert "evidence" in claims_rules[0] and "evidence" in claims_llm[0]


def test_simulated_llm_client_error_falls_back_to_rules(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test.db"
    storage_root = tmp_path / "storage"
    monkeypatch.setattr("app.core.db.DB_PATH", test_db)
    monkeypatch.setattr("app.core.config.STORAGE_ROOT", storage_root)
    monkeypatch.setattr("app.services.intelligence.claim_writer.STORAGE_ROOT", storage_root)
    monkeypatch.setattr("app.core.storage.STORAGE_ROOT", storage_root)

    mid = "meeting_fallback"
    _setup_fixture_meeting(mid, test_db, storage_root)

    class FailingClient(LLMClient):
        def complete_json(self, *args, **kwargs):
            raise LLMClientError("Simulated upstream network outage")

    stats = run_extraction(mid, mode="llm", client=FailingClient(provider="stub"), db_path=test_db)

    # Proves fallback succeeded without crashing
    assert stats["mode_used"] == "rules"
    assert "error" in stats
    assert "Simulated upstream network outage" in stats["error"]
    assert stats["accepted"] >= 1

    claims = _db.get_claims_with_evidence(mid, db_path=test_db)
    assert len(claims) == stats["accepted"]
