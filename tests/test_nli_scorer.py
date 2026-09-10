from __future__ import annotations

import random
import re
from uuid import uuid4

import pytest

from app.core import db
from app.schemas.utterance import Utterance
from app.services.verification.candidate_builder import (
    ClaimCandidates,
    EvidenceCandidate,
    Provenance,
)
from app.services.verification.nli_scorer import (
    MIN_SUPPORT_MARGIN,
    NLIScorer,
    background_entailment,
    build_hypotheses,
    content_coverage,
    expand_premise,
)
from app.services.verification.retrieval_index import EvidenceSpan


class FakePipe:
    def __init__(self, scores_by_pair=None, id2label=None):
        self.calls = 0
        self.scores_by_pair = scores_by_pair or {}

        class M:
            pass

        self.model = M()
        self.model.config = M()
        self.model.config.id2label = id2label or {
            0: "entailment",
            1: "neutral",
            2: "contradiction",
        }

    def __call__(self, inputs, batch_size=8):
        self.calls += 1
        out = []
        for item in inputs:
            key = (item["text"][:40], item["text_pair"][:40])
            score = self.scores_by_pair.get(
                key,
                {"entailment": 0.1, "neutral": 0.8, "contradiction": 0.1},
            )
            out.append(
                [{"label": str(k), "score": float(v)} for k, v in score.items()]
            )
        return out


def _meeting_with_ids(meeting_id: str, texts: list[str]):
    db.insert_meeting(meeting_id)
    rows = []
    for idx, text in enumerate(texts, start=1):
        rows.append(
            Utterance(
                meeting_id=meeting_id,
                utt_id=f"u{idx:05d}",
                start=float(idx),
                end=float(idx + 1),
                speaker_id=f"SPK_{idx % 2 + 1}",
                speaker_name=f"Speaker {idx % 2 + 1}",
                text=text,
                lang_tags=["en"],
                script="latin",
            )
        )
    db.bulk_insert_utterances(meeting_id, rows)
    return rows


def _make_claim_candidates(
    meeting_id: str,
    claim_id: str,
    claim_type: str,
    claim_text: str,
    cited_text: str,
    candidate_text: str,
    candidate_utt_ids: list[str] | None = None,
    cited_utt_ids: list[str] | None = None,
    cited_span: EvidenceSpan | None = None,
):
    cids = cited_utt_ids or ["u00001"]
    candidate_ids = candidate_utt_ids or ["u00002"]
    cspan = cited_span or EvidenceSpan(
        span_id=f"cited_{claim_id}",
        meeting_id=meeting_id,
        utt_ids=cids,
        text=cited_text,
        start=0.0,
        end=1.0,
        speakers=["S1"],
    )
    cand = EvidenceCandidate(
        span=EvidenceSpan(
            span_id=f"span_{claim_id}",
            meeting_id=meeting_id,
            utt_ids=candidate_ids,
            text=candidate_text,
            start=1.0,
            end=2.0,
            speakers=["S1"],
        ),
        provenance=Provenance.RETRIEVED_ONLY,
        retrieval_score=0.9,
        rank=0,
    )
    return ClaimCandidates(
        claim_id=claim_id,
        meeting_id=meeting_id,
        claim_type=claim_type,
        claim_text=claim_text,
        query_text=claim_text,
        cited_utt_ids=cids,
        candidates=[cand],
        cited_overlap_any=0.0,
        cited_recall_strict=0.0,
        cited_span=cspan,
        union_span_ids=[cand.span.span_id],
    )


def test_hypothesis_decomposition_action():
    claim = {
        "text": "Prepare the slides",
        "claim_type": "action",
        "owner": "Priya",
        "owner_type": "explicit_third_person",
        "deadline_text": "Friday",
    }
    hyps = build_hypotheses(claim)
    assert len(hyps) == 3
    assert {h.kind for h in hyps} == {"core", "owner", "deadline"}
    weights = {h.kind: h.weight for h in hyps}
    assert weights == {"core": 1.0, "owner": 0.6, "deadline": 0.5}


