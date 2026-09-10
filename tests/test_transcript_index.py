"""
tests/test_transcript_index.py
==============================
Tests for app/services/transcript/transcript_index.py:
- Contiguous and gap-free IDs starting at u00000
- Deterministic sorting: start ASC, end ASC, speaker ASC
- Byte-identical output across two consecutive runs
- Confidence mappings and WARNING logging for absent fields
- Window retrieval and span resolution
- DB and disk persistence
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from uuid import uuid4

import pytest

from app.core import db as _db
from app.core.config import STORAGE_ROOT
from app.services.transcript.transcript_index import (
    build_transcript_index,
    persist_transcript_index,
    get_utterance_window,
    resolve_spans,
)
from app.schemas.utterance import Utterance


def test_transcript_index_sorting_and_contiguous_ids(tmp_path: Path):
    mid = uuid4().hex
    
    # Intentionally out-of-order segments
    raw_segments = [
        {"start": 10.5, "end": 12.0, "speaker": "SPEAKER_01", "text": "Third utterance", "asr_confidence": "high", "speaker_attribution_confidence": 0.95},
        {"start": 1.0, "end": 3.0, "speaker": "SPEAKER_00", "text": "First utterance", "asr_confidence": "high", "speaker_attribution_confidence": 0.90},
        {"start": 5.0, "end": 7.0, "speaker": "SPEAKER_02", "text": "Second utterance", "asr_confidence": "medium", "speaker_attribution_confidence": 0.75},
        {"start": 10.5, "end": 11.5, "speaker": "SPEAKER_00", "text": "Tie-break by end", "asr_confidence": "low", "speaker_attribution_confidence": 0.60},
    ]

    utts = build_transcript_index(raw_segments, mid)

    assert len(utts) == 4
    # Contiguous and gap-free
    assert [u.utt_id for u in utts] == ["u00000", "u00001", "u00002", "u00003"]

    # Start times sorted: 1.0, 5.0, 10.5 (end=11.5), 10.5 (end=12.0)
    assert utts[0].text == "First utterance"
    assert utts[1].text == "Second utterance"
    assert utts[2].text == "Tie-break by end"
    assert utts[3].text == "Third utterance"
    assert utts[0].topic_id is None



def test_asr_conf_is_continuous_not_bucketed():
    """Build index with varied avg_logprob values; assert >=4 distinct values, none 0.90/0.70/0.40."""
    mid = uuid4().hex
    raw = [
        {"start": 1.0, "end": 2.0, "speaker": "SPK_0", "text": "Seg 1", "avg_logprob": -0.08},
        {"start": 2.0, "end": 3.0, "speaker": "SPK_0", "text": "Seg 2", "avg_logprob": -0.23},
        {"start": 3.0, "end": 4.0, "speaker": "SPK_0", "text": "Seg 3", "avg_logprob": -0.47},
        {"start": 4.0, "end": 5.0, "speaker": "SPK_0", "text": "Seg 4", "avg_logprob": -0.72},
    ]

    utts = build_transcript_index(raw, mid)
    asr_values = [u.asr_conf for u in utts]

    # At least 4 distinct values
    assert len(set(asr_values)) >= 4, f"Expected >=4 distinct values, got {set(asr_values)}"

    # None equal exactly the old discrete bucket values
    bucket_values = {0.90, 0.70, 0.40}
    for u in utts:
        assert u.asr_conf not in bucket_values, f"Continuous value {u.asr_conf} should not match bucket values"
        assert 0.0 <= u.asr_conf <= 1.0
        assert u.conf_source == "numeric"


def test_missing_confidence_is_none_not_one(caplog):
    """A segment with no confidence data yields asr_conf is None, spk_conf is None, and conf_source == 'missing'."""
    mid = uuid4().hex
    raw = [
        {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00", "text": "No confidence provided"},
    ]

    with caplog.at_level(logging.WARNING):
        utts = build_transcript_index(raw, mid)

    assert len(utts) == 1
    assert utts[0].asr_conf is None, "asr_conf must be None when absent, NOT 1.0"
    assert utts[0].spk_conf is None, "spk_conf must be None when absent, NOT 1.0"
    assert utts[0].conf_source == "missing"

    # Logger warning naming utt_id and fields
    log_text = caplog.text
    assert "u00000" in log_text
    assert "asr_conf" in log_text
    assert "spk_conf" in log_text


def test_ids_unchanged_after_confidence_refactor():
    """Same input produces the same deterministic utt_id sequence."""
    mid = uuid4().hex
    raw = [
        {"start": 2.0, "end": 4.0, "speaker": "SPEAKER_01", "text": "Part B", "avg_logprob": -0.15},
        {"start": 0.5, "end": 1.5, "speaker": "SPEAKER_00", "text": "Part A", "avg_logprob": -0.05},
    ]

    utts_1 = build_transcript_index(raw, mid)
    utts_2 = build_transcript_index(raw, mid)

    assert [u.utt_id for u in utts_1] == ["u00000", "u00001"]
    assert [u.utt_id for u in utts_2] == ["u00000", "u00001"]
    assert utts_1[0].text == "Part A"
    assert utts_1[1].text == "Part B"


def test_roundtrip_persist_to_db_none_stays_none():
    """Persist to DB and read back, asserting None stays None."""
    mid = uuid4().hex
    _db.init_db()
    _db.insert_meeting(mid, filename="test.mp4", status="indexed")

    utts = [
        Utterance(
            meeting_id=mid,
            utt_id="u00000",
            start=0.0,
            end=1.5,
            speaker_id="SPEAKER_00",
            text="Segment with no confidence",
            asr_conf=None,
            spk_conf=None,
            no_speech_prob=None,
            conf_source="missing",
        ),
        Utterance(
            meeting_id=mid,
            utt_id="u00001",
            start=2.0,
            end=3.5,
            speaker_id="SPEAKER_01",
            text="Segment with continuous confidence",
            asr_conf=0.8842,
            spk_conf=0.95,
            no_speech_prob=0.12,
            conf_source="numeric",
        ),
    ]

    _db.bulk_insert_utterances(mid, utts)
    retrieved = _db.get_utterances(mid)

    assert len(retrieved) == 2
    # None stays None
    assert retrieved[0].asr_conf is None
    assert retrieved[0].spk_conf is None
    assert retrieved[0].conf_source == "missing"
    assert retrieved[0].no_speech_prob is None

    # Numeric stays numeric
    assert retrieved[1].asr_conf == pytest.approx(0.8842, abs=1e-4)
    assert retrieved[1].spk_conf == pytest.approx(0.95, abs=1e-4)
    assert retrieved[1].conf_source == "numeric"
    assert retrieved[1].no_speech_prob == pytest.approx(0.12, abs=1e-4)


def test_persist_and_resolve_spans(tmp_path: Path):
    mid = uuid4().hex
    _db.init_db()
    _db.insert_meeting(mid, filename="test.mp4", status="transcribed")

    raw = [
        {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00", "text": "Intro", "avg_logprob": -0.05, "speaker_attribution_confidence": 0.95},
        {"start": 2.5, "end": 4.0, "speaker": "SPEAKER_01", "text": "Decision item", "avg_logprob": -0.10, "speaker_attribution_confidence": 0.91},
        {"start": 4.5, "end": 6.0, "speaker": "SPEAKER_00", "text": "Wrap up", "avg_logprob": -0.12, "speaker_attribution_confidence": 0.89},
    ]

    utts = build_transcript_index(raw, mid)
    index_path = persist_transcript_index(utts, mid)

    assert index_path.exists()
    assert json.loads(index_path.read_text(encoding="utf-8"))[0]["utt_id"] == "u00000"

    # Window
    window = get_utterance_window(mid, "u00000", "u00001")
    assert [u.utt_id for u in window] == ["u00000", "u00001"]

    # Spans
    found, missing = resolve_spans(mid, ["u00001", "u99999"])
    assert len(found) == 1
    assert found[0].utt_id == "u00001"
    assert missing == ["u99999"]


def test_persist_recreates_missing_meeting_and_is_idempotent(tmp_path: Path, monkeypatch):
    mid = uuid4().hex
    db_path = tmp_path / "transcript.db"
    monkeypatch.setattr(_db, "DB_PATH", db_path)
    monkeypatch.setattr(
        "app.services.transcript.transcript_index.STORAGE_ROOT",
        tmp_path / "storage",
    )
    _db.init_db(db_path)

    utterances = build_transcript_index(
        [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "Hello"}],
        mid,
    )

    persist_transcript_index(utterances, mid)
    persist_transcript_index(utterances, mid)

    assert _db.get_meeting(mid, db_path=db_path) is not None
    assert len(_db.get_utterances(mid, db_path=db_path)) == 1
