"""
Phase 1 API contract tests: /api/extract, /api/verify, /api/meetings/{id}/claims,
evidence-suggestion review, and MoM/PDF export filtering.

Uses the real FastAPI app via TestClient, the real DB/verification pipeline in
the temp storage root, and deterministic semantic fakes.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core import config, db
from app.core.storage import MeetingStorage
from tests.scenario_helpers import (
    FILLER,
    add_action,
    add_decision,
    make_meeting,
    reason_codes,
    record_extraction,
)
from tests.semantic_fakes import install_semantic_fakes


@pytest.fixture()
def client(monkeypatch):
    install_semantic_fakes(monkeypatch)
    return TestClient(app)


def _mixed_meeting():
    """One supported decision, one mis-cited decision, one proposal, one action."""
    mid = make_meeting(FILLER + [
        ("SPEAKER_00", "We decided to migrate the primary database to PostgreSQL."),     # u00004
        ("SPEAKER_01", "Great, the whole team is on board with that plan."),              # u00005
        ("SPEAKER_02", "Let us move the product release to Friday, everyone agrees."),    # u00006
        ("SPEAKER_00", "Maybe we could switch the build server to Kubernetes."),          # u00007
        ("SPEAKER_01", "Bob will configure the replica servers by Friday."),              # u00008
    ])
    ids = {
        "supported": add_decision(mid, 0, "Migrate the primary database to PostgreSQL", ["u00004"]),
        "miscited": add_decision(mid, 1, "Move the product release to Friday", ["u00000"]),
        "proposal": add_decision(mid, 2, "Switch the build server to Kubernetes", ["u00007"], state="proposed"),
        "action": add_action(mid, 0, "Configure the replica servers", ["u00008"], owner="Bob",
                             owner_type="explicit_third_person", deadline_text="by Friday",
                             deadline_resolved="2026-10-23"),
    }
    record_extraction(mid)
    return mid, ids


def _claim(payload, claim_id):
    return next(c for c in payload["claims"] if c["claim_id"] == claim_id)


def test_verify_returns_persisted_results_with_full_payload(client):
    mid, ids = _mixed_meeting()

    response = client.post(f"/api/verify/{mid}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "needs_review"
    assert sum(body["counts"].values()) == 4
    for claim in body["claims"]:
        verification = claim["verification"]
        assert {"deterministic", "semantic", "final"} <= set(verification)
        assert verification["final"]["label"] in ("verified", "verified_with_warning", "review", "rejected")
        assert verification["final"]["reasons"]
        assert verification["semantic"]["model_version"].startswith("nli=")
        for utterance in claim["evidence"]:
            assert {"utt_id", "speaker", "start", "end", "text"} <= set(utterance)
    miscited = _claim(body, ids["miscited"])
    assert miscited["suggestion"]["utt_ids"] and "u00006" in miscited["suggestion"]["utt_ids"]
    assert miscited["suggestion"]["evidence"][0]["speaker"]
    assert (MeetingStorage(mid).path_for("verification", "verification_summary.json")).exists()


def test_claims_endpoint_matches_verify_and_has_no_side_effects(client):
    mid, _ = _mixed_meeting()
    verified = client.post(f"/api/verify/{mid}").json()

    claims = client.get(f"/api/meetings/{mid}/claims").json()

    assert [c["verification"]["final"]["label"] for c in claims["claims"]] == [
        c["verification"]["final"]["label"] for c in verified["claims"]
    ]
    missing = client.get("/api/meetings/doesnotexist123/claims")
    assert missing.status_code == 404
    assert not (config.STORAGE_ROOT / "doesnotexist123").exists()


def test_accepting_a_suggestion_replaces_evidence_and_reverifies(client):
    mid, ids = _mixed_meeting()
    client.post(f"/api/verify/{mid}")

    response = client.post(
        f"/api/meetings/{mid}/claims/{ids['miscited']}/evidence-suggestion", json={"action": "accept"}
    )

    assert response.status_code == 200
    claim = response.json()["claim"]
    assert "u00006" in claim["evidence_ids"]
    assert "u00000" not in claim["evidence_ids"]
    assert claim["correction"]["action"] == "accepted"
    assert claim["correction"]["original_utt_ids"] == ["u00000"]
    assert claim["suggestion"] is None
    assert claim["verification"]["semantic"]["label"] == "supported"
    assert "evidence_corrected_by_reviewer" in reason_codes(claim)
    # persisted in the DB, not just in the response
    links = [c for c in db.get_claims(mid) if c["claim_id"] == ids["miscited"]][0]["evidence"]
    assert "u00006" in links
    again = client.post(
        f"/api/meetings/{mid}/claims/{ids['miscited']}/evidence-suggestion", json={"action": "accept"}
    )
    assert again.status_code == 409


def test_rejecting_a_suggestion_is_recorded_and_keeps_claim_in_review(client):
    mid, ids = _mixed_meeting()
    client.post(f"/api/verify/{mid}")

    response = client.post(
        f"/api/meetings/{mid}/claims/{ids['miscited']}/evidence-suggestion", json={"action": "reject"}
    )

    assert response.status_code == 200
    claim = response.json()["claim"]
    assert claim["evidence_ids"] == ["u00000"]
    assert claim["correction"]["action"] == "rejected"
    assert claim["verification"]["final"]["label"] == "review"
    assert "suggestion_rejected_by_reviewer" in reason_codes(claim)
    assert db.get_evidence_corrections(mid)[ids["miscited"]]["action"] == "rejected"


def test_suggestion_endpoint_validates_input(client):
    mid, ids = _mixed_meeting()
    client.post(f"/api/verify/{mid}")

    bad_action = client.post(
        f"/api/meetings/{mid}/claims/{ids['miscited']}/evidence-suggestion", json={"action": "maybe"}
    )
    no_suggestion = client.post(
        f"/api/meetings/{mid}/claims/{ids['supported']}/evidence-suggestion", json={"action": "accept"}
    )
    unknown = client.post(
        f"/api/meetings/{mid}/claims/nope/evidence-suggestion", json={"action": "accept"}
    )
    assert bad_action.status_code == 422
    assert no_suggestion.status_code == 409
    assert unknown.status_code == 404


def test_mom_requires_verification_and_exports_only_confirmed_claims(client):
    mid, ids = _mixed_meeting()

    assert client.post(f"/api/generate-mom/{mid}").status_code == 409  # not verified yet

    verified = client.post(f"/api/verify/{mid}").json()
    labels = {c["claim_id"]: c["verification"]["final"]["label"] for c in verified["claims"]}
    response = client.post(f"/api/generate-mom/{mid}")
    assert response.status_code == 200
    mom = response.json()["mom"]

    exported = {d["claim_id"] for d in mom["decisions"]} | {a["claim_id"] for a in mom["action_items"]}
    held = {item["claim_id"] for item in mom["needs_review"]}
    assert ids["supported"] in exported
    assert ids["miscited"] in held and ids["miscited"] not in exported
    assert ids["proposal"] in held and ids["proposal"] not in exported
    assert all(labels[c] in ("verified", "verified_with_warning") for c in exported)
    assert all(item["reasons"] for item in mom["needs_review"])
    # Gate B: every exported item traces to a claim row, evidence and a verification row.
    results = db.get_verification_results(mid)
    claim_rows = {c["claim_id"]: c for c in db.get_claims(mid)}
    for item in mom["decisions"] + mom["action_items"]:
        assert item["claim_id"] in claim_rows
        assert item["evidence_ids"] and item["evidence_ids"] == claim_rows[item["claim_id"]]["evidence"]
        assert results[item["claim_id"]]["fusion"]["label"] == item["verification_status"]
    # warnings stay visible on exported items
    action = next(a for a in mom["action_items"] if a["claim_id"] == ids["action"])
    if action["verification_status"] == "verified_with_warning":
        assert action["warnings"]

    pdf = client.get(f"/api/download-mom/{mid}")
    assert pdf.status_code == 200
    assert pdf.content[:4] == b"%PDF"


def test_rejected_claims_never_reach_the_mom(client):
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_00", "We will not launch the mobile app in March after all."),
    ] + FILLER[2:])
    cid = add_decision(mid, 0, "Launch the mobile app in March", ["u00002"])
    record_extraction(mid)
    client.post(f"/api/verify/{mid}")

    mom = client.post(f"/api/generate-mom/{mid}").json()["mom"]

    assert mom["decisions"] == []
    assert cid not in {item["claim_id"] for item in mom["needs_review"]}
    assert mom["verification"]["rejected"] == 1
    assert "No decisions could be confirmed" in mom["summary"]


def test_extract_indexes_bare_transcript_and_reports_fallback(client, monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "stub")
    monkeypatch.setattr(config, "EXTRACTOR", "llm")
    monkeypatch.delenv("EXTRACTOR", raising=False)
    mid = "bare-transcript-meeting"
    storage = MeetingStorage(mid)
    storage.ensure_dirs()
    storage.path_for("transcript", "frontend_transcript.json").write_text(json.dumps({
        "language": "en",
        "segments": [
            {"start": 0.0, "end": 4.0, "speaker": "Alice",
             "text": "We decided to migrate our database to PostgreSQL."},
            {"start": 4.5, "end": 8.0, "speaker": "Bob",
             "text": "The coffee machine on the third floor is broken again."},
        ],
    }), encoding="utf-8")

    # No DB utterances and no topics yet: the endpoint indexes and segments.
    response = client.post(f"/api/extract/{mid}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "extracted"
    assert body["extraction"]["mode_requested"] == "llm"
    assert body["extraction"]["mode_used"] == "rules"
    assert "stub" in body["extraction"]["fallback_reason"]
    assert [d["text"] for d in body["decisions"]] == ["We decided to migrate our database to PostgreSQL."]
    assert body["decisions"][0]["evidence_ids"] == ["u00000"]
    written = json.loads(storage.path_for("intelligence", "extraction.json").read_text(encoding="utf-8"))
    assert written["file_id"] == mid
    # Re-running reuses the authoritative claims instead of extracting again.
    assert client.post(f"/api/extract/{mid}").json()["reused_existing"] is True


def test_extract_without_transcript_is_an_error_not_an_empty_result(client):
    response = client.post("/api/extract/no-transcript-meeting")
    assert response.status_code == 404


def test_verify_before_extraction_is_a_conflict(client):
    mid = make_meeting(FILLER)
    assert client.post(f"/api/verify/{mid}").status_code == 409
