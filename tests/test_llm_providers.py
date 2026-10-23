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
    monkeypatch.setattr(config, "LLM_TIMEOUT_RETRIES", 1)
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


def test_gemini_default_model_is_the_documented_one():
    assert llm_module.DEFAULT_GEMINI_MODEL == "gemini-3.8-flash"


def test_explicit_gemini_model_beats_llm_model(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "LLM_MODEL", "gemini-3.6-flash")
    assert LLMClient(model="gemini-3.5-flash-lite").model == "gemini-3.5-flash-lite"


def test_configured_llm_model_overrides_the_gemini_default(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "LLM_MODEL", "  gemini-3.6-flash  ")
    assert LLMClient().model == "gemini-3.6-flash"


@pytest.mark.parametrize("configured", ["mock-v1", "", "   ", None])
def test_placeholder_or_missing_model_uses_the_gemini_default(monkeypatch, configured):
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "LLM_MODEL", configured)
    assert LLMClient().model == llm_module.DEFAULT_GEMINI_MODEL


@pytest.mark.parametrize("provider", ["openai", "anthropic", "local"])
def test_other_providers_use_llm_model_and_never_the_gemini_default(monkeypatch, provider):
    monkeypatch.setattr(config, "LLM_PROVIDER", provider)
    monkeypatch.setattr(config, "LLM_API_KEY", "k-123")
    monkeypatch.setattr(config, "LLM_MODEL", "my-model")
    assert LLMClient().model == "my-model"
    assert LLMClient(model="other-model").model == "other-model"
    monkeypatch.setattr(config, "LLM_MODEL", "mock-v1")
    assert LLMClient().model is None
    assert llm_unavailable_reason() == f"LLM_PROVIDER={provider} has no LLM_MODEL configured"


def test_stub_keeps_its_fixture_model(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "stub")
    monkeypatch.setattr(config, "LLM_MODEL", "mock-v1")
    assert LLMClient().model == "mock-v1"
    assert LLMClient(model="m").model == "m"


@pytest.mark.parametrize("explicit, configured, expected", [
    (None, "mock-v1", "gemini-3.8-flash"),
    (None, "gemini-3.6-flash", "gemini-3.6-flash"),
    ("gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.5-flash-lite"),
])
def test_selected_gemini_model_is_the_one_requested(gemini, monkeypatch, explicit, configured, expected):
    provider = gemini(gemini_body({"decisions": [], "actions": []}))
    monkeypatch.setattr(config, "LLM_MODEL", configured)
    LLMClient(model=explicit).complete_json(system="s", user="u")
    assert provider.requests[0]["url"].endswith(f"/models/{expected}:generateContent")


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


def test_timeout_is_retried_once_then_falls_back(gemini):
    provider = gemini(TimeoutError("timed out"))
    mid = make_meeting(LINES)
    stats = extract(mid)
    _assert_fell_back(stats, mid, "timed out after 12s (2 attempts)")
    assert "Raise LLM_TIMEOUT_SECONDS or choose a faster LLM_MODEL" in stats["fallback_reason"]
    assert len(provider.requests) == 2                        # 1 + LLM_TIMEOUT_RETRIES (1)
    assert discussion_file(mid).exists() is False             # never presented as an LLM run


@pytest.mark.parametrize("retries, expected_requests", [(0, 1), (2, 3), (9, 4)])
def test_timeout_retries_are_configurable_and_capped(gemini, monkeypatch, retries, expected_requests):
    provider = gemini(TimeoutError("timed out"))
    monkeypatch.setattr(config, "LLM_TIMEOUT_RETRIES", retries)
    with pytest.raises(LLMClientError, match=r"timed out after 12s"):
        LLMClient().complete_json(system="s", user="u")
    # Never more than the general retry budget (1 + 3), whatever is configured.
    assert len(provider.requests) == expected_requests


def test_connect_timeout_wrapped_in_urlerror_counts_as_a_timeout(gemini):
    provider = gemini(urllib.error.URLError(TimeoutError("timed out")))
    with pytest.raises(LLMClientError, match=r"timed out after 12s \(2 attempts\)"):
        LLMClient().complete_json(system="s", user="u")
    assert len(provider.requests) == 2


def test_slow_first_attempt_then_success_uses_the_llm(gemini, monkeypatch):
    install_semantic_fakes(monkeypatch)
    provider = gemini(TimeoutError("timed out"), gemini_body(GOOD_PAYLOAD))
    stats = extract(make_meeting(LINES))
    assert stats["mode_used"] == "llm" and "fallback_reason" not in stats
    assert len(provider.requests) == 2
    assert all(r["timeout"] == 12.0 for r in provider.requests)


