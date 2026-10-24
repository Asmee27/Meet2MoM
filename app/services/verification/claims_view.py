"""
Read model for claims and their persisted verification (Phase 1).

The API (claims/verify endpoints), the Review UI, the verification snapshot and
the MoM export all build on build_claims_view(), so they always show the same
authoritative claims and statuses.
"""
from __future__ import annotations

from typing import Any, Optional

from app.core import db
from app.services.verification.fusion import EXPORTABLE_LABELS, FINAL_LABELS, REVIEW

UNVERIFIED = "unverified"

# Phase 8: a verification result older than a transcript correction was
# computed against words the reviewer has since changed. It is held for review
# (never shown as verified) until verification runs again.
STALE_REASON_CODE = "transcript_corrected_after_verification"


def _stale_reason(corrected_ids: list[str]) -> dict[str, Any]:
    cited = f" Corrected cited utterances: {', '.join(corrected_ids)}." if corrected_ids else ""
    return {
        "code": STALE_REASON_CODE,
        "severity": "review",
        "message": "The transcript was corrected after this claim was verified. "
                   "Run verification again before approving." + cited,
        "utt_ids": corrected_ids,
    }


def _utterance(u: Any) -> dict[str, Any]:
    return {
        "utt_id": u.utt_id,
        "speaker": u.speaker_name or u.speaker_id,
        "speaker_id": u.speaker_id,
        "start": u.start,
        "end": u.end,
        "text": u.text,
        "asr_conf": u.asr_conf,
        "spk_conf": u.spk_conf,
        "overlap": bool(u.overlap),
    }


def _utterances(ids: list[str], by_id: dict[str, Any]) -> list[dict[str, Any]]:
    return [_utterance(by_id[i]) for i in ids if i in by_id]


def _method(row: Optional[dict[str, Any]], by_id: dict[str, Any]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return {
        "label": row["label"],
        "cited_entailment": row.get("cited_entailment"),
        "best_support": row.get("best_support"),
        "best_utt_ids": row.get("best_utt_ids") or [],
        "best_evidence": _utterances(row.get("best_utt_ids") or [], by_id),
        "contradiction": row.get("contradiction"),
        "contradiction_utt_ids": row.get("contradiction_utt_ids") or [],
        "reasons": row.get("reasons") or [],
        "details": row.get("details") or {},
        "model_version": row.get("model_version"),
        "config_version": row.get("config_version"),
        "latency_ms": row.get("latency_ms"),
        "created_at": row.get("created_at"),
    }


def build_claims_view(meeting_id: str) -> dict[str, Any]:
    db.init_db()
    utterances = db.get_utterances(meeting_id)
    by_id = {u.utt_id: u for u in utterances}
    results = db.get_verification_results(meeting_id)
    corrections = db.get_evidence_corrections(meeting_id)
    transcript_corrected_at = db.latest_transcript_correction_at(meeting_id)
    corrected_utts = db.get_active_transcript_corrections(meeting_id)

    claims_out: list[dict[str, Any]] = []
    counts = {label: 0 for label in (*FINAL_LABELS, UNVERIFIED)}
    stale_claims = 0
    for claim in db.get_claims_with_evidence(meeting_id):
        payload = claim.get("payload") or {}
        methods = results.get(claim["claim_id"], {})
        fusion = methods.get("fusion")
        semantic = methods.get("semantic_nli")
        stale = bool(
            fusion and transcript_corrected_at
            and (fusion.get("created_at") or "") < transcript_corrected_at
        )
        final_label = REVIEW if stale else (fusion["label"] if fusion else UNVERIFIED)
        final_reasons = (fusion or {}).get("reasons") or [
            {"code": "not_verified", "severity": "review",
             "message": "This claim has not been verified yet.", "utt_ids": []}
        ]
        if stale:
            stale_claims += 1
            cited_fixed = [u for u in claim["evidence"] if u in corrected_utts]
            final_reasons = [_stale_reason(cited_fixed), *final_reasons]
        counts[final_label] += 1
        correction = corrections.get(claim["claim_id"])

        suggestion = None
        proposed = (semantic or {}).get("details", {}).get("proposed_utt_ids") or []
        if proposed and not correction:
            suggestion = {
                "utt_ids": proposed,
                "evidence": _utterances(proposed, by_id),
                "support": (semantic or {}).get("best_support"),
                "cited_support": (semantic or {}).get("cited_entailment"),
            }

        contradiction = None
        x_ids = (fusion or {}).get("contradiction_utt_ids") or []
        if fusion and any(r.get("code", "").startswith(("contradicting", "cited_evidence_contra", "cited_evidence_partially"))
                          for r in fusion.get("reasons", [])):
            contradiction = {
                "utt_ids": x_ids,
                "evidence": _utterances(x_ids, by_id),
                "score": fusion.get("contradiction"),
            }

        cited = _utterances(claim["evidence"], by_id)
        claims_out.append({
            "claim_id": claim["claim_id"],
            "claim_type": claim["claim_type"],
            "text": claim["text"],
            "state": payload.get("state"),
            "conditional": payload.get("conditional"),
            "proposer": payload.get("proposer"),
            "owner": payload.get("owner"),
            "owner_type": payload.get("owner_type"),
            "deadline_text": payload.get("deadline_text"),
            "deadline_resolved": payload.get("deadline_resolved"),
            "deadline_type": payload.get("deadline_type"),
            "commitment_type": payload.get("commitment_type"),
            "evidence_ids": list(claim["evidence"]),
            "evidence": cited,
            "speaker": cited[0]["speaker"] if cited else None,
            "timestamp": cited[0]["start"] if cited else None,
            "verification": {
                "deterministic": _method(methods.get("deterministic"), by_id),
                "semantic": _method(semantic, by_id),
                "final": {
                    "label": final_label,
                    "reasons": final_reasons,
                    "config_version": (fusion or {}).get("config_version"),
                },
            },
            "exportable": final_label in EXPORTABLE_LABELS and (
                claim["claim_type"] != "decision" or payload.get("state") == "accepted"
            ),
            "verification_stale": stale,
            "suggestion": suggestion,
            "contradiction": contradiction,
            "correction": correction,
        })

    return {
        "meeting_id": meeting_id,
        "extraction": db.get_extraction_run(meeting_id),
        "verification_run": db.get_verification_run(meeting_id),
        "counts": counts,
        "claims": claims_out,
        "transcript": {
            "corrected_utterances": sorted(corrected_utts),
            "last_corrected_at": transcript_corrected_at,
            "stale_claims": stale_claims,
        },
    }
