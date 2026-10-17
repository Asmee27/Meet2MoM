"""Phase 2: scoring logic of the evaluation harness (evidence matching, confirmations)."""
from __future__ import annotations

from pathlib import Path

import pytest

from evaluation.harness import Case, GoldItem, aggregate, annotated_cases, score_case


def _claim(cid, kind, evidence, state=None, commitment=None, owner=None, deadline=None):
    payload = {"state": state, "commitment_type": commitment, "owner": owner, "deadline_text": deadline}
    return {"claim_id": cid, "claim_type": kind, "evidence": evidence, "payload": payload, "text": cid}


def _case():
    gold = [
        GoldItem("d1", "decision", "agreed", ["u00010", "u00011"], confirmed=True, state="accepted"),
        GoldItem("d2", "decision", "only proposed", ["u00020"], confirmed=False, state="proposed"),
        GoldItem("a1", "action", "send report", ["u00030"], confirmed=True, state="firm",
                 owner="Ravi", owner_aliases=["Ravi"], deadline="by Friday"),
    ]
    return Case("t", "real", "real-dev", [], gold,
                negatives=[{"evidence": ["u00040"], "reason": "question"}])


def test_strict_and_near_matching():
    claims = [_claim("c1", "decision", ["u00011"], state="accepted"),   # strict hit d1
              _claim("c2", "decision", ["u00022"], state="proposed"),   # near hit d2 (+2)
              _claim("c3", "action", ["u00050"], commitment="tentative")]  # miss
    s = score_case(_case(), claims)
    assert s["decision_strict"]["tp_gold"] == 1 and s["decision_near"]["tp_gold"] == 2
    assert s["action_strict"]["tp_gold"] == 0


def test_false_confirmations_and_confirmed_unconfirmed_items():
    claims = [_claim("c1", "decision", ["u00020"], state="accepted"),   # confirms a proposal
              _claim("c2", "decision", ["u00040"], state="accepted"),   # confirms a negative
              _claim("c3", "decision", ["u00010"], state="accepted")]   # correct
    conf = score_case(_case(), claims)["confirmations"]
    assert conf["final_claims"] == 3
    assert conf["false_confirmations"] == 2
    assert conf["confirmed_an_unconfirmed_gold_item"] == 1
    assert conf["confirmed_a_negative"] == 1
    assert conf["confirmed_gold_found_as_final"] == 1


def test_attribute_accuracy_on_matched_pairs():
    claims = [_claim("c1", "action", ["u00030"], commitment="firm", owner="Ravi", deadline="by Friday"),
              _claim("c2", "decision", ["u00010", "u00011"], state="conditional")]
    s = score_case(_case(), claims)
    a = s["attributes"]
    assert a["owner_correct"] == 1 and a["deadline_correct"] == 1
    assert a["state_checked"] == 2 and a["state_correct"] == 1   # conditional != accepted
    agg = aggregate([s])
    assert agg["attributes"]["owner_accuracy"] == 1.0
    assert agg["attributes"]["state_accuracy"] == 0.5


def test_exportable_set_overrides_extractor_states():
    claims = [_claim("c1", "decision", ["u00010"], state="proposed")]
    conf = score_case(_case(), claims, exportable={"c1"})["confirmations"]
    assert conf["final_claims"] == 1 and conf["false_confirmations"] == 0


def test_all_annotation_files_are_valid_and_evidence_exists():
    cases = annotated_cases()
    assert {c.case_id for c in cases} >= {"demo_01", "q4_release"}
    assert sum(c.source == "synthetic" for c in cases) == 8
    for case in cases:
        ids = {s.get("utt_id") or f"u{i:05d}" for i, s in enumerate(case.segments)}
        for item in case.gold:
            assert item.evidence and set(item.evidence) <= ids, (case.case_id, item.item_id)
            assert item.item_type in ("decision", "action")


@pytest.mark.skipif(not Path("eval_data/ami/ami_public_manual_1.6.2.zip").exists(),
                    reason="AMI annotations not downloaded (python -c 'from evaluation.ami import fetch; fetch()')")
def test_ami_loader_produces_linked_gold():
    from evaluation.ami import AMICorpus, split_of

    meeting = AMICorpus().load("ES2002a")
    assert split_of("ES2002a") == "train" and split_of("IS1000a") == "dev" and split_of("TS3003a") == "test"
    assert len(meeting.utterances) > 300
    assert [u.utt_id for u in meeting.utterances][:3] == ["u00000", "u00001", "u00002"]
    linked = [g for g in meeting.gold if g.evidence]
    assert {g.item_type for g in linked} == {"decision", "action"}
    texts = {u.utt_id: u.text for u in meeting.utterances}
    assert any("twenty five" in texts[e].lower() for g in linked if "25 Euro" in g.text for e in g.evidence)
