"""
Claim verification orchestrator (Phase 1).

For every claim in the authoritative claims table:
  1. deterministic check  - the existing EvidenceVerifier (text/timestamp match)
  2. semantic check       - independent retrieval (embeddings + BM25) and
                            mDeBERTa NLI on cited and retrieved spans
  3. fusion               - transparent rules -> verified / verified_with_warning
                            / review / rejected, with reasons

One row per claim and method is persisted in verification_results (re-runs
replace the meeting's rows, so retries never duplicate). A verification_runs
row records the overall outcome and timings so "verifier failed/unavailable"
is distinguishable from "claim unsupported".

Works on normalized transcript utterances only, so it is independent of how
the transcript was produced (recording, imported file, platform transcript).
"""
from __future__ import annotations

import io
import json
import logging
import time
from contextlib import redirect_stdout
from typing import Any, Optional

from app.core import db
from app.core.storage import MeetingStorage
from app.services.verification.fusion import FUSION_CONFIG, fuse

logger = logging.getLogger(__name__)

DETERMINISTIC_METHOD = "deterministic"
SEMANTIC_METHOD = "semantic_nli"
FUSION_METHOD = "fusion"
DETERMINISTIC_VERSION = "evidence-verifier-v1"


def model_version() -> str:
    from app.services.verification.nli_scorer import NLI_MODEL
    from app.services.verification.retrieval_index import EMBED_MODEL_NAME

    return f"nli={NLI_MODEL};embed={EMBED_MODEL_NAME}"


# ---------------------------------------------------------------------------
# Deterministic check (existing EvidenceVerifier, adapted to claim records)
# ---------------------------------------------------------------------------

def _transcript_segments(utterances: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "text": u.text,
            "start": u.start,
            "end": u.end,
            "speaker": u.speaker_name or u.speaker_id,
            "speaker_attribution_confidence": u.spk_conf,
            "possible_overlap": bool(u.overlap),
        }
        for u in utterances
    ]


def deterministic_check(verifier: Any, claim: dict[str, Any], segments: list[dict], utterances: list[Any], cited_utts: list[Any]) -> dict[str, Any]:
    timestamp = cited_utts[0].start if cited_utts else None
    with redirect_stdout(io.StringIO()):
        if claim["claim_type"] == "decision":
            result = verifier.verify_decision({"text": claim["text"], "timestamp": timestamp}, segments)
        else:
            result = verifier.verify_action_item({"task": claim["text"], "timestamp": timestamp}, segments)
    evidence = (result.get("evidence") or [None])[0]
    matched_utt_id = None
    if evidence and evidence.get("segment_index") is not None:
        index = int(evidence["segment_index"])
        if 0 <= index < len(utterances):
            matched_utt_id = utterances[index].utt_id
    return {
        "label": result.get("verification_status", "rejected"),
        "confidence": result.get("evidence_confidence"),
        "matched_utt_id": matched_utt_id,
        "matches_cited": matched_utt_id in set(claim["evidence"]) if matched_utt_id else False,
        "reasons": list(result.get("verification_review_reasons") or []),
    }


# ---------------------------------------------------------------------------
# Semantic check (existing retrieval + NLI scorer)
# ---------------------------------------------------------------------------

def semantic_label(result: Any, cited_ids: list[str]) -> str:
    from app.services.verification.nli_scorer import MIN_SUPPORT_MARGIN

    cfg = FUSION_CONFIG
    if result.x_cited >= cfg["contradiction_reject"] and result.x_cited > result.e_cited:
        return "contradicted"
    if not result.evidence_mismatch:
        strict = getattr(result, "e_cited_strict", None)
        if strict is not None and strict < cfg["cited_strict_min"]:
            # Supported only once neighbouring utterances are added as context:
            # the citation itself does not contain the claim.
            return "supported_with_context"
        return "supported"
    proposed = list(result.proposed_utt_ids or [])
    if (
        proposed
        and not set(proposed) <= set(cited_ids)
        and result.e_union_margin >= MIN_SUPPORT_MARGIN
    ):
        return "supported_elsewhere"
    return "unsupported"


