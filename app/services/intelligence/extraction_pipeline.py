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
from pathlib import Path
from typing import Any, Dict, Optional

from app.core import config
from app.core.llm_client import LLMClient, LLMClientError
from app.core.storage import MeetingStorage
from app.services.intelligence.claim_writer import persist_extraction
from app.services.intelligence.context_builder import build_windows
from app.services.intelligence.llm_extractor import extract_meeting
from app.services.intelligence.rules_adapter import extract_with_rules
from app.services.intelligence.span_validator import validate_candidates

logger = logging.getLogger(__name__)


def run_extraction(
    meeting_id: str,
    mode: Optional[str] = None,
    client: Optional[LLMClient] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """
    Orchestrate candidate extraction, span validation, and persistence.
    """
    requested_mode = (
        mode
        if mode is not None
        else os.getenv("EXTRACTOR")
        or getattr(config, "EXTRACTOR", "llm")
        or "llm"
    ).strip().lower()

    if requested_mode not in ("rules", "llm"):
        raise ValueError(
            f"Invalid extractor mode '{requested_mode}'. Expected 'rules' or 'llm'."
        )

    windows = build_windows(meeting_id)
    if not windows:
        logger.warning("No extraction windows generated for meeting %s", meeting_id)
        stats = {
            "mode_requested": requested_mode,
            "mode_used": requested_mode,
            "accepted": 0,
            "dropped": 0,
            "by_reason": {},
            "drop_rate": 0.0,
        }
        return stats

    mode_used = requested_mode
    error_msg: Optional[str] = None
    candidates = []

    if requested_mode == "llm":
        try:
            llm_client = client or LLMClient()
            candidates = extract_meeting(meeting_id, windows, llm_client)
        except LLMClientError as e:
            logger.error(
                "LLM extraction failed for meeting %s (%s). Falling back to rules baseline.",
                meeting_id,
                e,
            )
            error_msg = str(e)
            mode_used = "rules"
            candidates = extract_with_rules(meeting_id, windows)
    else:
        candidates = extract_with_rules(meeting_id, windows)

    accepted, dropped = validate_candidates(meeting_id, candidates, windows)
    stats = persist_extraction(meeting_id, accepted, dropped, db_path=db_path)

    stats["mode_requested"] = requested_mode
    stats["mode_used"] = mode_used
    if error_msg:
        stats["error"] = error_msg
        stats["fallback_reason"] = error_msg

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
