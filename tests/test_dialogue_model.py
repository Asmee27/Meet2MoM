"""
Phase 2: dialogue model (learned extractor) and hybrid-merge policy.

The real embedding model is replaced by a deterministic fake so these tests do
not download models; the linear heads are the real shipped weights.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import numpy as np
import pytest

from app.core import db
from app.services.intelligence import dialogue_model as dm
from app.services.intelligence.extraction_pipeline import run_extraction
from tests.scenario_helpers import FILLER, make_meeting, write_single_topic
from tests.semantic_fakes import FakeEmbedder


@pytest.fixture()
def fake_embed(monkeypatch):
    embedder = FakeEmbedder()

    def _embed(texts):
        vecs = embedder.encode(texts)
        # project the 256-d fake vectors to the model's 384-d input space
        out = np.zeros((len(texts), 384), dtype=np.float32)
        out[:, : vecs.shape[1]] = vecs
        return out

    monkeypatch.setattr(dm, "embed", _embed)
    return _embed


def test_shipped_model_loads_without_pickle_and_documents_its_selection():
    assert dm.model_available()
    model = dm.load_model()
    assert set(model.heads) == {"speech_act", "decision", "action", "polarity"}
    assert "NONE" in model.heads["polarity"].classes
    assert "offer" in model.heads["speech_act"].classes
    for key in ("decision", "action", "pos", "neg", "part"):
        assert 0 < model.thresholds[key] <= 1
    selection = model.meta["threshold_selection"]
    assert "AMI dev" in selection["set"] and "test never read" in selection["set"]
    np.load(dm.MODEL_PATH, allow_pickle=False)  # plain arrays only


def test_learned_only_claims_are_never_final():
    rules = [{"claim_type": "decision", "state": "accepted", "evidence": ["u00001"], "text": "a"}]
    learned = [
        {"claim_type": "decision", "state": "accepted", "evidence": ["u00009"], "text": "b"},
        {"claim_type": "action", "commitment_type": "firm", "evidence": ["u00012"], "text": "c",
         "owner": "Marcus", "owner_type": "explicit_first_person", "deadline_text": None},
    ]
    merged = dm.merge_candidates(rules, learned)
    by_ev = {tuple(c["evidence"]): c for c in merged}
    assert by_ev[("u00001",)]["state"] == "accepted"          # rules claim untouched
    assert by_ev[("u00009",)]["state"] == "proposed"          # learned-only: never accepted
    assert by_ev[("u00012",)]["commitment_type"] == "tentative"


def test_merge_only_makes_states_more_cautious():
    rules = [
        {"claim_type": "decision", "state": "accepted", "evidence": ["u00003", "u00004"], "text": "x"},
        {"claim_type": "decision", "state": "proposed", "evidence": ["u00007"], "text": "y"},
    ]
    learned = [
        {"claim_type": "decision", "state": "conditional", "evidence": ["u00004", "u00005"], "text": "x'"},
        {"claim_type": "decision", "state": "accepted", "evidence": ["u00007"], "text": "y'"},
    ]
    merged = dm.merge_candidates(rules, learned)
    assert [c["state"] for c in merged] == ["conditional", "proposed"]
    assert merged[0]["conditional"] is True
    assert len(merged) == 2  # twins are merged, not duplicated


def test_merge_fills_missing_owner_and_deadline_only():
    rules = [{"claim_type": "action", "commitment_type": "firm", "evidence": ["u00002"], "text": "t",
              "owner": None, "owner_type": "unassigned", "deadline_text": None}]
    learned = [{"claim_type": "action", "commitment_type": "tentative", "evidence": ["u00002"], "text": "t",
                "owner": "Ravi", "owner_type": "explicit_first_person", "deadline_text": "by Thursday"}]
    merged = dm.merge_candidates(rules, learned)[0]
    assert merged["owner"] == "Ravi" and merged["deadline_text"] == "by Thursday"
    assert merged["commitment_type"] == "firm"  # modality stays with the rules


def test_decode_returns_well_formed_candidates(fake_embed):
    utts = [type("U", (), {"utt_id": f"u{i:05d}", "text": t, "speaker_id": s})()
            for i, (s, t) in enumerate(FILLER + [("SPEAKER_00", "I will send the signed contract tomorrow."),
                                                  ("SPEAKER_01", "Yes, that works for everyone.")])]
    model = dm.load_model()
    cands = dm.decode(model, utts, fake_embed([u.text for u in utts]))
    for c in cands:
        assert c["claim_type"] in ("decision", "action")
        assert c["evidence"] and all(e.startswith("u") for e in c["evidence"])
        if c["claim_type"] == "decision":
            assert c["state"] in ("proposed", "accepted", "conditional", "rejected")
        else:
            assert c["commitment_type"] in ("firm", "tentative")


@pytest.mark.parametrize("lines", [
    FILLER[:2] + [("SPEAKER_00", "I suggest we move the product release to Friday."),
                  ("SPEAKER_01", "Yes, that works for everyone."),
                  ("SPEAKER_02", "I can deploy a webhook workaround."),
                  ("SPEAKER_00", "We must fix the pipeline.")] + FILLER[2:],
    FILLER[:2] + [("SPEAKER_00", "If the budget is approved, we will hire two more engineers."),
                  ("SPEAKER_01", "Maybe we could switch the build server to Kubernetes.")] + FILLER[2:],
])
def test_hybrid_never_adds_confirmations_beyond_rules(fake_embed, lines):
    def finals(meeting_id):
        out = set()
        for c in db.get_claims(meeting_id):
            p = c["payload"]
            if (c["claim_type"] == "decision" and p.get("state") == "accepted") or (
                    c["claim_type"] == "action" and p.get("commitment_type") == "firm"):
                out.add((c["claim_type"], tuple(c["evidence"])))
        return out

    rules_mid, hybrid_mid = make_meeting(lines), make_meeting(lines)
    for mid in (rules_mid, hybrid_mid):
        write_single_topic(mid)
    with redirect_stdout(io.StringIO()):
        run_extraction(rules_mid, mode="rules")
        stats = run_extraction(hybrid_mid, mode="hybrid")
    assert stats["mode_used"] == "hybrid"
    assert len(db.get_claims(hybrid_mid)) >= len(db.get_claims(rules_mid))
    assert finals(hybrid_mid) <= finals(rules_mid)


def test_missing_model_falls_back_to_rules_and_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(dm, "MODEL_PATH", tmp_path / "missing.npz")
    mid = make_meeting(FILLER[:2] + [("SPEAKER_00", "We decided to migrate the primary database to PostgreSQL.")])
    write_single_topic(mid)
    with redirect_stdout(io.StringIO()):
        stats = run_extraction(mid, mode="hybrid")
    assert stats["mode_used"] == "rules"
    assert "dialogue model file not found" in stats["fallback_reason"]
    assert db.get_extraction_run(mid)["fallback_reason"].startswith("dialogue model file not found")


def test_default_llm_fallback_is_hybrid_and_recorded(fake_embed, monkeypatch):
    from app.core import config

    monkeypatch.delenv("FALLBACK_EXTRACTOR", raising=False)
    monkeypatch.setattr(config, "LLM_PROVIDER", "stub")
    mid = make_meeting(FILLER[:2] + [("SPEAKER_00", "We decided to migrate the primary database to PostgreSQL.")] + FILLER[2:])
    write_single_topic(mid)
    with redirect_stdout(io.StringIO()):
        stats = run_extraction(mid, mode="llm")
    run = db.get_extraction_run(mid)
    assert stats["mode_used"] == "hybrid" and run["mode_used"] == "hybrid"
    assert "stub" in run["fallback_reason"]
    assert run["extractor_version"].startswith("rules-v2+dialogue-v1")


def test_hybrid_degrades_to_rules_when_embedding_fails(monkeypatch):
    def _broken(texts):
        raise OSError("sentence-transformers weights missing")

    monkeypatch.setattr(dm, "embed", _broken)
    mid = make_meeting(FILLER[:2] + [("SPEAKER_00", "We decided to migrate the primary database to PostgreSQL.")])
    write_single_topic(mid)
    with redirect_stdout(io.StringIO()):
        stats = run_extraction(mid, mode="hybrid")
    assert stats["status"] == "completed" and stats["mode_used"] == "rules"
    assert "dialogue model failed" in stats["fallback_reason"]
