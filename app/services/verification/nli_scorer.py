from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core import db
from app.core.storage import MeetingStorage
from app.services.verification.candidate_builder import ClaimCandidates, build_candidates

logger = logging.getLogger(__name__)

MISMATCH_DELTA = 0.30
MIN_SUPPORT_MARGIN = 0.25
MAX_PREMISE_CHARS = 1200
PREMISE_CONTEXT = 1
BACKGROUND_N = 5
CONTENT_COVERAGE_MIN = 0.5
# A retrieved utterance can contradict a claim only if it is about the claim:
# at least this share of the claim's content words must appear in it. Without
# this, NLI scored one person's "I will finalize the backend API" as a 0.97
# contradiction of another person's unrelated "complete the front-end
# validation" task. Cited evidence is always checked, whatever its wording.
CONTRADICTION_TOPIC_MIN = 0.34
SECONDARY_HYPOTHESIS_TOP_N = 3
NLI_MODEL = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at",
    "by", "is", "are", "was", "were", "be", "been", "will", "would", "could",
    "should", "that", "this", "it", "we", "i", "you", "he", "she", "they",
    "so", "but", "if", "then", "there", "here", "do", "does", "did", "have",
    "has", "had", "not", "no", "yes", "ok", "okay", "just", "about", "from",
}


@dataclass
class Hypothesis:
    kind: str
    text: str
    weight: float


@dataclass
class PairScore:
    span_id: str
    utt_ids: list[str]
    hypothesis_kind: str
    entail: float
    neutral: float
    contradict: float
    provenance: str
    expanded: bool
    # Utterances actually included in the scored premise (span + context).
    premise_utt_ids: list[str] = field(default_factory=list)
    premise: str = ""


@dataclass
class ClaimNLIResult:
    claim_id: str
    meeting_id: str
    e_union: float
    x_union: float
    best_span_id: str | None
    best_span_utt_ids: list[str]
    e_cited: float
    x_cited: float
    e_background: float
    e_cited_margin: float
    e_union_margin: float
    content_coverage: float
    per_hypothesis: dict[str, float]
    n_pairs: int
    evidence_mismatch: bool
    proposed_utt_ids: list[str]
    # Where the strongest contradiction came from (core hypothesis only), so a
    # possible reversal can be shown to the reviewer instead of just a number.
    x_union_utt_ids: list[str] = field(default_factory=list)
    x_union_provenance: str | None = None
    x_core: float = 0.0
    # Entailment from the cited utterances alone (no neighbouring context).
    e_cited_strict: float | None = None
    # Strongest contradiction from retrieved utterances about something else
    # (kept for transparency; it does not hold the claim).
    x_off_topic: float = 0.0
    x_off_topic_utt_ids: list[str] = field(default_factory=list)


def content_coverage(claim_text: str, premise_text: str) -> float:
    claim_tokens = [
        token for token in re.split(r"\W+", str(claim_text).lower(), flags=re.UNICODE) if token
    ]
    premise_tokens = [
        token for token in re.split(r"\W+", str(premise_text).lower(), flags=re.UNICODE) if token
    ]
    content = {
        token for token in claim_tokens if token not in STOPWORDS and len(token) >= 3
    }
    if not content:
        return 1.0
    overlap = sum(1 for token in content if token in premise_tokens)
    return overlap / len(content)


def expand_premise(meeting_id: str, utt_ids: list[str], context: int = PREMISE_CONTEXT) -> str:
    """Expand a premise by neighboring utterances without altering stored evidence IDs."""
    if not utt_ids:
        return ""
    utts = db.get_utterances(meeting_id)
    positions = []
    by_id = {u.utt_id: u for u in utts}
    for utt_id in utt_ids:
        if utt_id in by_id:
            positions.append(utts.index(by_id[utt_id]))
    if not positions:
        return " ".join(
            str(utt.text).strip()
            for utt in utts
            if utt.utt_id in set(utt_ids)
            and str(utt.text).strip()
        )
    start = max(0, min(positions) - context)
    end = min(len(utts), max(positions) + context + 1)
    expanded = []
    for utt in utts[start:end]:
        text = str(utt.text).strip()
        if text:
            expanded.append(text)
    return " ".join(expanded)


def expand_utt_ids(meeting_id: str, utt_ids: list[str], context: int = PREMISE_CONTEXT) -> list[str]:
    """The utterance ids covered by expand_premise() for the same inputs."""
    if not utt_ids:
        return []
    utts = db.get_utterances(meeting_id)
    order = [u.utt_id for u in utts]
    positions = [order.index(i) for i in utt_ids if i in order]
    if not positions:
        return list(utt_ids)
    start = max(0, min(positions) - context)
    end = min(len(order), max(positions) + context + 1)
    return order[start:end]