def test_hypothesis_decomposition_decision_accepted():
    claim = {"text": "Ship the release", "claim_type": "decision", "state": "accepted"}
    hyps = build_hypotheses(claim)
    assert {h.kind for h in hyps} == {"core", "state"}
    assert [h.weight for h in hyps if h.kind == "core"] == [1.0]
    assert [h.weight for h in hyps if h.kind == "state"] == [0.4]


def test_unassigned_owner_no_owner_hypothesis():
    claim = {
        "text": "Prepare the slides",
        "claim_type": "action",
        "owner": "unassigned",
        "owner_type": "unassigned",
    }
    hyps = build_hypotheses(claim)
    assert all(h.kind != "owner" for h in hyps)


def test_evidence_mismatch_flagged(monkeypatch):
    meeting_id = f"mismatch-{uuid4().hex}"
    _meeting_with_ids(
        meeting_id,
        [
            "alpha",
            "So perhaps you and I could look at the report together? Yes, OK.",
            "the full report first, of course.",
        ],
    )
    cc = _make_claim_candidates(
        meeting_id=meeting_id,
        claim_id="c-1",
        claim_type="action",
        claim_text="Look at the report together.",
        cited_text="the full report first, of course.",
        candidate_text="So perhaps you and I could look at the report together? Yes, OK.",
        candidate_utt_ids=["u00002"],
        cited_utt_ids=["u00003"],
    )
    cc.cited_span = EvidenceSpan(
        span_id="citedspan_c-1",
        meeting_id=meeting_id,
        utt_ids=["u00003"],
        text="the full report first, of course.",
        start=10.0,
        end=11.0,
        speakers=["S1"],
    )
    fake_pipe = FakePipe(
        scores_by_pair={
            ("alpha So perhaps you and I could look at", "Look at the report together."): {
                "entailment": 0.85,
                "neutral": 0.10,
                "contradiction": 0.05,
            },
            ("So perhaps you and I could look at the r", "Look at the report together."): {
                "entailment": 0.20,
                "neutral": 0.75,
                "contradiction": 0.05,
            },
        }
    )
    monkeypatch.setattr("app.core.model_registry.get_hf_pipeline", lambda *args, **kwargs: fake_pipe)
    scorer = NLIScorer()
    result = scorer.score_claim(
        cc, {"text": "Look at the report together.", "claim_type": "action"}
    )
    assert result.evidence_mismatch is True
    assert result.proposed_utt_ids == result.best_span_utt_ids
    assert result.e_cited == 0.2
    assert result.e_union == 0.85


def test_cited_span_none(monkeypatch):
    meeting_id = f"cited-none-{uuid4().hex}"
    _meeting_with_ids(meeting_id, ["We are ready to ship the release.", "The release is ready."])
    cc = _make_claim_candidates(
        meeting_id=meeting_id,
        claim_id="c-2",
        claim_type="decision",
        claim_text="Ship the release",
        cited_text="the release is ready",
        candidate_text="We are ready to ship the release.",
        candidate_utt_ids=["u00001"],
        cited_utt_ids=["u00002"],
        cited_span=None,
    )
    cc.cited_span = None
    fake_pipe = FakePipe(
        scores_by_pair={
            ("We are ready to ship the release.", "Ship the release"): {
                "entailment": 0.90,
                "neutral": 0.08,
                "contradiction": 0.02,
            },
        }
    )
    monkeypatch.setattr("app.core.model_registry.get_hf_pipeline", lambda *args, **kwargs: fake_pipe)
    scorer = NLIScorer()
    result = scorer.score_claim(cc, {"text": "Ship the release", "claim_type": "decision"})
    assert result.e_cited == 0.0
    assert result.x_cited == 0.0
    assert result.evidence_mismatch is True
    assert not result.proposed_utt_ids
    assert result.best_span_utt_ids == ["u00001"]


