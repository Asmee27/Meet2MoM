"""
Real-provider LLM extraction (Phase 4.4).

The provider HTTP call is faked at urllib.request.urlopen, so the real
request building, retries, response parsing, schema checks, grounding,
fallback, persistence and verification all run without credentials or cost.
"""
from __future__ import annotations

import io
import json
import logging
import urllib.error
from contextlib import redirect_stdout

import pytest

from app.core import config, db
from app.core import llm_client as llm_module
from app.core.llm_client import LLMClient, LLMClientError, LLMOutputError
from app.core.storage import MeetingStorage
from app.services.intelligence import extraction_pipeline as pipeline
from app.services.intelligence.extraction_pipeline import (
    DISCUSSION_POINTS_FILE,
    llm_unavailable_reason,
    run_extraction,
)
from app.services.verification.claims_view import build_claims_view
from tests.scenario_helpers import make_meeting, write_single_topic
from tests.semantic_fakes import install_semantic_fakes

FAKE_KEY = "AIza-test-key-not-real-0123456789"

LINES = [
    ("SPEAKER_00", "We decided to migrate the primary database to PostgreSQL."),
    ("SPEAKER_01", "Agreed. I will set up the database cluster by Friday."),
    ("SPEAKER_00", "The coffee machine on the third floor is broken again."),
]

GOOD_PAYLOAD = {
    "decisions": [{
        "text": "Migrate the primary database to PostgreSQL",
        "state": "accepted",
        "proposer": "SPEAKER_00",
        "conditional": False,
        "condition_text": None,
        "uncertainty": "low",
        "evidence": ["u00000", "u00001"],
    }],
    "actions": [{
        "text": "Set up the database cluster",
        "owner": None,
        "owner_type": "explicit_first_person",
        "deadline_text": "by Friday",
        "commitment_type": "firm",
        "evidence": ["u00001"],
    }],
    "discussion_points": [{
        "text": "The team agreed to move the primary database to PostgreSQL",
        "evidence": ["u00000", "u00001"],
    }],
}