def background_entailment(
    scorer: "NLIScorer",
    meeting_id: str,
    claim_text: str,
    exclude_utt_ids: set[str],
    spans: list,
) -> float:
    """Deterministically sample unrelated spans to estimate the entailment noise floor."""
    candidate_spans: list[Any] = []
    if spans:
        for span in spans:
            if not span:
                continue
            utt_ids = getattr(span, "utt_ids", None)
            if not utt_ids:
                continue
            if not set(utt_ids).intersection(exclude_utt_ids):
                candidate_spans.append(span)
    if not candidate_spans:
        utterances = db.get_utterances(meeting_id)
        candidate_spans = [
            type("BGSpan", (), {"utt_ids": [u.utt_id], "text": u.text})()
            for u in utterances
            if u.utt_id not in exclude_utt_ids
        ]
    if not candidate_spans:
        return 0.0
    seed = int(hashlib.sha256(claim_text.encode("utf-8")).hexdigest()[:8], 16) & 0xFFFFFFFF
    rng = random.Random(seed)
    sample = rng.sample(candidate_spans, k=min(BACKGROUND_N, len(candidate_spans)))
    pairs = []
    for span in sample:
        premise = expand_premise(meeting_id, list(getattr(span, "utt_ids", [])), PREMISE_CONTEXT)
        if not premise:
            premise = getattr(span, "text", "")
        pairs.append((premise, claim_text))
    raw = scorer.score_pairs_cached(meeting_id, pairs)
    scores = [float(item.get("entailment", 0.0)) for item in raw]
    return sum(scores) / len(scores) if scores else 0.0


def build_hypotheses(claim: dict) -> list[Hypothesis]:
    hyps = [Hypothesis("core", str(claim.get("text", "")).strip(), 1.0)]
    claim_type = claim.get("claim_type")
    if claim_type == "action":
        owner = claim.get("owner")
        if owner and claim.get("owner_type") != "unassigned":
            hyps.append(
                Hypothesis(
                    "owner",
                    f"{owner} is responsible for: {claim['text']}",
                    0.6,
                )
            )
        deadline_text = claim.get("deadline_text")
        if deadline_text:
            hyps.append(
                Hypothesis(
                    "deadline",
                    f"The following is due by {deadline_text}: {claim['text']}",
                    0.5,
                )
            )
    else:
        state = claim.get("state")
        if state == "accepted":
            hyps.append(
                Hypothesis(
                    "state",
                    f"The group agreed to: {claim['text']}",
                    0.4,
                )
            )
        elif state == "rejected":
            hyps.append(
                Hypothesis(
                    "state",
                    f"The group rejected: {claim['text']}",
                    0.4,
                )
            )
    return hyps