def test_label_map_read_from_config(monkeypatch):
    fake_pipe = FakePipe(
        scores_by_pair={
            ("premise", "hypothesis"): {
                "LABEL_0": 0.90,
                "LABEL_1": 0.05,
                "LABEL_2": 0.05,
            }
        },
        id2label={0: "contradiction", 1: "entailment", 2: "neutral"},
    )
    monkeypatch.setattr("app.core.model_registry.get_hf_pipeline", lambda *args, **kwargs: fake_pipe)
    scorer = NLIScorer()
    scores = scorer.score_pairs([("premise", "hypothesis")])
    assert scores[0]["contradiction"] == 0.9
    assert scores[0]["entailment"] == 0.05
    assert scores[0]["neutral"] == 0.05


def test_cache_hit(monkeypatch):
    meeting_id = f"cache-check-{uuid4().hex}"
    _meeting_with_ids(meeting_id, ["We are ready to ship the release.", "The release is ready."])
    fake_pipe = FakePipe(
        scores_by_pair={
            ("We are ready to ship the release.", "Ship the release"): {
                "entailment": 0.90,
                "neutral": 0.08,
                "contradiction": 0.02,
            }
        }
    )
    monkeypatch.setattr("app.core.model_registry.get_hf_pipeline", lambda *args, **kwargs: fake_pipe)
    cc = _make_claim_candidates(
        meeting_id=meeting_id,
        claim_id="cache-claim",
        claim_type="decision",
        claim_text="Ship the release",
        cited_text="The release is ready.",
        candidate_text="We are ready to ship the release.",
        candidate_utt_ids=["u00001"],
        cited_utt_ids=["u00002"],
    )
    scorer = NLIScorer()
    scorer.score_claim(cc, {"text": "Ship the release", "claim_type": "decision"})
    first_calls = fake_pipe.calls
    scorer.score_claim(cc, {"text": "Ship the release", "claim_type": "decision"})
    assert fake_pipe.calls == first_calls


def test_expand_premise_widens_text_not_ids():
    meeting_id = f"expand-{uuid4().hex}"
    _meeting_with_ids(meeting_id, ["alpha", "beta", "gamma"])
    expanded = expand_premise(meeting_id, ["u00002"], context=1)
    assert "alpha" in expanded and "gamma" in expanded
    assert "beta" in expanded
    assert expand_premise(meeting_id, ["u00002"], context=1) != "beta"


def test_expand_premise_clamps_at_boundaries():
    meeting_id = f"clamp-{uuid4().hex}"
    _meeting_with_ids(meeting_id, ["alpha", "beta", "gamma"])
    first = expand_premise(meeting_id, ["u00001"], context=2)
    last = expand_premise(meeting_id, ["u00003"], context=2)
    assert "alpha" in first and "beta" in first
    assert "beta" in last and "gamma" in last


def test_background_is_deterministic():
    meeting_id = f"background-{uuid4().hex}"
    _meeting_with_ids(meeting_id, [f"sentence {i}" for i in range(8)])
    spans = [
        EvidenceSpan(f"s{i}", meeting_id, [f"u{i:05d}"], f"sentence {i}", i, i + 1, ["S"])
        for i in range(1, 8)
    ]
    scorer = NLIScorer()
    scorer._pipe = FakePipe(
        scores_by_pair={
            ("sentence 1", "hello world"): {"entailment": 0.11, "neutral": 0.81, "contradiction": 0.08},
            ("sentence 2", "hello world"): {"entailment": 0.12, "neutral": 0.80, "contradiction": 0.08},
            ("sentence 3", "hello world"): {"entailment": 0.13, "neutral": 0.79, "contradiction": 0.08},
            ("sentence 4", "hello world"): {"entailment": 0.14, "neutral": 0.78, "contradiction": 0.08},
            ("sentence 5", "hello world"): {"entailment": 0.15, "neutral": 0.77, "contradiction": 0.08},
            ("sentence 6", "hello world"): {"entailment": 0.16, "neutral": 0.76, "contradiction": 0.08},
        }
    )
    first = background_entailment(scorer, meeting_id, "hello world", {"u00001"}, spans)
    second = background_entailment(scorer, meeting_id, "hello world", {"u00001"}, spans)
    assert first == second