def test_server_errors_keep_their_full_retry_budget(gemini, monkeypatch):
    monkeypatch.setattr(config, "LLM_TIMEOUT_RETRIES", 0)
    error = urllib.error.HTTPError("u", 503, "unavailable", {}, io.BytesIO(b"busy"))
    provider = gemini(error)
    with pytest.raises(LLMClientError, match="HTTP 503"):
        LLMClient().complete_json(system="s", user="u")
    assert len(provider.requests) == 4                        # unchanged: 1 + 3 retries


def test_timeout_settings_come_from_config(monkeypatch):
    monkeypatch.setattr(config, "LLM_TIMEOUT_SECONDS", 150.0)
    monkeypatch.setattr(config, "LLM_TIMEOUT_RETRIES", 0)
    client = LLMClient(provider="gemini", api_key="k")
    assert (client.timeout, client.timeout_retries) == (150.0, 0)
    assert LLMClient(provider="gemini", api_key="k", timeout=5).timeout == 5.0


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


# ---------------------------------------------------------------------------
# Malformed LLM_MODEL (two .env lines run together) must never leak a secret
# ---------------------------------------------------------------------------

MERGED_MODEL = f"gemini-3.5-flash-liteGEMINI_API_KEY={FAKE_KEY}"


def test_malformed_model_name_is_rejected_without_echoing_it(gemini, monkeypatch):
    provider = gemini(gemini_body(GOOD_PAYLOAD))
    monkeypatch.setattr(config, "LLM_MODEL", MERGED_MODEL)
    monkeypatch.setattr(config, "LLM_API_KEY", "another-key")   # even with a usable key
    reason = llm_unavailable_reason()
    assert "LLM_MODEL is not a valid model name" in reason
    assert FAKE_KEY not in reason and "GEMINI_API_KEY=" not in reason

    mid = make_meeting(LINES)
    stats = extract(mid)
    assert stats["mode_used"] == "rules" and provider.requests == []   # nothing sent
    run = db.get_extraction_run(mid)
    for value in (stats["fallback_reason"], run["fallback_reason"], run["extractor_version"]):
        assert FAKE_KEY not in value
    claims_json = MeetingStorage(mid).path_for("intelligence", "claims.json")
    if claims_json.exists():
        assert FAKE_KEY not in claims_json.read_text(encoding="utf-8")


@pytest.mark.parametrize("model", [
    "gemini-3.5-flash-lite", "gemini-3.8-flash", "llama3.1:8b", "models/gemini-3.6-flash",
    "org/model-name", "gpt-4o-mini-2024-07-18",
])
def test_ordinary_model_names_are_valid(model):
    assert llm_module.model_name_is_valid(model)


@pytest.mark.parametrize("model", [MERGED_MODEL, "gemini flash", '"gemini-3.6-flash"', "-flag"])
def test_malformed_model_names_are_invalid(model):
    assert not llm_module.model_name_is_valid(model)


def test_provider_error_bodies_are_redacted(gemini):
    body = json.dumps({"error": {"message": f"models/x-{FAKE_KEY} is not found"}}).encode()
    gemini(urllib.error.HTTPError("u", 404, "not found", {}, io.BytesIO(body)))
    with pytest.raises(LLMClientError) as info:
        LLMClient().complete_json(system="s", user="u")
    assert FAKE_KEY not in str(info.value) and "<redacted>" in str(info.value)


# ---------------------------------------------------------------------------
# Total time budget: every window, retry and backoff together
# ---------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(llm_module, "_clock", c)
    monkeypatch.setattr(llm_module.time, "sleep", lambda s: setattr(c, "now", c.now + s))
    return c


class SlowProvider(FakeProvider):
    """Each request 'takes' `seconds`; past its timeout it raises TimeoutError, like urlopen."""

    def __init__(self, clock, seconds, *outcomes):
        super().__init__(*outcomes)
        self.clock, self.seconds = clock, seconds

    def __call__(self, request, timeout=None):
        if self.seconds > timeout:
            self.requests.append({"timeout": timeout})
            self.clock.now += timeout
            raise TimeoutError("timed out")
        self.clock.now += self.seconds
        return super().__call__(request, timeout)


def test_budget_caps_each_request_timeout(gemini, clock, monkeypatch):
    provider = gemini(gemini_body({"decisions": [], "actions": []}))
    client = LLMClient()
    client.start_budget(5)
    client.complete_json(system="s", user="u")
    assert provider.requests[0]["timeout"] == 5.0                # not the 12 s request timeout


