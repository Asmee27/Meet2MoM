from __future__ import annotations

"""
extraction_pipeline.py
======================
Top-level extractor orchestrator.
Dispatches to either LLM-based extraction (llm_extractor) or the rule-based
baseline (rules_adapter), validates output through span_validator, and
atomically persists and mirrors claims via claim_writer.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from app.core import config
from app.core import db as _db
from app.core.llm_client import LLMClient, LLMClientError
from app.core.storage import MeetingStorage
from app.services.intelligence.claim_writer import persist_extraction
from app.services.intelligence.context_builder import build_windows
from app.services.intelligence.llm_extractor import PROMPT_VERSION, extract_meeting
from app.services.intelligence.rules_adapter import extract_with_rules
from app.services.intelligence.span_validator import validate_candidates

logger = logging.getLogger(__name__)


# Extraction outcome vocabulary persisted in extraction_runs.status:
#   completed             - an extractor ran and produced at least one claim
#   no_claims             - an extractor genuinely ran and found nothing
#   extractor_unavailable - the requested extractor cannot run and no fallback was allowed
#   failed                - extraction raised, or there was no transcript to extract from
EXTRACTION_STATUSES = ("completed", "no_claims", "extractor_unavailable", "failed")
RULES_EXTRACTOR_VERSION = "rules-v2"
EXTRACTOR_MODES = ("llm", "hybrid", "dialogue", "rules")
# Local extractor used when the LLM is unavailable or fails. "hybrid" (rules +
# learned dialogue model) was chosen in Phase 2 on the AMI dev split: higher
# recall/F1 than rules with no more false confirmations (docs/phase-2).
# Overridable with FALLBACK_EXTRACTOR=rules|dialogue|hybrid.
DEFAULT_FALLBACK_EXTRACTOR = "hybrid"


def fallback_mode() -> str:
    mode = (os.getenv("FALLBACK_EXTRACTOR") or DEFAULT_FALLBACK_EXTRACTOR).strip().lower()
    return mode if mode in ("hybrid", "dialogue", "rules") else "rules"


def _run_local(meeting_id: str, windows, mode: str):
    """
    Run a local (non-LLM) extractor. Returns (candidates, mode_used, version, note).
    A missing dialogue model is reported in `note` and falls back to rules.
    """
    from app.services.intelligence import dialogue_model

    if mode in ("dialogue", "hybrid") and not dialogue_model.model_available():
        note = f"dialogue model file not found ({dialogue_model.MODEL_PATH.name}); used rules"
        return extract_with_rules(meeting_id, windows), "rules", RULES_EXTRACTOR_VERSION, note
    if mode == "rules":
        return extract_with_rules(meeting_id, windows), "rules", RULES_EXTRACTOR_VERSION, None
    try:
        version = dialogue_model.load_model().version
        learned = dialogue_model.extract_with_dialogue_model(meeting_id, windows)
    except Exception as exc:  # embedding model missing/broken: degrade, never crash extraction
        logger.exception("Dialogue model failed for %s; using rules", meeting_id)
        note = f"dialogue model failed ({type(exc).__name__}: {exc}); used rules"
        return extract_with_rules(meeting_id, windows), "rules", RULES_EXTRACTOR_VERSION, note
    if mode == "dialogue":
        return learned, "dialogue", version, None
    rules = extract_with_rules(meeting_id, windows)
    return (dialogue_model.merge_candidates(rules, learned), "hybrid",
            f"{RULES_EXTRACTOR_VERSION}+{version}", None)
_PROVIDERS_NEEDING_KEY = {"openai", "anthropic", "gemini"}


def llm_unavailable_reason(client: Optional[LLMClient] = None) -> Optional[str]:
    """
    Explain why the configured LLM extractor cannot produce real output, or
    return None when it can. An injected client (tests, callers) is trusted.
    The "stub" provider is a test fixture: in production it always returns an
    empty result, so it must never count as a successful extraction.
    """
    if client is not None:
        if getattr(client, "provider", None) == "stub" and "PYTEST_CURRENT_TEST" not in os.environ and not os.environ.get("TESTING"):
            return "LLM_PROVIDER=stub cannot be used outside of tests"
        return None
    provider = (config.LLM_PROVIDER or "stub").strip().lower()
    if provider == "stub":
        return "LLM_PROVIDER=stub is a test fixture, not a real extractor"
    if provider in _PROVIDERS_NEEDING_KEY and not (config.LLM_API_KEY or config.GEMINI_API_KEY):
        return f"LLM_PROVIDER={provider} has no API key configured"
    return None


def run_extraction(
    meeting_id: str,
    mode: Optional[str] = None,
    client: Optional[LLMClient] = None,
    db_path: Optional[Path | str] = None,
    allow_fallback: bool = True,
) -> Dict[str, Any]:
    """
    Orchestrate candidate extraction, span validation, and persistence.

    The outcome is always recorded in extraction_runs so downstream stages can
    tell "no claims found" from "extractor unavailable" or "extraction failed".
    """
    requested_mode = (
        mode
        if mode is not None
        else os.getenv("EXTRACTOR")
        or getattr(config, "EXTRACTOR", "llm")
        or "llm"
    ).strip().lower()

    if requested_mode not in EXTRACTOR_MODES:
        raise ValueError(
            f"Invalid extractor mode '{requested_mode}'. Expected one of {', '.join(EXTRACTOR_MODES)}."
        )

    started_at = datetime.utcnow().isoformat()

    def _record(status: str, **fields: Any) -> None:
        _db.upsert_extraction_run(
            meeting_id,
            status=status,
            mode_requested=requested_mode,
            started_at=started_at,
            db_path=db_path,
            **fields,
        )

    windows = build_windows(meeting_id)
    if not windows:
        reason = "No indexed transcript utterances to extract from"
        logger.warning("%s (meeting %s)", reason, meeting_id)
        _record("failed", mode_used=None, reason=reason, accepted=0, dropped=0)
        return {
            "status": "failed",
            "reason": reason,
            "mode_requested": requested_mode,
            "mode_used": None,
            "accepted": 0,
            "dropped": 0,
            "by_reason": {},
            "drop_rate": 0.0,
        }

    mode_used = requested_mode
    error_msg: Optional[str] = None
    fallback_reason: Optional[str] = None
    extractor_version = RULES_EXTRACTOR_VERSION
    candidates = []

    try:
        if requested_mode == "llm":
            unavailable = llm_unavailable_reason(client)
            if unavailable and not allow_fallback:
                _record(
                    "extractor_unavailable", mode_used=None, reason=unavailable,
                    accepted=0, dropped=0,
                )
                return {
                    "status": "extractor_unavailable",
                    "reason": unavailable,
                    "mode_requested": requested_mode,
                    "mode_used": None,
                    "accepted": 0,
                    "dropped": 0,
                    "by_reason": {},
                    "drop_rate": 0.0,
                }
            if unavailable:
                logger.warning(
                    "LLM extractor unavailable for meeting %s (%s); using the rules baseline.",
                    meeting_id, unavailable,
                )
                fallback_reason = unavailable
                candidates, mode_used, extractor_version, local_note = _run_local(
                    meeting_id, windows, fallback_mode()
                )
                if local_note:
                    fallback_reason = f"{fallback_reason}; {local_note}"
            else:
                try:
                    llm_client = client or LLMClient()
                    candidates = extract_meeting(meeting_id, windows, llm_client)
                    extractor_version = f"llm:{llm_client.provider}:{llm_client.model}:{PROMPT_VERSION}"
                except LLMClientError as e:
                    logger.error(
                        "LLM extraction failed for meeting %s (%s). Falling back to rules baseline.",
                        meeting_id,
                        e,
                    )
                    error_msg = str(e)
                    fallback_reason = f"LLM extraction failed: {e}"
                    candidates, mode_used, extractor_version, local_note = _run_local(
                        meeting_id, windows, fallback_mode()
                    )
                    if local_note:
                        fallback_reason = f"{fallback_reason}; {local_note}"
        else:
            candidates, mode_used, extractor_version, local_note = _run_local(
                meeting_id, windows, requested_mode
            )
            if local_note:
                fallback_reason = local_note

        accepted, dropped = validate_candidates(meeting_id, candidates, windows)
        stats = persist_extraction(meeting_id, accepted, dropped, db_path=db_path)
    except Exception as exc:
        _record(
            "failed", mode_used=mode_used, reason=f"{type(exc).__name__}: {exc}",
            fallback_reason=fallback_reason, accepted=0, dropped=0,
            extractor_version=extractor_version,
        )
        raise

    status = "completed" if stats["accepted"] > 0 else "no_claims"
    _record(
        status,
        mode_used=mode_used,
        fallback_reason=fallback_reason,
        accepted=stats["accepted"],
        dropped=stats["dropped"],
        extractor_version=extractor_version,
    )

    stats["status"] = status
    stats["mode_requested"] = requested_mode
    stats["mode_used"] = mode_used
    stats["extractor_version"] = extractor_version
    if fallback_reason:
        stats["fallback_reason"] = fallback_reason
    if error_msg:
        stats["error"] = error_msg

    # Also update mirror claims.json with mode_used and error
    try:
        storage = MeetingStorage(meeting_id)
        claims_mirror = storage.path_for("intelligence", "claims.json")
        if claims_mirror.exists():
            data = json.loads(claims_mirror.read_text(encoding="utf-8"))
            data["stats"]["mode_used"] = mode_used
            if error_msg:
                data["stats"]["error"] = error_msg
            claims_mirror.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to update claims.json stats mirror: %s", exc)

    logger.info(
        "Extraction pipeline complete for %s (mode: %s): %d accepted, %d dropped",
        meeting_id,
        mode_used,
        stats["accepted"],
        stats["dropped"],
    )
    return stats