def test_background_excludes_cited(monkeypatch):
    meeting_id = f"background-exclude-{uuid4().hex}"
    _meeting_with_ids(meeting_id, [f"sentence {i}" for i in range(8)])
    spans = [
        EvidenceSpan(f"s{i}", meeting_id, [f"u{i:05d}"], f"sentence {i}", i, i + 1, ["S"])
        for i in range(1, 8)
    ]
    scorer = NLIScorer()
    scorer._pipe = FakePipe()
    original = random.Random.sample

    def spy(self, seq, k):
        assert all(not set(span.utt_ids).intersection({"u00001", "u00002"}) for span in seq[:k])
        return original(self, seq, k)

    monkeypatch.setattr(random.Random, "sample", spy)
    score = background_entailment(scorer, meeting_id, "hello world", {"u00001", "u00002"}, spans)
    assert isinstance(score, float)


def test_margin_lowers_mismatch_threshold():
    e_cited = 0.64
    e_background = 0.55
    e_union = 0.90
    e_cited_margin = e_cited - e_background
    e_union_margin = e_union - e_background
    mismatch = ((e_union_margin - e_cited_margin) > 0.30) or (e_cited_margin < MIN_SUPPORT_MARGIN)
    assert e_cited_margin < MIN_SUPPORT_MARGIN
    assert mismatch is True


def test_content_coverage_devanagari():
    claim = "The script will be ready by Friday"
    premise = "मैं शुक्रवार तक script ready कर दूँगी"
    cov = content_coverage(claim, premise)
    assert cov >= 0.5
    tokens = re.split(r"\W+", premise.lower(), flags=re.UNICODE)
    assert "script" in tokens
    assert "ready" in tokens


def test_content_coverage_full_match():
    assert content_coverage("The script will be ready by Friday", "script ready by friday") == 1.0


@pytest.mark.slow
def test_real_model_supported(capsys):
    scorer = NLIScorer()
    premise = "So perhaps you and I could look at the report together? Yes, OK."
    hypothesis = "Look at the report together."
    scores = scorer.score_pairs([(premise, hypothesis)])
    print("supported", scores[0])
    assert scores[0]["entailment"] > 0.6


@pytest.mark.slow
def test_real_model_unsupported_is_caught(capsys):
    scorer = NLIScorer()
    premise = "the full report first, of course."
    hypothesis = "Look at the report together."
    raw = scorer.score_pairs([(premise, hypothesis)])[0]
    cov = content_coverage(hypothesis, premise)
    print("raw_entailment", raw["entailment"])
    print("content_coverage", cov)
    assert raw["entailment"] > 0.6
    assert cov < 0.5


@pytest.mark.slow
def test_real_model_contradicted(capsys):
    scorer = NLIScorer()
    premise = "We decided not to do any more training."
    hypothesis = "The team agreed to run additional training."
    scores = scorer.score_pairs([(premise, hypothesis)])
    print("contradicted", scores[0])
    assert scores[0]["contradiction"] > 0.5


@pytest.mark.slow
def test_real_model_wrong_owner(capsys):
    scorer = NLIScorer()
    premise = "I'll prepare the slides, said Anna."
    hypothesis = "Priya is responsible for: prepare the slides"
    scores = scorer.score_pairs([(premise, hypothesis)])
    print("wrong_owner", scores[0])
    assert scores[0]["entailment"] < 0.5


@pytest.mark.slow
def test_real_model_codeswitch(capsys):
    scorer = NLIScorer()
    premise = "मैं शुक्रवार तक script ready कर दूँगी"
    hypothesis = "The script will be ready by Friday."
    scores = scorer.score_pairs([(premise, hypothesis)])
    print("codeswitch", scores[0])
    assert scores[0]["entailment"] > 0.5