def test_budget_stops_retries_and_never_starts_a_request_after_it(gemini, clock, monkeypatch):
    provider = SlowProvider(clock, 999, gemini_body(GOOD_PAYLOAD))
    monkeypatch.setattr(llm_module.urllib.request, "urlopen", provider)
    monkeypatch.setattr(config, "LLM_TIMEOUT_RETRIES", 3)
    client = LLMClient()
    client.start_budget(20)
    with pytest.raises(LLMClientError, match=r"20s time budget \(LLM_EXTRACTION_BUDGET_SECONDS\) ran out"):
        client.complete_json(system="s", user="u")
    assert [r["timeout"] for r in provider.requests] == [12.0, 7.0]   # 12 + 1 s backoff + 7 = 20
    assert clock.now - 1000.0 <= 20.0


def test_backoff_that_would_cross_the_budget_is_not_slept(gemini, clock):
    error = urllib.error.HTTPError("u", 503, "busy", {}, io.BytesIO(b"busy"))
    provider = gemini(error)
    client = LLMClient()
    client.start_budget(0.5)
    with pytest.raises(LLMClientError, match="time budget"):
        client.complete_json(system="s", user="u")
    assert len(provider.requests) == 1 and clock.now == 1000.0


def test_budget_running_out_between_windows_falls_back_honestly(gemini, clock, monkeypatch):
    # 70 utterances -> 2 extraction windows; each successful request takes 8 s.
    provider = SlowProvider(clock, 8, gemini_body(GOOD_PAYLOAD))
    monkeypatch.setattr(llm_module.urllib.request, "urlopen", provider)
    monkeypatch.setattr(config, "LLM_EXTRACTION_BUDGET_SECONDS", 10)
    mid = make_meeting(LINES * 24)
    stats = extract(mid)
    _assert_fell_back(stats, mid, "10s time budget")
    assert stats["mode_used"] != "llm" and not stats["extractor_version"].startswith("llm:")
    assert len(provider.requests) == 2                           # window 1 ok, window 2 capped at 2 s
    assert provider.requests[1]["timeout"] == pytest.approx(2.0)


def test_worst_case_with_defaults_is_bounded_by_the_budget(gemini, clock, monkeypatch):
    for attr, value in (("LLM_TIMEOUT_SECONDS", 180.0), ("LLM_TIMEOUT_RETRIES", 1),
                        ("LLM_EXTRACTION_BUDGET_SECONDS", 600.0)):
        monkeypatch.setattr(config, attr, value)
    provider = SlowProvider(clock, 10_000, gemini_body(GOOD_PAYLOAD))   # never answers
    monkeypatch.setattr(llm_module.urllib.request, "urlopen", provider)
    stats = extract(make_meeting(LINES))
    assert stats["mode_used"] == "rules"
    assert "timed out after 180s (2 attempts)" in stats["fallback_reason"]
    assert clock.now - 1000.0 == 361.0                            # 180 + 1 s backoff + 180


def test_zero_budget_means_no_overall_deadline():
    client = LLMClient(provider="gemini", api_key="k")
    client.start_budget(0)
    assert client.deadline is None


# ---------------------------------------------------------------------------
# Phase 5: provider wait time, request count and identity in extraction stats
# ---------------------------------------------------------------------------

def test_extraction_stats_report_the_llm_actually_called(gemini, monkeypatch):
    install_semantic_fakes(monkeypatch)
    provider = gemini(gemini_body(GOOD_PAYLOAD))
    stats = extract(make_meeting(LINES))
    assert stats["mode_used"] == "llm"
    assert stats["llm_provider"] == "gemini"
    assert stats["llm_model"] == llm_module.DEFAULT_GEMINI_MODEL
    assert stats["llm_requests"] == len(provider.requests) == 1
    assert stats["llm_provider_seconds"] >= 0 and stats["llm_cache_hits"] == 0


def test_fallback_stats_still_report_the_failed_attempts(gemini):
    gemini(TimeoutError("timed out"))
    stats = extract(make_meeting(LINES))
    assert stats["mode_used"] == "rules"
    assert stats["llm_requests"] == 2 and stats["llm_provider"] == "gemini"


def test_provider_wait_excludes_backoff_sleeps(monkeypatch):
    from types import SimpleNamespace

    ticks = iter([10.0, 13.0, 20.0, 26.5])             # two calls: 3 s and 6.5 s
    sleeps = []
    monkeypatch.setattr(llm_module, "time", SimpleNamespace(monotonic=lambda: next(ticks), sleep=sleeps.append))
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "LLM_MODEL", "mock-v1")
    monkeypatch.setattr(config, "GEMINI_API_KEY", FAKE_KEY)
    error = urllib.error.HTTPError("u", 503, "busy", {}, io.BytesIO(b"busy"))
    monkeypatch.setattr(llm_module.urllib.request, "urlopen",
                        FakeProvider(error, gemini_body({"decisions": [], "actions": []})))
    client = LLMClient()
    client.complete_json(system="s", user="u")
    assert sleeps == [1.0]                              # one backoff, not counted as provider time
    assert client.requests_made == 2
    assert client.provider_seconds == pytest.approx(9.5)


