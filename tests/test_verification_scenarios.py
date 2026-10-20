"""
Phase 1 adversarial verification scenarios (synthetic transcripts).

Each test inserts claims with a controlled defect and runs the real
verification orchestrator (deterministic verifier + retrieval + NLI scorer +
fusion + persistence). The embedding model and NLI pipeline are replaced by
transparent deterministic fakes (tests/semantic_fakes.py), so these tests
check the decision logic, not model accuracy. Real-model checks are in
tests/test_semantic_real_models.py (slow).
"""
from __future__ import annotations

from app.core import db
from app.services.verification.claim_verification import verify_meeting
from app.services.verification.claims_view import build_claims_view
from tests.scenario_helpers import (
    FILLER,
    add_action,
    add_decision,
    final_label,
    make_meeting,
    reason_codes,
)
from tests.semantic_fakes import install_semantic_fakes


def _verify(meeting_id):
    summary = verify_meeting(meeting_id)
    claims = {c["claim_id"]: c for c in build_claims_view(meeting_id)["claims"]}
    return summary, claims


def test_well_supported_decision_is_verified(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_00", "We decided to migrate the primary database to PostgreSQL."),
        ("SPEAKER_01", "Great, the whole team is on board with that plan."),
    ] + FILLER[2:])
    cid = add_decision(mid, 0, "Migrate the primary database to PostgreSQL", ["u00002"])

    summary, claims = _verify(mid)

    assert summary["status"] == "completed"
    claim = claims[cid]
    assert claim["verification"]["semantic"]["label"] == "supported"
    assert final_label(claim) in ("verified", "verified_with_warning")
    assert claim["exportable"] is True
    assert "cited_evidence_entails" in reason_codes(claim)
    # every final status carries reasons
    assert claim["verification"]["final"]["reasons"]


def test_incorrect_citation_with_correct_evidence_elsewhere_is_flagged_and_suggested(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER + [
        ("SPEAKER_00", "Let us move the product release to Friday, everyone agrees."),
        ("SPEAKER_01", "Sounds good to me."),
    ])
    # Cites the coffee-machine utterance instead of u00004.
    cid = add_decision(mid, 0, "Move the product release to Friday", ["u00000"])

    _, claims = _verify(mid)
    claim = claims[cid]

    assert claim["verification"]["semantic"]["label"] == "supported_elsewhere"
    assert final_label(claim) == "review"
    assert "possible_miscitation" in reason_codes(claim)
    assert claim["suggestion"] is not None
    assert "u00004" in claim["suggestion"]["utt_ids"]
    assert claim["exportable"] is False


def test_citation_supported_only_by_a_neighbouring_utterance_is_incomplete(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_00", "We decided to move the product release to Friday."),   # u00002: the decision
        ("SPEAKER_01", "Sounds good to me."),                                  # u00003: cited
    ] + FILLER[2:])
    cid = add_decision(mid, 0, "Move the product release to Friday", ["u00003"])

    _, claims = _verify(mid)
    claim = claims[cid]

    semantic = claim["verification"]["semantic"]
    assert semantic["label"] == "supported_with_context"
    assert semantic["details"]["e_cited_strict"] < 0.5 <= semantic["cited_entailment"]
    assert "citation_incomplete" in reason_codes(claim)
    assert final_label(claim) == "review"
    assert "u00002" in claim["suggestion"]["utt_ids"]


def test_cited_evidence_that_contradicts_the_claim_is_rejected(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_00", "We will not launch the mobile app in March after all."),
    ] + FILLER[2:])
    cid = add_decision(mid, 0, "Launch the mobile app in March", ["u00002"])

    _, claims = _verify(mid)
    claim = claims[cid]

    assert claim["verification"]["semantic"]["label"] == "contradicted"
    assert final_label(claim) == "rejected"
    assert "cited_evidence_contradicts" in reason_codes(claim)


def test_later_reversal_is_surfaced_for_review_not_assumed(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting([
        FILLER[0],
        FILLER[1],
        ("SPEAKER_00", "We decided to use PostgreSQL for the main database."),
        FILLER[2],
        FILLER[3],
        ("SPEAKER_01", "Actually drop PostgreSQL for the main database, use MySQL instead."),
    ])
    cid = add_decision(mid, 0, "Use PostgreSQL for the main database", ["u00002"])

    _, claims = _verify(mid)
    claim = claims[cid]

    # The cited span supports the claim, but a later utterance contradicts it.
    assert claim["verification"]["semantic"]["label"] == "supported"
    assert "contradicting_evidence_elsewhere" in reason_codes(claim)
    assert final_label(claim) == "review"
    assert claim["contradiction"] is not None
    assert "u00005" in claim["contradiction"]["utt_ids"]


def test_proposal_conditional_and_rejected_states_are_never_final(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_00", "Maybe we could switch the build server to Kubernetes."),
        ("SPEAKER_01", "If the budget is approved we will hire two more engineers."),
        ("SPEAKER_02", "How about we redesign the company logo this year."),
    ] + FILLER[2:])
    proposed = add_decision(mid, 0, "Switch the build server to Kubernetes", ["u00002"], state="proposed")
    conditional = add_decision(
        mid, 1, "Hire two more engineers", ["u00003"], state="conditional",
        conditional=True, condition_text="if the budget is approved",
    )
    rejected = add_decision(mid, 2, "Redesign the company logo this year", ["u00004"], state="rejected")

    _, claims = _verify(mid)

    assert "proposal_without_agreement" in reason_codes(claims[proposed])
    assert "conditional_decision" in reason_codes(claims[conditional])
    assert "rejected_proposal" in reason_codes(claims[rejected])
    for cid in (proposed, conditional, rejected):
        assert final_label(claims[cid]) == "review"
        assert claims[cid]["exportable"] is False


