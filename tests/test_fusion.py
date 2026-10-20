"""Unit tests for the transparent fusion rules (app/services/verification/fusion.py)."""
from __future__ import annotations

from types import SimpleNamespace

from app.services.verification.fusion import FUSION_CONFIG, fuse


def _utt(utt_id="u00001", asr=0.9, spk=0.9, overlap=False, no_speech=0.1, speaker="SPEAKER_00"):
    return SimpleNamespace(
        utt_id=utt_id, asr_conf=asr, spk_conf=spk, overlap=overlap,
        no_speech_prob=no_speech, speaker_id=speaker,
    )


def _decision(state="accepted", **payload):
    return {"claim_type": "decision", "evidence": ["u00001"], "payload": {"state": state, **payload}}


def _action(**payload):
    base = {"owner": "Bob", "owner_type": "explicit_third_person", "deadline_text": "by Friday",
            "deadline_resolved": "2026-10-23", "commitment_type": "firm"}
    return {"claim_type": "action", "evidence": ["u00001"], "payload": {**base, **payload}}


SUPPORTED = {"label": "supported", "e_cited": 0.95, "e_cited_margin": 0.9, "per_hypothesis": {}}
DET_OK = {"label": "verified", "matched_utt_id": "u00001", "matches_cited": True}


def _codes(result):
    return {r["code"] for r in result["reasons"]}


def test_clean_support_is_verified_with_a_reason():
    result = fuse(_decision(), DET_OK, SUPPORTED, [_utt()])
    assert result["label"] == "verified"
    assert "cited_evidence_entails" in _codes(result)
    assert result["config_version"] == FUSION_CONFIG["version"]


def test_missing_confidence_is_unknown_not_perfect():
    result = fuse(_decision(), DET_OK, SUPPORTED, [_utt(asr=None, spk=None)])
    assert result["label"] == "verified_with_warning"
    assert {"asr_confidence_unknown", "speaker_confidence_unknown"} <= _codes(result)


def test_never_verified_without_positive_semantic_support():
    for semantic in ({"label": "unsupported"}, {"label": "unavailable", "reason": "x"},
                     {"label": "error", "reason": "boom"}):
        result = fuse(_decision(), DET_OK, semantic, [_utt()])
        assert result["label"] in ("review", "rejected"), semantic


def test_high_text_similarity_alone_is_not_agreement():
    # Deterministic says "verified" (text matches) but meaning is not entailed.
    result = fuse(_decision(), DET_OK, {"label": "unsupported", "e_cited": 0.1}, [_utt()])
    assert result["label"] == "review"
    assert "not_entailed" in _codes(result)


def test_deterministic_miss_with_semantic_support_is_only_a_warning():
    result = fuse(_decision(), {"label": "rejected"}, SUPPORTED, [_utt()])
    assert result["label"] == "verified_with_warning"
    assert "deterministic_no_match" in _codes(result)


def test_contradiction_on_cited_span_rejects():
    result = fuse(_decision(), DET_OK, {"label": "contradicted", "e_cited": 0.05, "x_cited": 0.9}, [_utt()])
    assert result["label"] == "rejected"


def test_contradiction_elsewhere_holds_for_review_without_superseding():
    semantic = {**SUPPORTED, "x_core": 0.7, "x_core_utt_ids": ["u00009"]}
    result = fuse(_decision(), DET_OK, semantic, [_utt()])
    assert result["label"] == "review"
    assert "contradicting_evidence_elsewhere" in _codes(result)


def test_low_contradiction_is_ignored():
    semantic = {**SUPPORTED, "x_core": 0.2, "x_core_utt_ids": ["u00009"]}
    assert fuse(_decision(), DET_OK, semantic, [_utt()])["label"] == "verified"


def test_non_final_decision_states_need_review():
    for state, code in (("proposed", "proposal_without_agreement"), ("conditional", "conditional_decision"),
                        ("deferred", "deferred_decision"), ("rejected", "rejected_proposal")):
        result = fuse(_decision(state=state), DET_OK, SUPPORTED, [_utt()])
        assert result["label"] == "review" and code in _codes(result), state


def test_missing_evidence_rejects():
    claim = {"claim_type": "decision", "evidence": [], "payload": {"state": "accepted"}}
    assert fuse(claim, DET_OK, SUPPORTED, [])["label"] == "rejected"


def test_owner_and_deadline_rules():
    unsupported_owner = {**SUPPORTED, "per_hypothesis": {"owner": 0.1, "deadline": 0.9}}
    assert "owner_not_supported" in _codes(fuse(_action(), DET_OK, unsupported_owner, [_utt()]))
    no_owner = fuse(_action(owner=None, owner_type="unassigned"), DET_OK, SUPPORTED, [_utt()])
    assert no_owner["label"] == "verified_with_warning" and "no_owner" in _codes(no_owner)
    vague = fuse(_action(deadline_resolved=None, deadline_text="soonish"), DET_OK, SUPPORTED, [_utt()])
    assert "deadline_ambiguous" in _codes(vague)
    first_person = _action(owner="SPEAKER_00", owner_type="explicit_first_person")
    assert fuse(first_person, DET_OK, SUPPORTED, [_utt(spk=0.3)])["label"] == "review"


def test_reliability_warnings():
    result = fuse(_decision(), DET_OK, SUPPORTED, [_utt(asr=0.3, overlap=True, no_speech=0.9, speaker="UNKNOWN")])
    assert {"low_asr_confidence", "overlapping_speech", "possible_non_speech", "unknown_speaker"} <= _codes(result)
    assert result["label"] == "verified_with_warning"


def test_reasons_are_sorted_by_severity_and_deduplicated():
    semantic = {**SUPPORTED, "x_core": 0.9, "x_core_utt_ids": ["u00009"]}
    result = fuse(_decision(state="proposed"), {"label": "rejected"}, semantic, [_utt(asr=None)])
    severities = [r["severity"] for r in result["reasons"]]
    order = {"reject": 3, "review": 2, "warning": 1, "info": 0}
    assert severities == sorted(severities, key=lambda s: -order[s])
    assert len(_codes(result)) == len(result["reasons"])
