from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum

from app.core import db
from app.services.transcript.transcript_index import resolve_spans
from app.services.verification.retrieval_index import (
    EvidenceSpan,
    RetrievalIndex,
)

logger = logging.getLogger(__name__)


class Provenance(str, Enum):
    CITED_ONLY = "cited_only"
    RETRIEVED_ONLY = "retrieved_only"
    BOTH = "both"


@dataclass
class EvidenceCandidate:
    span: EvidenceSpan
    provenance: Provenance
    retrieval_score: float
    rank: int | None


@dataclass
class ClaimCandidates:
    claim_id: str
    meeting_id: str
    claim_type: str
    claim_text: str
    query_text: str
    cited_utt_ids: list[str]
    candidates: list[EvidenceCandidate]
    cited_overlap_any: float
    cited_recall_strict: float
    cited_span: EvidenceSpan | None
    union_span_ids: list[str]


def build_query(claim: dict) -> str:
    parts = [claim.get("text", "")]
    if claim.get("claim_type") == "action":
        if claim.get("owner"):
            parts.append(str(claim["owner"]))
        if claim.get("deadline_text"):
            parts.append(str(claim["deadline_text"]))
    return " ".join(str(part) for part in parts if part).strip()


def build_candidates(
    meeting_id: str,
    k: int = 8,
    index: RetrievalIndex | None = None,
) -> list[ClaimCandidates]:
    index = index or RetrievalIndex.load_or_build(meeting_id)
    output: list[ClaimCandidates] = []
    for claim in db.get_claims_with_evidence(meeting_id):
        payload = dict(claim.get("payload") or {})
        payload.update({"text": claim["text"], "claim_type": claim["claim_type"]})
        query = build_query(payload)
        hits = index.retrieve(query, k=k)
        cited = list(dict.fromkeys(claim["evidence"]))
        cited_set = set(cited)
        covered_cited: set[str] = set()
        by_ids: dict[frozenset[str], EvidenceCandidate] = {}
        priority = {
            Provenance.CITED_ONLY: 0,
            Provenance.RETRIEVED_ONLY: 1,
            Provenance.BOTH: 2,
        }
        for rank, hit in enumerate(hits):
            overlap = cited_set.intersection(hit.span.utt_ids)
            provenance = Provenance.BOTH if overlap else Provenance.RETRIEVED_ONLY
            covered_cited.update(overlap)
            candidate = EvidenceCandidate(
                hit.span, provenance, hit.hybrid_score, rank
            )
            key = frozenset(hit.span.utt_ids)
            existing = by_ids.get(key)
            if existing is None or (
                priority[provenance], hit.hybrid_score
            ) > (priority[existing.provenance], existing.retrieval_score):
                by_ids[key] = candidate

        found_cited, unresolved_cited = resolve_spans(meeting_id, cited)
        if unresolved_cited:
            logger.error(
                "claim %s cites unresolvable %s",
                claim["claim_id"],
                unresolved_cited,
            )
        for utt in found_cited:
            if utt.utt_id not in covered_cited:
                span = EvidenceSpan(
                    span_id=f"cited_{utt.utt_id}",
                    meeting_id=meeting_id,
                    utt_ids=[utt.utt_id],
                    text=utt.text,
                    start=utt.start,
                    end=utt.end,
                    speakers=[utt.speaker_id],
                )
                by_ids.setdefault(
                    frozenset(span.utt_ids),
                    EvidenceCandidate(span, Provenance.CITED_ONLY, 0.0, None),
                )

        candidates = sorted(
            by_ids.values(),
            key=lambda item: (
                -priority[item.provenance],
                -item.retrieval_score,
            ),
        )
        cited_overlap_any = len(covered_cited) / len(cited) if cited else 0.0
        single_hit_utts = {
            utt_id
            for hit in hits
            if len(hit.span.utt_ids) == 1
            for utt_id in hit.span.utt_ids
        }
        cited_recall_strict = (
            len(set(cited).intersection(single_hit_utts)) / len(cited)
            if cited
            else 0.0
        )
        if cited_recall_strict == 0.0:
            logger.warning(
                "Claim %s has zero strict cited recall from independent retrieval",
                claim["claim_id"],
            )
        cited_span = None
        if not unresolved_cited and found_cited:
            ordered = sorted(found_cited, key=lambda utt: (utt.start, utt.end, utt.utt_id))
            cited_span = EvidenceSpan(
                span_id=f"citedspan_{claim['claim_id']}",
                meeting_id=meeting_id,
                utt_ids=[utt.utt_id for utt in ordered],
                text=" ".join(utt.text.strip() for utt in ordered if utt.text),
                start=ordered[0].start,
                end=ordered[-1].end,
                speakers=list(dict.fromkeys(utt.speaker_id for utt in ordered)),
            )
        output.append(
            ClaimCandidates(
                claim_id=claim["claim_id"],
                meeting_id=meeting_id,
                claim_type=claim["claim_type"],
                claim_text=claim["text"],
                query_text=query,
                cited_utt_ids=cited,
                candidates=candidates,
                cited_overlap_any=cited_overlap_any,
                cited_recall_strict=cited_recall_strict,
                cited_span=cited_span,
                union_span_ids=[candidate.span.span_id for candidate in candidates],
            )
        )
    return output