def test_wrong_owner_attribution_is_held_for_review(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_01", "Bob will prepare the budget report by Monday."),
    ] + FILLER[2:])
    cid = add_action(
        mid, 0, "Prepare the budget report", ["u00002"], owner="Carol",
        owner_type="explicit_third_person", deadline_text="by Monday",
        deadline_resolved="2026-10-19",
    )

    _, claims = _verify(mid)
    claim = claims[cid]

    assert "owner_not_supported" in reason_codes(claim)
    assert final_label(claim) == "review"


def test_first_person_owner_with_ambiguous_speaker_is_held_for_review(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_02", "I will send the signed contract to the client tomorrow.", {"spk": 0.35}),
    ] + FILLER[2:])
    cid = add_action(
        mid, 0, "Send the signed contract to the client", ["u00002"], owner="SPEAKER_02",
        owner_type="explicit_first_person", deadline_text="tomorrow", deadline_resolved="2026-10-17",
    )

    _, claims = _verify(mid)
    claim = claims[cid]

    assert "owner_from_uncertain_speaker" in reason_codes(claim)
    assert "low_speaker_confidence" in reason_codes(claim)
    assert final_label(claim) == "review"


def test_unknown_confidence_from_imported_transcript_is_not_treated_as_perfect(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [
        ("Priya", "We decided to adopt the new expense policy from next month."),
    ] + FILLER[2:], asr=None, spk=None)
    cid = add_decision(mid, 0, "Adopt the new expense policy from next month", ["u00002"])

    _, claims = _verify(mid)
    claim = claims[cid]

    codes = reason_codes(claim)
    assert {"asr_confidence_unknown", "speaker_confidence_unknown"} <= codes
    assert final_label(claim) == "verified_with_warning"  # supported, but never clean


def test_missing_and_ambiguous_deadlines_are_warned(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_00", "Asha will update the onboarding guide for new hires."),
        ("SPEAKER_01", "Ravi will clean up the test servers sometime soon."),
    ] + FILLER[2:])
    no_deadline = add_action(
        mid, 0, "Update the onboarding guide for new hires", ["u00002"], owner="Asha",
        owner_type="explicit_third_person",
    )
    vague = add_action(
        mid, 1, "Clean up the test servers", ["u00003"], owner="Ravi",
        owner_type="explicit_third_person", deadline_text="sometime soon",
    )

    _, claims = _verify(mid)

    assert "no_deadline" in reason_codes(claims[no_deadline])
    assert "deadline_ambiguous" in reason_codes(claims[vague])
    for cid in (no_deadline, vague):
        assert final_label(claims[cid]) != "verified"


def test_hypothetical_commitment_is_not_a_task(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_00", "If I had more time I would rewrite the parser module."),
    ] + FILLER[2:])
    cid = add_action(
        mid, 0, "Rewrite the parser module", ["u00002"], owner="SPEAKER_00",
        owner_type="explicit_first_person", commitment_type="hypothetical",
    )

    _, claims = _verify(mid)

    assert "hypothetical_commitment" in reason_codes(claims[cid])
    assert claims[cid]["exportable"] is False


def test_unavailable_semantic_verifier_is_explicit_and_never_verifies(monkeypatch):
    from app.services.verification.retrieval_index import RetrievalIndex

    def _broken(self):
        raise OSError("embedding model files not found")

    monkeypatch.setattr(RetrievalIndex, "_load_model", _broken)
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_00", "We decided to migrate the primary database to PostgreSQL."),
    ] + FILLER[2:])
    cid = add_decision(mid, 0, "Migrate the primary database to PostgreSQL", ["u00002"])

    summary, claims = _verify(mid)

    assert summary["status"] == "semantic_unavailable"
    assert "embedding model files not found" in summary["reason"]
    claim = claims[cid]
    assert claim["verification"]["semantic"]["label"] == "unavailable"
    assert claim["verification"]["semantic"]["cited_entailment"] is None  # no fabricated score
    assert "semantic_verifier_unavailable" in reason_codes(claim)
    assert final_label(claim) == "review"
    run = db.get_verification_run(mid)
    assert run["status"] == "semantic_unavailable"


def test_no_claims_is_recorded_as_no_claims(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER)

    summary, claims = _verify(mid)

    assert summary["status"] == "no_claims"
    assert claims == {}
    assert db.get_verification_run(mid)["status"] == "no_claims"


def test_verification_is_idempotent_and_records_latency(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [
        ("SPEAKER_00", "We decided to migrate the primary database to PostgreSQL."),
        ("SPEAKER_01", "Bob will configure the replica servers by Friday."),
    ] + FILLER[2:])
    add_decision(mid, 0, "Migrate the primary database to PostgreSQL", ["u00002"])
    add_action(mid, 0, "Configure the replica servers", ["u00003"], owner="Bob",
               owner_type="explicit_third_person", deadline_text="by Friday")

    first, _ = _verify(mid)
    second, _ = _verify(mid)

    results = db.get_verification_results(mid)
    assert sorted(len(methods) for methods in results.values()) == [3, 3]
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM verification_results WHERE meeting_id = ?", (mid,)
        ).fetchone()[0]
    assert rows == 6  # 2 claims x 3 methods, not duplicated by the second run
    for summary in (first, second):
        assert summary["timings"]["semantic_ms_per_claim_mean"] is not None
        assert len(summary["timings"]["per_claim_ms"]) == 2
    semantic = results[f"{mid}_d00000"]["semantic_nli"]
    assert semantic["model_version"].startswith("nli=")
    assert semantic["config_version"] == "fusion-v1-provisional"
    assert semantic["latency_ms"] is not None