class NLIScorer:
    NLI_MODEL = NLI_MODEL
    MAX_PREMISE_CHARS = MAX_PREMISE_CHARS

    def __init__(self, model_name: str = NLI_MODEL, batch_size: int = 8):
        self.model_name = model_name
        self.batch_size = batch_size
        self._pipe = None
        self._label_map: dict[int, str] | None = None
        self._pair_cache: dict[str, dict[str, float]] = {}
        self.cache_hits = 0
        self.cache_misses = 0

    def _cache_path(self, meeting_id: str) -> Path:
        storage = MeetingStorage(meeting_id)
        storage.ensure_dirs()
        cache_dir = storage.path_for("verification", "nli_cache")
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "pairs.json"

    def _load_cache(self, meeting_id: str) -> dict[str, dict[str, float]]:
        path = self._cache_path(meeting_id)
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return {str(k): dict(v) for k, v in payload.items() if isinstance(v, dict)}
        except (OSError, ValueError, TypeError):
            logger.warning("Failed to read NLI cache for %s", meeting_id)
        return {}

    def _save_cache(self, meeting_id: str, cache: dict[str, dict[str, float]]) -> None:
        path = self._cache_path(meeting_id)
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _pair_cache_key(premise: str, hypothesis: str, model_name: str) -> str:
        raw = f"{premise}\0{hypothesis}\0{model_name}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _pipe_or_load(self):
        if self._pipe is None:
            from app.core.model_registry import get_hf_pipeline

            self._pipe = get_hf_pipeline(
                "text-classification",
                self.model_name,
                top_k=None,
                truncation=True,
            )
            try:
                id2label = self._pipe.model.config.id2label
            except Exception:
                id2label = {0: "entailment", 1: "neutral", 2: "contradiction"}
            self._label_map = {
                int(k): str(v).lower() for k, v in sorted(id2label.items(), key=lambda item: int(item[0]))
            }
        return self._pipe

    @staticmethod
    def _normalize_label(label: str) -> str:
        return str(label).strip().lower()

    def score_pairs(self, pairs: list[tuple[str, str]]) -> list[dict]:
        if not pairs:
            return []
        pipe = self._pipe_or_load()
        inputs = [
            {"text": premise[:MAX_PREMISE_CHARS], "text_pair": hypothesis}
            for premise, hypothesis in pairs
        ]
        # Batch similar lengths together to avoid padding every pair to the
        # longest premise; results are restored to the caller's order.
        order = sorted(range(len(inputs)), key=lambda i: len(inputs[i]["text"]) + len(inputs[i]["text_pair"]))
        raw_sorted = pipe([inputs[i] for i in order], batch_size=self.batch_size)
        raw = [None] * len(inputs)
        for position, index in enumerate(order):
            raw[index] = raw_sorted[position]
        results: list[dict[str, float]] = []
        for scores in raw:
            out = {"entailment": 0.0, "neutral": 0.0, "contradiction": 0.0}
            if isinstance(scores, dict):
                scores = [scores]
            for item in scores:
                if not isinstance(item, dict):
                    continue
                label_value = self._normalize_label(item.get("label", ""))
                score_value = float(item.get("score", 0.0))
                mapped_key = None
                if label_value in {"entailment", "neutral", "contradiction"}:
                    mapped_key = label_value
                elif label_value.startswith("label_"):
                    try:
                        label_index = int(label_value.split("_")[-1])
                    except ValueError:
                        label_index = None
                    if label_index is not None and self._label_map is not None:
                        mapped_key = self._label_map.get(label_index)
                elif self._label_map is not None:
                    for label in self._label_map.values():
                        if label_value == label or label_value.startswith(label[:5]) or label.startswith(label_value[:5]):
                            mapped_key = label
                            break
                if mapped_key is not None and mapped_key in out:
                    out[mapped_key] = score_value
            results.append(out)
        return results

    def score_pairs_cached(self, meeting_id: str, pairs: list[tuple[str, str]]) -> list[dict]:
        """Score pairs through the per-meeting pair cache, in one batch."""
        cache = self._load_cache(meeting_id)
        keys = [self._pair_cache_key(p, h, self.model_name) for p, h in pairs]
        missing = [(key, pair) for key, pair in zip(keys, pairs) if key not in cache]
        if missing:
            self.cache_misses += len(missing)
            for (key, _), scores in zip(missing, self.score_pairs([pair for _, pair in missing])):
                cache[key] = {
                    "entailment": float(scores.get("entailment", 0.0)),
                    "neutral": float(scores.get("neutral", 0.0)),
                    "contradiction": float(scores.get("contradiction", 0.0)),
                }
            self._save_cache(meeting_id, cache)
        self.cache_hits += len(pairs) - len(missing)
        return [cache[key] for key in keys]

    def score_claim(self, cc: ClaimCandidates, claim_payload: dict) -> ClaimNLIResult:
        hyps = build_hypotheses(claim_payload)
        pair_specs: list[dict[str, Any]] = []
        all_candidate_spans = list(getattr(cc, "candidates", []))
        for rank, candidate in enumerate(all_candidate_spans):
            # The core hypothesis is checked against every candidate; secondary
            # hypotheses (state/owner/deadline) only against the strongest few,
            # which is all fusion needs and roughly halves NLI cost per claim.
            candidate_hyps = hyps if rank < SECONDARY_HYPOTHESIS_TOP_N else hyps[:1]
            for hyp in candidate_hyps:
                premise_text = getattr(candidate.span, "text", "")
                expanded = expand_premise(cc.meeting_id, list(candidate.span.utt_ids), PREMISE_CONTEXT)
                candidate_premise = expanded or premise_text
                pair_specs.append(
                    {
                        "span_id": candidate.span.span_id,
                        "utt_ids": list(candidate.span.utt_ids),
                        "hypothesis_kind": hyp.kind,
                        "provenance": candidate.provenance.value,
                        "premise": candidate_premise,
                        "hypothesis": hyp.text,
                        "expanded": candidate_premise != premise_text,
                        "premise_utt_ids": expand_utt_ids(cc.meeting_id, list(candidate.span.utt_ids), PREMISE_CONTEXT)
                        if candidate_premise != premise_text else list(candidate.span.utt_ids),
                    }
                )
        if cc.cited_span is not None:
            # The cited utterances alone, without neighbouring context: detects
            # citations that are only "supported" thanks to an adjacent utterance.
            core = next(h for h in hyps if h.kind == "core")
            pair_specs.append(
                {
                    "span_id": cc.cited_span.span_id,
                    "utt_ids": list(cc.cited_span.utt_ids),
                    "hypothesis_kind": core.kind,
                    "provenance": "cited_strict",
                    "premise": cc.cited_span.text,
                    "hypothesis": core.text,
                    "expanded": False,
                    "premise_utt_ids": list(cc.cited_span.utt_ids),
                }
            )
            for hyp in hyps:
                cited_text = cc.cited_span.text
                expanded = expand_premise(cc.meeting_id, list(cc.cited_span.utt_ids), PREMISE_CONTEXT)
                cited_premise = expanded or cited_text
                pair_specs.append(
                    {
                        "span_id": cc.cited_span.span_id,
                        "utt_ids": list(cc.cited_span.utt_ids),
                        "hypothesis_kind": hyp.kind,
                        "provenance": "cited_span",
                        "premise": cited_premise,
                        "hypothesis": hyp.text,
                        "expanded": cited_premise != cited_text,
                        "premise_utt_ids": expand_utt_ids(cc.meeting_id, list(cc.cited_span.utt_ids), PREMISE_CONTEXT)
                        if cited_premise != cited_text else list(cc.cited_span.utt_ids),
                    }
                )

        cache = self._load_cache(cc.meeting_id)
        self._pair_cache = cache
        pending: list[tuple[str, str]] = []
        pending_specs: list[dict[str, Any]] = []
        for spec in pair_specs:
            key = self._pair_cache_key(spec["premise"], spec["hypothesis"], self.model_name)
            if key in cache:
                self.cache_hits += 1
                continue
            pending.append((spec["premise"], spec["hypothesis"]))
            pending_specs.append(spec)
        if pending:
            self.cache_misses += len(pending)
            raw_scores = self.score_pairs(pending)
            for spec, scores in zip(pending_specs, raw_scores):
                key = self._pair_cache_key(spec["premise"], spec["hypothesis"], self.model_name)
                cache[key] = {
                    "entailment": float(scores.get("entailment", 0.0)),
                    "neutral": float(scores.get("neutral", 0.0)),
                    "contradiction": float(scores.get("contradiction", 0.0)),
                }
            self._save_cache(cc.meeting_id, cache)

        scored: list[PairScore] = []
        for spec in pair_specs:
            key = self._pair_cache_key(spec["premise"], spec["hypothesis"], self.model_name)
            cached = cache.get(key, {})
            score = {
                "entailment": float(cached.get("entailment", 0.0)),
                "neutral": float(cached.get("neutral", 0.0)),
                "contradiction": float(cached.get("contradiction", 0.0)),
            }
            scored.append(
                PairScore(
                    span_id=spec["span_id"],
                    utt_ids=list(spec["utt_ids"]),
                    hypothesis_kind=spec["hypothesis_kind"],
                    entail=score["entailment"],
                    neutral=score["neutral"],
                    contradict=score["contradiction"],
                    provenance=spec["provenance"],
                    expanded=bool(spec["expanded"]),
                    premise_utt_ids=list(spec["premise_utt_ids"]),
                    premise=spec["premise"],
                )
            )

        claim_text = str(claim_payload.get("text", ""))

        def can_contradict(score: PairScore) -> bool:
            # Cited evidence (and spans overlapping it) always counts; a premise
            # found only by retrieval must be about the claim's subject.
            if score.provenance != "retrieved_only":
                return True
            return content_coverage(claim_text, score.premise) >= CONTRADICTION_TOPIC_MIN

        core_scores = [s for s in scored if s.hypothesis_kind == "core" and s.provenance != "cited_strict"]
        e_union = max((s.entail for s in core_scores), default=0.0)
        x_union = max(
            (s.contradict for s in scored if s.provenance != "cited_strict" and can_contradict(s)),
            default=0.0,
        )
        off_topic = max(
            (s for s in core_scores if not can_contradict(s)), key=lambda item: item.contradict, default=None
        )
        cited_scores = [s for s in scored if s.hypothesis_kind == "core" and s.provenance == "cited_span"]
        e_cited = max((s.entail for s in cited_scores), default=0.0)
        strict_scores = [s for s in scored if s.provenance == "cited_strict"]
        e_cited_strict = max((s.entail for s in strict_scores), default=None)
        # "Best alternative" and contradictions come from cited/retrieved premises
        # with context, not from the strict probe.
        scored_for_union = [s for s in scored if s.provenance != "cited_strict"]
        x_cited = max((s.contradict for s in cited_scores), default=0.0)

        exclude_utt_ids = set()
        for s in scored:
            exclude_utt_ids.update(s.utt_ids)
        background = background_entailment(
            self,
            cc.meeting_id,
            claim_payload.get("text", ""),
            exclude_utt_ids,
            [candidate.span for candidate in all_candidate_spans] + ([cc.cited_span] if cc.cited_span is not None else []),
        )
        e_background = float(background)
        e_cited_margin = e_cited - e_background
        e_union_margin = e_union - e_background

        content_value = 0.0
        expanded_cited_premise = ""
        if cc.cited_span is not None:
            expanded_cited_premise = expand_premise(cc.meeting_id, list(cc.cited_span.utt_ids), PREMISE_CONTEXT) or cc.cited_span.text
            content_value = content_coverage(str(claim_payload.get("text", "")), expanded_cited_premise)
        content_ok = content_value >= CONTENT_COVERAGE_MIN if cc.cited_span is not None else False

        per_hypothesis: dict[str, float] = {}
        for kind in {item.hypothesis_kind for item in scored_for_union}:
            per_hypothesis[kind] = max(
                (s.entail for s in scored_for_union if s.hypothesis_kind == kind),
                default=0.0,
            )

        strongest_contradiction = max(
            (s for s in core_scores if can_contradict(s)), key=lambda item: item.contradict, default=None
        )

        best_span = max(core_scores, key=lambda item: item.entail, default=None)
        best_span_id = best_span.span_id if best_span is not None else None
        best_span_utt_ids = list(best_span.utt_ids) if best_span is not None else []

        if cc.cited_span is None:
            e_cited = 0.0
            x_cited = 0.0
            evidence_mismatch = True
            proposed_utt_ids: list[str] = []
        else:
            evidence_mismatch = (
                (e_union_margin - e_cited_margin) > MISMATCH_DELTA
                or e_cited_margin < MIN_SUPPORT_MARGIN
                or not content_ok
            )
            proposed_utt_ids = list(best_span_utt_ids) if evidence_mismatch and best_span is not None else []

        return ClaimNLIResult(
            claim_id=cc.claim_id,
            meeting_id=cc.meeting_id,
            e_union=e_union,
            x_union=x_union,
            best_span_id=best_span_id,
            best_span_utt_ids=best_span_utt_ids,
            e_cited=e_cited,
            x_cited=x_cited,
            e_background=e_background,
            e_cited_margin=e_cited_margin,
            e_union_margin=e_union_margin,
            content_coverage=content_value,
            per_hypothesis=per_hypothesis,
            n_pairs=len(scored),
            evidence_mismatch=evidence_mismatch,
            proposed_utt_ids=proposed_utt_ids,
            x_union_utt_ids=list(strongest_contradiction.premise_utt_ids) if strongest_contradiction else [],
            x_union_provenance=strongest_contradiction.provenance if strongest_contradiction else None,
            x_core=strongest_contradiction.contradict if strongest_contradiction else 0.0,
            e_cited_strict=e_cited_strict,
            x_off_topic=off_topic.contradict if off_topic else 0.0,
            x_off_topic_utt_ids=list(off_topic.premise_utt_ids) if off_topic else [],
        )


def score_meeting(meeting_id: str, k: int = 8) -> list[ClaimNLIResult]:
    started = time.perf_counter()
    candidate_map = {cc.claim_id: cc for cc in build_candidates(meeting_id, k=k)}
    scorer = NLIScorer()
    results: list[ClaimNLIResult] = []
    total_pairs = 0
    for claim in db.get_claims_with_evidence(meeting_id):
        payload = dict(claim.get("payload") or {})
        payload.update({
            "text": claim["text"],
            "claim_type": claim["claim_type"],
            "claim_id": claim["claim_id"],
        })
        cc = candidate_map.get(claim["claim_id"])
        if cc is None:
            continue
        result = scorer.score_claim(cc, payload)
        total_pairs += result.n_pairs
        results.append(result)
    logger.info(
        "Scored %d claims for %s: total_pairs=%d cache_hits=%d cache_misses=%d seconds=%.3f",
        len(results),
        meeting_id,
        total_pairs,
        scorer.cache_hits,
        scorer.cache_misses,
        time.perf_counter() - started,
    )
    return results