def gemini_body(payload) -> bytes:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return json.dumps({
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
    }).encode("utf-8")


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeProvider:
    """Replays scripted outcomes for urlopen: bytes bodies or exceptions."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append({
            "url": request.full_url,
            "headers": {k.lower(): v for k, v in request.header_items()},
            "body": json.loads(request.data.decode("utf-8")) if request.data else None,
            "timeout": timeout,
        })
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return _Response(outcome)


@pytest.fixture
def gemini(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "LLM_MODEL", "mock-v1")       # placeholder -> Gemini default
    monkeypatch.setattr(config, "LLM_API_KEY", None)
    monkeypatch.setattr(config, "GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setattr(config, "LLM_TIMEOUT_SECONDS", 12.0)
    monkeypatch.setattr(llm_module.time, "sleep", lambda _s: None)

    def install(*outcomes):
        provider = FakeProvider(*outcomes)
        monkeypatch.setattr(llm_module.urllib.request, "urlopen", provider)
        return provider

    return install


def extract(meeting_id):
    write_single_topic(meeting_id)
    with redirect_stdout(io.StringIO()):
        return run_extraction(meeting_id, mode="llm")


def discussion_file(meeting_id):
    return MeetingStorage(meeting_id).path_for("intelligence", DISCUSSION_POINTS_FILE)


# ---------------------------------------------------------------------------
# Configuration: clear, provider-specific reasons before any network call
# ---------------------------------------------------------------------------

def test_stub_is_never_reported_as_a_real_extractor(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "stub")
    assert llm_unavailable_reason() == "LLM_PROVIDER=stub is a test fixture, not a real extractor"


@pytest.mark.parametrize("provider, key_attr", [
    ("gemini", "GEMINI_API_KEY"), ("openai", "OPENAI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY"),
])
def test_provider_specific_keys_are_used(monkeypatch, provider, key_attr):
    for attr in ("LLM_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setattr(config, attr, None)
    monkeypatch.setattr(config, "LLM_PROVIDER", provider)
    monkeypatch.setattr(config, "LLM_MODEL", "some-model")
    assert "no API key" in llm_unavailable_reason()
    monkeypatch.setattr(config, key_attr, "k-123")
    assert llm_unavailable_reason() is None
    assert LLMClient().api_key == "k-123"


def test_other_providers_require_an_explicit_model(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "k-123")
    monkeypatch.setattr(config, "LLM_MODEL", "mock-v1")
    assert llm_unavailable_reason() == "LLM_PROVIDER=openai has no LLM_MODEL configured"


def test_gemini_honours_llm_model_and_defaults_otherwise(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "LLM_MODEL", "gemini-2.5-pro")
    assert LLMClient().model == "gemini-2.5-pro"
    monkeypatch.setattr(config, "LLM_MODEL", "mock-v1")
    assert LLMClient().model == llm_module.DEFAULT_GEMINI_MODEL


def test_unsupported_provider_is_reported(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "groq")
    assert "not a supported provider" in llm_unavailable_reason()


def test_missing_key_falls_back_without_any_network_call(gemini, monkeypatch):
    provider = gemini(gemini_body(GOOD_PAYLOAD))
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    stats = extract(make_meeting(LINES))
    assert stats["mode_used"] == "rules"
    assert "no API key" in stats["fallback_reason"]
    assert provider.requests == []


# ---------------------------------------------------------------------------
# Successful structured extraction, grounded and independently verified
# ---------------------------------------------------------------------------

def test_gemini_extraction_end_to_end_with_verification(gemini, monkeypatch, caplog):
    install_semantic_fakes(monkeypatch)
    provider = gemini(gemini_body(GOOD_PAYLOAD))
    mid = make_meeting(LINES)

    with caplog.at_level(logging.DEBUG):
        stats = extract(mid)

    assert stats["status"] == "completed"
    assert stats["mode_used"] == "llm"
    assert "fallback_reason" not in stats
    assert stats["extractor_version"] == f"llm:gemini:{llm_module.DEFAULT_GEMINI_MODEL}:v2"
    run = db.get_extraction_run(mid)
    assert run["mode_used"] == "llm" and run["fallback_reason"] is None

    # The real request: right endpoint, key in a header (never the URL), JSON mode, timeout.
    request = provider.requests[0]
    assert request["url"].endswith(f"/models/{llm_module.DEFAULT_GEMINI_MODEL}:generateContent")
    assert request["headers"]["x-goog-api-key"] == FAKE_KEY
    assert FAKE_KEY not in request["url"]
    assert request["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert request["timeout"] == 12.0
    assert FAKE_KEY not in caplog.text

    # Independent verification still decides what is confirmed.
    from app.services.pipeline.intelligence_service import ensure_verification

    ensure_verification(mid, force=True)
    claims = build_claims_view(mid)["claims"]
    action = next(c for c in claims if c["claim_type"] == "action")
    assert action["owner"] == "SPEAKER_01"                # from the evidence speaker, not the LLM
    assert action["deadline_text"] == "by Friday"
    assert all(c["verification"]["final"]["label"] for c in claims)

    saved = json.loads(discussion_file(mid).read_text(encoding="utf-8"))
    assert saved["status"] == "cited_not_verified"
    assert saved["points"][0]["evidence"] == ["u00000", "u00001"]


def test_mom_reports_the_real_llm_and_cited_summary(gemini, monkeypatch):
    from app.api.meeting_routes import create_mom
    from app.services.pipeline.intelligence_service import ensure_verification

    install_semantic_fakes(monkeypatch)
    gemini(gemini_body(GOOD_PAYLOAD))
    mid = make_meeting(LINES)
    extract(mid)
    ensure_verification(mid, force=True)

    mom = create_mom(mid)
    assert mom["extraction"]["llm_used"] is True
    assert mom["extraction"]["llm_provider"] == "gemini"
    assert mom["extraction"]["llm_model"] == llm_module.DEFAULT_GEMINI_MODEL
    assert mom["extraction"]["degraded_extraction"] is False
    summary = mom["llm_discussion_summary"]
    assert summary["status"] == "cited_not_verified"
    assert summary["points"][0]["evidence"] == ["u00000", "u00001"]
    assert summary["points"][0]["start"] == 0.0 and summary["points"][0]["end"] == 9.0


def test_approved_export_and_pdf_keep_the_extraction_source(gemini, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api.main import app
    from app.services.pipeline.intelligence_service import ensure_verification

    install_semantic_fakes(monkeypatch)
    gemini(gemini_body(GOOD_PAYLOAD))
    mid = make_meeting(LINES)
    extract(mid)
    ensure_verification(mid, force=True)

    client = TestClient(app)
    assert client.get(f"/api/meetings/{mid}/review").status_code == 200
    assert client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Tester"}).status_code == 200
    approved = client.get(f"/api/meetings/{mid}/export/json").json()
    assert approved["extraction"]["llm_used"] is True
    assert approved["extraction"]["llm_provider"] == "gemini"
    assert approved["llm_discussion_summary"]["status"] == "cited_not_verified"
    pdf = client.get(f"/api/download-mom/{mid}")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_llm_details_not_in_the_cited_words_are_removed(gemini):
    payload = json.loads(json.dumps(GOOD_PAYLOAD))
    payload["actions"][0]["deadline_text"] = "by next Monday"          # not said
    payload["decisions"][0]["proposer"] = "Priya"                      # nobody named Priya
    payload["discussion_points"].append({"text": "Outside the window", "evidence": ["u00099"]})
    gemini(gemini_body(payload))
    mid = make_meeting(LINES)

    stats = extract(mid)

    removed = {(r["claim_type"], r["field"]) for r in stats["llm_grounding"]["ungrounded_fields_removed"]}
    assert removed == {("action", "deadline_text"), ("decision", "proposer")}
    assert stats["llm_grounding"]["discussion_points_dropped"] == 1
    persisted = {c["claim_type"]: c["payload"] for c in db.get_claims(mid)}
    assert set(persisted) == {"action", "decision"}                    # both claims kept
    assert "deadline_text" in persisted["action"] and "proposer" in persisted["decision"]
    assert persisted["action"]["deadline_text"] is None                # ...without the invented date
    assert persisted["action"]["deadline_type"] == "none"
    assert persisted["decision"]["proposer"] is None                   # ...or the invented proposer


def test_invented_evidence_is_dropped_by_span_validation(gemini):
    payload = json.loads(json.dumps(GOOD_PAYLOAD))
    payload["actions"][0]["evidence"] = ["u00042"]                     # no such utterance
    gemini(gemini_body(payload))
    mid = make_meeting(LINES)

    stats = extract(mid)
    assert stats["mode_used"] == "llm"
    assert stats["accepted"] == 1 and stats["dropped"] == 1
    assert [d["reason"] for d in db.get_dropped_claims(mid)] == ["unresolvable_utt_id"]


# ---------------------------------------------------------------------------
# Invalid output and provider failures: clear status, safe fallback
# ---------------------------------------------------------------------------

def _assert_fell_back(stats, mid, text):
    assert stats["mode_used"] == "rules"                      # FALLBACK_EXTRACTOR=rules in tests
    assert stats["fallback_reason"].startswith("LLM extraction failed")
    assert text in stats["fallback_reason"]
    assert db.get_extraction_run(mid)["fallback_reason"] == stats["fallback_reason"]
    assert not discussion_file(mid).exists()
    assert "llm_grounding" not in stats


def test_non_json_output_twice_falls_back(gemini):
    provider = gemini(gemini_body("Sure! Here are the decisions: ..."))
    mid = make_meeting(LINES)
    stats = extract(mid)
    _assert_fell_back(stats, mid, "could not be parsed as valid JSON")
    assert len(provider.requests) == 2                        # original + one repair attempt


def test_json_that_is_not_the_schema_falls_back_and_is_not_cached(gemini):
    gemini(gemini_body({"summary": "We talked about databases."}))
    mid = make_meeting(LINES)
    stats = extract(mid)
    _assert_fell_back(stats, mid, "does not match the extraction schema")
    cache = MeetingStorage(mid).root / "intelligence" / "llm_cache"
    assert not list(cache.glob("*.json"))


def test_wrong_field_types_fall_back(gemini):
    gemini(gemini_body({"decisions": "none", "actions": []}))
    mid = make_meeting(LINES)
    _assert_fell_back(extract(mid), mid, "'decisions' is not a list")


def test_http_error_falls_back_with_provider_message(gemini):
    error = urllib.error.HTTPError(
        "https://example.invalid", 404, "Not Found", {},
        io.BytesIO(b'{"error": {"message": "models/gemini-x is not found"}}'),
    )
    gemini(error)
    mid = make_meeting(LINES)
    _assert_fell_back(extract(mid), mid, "HTTP 404")


def test_rate_limit_is_retried_then_succeeds(gemini):
    limited = urllib.error.HTTPError("https://example.invalid", 429, "Too Many", {}, io.BytesIO(b"{}"))
    provider = gemini(limited, gemini_body(GOOD_PAYLOAD))
    stats = extract(make_meeting(LINES))
    assert stats["mode_used"] == "llm" and stats["accepted"] == 2
    assert len(provider.requests) == 2


def test_timeout_is_retried_then_falls_back(gemini):
    provider = gemini(TimeoutError("timed out"))
    mid = make_meeting(LINES)
    stats = extract(mid)
    _assert_fell_back(stats, mid, "timed out after 12s")
    assert len(provider.requests) == 4                        # 1 + 3 retries


def test_blocked_gemini_response_has_a_clear_reason(gemini):
    gemini(json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}).encode())
    mid = make_meeting(LINES)
    _assert_fell_back(extract(mid), mid, "no candidates (blocked: SAFETY)")


def test_stale_discussion_summary_is_removed_on_fallback(gemini):
    gemini(gemini_body(GOOD_PAYLOAD))
    mid = make_meeting(LINES)
    extract(mid)
    assert discussion_file(mid).exists()
    gemini(TimeoutError("timed out"))
    # New prompt input is needed to bypass the window cache: change the model.
    pipeline_client = LLMClient(model="gemini-2.5-pro")
    write_single_topic(mid)
    with redirect_stdout(io.StringIO()):
        stats = run_extraction(mid, mode="llm", client=pipeline_client)
    assert stats["mode_used"] == "rules"
    assert not discussion_file(mid).exists()


# ---------------------------------------------------------------------------
# Client-level failures never leak raw exceptions or keys
# ---------------------------------------------------------------------------

def test_openai_response_without_content_is_an_output_error(monkeypatch):
    monkeypatch.setattr(llm_module.urllib.request, "urlopen", FakeProvider(b'{"choices": []}'))
    client = LLMClient(provider="openai", model="m", api_key="sk-test", backoff_delays=[])
    with pytest.raises(LLMOutputError, match="no message content"):
        client.complete_json("s", "u")


def test_misconfigured_client_raises_before_any_request(monkeypatch):
    provider = FakeProvider(b"{}")
    monkeypatch.setattr(llm_module.urllib.request, "urlopen", provider)
    for attr in ("LLM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setattr(config, attr, None)
    with pytest.raises(LLMClientError, match="no API key"):
        LLMClient(provider="openai", model="m").complete_json("s", "u")
    assert provider.requests == []
