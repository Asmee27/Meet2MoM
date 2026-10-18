"""
tests/test_llm_extractor.py
===========================
Tests for:
- Stub provider returning valid JSON -> parsed candidates carry window_id and meeting_id
- Stub returning fenced ```json -> parsed correctly
- Stub returning malformed JSON twice -> raises LLMOutputError, does not hang
- Disk cache: two identical calls, second makes zero client calls (assert with counter)
- Confirm API key never appears in log output
"""
from __future__ import annotations

import json
import logging
from uuid import uuid4
import pytest

from app.core.llm_client import LLMClient, LLMClientError, LLMOutputError
from app.services.intelligence.context_builder import ExtractionWindow
from app.services.intelligence.llm_extractor import (
    extract_from_window,
    extract_meeting,
)


def _make_sample_window(meeting_id: str, window_id: str = "w000") -> ExtractionWindow:
    text_block = (
        "[u00010] SPK_01 (01:15) Alice: We should migrate database to PostgreSQL.\n"
        "[u00011] SPK_02 (01:25) Bob: Agreed, I will deploy the PostgreSQL cluster by Friday."
    )
    return ExtractionWindow(
        window_id=window_id,
        meeting_id=meeting_id,
        utt_ids=["u00010", "u00011"],
        start_time=75.0,
        end_time=85.0,
        text_block=text_block,
        topic_id="t0",
    )


def test_stub_returning_valid_json_carries_window_id():
    mid = uuid4().hex
    window = _make_sample_window(mid, window_id="w042")
    client = LLMClient(provider="stub", model="test-stub", backoff_delays=[0.01])

    stub_payload = {
        "decisions": [
            {
                "text": "Migrate database to PostgreSQL",
                "state": "accepted",
                "proposer": "Alice",
                "conditional": False,
                "condition_text": None,
                "uncertainty": "low",
                "evidence": ["u00010", "u00011"],
            }
        ],
        "actions": [
            {
                "text": "Deploy PostgreSQL cluster",
                "owner": "Bob",
                "owner_type": "explicit_first_person",
                "deadline_text": "by Friday",
                "commitment_type": "firm",
                "evidence": ["u00011"],
            }
        ],
    }
    client.set_stub_response(stub_payload)

    result = extract_from_window(window, client)
    assert result["window_id"] == "w042"
    assert result["meeting_id"] == mid

    assert len(result["decisions"]) == 1
    assert result["decisions"][0]["window_id"] == "w042"
    assert result["decisions"][0]["meeting_id"] == mid
    assert result["decisions"][0]["claim_type"] == "decision"

    assert len(result["actions"]) == 1
    assert result["actions"][0]["window_id"] == "w042"
    assert result["actions"][0]["meeting_id"] == mid
    assert result["actions"][0]["claim_type"] == "action"

    # Also test extract_meeting
    client.set_stub_response(stub_payload)
    candidates = extract_meeting(mid, [window], client)
    assert len(candidates) == 2
    for c in candidates:
        assert c["window_id"] == "w042"
        assert c["meeting_id"] == mid


def test_stub_returning_fenced_json_parsed_correctly():
    mid = uuid4().hex
    window = _make_sample_window(mid, window_id="w001")
    client = LLMClient(provider="stub", model="test-stub", backoff_delays=[0.01])

    fenced_payload = """```json
{
  "decisions": [
    {
      "text": "Adopt new testing guidelines",
      "state": "accepted",
      "proposer": "Alice",
      "conditional": false,
      "condition_text": null,
      "uncertainty": "low",
      "evidence": ["u00010"]
    }
  ],
  "actions": []
}
```"""
    client.set_stub_response(fenced_payload)

    result = extract_from_window(window, client)
    assert len(result["decisions"]) == 1
    assert result["decisions"][0]["text"] == "Adopt new testing guidelines"
    assert result["decisions"][0]["window_id"] == "w001"


def test_stub_malformed_json_twice_raises_llm_output_error():
    mid = uuid4().hex
    window = _make_sample_window(mid, window_id="w002")
    client = LLMClient(provider="stub", model="test-stub", backoff_delays=[0.01])

    # First attempt returns malformed JSON, second attempt (repair) also returns malformed JSON
    client.set_stub_response("Not valid JSON at all")
    client.set_stub_response("{also broken json: true")

    with pytest.raises(LLMOutputError) as exc_info:
        extract_from_window(window, client)

    assert "could not be parsed as valid JSON" in str(exc_info.value)


def test_disk_cache_skips_second_client_call():
    mid = uuid4().hex
    window = _make_sample_window(mid, window_id="w003")
    client = LLMClient(provider="stub", model="cache-test-model", backoff_delays=[0.01])

    stub_payload = {
        "decisions": [
            {
                "text": "Cached decision",
                "state": "accepted",
                "proposer": "Bob",
                "conditional": False,
                "condition_text": None,
                "uncertainty": "low",
                "evidence": ["u00011"],
            }
        ],
        "actions": [],
    }
    client.set_stub_response(stub_payload)

    # First call: cache miss -> invokes client
    res1 = extract_from_window(window, client)
    assert client.call_count == 1
    assert len(res1["decisions"]) == 1
    assert res1["decisions"][0]["text"] == "Cached decision"

    # Second call: cache hit -> must make ZERO client calls
    calls_before = client.call_count
    res2 = extract_from_window(window, client)
    assert client.call_count == calls_before, "Cache hit must make zero additional client calls"
    assert len(res2["decisions"]) == 1
    assert res2["decisions"][0]["text"] == "Cached decision"
    assert res2["decisions"][0]["window_id"] == "w003"


def test_api_key_never_logged(caplog):
    secret_key = "sk-super-secret-production-token-12345"
    client = LLMClient(
        provider="stub",
        model="test-stub",
        api_key=secret_key,
        backoff_delays=[0.01],
    )
    with caplog.at_level(logging.DEBUG):
        client.complete_json(system="sys", user="user")

    assert secret_key not in caplog.text, "API key must never appear in log records"


def test_stub_provider_cannot_be_used_outside_tests(monkeypatch):
    client = LLMClient(provider="stub", model="test-stub")
    # Simulate production/outside-test environment
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("TESTING", raising=False)

    with pytest.raises((RuntimeError, LLMClientError), match="cannot be used outside of tests"):
        client.complete_json(system="sys", user="user")


def test_degraded_extraction_marked_in_summary():
    from app.api.meeting_routes import build_summary
    topics = [{"topic_label": "Architecture"}]
    decisions = [{"text": "Use SQLite"}]
    actions = []
    extraction = {
        "status": "completed",
        "fallback_reason": "LLM_PROVIDER=stub is a test fixture, not a real extractor",
    }
    summary = build_summary(topics, decisions, actions, review_count=0, extraction=extraction)
    assert "[Degraded extraction: LLM_PROVIDER=stub is a test fixture, not a real extractor]" in summary