def _semantic_payload(result: Any, cited_ids: list[str], has_cited_span: bool) -> dict[str, Any]:
    label = semantic_label(result, cited_ids) if has_cited_span else "no_cited_evidence"
    proposed: list[str] = []
    if label == "supported_elsewhere":
        proposed = list(result.proposed_utt_ids)
    elif label == "supported_with_context" and result.best_span_utt_ids:
        proposed = list(result.best_span_utt_ids)
    strict = getattr(result, "e_cited_strict", None)
    return {
        "label": label,
        "e_cited": round(result.e_cited, 4),
        "e_cited_strict": round(strict, 4) if strict is not None else None,
        "x_cited": round(result.x_cited, 4),
        "e_cited_margin": round(result.e_cited_margin, 4),
        "e_background": round(result.e_background, 4),
        "best_support": round(result.e_union, 4),
        "best_utt_ids": list(result.best_span_utt_ids),
        "x_core": round(result.x_core, 4),
        "x_core_utt_ids": list(result.x_union_utt_ids),
        "x_core_provenance": result.x_union_provenance,
        "content_coverage": round(result.content_coverage, 4),
        "per_hypothesis": {k: round(v, 4) for k, v in result.per_hypothesis.items()},
        "proposed_utt_ids": proposed,
        "n_pairs": result.n_pairs,
    }