def test_cached_windows_are_counted(gemini, monkeypatch):
    install_semantic_fakes(monkeypatch)
    provider = gemini(gemini_body(GOOD_PAYLOAD))
    mid = make_meeting(LINES)
    extract(mid)
    stats = extract(mid)                                # same windows, same model: cache
    assert stats["llm_cache_hits"] == 1 and stats["llm_requests"] == 0
    assert len(provider.requests) == 1


def test_invalid_model_name_is_never_reported_as_the_model():
    from app.services.intelligence.extraction_pipeline import _llm_call_stats

    class C:
        provider, model, requests_made, provider_seconds = "gemini", MERGED_MODEL, 0, 0.0

    stats = _llm_call_stats(C(), {}, 1)
    assert stats["llm_model"] is None and FAKE_KEY not in json.dumps(stats)


# ---------------------------------------------------------------------------
# Phase 5: scripts/check_llm.py never prints the key
# ---------------------------------------------------------------------------

def test_check_llm_reports_readiness_without_secrets(gemini, capsys):
    from scripts import check_llm

    assert check_llm.main([]) == 0
    out = capsys.readouterr().out
    assert '"api_key_configured": true' in out and "LLM ready" in out
    assert FAKE_KEY not in out


def test_check_llm_hides_a_malformed_model(gemini, monkeypatch, capsys):
    from scripts import check_llm

    monkeypatch.setattr(config, "LLM_MODEL", MERGED_MODEL)
    assert check_llm.main(["--live"]) == 1            # not ready: no request is made
    out = capsys.readouterr().out
    assert FAKE_KEY not in out and '"model": null' in out and "Skipping the live request" in out


def test_check_llm_live_request_is_one_synthetic_call(gemini, capsys):
    from scripts import check_llm

    provider = gemini(gemini_body(GOOD_PAYLOAD))
    assert check_llm.main(["--live", "--timeout", "5"]) == 0
    out = capsys.readouterr().out
    assert '"success": true' in out and FAKE_KEY not in out
    assert len(provider.requests) == 1 and provider.requests[0]["timeout"] == 5.0
    sent = json.dumps(provider.requests[0]["body"])
    assert "ship the demo build" in sent                # synthetic text only


def test_env_flag_parsing(monkeypatch):
    from app.core.config import _env_flag

    for value, expected in (("true", True), ("TRUE", True), ("1", True), ("false", False), ("no", False)):
        monkeypatch.setenv("M2M_FLAG_TEST", value)
        assert _env_flag("M2M_FLAG_TEST", not expected) is expected
    monkeypatch.setenv("M2M_FLAG_TEST", "")
    assert _env_flag("M2M_FLAG_TEST", True) is True


# ---------------------------------------------------------------------------
# Phase 6: every provider attempt is recorded with its outcome
# ---------------------------------------------------------------------------

def test_attempts_record_a_503_then_success(gemini):
    error = urllib.error.HTTPError("u", 503, "busy", {}, io.BytesIO(b"busy"))
    gemini(error, gemini_body({"decisions": [], "actions": []}))
    client = LLMClient()
    client.complete_json(system="s", user="u")
    assert [a["outcome"] for a in client.attempts] == ["http_503", "ok"]
    assert all(a["seconds"] >= 0 for a in client.attempts)


def test_attempts_record_timeouts_and_reach_the_stats(gemini):
    gemini(TimeoutError("timed out"))
    stats = extract(make_meeting(LINES))
    assert stats["mode_used"] == "rules"                       # fallback, never a false success
    assert [a["outcome"] for a in stats["llm_attempts"]] == ["timeout", "timeout"]


def test_a_request_refused_by_configuration_is_not_an_attempt(monkeypatch):
    monkeypatch.setattr(llm_module.urllib.request, "urlopen", FakeProvider(b"{}"))
    client = LLMClient(provider="openai", model="m", api_key=None, backoff_delays=[])
    for attr in ("LLM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setattr(config, attr, None)
    client.api_key = None
    with pytest.raises(LLMClientError):
        client.complete_json("s", "u")
    assert client.attempts == [] and client.requests_made == 0
