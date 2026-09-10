from __future__ import annotations

"""
span_validator.py
=================
Deterministic span and invariant validator for candidate claims.
Rejects claims that fail evidence structure, schema, transcript existence,
extraction window locality, or duplicate constraints.
Never repairs, never re-cites, never falls back to nearest utterance.
"""

from dataclasses import dataclass
from enum import Enum
import logging
import string
from typing import List, Tuple, Union

from pydantic import ValidationError

from app.schemas.claim import ActionItem, Decision, UTT_ID_PATTERN
from app.services.intelligence.context_builder import ExtractionWindow, window_utt_id_set
from app.services.transcript.transcript_index import resolve_spans

logger = logging.getLogger(__name__)


class DropReason(str, Enum):
    UNRESOLVABLE_UTT_ID = "unresolvable_utt_id"
    OUT_OF_WINDOW       = "out_of_window"
    EMPTY_EVIDENCE      = "empty_evidence"
    DUPLICATE_CLAIM     = "duplicate_claim"
    SCHEMA_INVALID      = "schema_invalid"


@dataclass
class DroppedClaim:
    meeting_id: str
    window_id: str
    claim_type: str
    text: str
    cited_evidence: list[str]
    reason: DropReason
    detail: str


def _normalise_text(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    t = text.lower()
    t = t.translate(str.maketrans("", "", string.punctuation))
    return " ".join(t.split())


def validate_candidates(
    meeting_id: str,
    candidates: list[dict],
    windows: list[ExtractionWindow],
) -> tuple[list[Decision | ActionItem], list[DroppedClaim]]:
    """
    Validate extraction candidates deterministically against extraction windows
    and database-backed utterances.

    RULES, applied in this order (first failure wins):
    1. EMPTY_EVIDENCE: evidence missing, not a list, or empty after dedup.
    2. SCHEMA_INVALID: Pydantic construction raises, or any evidence id fails ^u\\d{5}$.
    3. UNRESOLVABLE_UTT_ID: resolve_spans returns non-empty missing list.
    4. OUT_OF_WINDOW: any cited id is not in window_utt_id_set for the producing window.
    5. DUPLICATE_CLAIM: normalised (claim_type, text) already accepted with overlapping evidence.
    """
    windows_by_id = {w.window_id: w for w in windows}
    
    accepted_claims: list[tuple[Decision | ActionItem, float]] = []
    accepted_index: list[tuple[str, str, set[str]]] = []  # (claim_type, norm_text, ev_set)
    dropped_claims: list[DroppedClaim] = []

    for cand in candidates:
        if not isinstance(cand, dict):
            # Treat non-dict candidate as invalid schema
            dropped = DroppedClaim(
                meeting_id=meeting_id,
                window_id="",
                claim_type="unknown",
                text="",
                cited_evidence=[],
                reason=DropReason.SCHEMA_INVALID,
                detail="Candidate is not a dictionary.",
            )
            dropped_claims.append(dropped)
            logger.info("Dropped claim [%s] reason=%s: %s", dropped.claim_type, dropped.reason.value, dropped.text[:80])
            continue

        cand_meeting_id = str(cand.get("meeting_id") or meeting_id)
        cand_window_id = str(cand.get("window_id") or "")
        cand_claim_type = str(cand.get("claim_type") or "")
        cand_text = str(cand.get("text") or "")
        raw_evidence = cand.get("evidence")

        # Helper for dropping candidate
        def _drop(reason: DropReason, detail: str, evidence_list: list[str]) -> None:
            dropped = DroppedClaim(
                meeting_id=cand_meeting_id,
                window_id=cand_window_id,
                claim_type=cand_claim_type,
                text=cand_text,
                cited_evidence=evidence_list,
                reason=reason,
                detail=detail,
            )
            dropped_claims.append(dropped)
            logger.info("Dropped claim [%s] reason=%s: %s", dropped.claim_type, dropped.reason.value, dropped.text[:80])

        # RULE 1: EMPTY_EVIDENCE
        if raw_evidence is None or not isinstance(raw_evidence, list):
            ev_preview = [str(x) for x in raw_evidence] if isinstance(raw_evidence, list) else []
            _drop(DropReason.EMPTY_EVIDENCE, "Evidence missing or not a list.", ev_preview)
            continue

        clean_evidence = list(dict.fromkeys(raw_evidence))
        if not clean_evidence:
            _drop(DropReason.EMPTY_EVIDENCE, "Evidence list is empty after deduplication.", [])
            continue

        # RULE 2: SCHEMA_INVALID
        # Check evidence regex format
        invalid_evidence_id = False
        for uid in clean_evidence:
            if not isinstance(uid, str) or not UTT_ID_PATTERN.match(uid):
                _drop(
                    DropReason.SCHEMA_INVALID,
                    f"Evidence ID '{uid}' does not match pattern ^u\\d{{5}}$",
                    [str(x) for x in clean_evidence],
                )
                invalid_evidence_id = True
                break
        if invalid_evidence_id:
            continue

        if cand_claim_type not in ("decision", "action"):
            _drop(
                DropReason.SCHEMA_INVALID,
                f"Invalid or missing claim_type: '{cand_claim_type}'",
                [str(x) for x in clean_evidence],
            )
            continue

        cand_payload = dict(cand)
        cand_payload["meeting_id"] = cand_meeting_id
        if not cand_payload.get("claim_id"):
            cand_payload["claim_id"] = "pending"
        cand_payload["evidence"] = clean_evidence

        try:
            if cand_claim_type == "decision":
                model_obj = Decision(**cand_payload)
            else:
                model_obj = ActionItem(**cand_payload)
        except ValidationError as e:
            _drop(DropReason.SCHEMA_INVALID, str(e), [str(x) for x in clean_evidence])
            continue
        except Exception as e:
            _drop(DropReason.SCHEMA_INVALID, str(e), [str(x) for x in clean_evidence])
            continue

        # RULE 3: UNRESOLVABLE_UTT_ID
        found_utts, missing_ids = resolve_spans(meeting_id, clean_evidence)
        if missing_ids:
            _drop(
                DropReason.UNRESOLVABLE_UTT_ID,
                f"Missing utterance IDs in transcript index: {missing_ids}",
                [str(x) for x in clean_evidence],
            )
            continue

        # RULE 4: OUT_OF_WINDOW
        window = windows_by_id.get(cand_window_id)
        if not window:
            _drop(
                DropReason.OUT_OF_WINDOW,
                f"Window '{cand_window_id}' not found in provided extraction windows.",
                [str(x) for x in clean_evidence],
            )
            continue

        allowed_window_ids = window_utt_id_set(window)
        out_of_window = [uid for uid in clean_evidence if uid not in allowed_window_ids]
        if out_of_window:
            _drop(
                DropReason.OUT_OF_WINDOW,
                f"Cited utterance IDs not in window {cand_window_id}: {out_of_window}",
                [str(x) for x in clean_evidence],
            )
            continue

        # RULE 5: DUPLICATE_CLAIM
        norm_text = _normalise_text(model_obj.text)
        cand_ev_set = set(clean_evidence)
        is_dup = False
        dup_detail = ""

        for accepted_type, accepted_norm, accepted_ev_set in accepted_index:
            if accepted_type == cand_claim_type and accepted_norm == norm_text:
                overlap = cand_ev_set & accepted_ev_set
                if overlap:
                    is_dup = True
                    dup_detail = f"Duplicate of previously accepted claim with overlapping evidence: {sorted(overlap)}"
                    break

        if is_dup:
            _drop(DropReason.DUPLICATE_CLAIM, dup_detail, [str(x) for x in clean_evidence])
            continue

        # Accepted! Deduplicate and sort evidence by start time
        utt_map = {u.utt_id: u for u in found_utts}
        sorted_evidence = sorted(clean_evidence, key=lambda uid: (utt_map[uid].start, uid))
        model_obj = model_obj.model_copy(update={"evidence": sorted_evidence})

        earliest_start = min(utt_map[uid].start for uid in sorted_evidence)
        accepted_claims.append((model_obj, earliest_start))
        accepted_index.append((cand_claim_type, norm_text, set(sorted_evidence)))

    # Sort accepted claims by (earliest evidence start_time, claim_type)
    accepted_claims.sort(key=lambda item: (item[1], item[0].claim_type))

    final_accepted: list[Decision | ActionItem] = []
    dec_counter = 0
    act_counter = 0

    for model_obj, _ in accepted_claims:
        if model_obj.claim_type == "decision":
            assigned_id = f"{meeting_id}_d{dec_counter:05d}"
            dec_counter += 1
        else:
            assigned_id = f"{meeting_id}_a{act_counter:05d}"
            act_counter += 1

        final_accepted.append(model_obj.model_copy(update={"claim_id": assigned_id}))

    return final_accepted, dropped_claims