def _claim_payload(claim: dict[str, Any]) -> dict[str, Any]:
    payload = dict(claim.get("payload") or {})
    payload.update({"text": claim["text"], "claim_type": claim["claim_type"], "claim_id": claim["claim_id"]})
    return payload


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def verify_meeting(
    meeting_id: str,
    *,
    index: Any = None,
    scorer: Any = None,
    k: int = 8,
) -> dict[str, Any]:
    """Verify every claim of a meeting, persist the results, return a summary."""
    started = time.perf_counter()
    db.init_db()
    claims = db.get_claims_with_evidence(meeting_id)
    utterances = db.get_utterances(meeting_id)
    by_id = {u.utt_id: u for u in utterances}
    corrections = db.get_evidence_corrections(meeting_id)
    version = model_version()

    if not claims:
        db.replace_verification_results(meeting_id, [])
        db.upsert_verification_run(
            meeting_id, status="no_claims", reason="No claims to verify",
            claims_total=0, seconds_total=round(time.perf_counter() - started, 3),
            model_version=version, config_version=FUSION_CONFIG["version"],
        )
        summary = {"status": "no_claims", "claims_total": 0, "timings": {}}
        _write_snapshot(meeting_id, summary)
        return summary

    timings: dict[str, Any] = {"per_claim_ms": {}}

    # 1. Deterministic verifier (pure Python, always available).
    from app.services.verification.evidence_verifier import EvidenceVerifier

    storage = MeetingStorage(meeting_id)
    verifier = EvidenceVerifier(output_dir=str(storage.verification))
    segments = _transcript_segments(utterances)

    # 2. Semantic verifier: build the index and load the models once.
    semantic_error: Optional[str] = None
    candidates: dict[str, Any] = {}
    t0 = time.perf_counter()
    try:
        from app.services.verification.candidate_builder import build_candidates
        from app.services.verification.nli_scorer import NLIScorer
        from app.services.verification.retrieval_index import RetrievalIndex

        index = index or RetrievalIndex.load_or_build(meeting_id)
        timings["retrieval_index_seconds"] = round(time.perf_counter() - t0, 3)
        t1 = time.perf_counter()
        candidates = {cc.claim_id: cc for cc in build_candidates(meeting_id, k=k, index=index)}
        timings["candidate_retrieval_seconds"] = round(time.perf_counter() - t1, 3)
        scorer = scorer or NLIScorer()
        t2 = time.perf_counter()
        if hasattr(scorer, "_pipe_or_load"):
            scorer._pipe_or_load()
        timings["nli_model_load_seconds"] = round(time.perf_counter() - t2, 3)
    except Exception as exc:  # model missing, import failure, corrupt cache...
        semantic_error = f"{type(exc).__name__}: {exc}"
        logger.error("Semantic verification unavailable for %s: %s", meeting_id, semantic_error)

    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    semantic_seconds = 0.0
    for claim in claims:
        claim_started = time.perf_counter()
        cited_ids = list(claim["evidence"])
        cited_utts = [by_id[i] for i in cited_ids if i in by_id]

        try:
            deterministic = deterministic_check(verifier, claim, segments, utterances, cited_utts)
        except Exception as exc:
            logger.exception("Deterministic check failed for %s", claim["claim_id"])
            deterministic = {"label": "error", "reasons": [f"{type(exc).__name__}: {exc}"]}

        semantic_started = time.perf_counter()
        if semantic_error:
            semantic = {"label": "unavailable", "reason": semantic_error}
        else:
            cc = candidates.get(claim["claim_id"])
            try:
                if cc is None:
                    raise RuntimeError("no retrieval candidates were built for this claim")
                result = scorer.score_claim(cc, _claim_payload(claim))
                semantic = _semantic_payload(result, cited_ids, cc.cited_span is not None)
            except Exception as exc:
                logger.exception("Semantic check failed for %s", claim["claim_id"])
                semantic = {"label": "error", "reason": f"{type(exc).__name__}: {exc}"}
        semantic_ms = (time.perf_counter() - semantic_started) * 1000
        semantic_seconds += semantic_ms / 1000

        fused = fuse(claim, deterministic, semantic, cited_utts, corrections.get(claim["claim_id"]))
        counts[fused["label"]] = counts.get(fused["label"], 0) + 1
        total_ms = (time.perf_counter() - claim_started) * 1000
        timings["per_claim_ms"][claim["claim_id"]] = {
            "semantic": round(semantic_ms, 1), "total": round(total_ms, 1),
        }

        rows.append({
            "claim_id": claim["claim_id"], "method": DETERMINISTIC_METHOD,
            "label": deterministic["label"], "cited_utt_ids": cited_ids,
            "best_utt_ids": [deterministic["matched_utt_id"]] if deterministic.get("matched_utt_id") else [],
            "best_support": deterministic.get("confidence"),
            "reasons": deterministic.get("reasons", []),
            "details": {"matches_cited": deterministic.get("matches_cited")},
            "model_version": DETERMINISTIC_VERSION, "config_version": DETERMINISTIC_VERSION,
        })
        rows.append({
            "claim_id": claim["claim_id"], "method": SEMANTIC_METHOD,
            "label": semantic["label"], "cited_utt_ids": cited_ids,
            "cited_entailment": semantic.get("e_cited"),
            "best_utt_ids": semantic.get("best_utt_ids", []),
            "best_support": semantic.get("best_support"),
            "contradiction": semantic.get("x_core"),
            "contradiction_utt_ids": semantic.get("x_core_utt_ids", []),
            "reasons": [semantic["reason"]] if semantic.get("reason") else [],
            "details": semantic, "model_version": version,
            "config_version": FUSION_CONFIG["version"], "latency_ms": round(semantic_ms, 1),
        })
        rows.append({
            "claim_id": claim["claim_id"], "method": FUSION_METHOD,
            "label": fused["label"], "cited_utt_ids": cited_ids,
            "cited_entailment": semantic.get("e_cited"),
            "best_utt_ids": semantic.get("proposed_utt_ids", []),
            "best_support": semantic.get("best_support"),
            "contradiction": semantic.get("x_core"),
            "contradiction_utt_ids": semantic.get("x_core_utt_ids", []),
            "reasons": fused["reasons"],
            "details": {"deterministic_label": deterministic["label"], "semantic_label": semantic["label"]},
            "model_version": version, "config_version": fused["config_version"],
            "latency_ms": round(total_ms, 1),
        })

    db.replace_verification_results(meeting_id, rows)
    seconds_total = round(time.perf_counter() - started, 3)
    status = "semantic_unavailable" if semantic_error else "completed"
    db.upsert_verification_run(
        meeting_id, status=status, reason=semantic_error, claims_total=len(claims),
        seconds_total=seconds_total, semantic_seconds=round(semantic_seconds, 3),
        model_version=version, config_version=FUSION_CONFIG["version"],
    )
    per_claim = [v["semantic"] for v in timings["per_claim_ms"].values()]
    timings["semantic_ms_per_claim_mean"] = round(sum(per_claim) / len(per_claim), 1) if per_claim else None
    timings["seconds_total"] = seconds_total
    summary = {
        "status": status,
        "reason": semantic_error,
        "claims_total": len(claims),
        "final_counts": counts,
        "timings": timings,
    }
    logger.info("Verified %d claims for %s in %.2fs: %s", len(claims), meeting_id, seconds_total, counts)
    _write_snapshot(meeting_id, summary)
    return summary


def _write_snapshot(meeting_id: str, summary: dict[str, Any]) -> None:
    """Human-readable mirror of the persisted results (the DB stays authoritative)."""
    from app.services.verification.claims_view import build_claims_view

    try:
        storage = MeetingStorage(meeting_id)
        storage.ensure_dirs()
        view = build_claims_view(meeting_id)
        view["run_summary"] = summary
        storage.path_for("verification", "verified_intelligence.json").write_text(
            json.dumps(view, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("Failed to write verification snapshot for %s: %s", meeting_id, exc)
