"""
Phase 1 extraction scenarios: the real rule-based extractor (the explicit
fallback when no LLM is available) on synthetic transcripts, followed by
verification with deterministic semantic fakes.

They check that proposals, rejections, conditionals and hypotheticals are not
turned into confirmed decisions, and that "no decisions" is reported as such.
"""
from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from app.core import config, db
from app.core.llm_client import LLMClient, LLMClientError
from app.services.intelligence.extraction_pipeline import run_extraction
from app.services.verification.claim_verification import verify_meeting
from app.services.verification.claims_view import build_claims_view
from tests.scenario_helpers import FILLER, final_label, make_meeting, write_single_topic
from tests.semantic_fakes import install_semantic_fakes


def _extract(meeting_id, **kwargs):
    write_single_topic(meeting_id)
    with redirect_stdout(io.StringIO()):
        return run_extraction(meeting_id, **kwargs)


def _decisions(meeting_id):
    return [c for c in build_claims_view(meeting_id)["claims"] if c["claim_type"] == "decision"]


def test_proposal_without_agreement_is_kept_as_proposed_not_accepted(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_00", "Maybe we could switch the build server to Kubernetes next quarter."),
        ("SPEAKER_01", "The quarterly newsletter came out yesterday afternoon."),
    ])
    stats = _extract(mid, mode="rules")
    verify_meeting(mid)

    decisions = _decisions(mid)
    assert stats["status"] == "completed"
    assert [d["state"] for d in decisions] == ["proposed"]
    assert not any(d["exportable"] for d in decisions)
    assert final_label(decisions[0]) == "review"


def test_explicit_acceptance_cites_proposal_and_confirmation(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_00", "I suggest we move the product release to Friday."),
        ("SPEAKER_01", "Yes, that works for everyone."),
    ] + FILLER[2:])
    _extract(mid, mode="rules")

    decisions = _decisions(mid)
    accepted = [d for d in decisions if d["state"] == "accepted"]
    assert len(accepted) == 1
    # proposal (u00002) and agreement (u00003) are both cited
    assert accepted[0]["evidence_ids"] == ["u00002", "u00003"]


def test_rejected_proposal_is_not_a_confirmed_decision(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_00", "How about we migrate the reporting API to GraphQL?"),
        ("SPEAKER_01", "No, that won't work for our enterprise customers."),
    ] + FILLER[2:])
    _extract(mid, mode="rules")
    verify_meeting(mid)

    decisions = _decisions(mid)
    # The rejected proposal stays visible for review instead of vanishing...
    assert [d["state"] for d in decisions] == ["rejected"]
    assert decisions[0]["evidence_ids"] == ["u00002"]
    # ...but is never a confirmed decision. (The cited "No, that won't work"
    # contradicts the proposal text, so fusion may reject it outright.)
    assert decisions[0]["exportable"] is False
    assert final_label(decisions[0]) in ("review", "rejected")
    assert "rejected_proposal" in {r["code"] for r in decisions[0]["verification"]["final"]["reasons"]}


def test_conditional_decision_is_extracted_as_conditional(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_00", "If the budget is approved, we will hire two more engineers."),
    ] + FILLER[2:])
    _extract(mid, mode="rules")
    verify_meeting(mid)

    decisions = _decisions(mid)
    assert [d["state"] for d in decisions] == ["conditional"]
    assert decisions[0]["conditional"] is True
    assert final_label(decisions[0]) == "review"
    assert decisions[0]["exportable"] is False


def test_hypothetical_discussion_produces_no_exportable_claims(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_00", "Hypothetically, if I had time I would rewrite the parser."),
    ] + FILLER[2:])
    _extract(mid, mode="rules")
    verify_meeting(mid)

    assert not any(c["exportable"] for c in build_claims_view(mid)["claims"])


def test_meeting_without_decisions_or_actions_reports_no_claims(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER)
    stats = _extract(mid, mode="rules")
    summary = verify_meeting(mid)

    assert stats["status"] == "no_claims"
    assert stats["accepted"] == 0
    assert db.get_extraction_run(mid)["status"] == "no_claims"
    assert summary["status"] == "no_claims"


# ---------------------------------------------------------------------------
# Empty / failed / unavailable LLM extraction
# ---------------------------------------------------------------------------

def test_default_stub_provider_falls_back_to_rules_and_says_so(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "stub")
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_00", "We decided to migrate the primary database to PostgreSQL."),
    ] + FILLER[2:])
    stats = _extract(mid, mode="llm")

    run = db.get_extraction_run(mid)
    assert stats["mode_used"] == "rules"
    assert "stub" in stats["fallback_reason"]
    assert run["mode_requested"] == "llm" and run["mode_used"] == "rules"
    assert run["status"] == "completed" and run["accepted"] == 1
    assert "stub" in run["fallback_reason"]


def test_stub_provider_without_fallback_is_extractor_unavailable(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "stub")
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_00", "We decided to migrate the primary database to PostgreSQL."),
    ])
    stats = _extract(mid, mode="llm", allow_fallback=False)

    assert stats["status"] == "extractor_unavailable"
    assert db.get_extraction_run(mid)["status"] == "extractor_unavailable"
    assert db.get_claims(mid) == []


def test_provider_without_api_key_is_unavailable(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_API_KEY", None)
    monkeypatch.setattr(config, "GEMINI_API_KEY", None)
    mid = make_meeting(FILLER)
    stats = _extract(mid, mode="llm")

    assert stats["mode_used"] == "rules"
    assert "no API key" in stats["fallback_reason"]


def test_llm_error_falls_back_and_records_reason():
    class FailingClient(LLMClient):
        def complete_json(self, *args, **kwargs):
            raise LLMClientError("HTTP 404 model not found")

    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_00", "We decided to migrate the primary database to PostgreSQL."),
    ])
    stats = _extract(mid, mode="llm", client=FailingClient(provider="openai"))

    run = db.get_extraction_run(mid)
    assert stats["mode_used"] == "rules"
    assert "HTTP 404" in run["fallback_reason"]
    assert run["status"] == "completed"


def test_empty_llm_result_from_a_real_client_is_no_claims_not_failure():
    client = LLMClient(provider="stub")
    client.set_stub_response({"decisions": [], "actions": []})  # an injected client really ran
    mid = make_meeting(FILLER)
    stats = _extract(mid, mode="llm", client=client)

    assert stats["mode_used"] == "llm"
    assert stats["status"] == "no_claims"


def test_meeting_without_transcript_is_failed_not_empty_success():
    mid = make_meeting([("SPEAKER_00", "placeholder")])
    with db.get_conn() as conn:
        conn.execute("DELETE FROM utterances WHERE meeting_id = ?", (mid,))
    with redirect_stdout(io.StringIO()):
        stats = run_extraction(mid, mode="rules")

    assert stats["status"] == "failed"
    assert db.get_extraction_run(mid)["status"] == "failed"


def test_extractor_exception_is_recorded_as_failed(monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("rule engine crashed")

    monkeypatch.setattr(
        "app.services.intelligence.extraction_pipeline.extract_with_rules", _boom
    )
    mid = make_meeting(FILLER)
    write_single_topic(mid)
    with pytest.raises(RuntimeError):
        run_extraction(mid, mode="rules")

    run = db.get_extraction_run(mid)
    assert run["status"] == "failed"
    assert "rule engine crashed" in run["reason"]
