from __future__ import annotations

"""
claim_writer.py
===============
Atomic persistence and audit mirroring for extracted claims.
Ensures:
- Idempotent re-runs via single-transaction clean deletion of prior extraction data.
- Strict foreign key invariant: claims are only inserted through insert_claim_with_evidence.
- Atomic all-or-nothing transaction: any IntegrityError rolls back the entire batch.
- Human-readable mirror written to storage/<meeting_id>/intelligence/claims.json.
- Full extraction stats reporting.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import sqlite3

from datetime import date, datetime
from app.core import db as _db
from app.core.config import STORAGE_ROOT
from app.schemas.claim import ActionItem, Decision
from app.services.intelligence.deadline_resolver import resolve_deadline
from app.services.intelligence.owner_resolver import resolve_owner
from app.services.intelligence.span_validator import DroppedClaim

logger = logging.getLogger(__name__)


def persist_extraction(
    meeting_id: str,
    accepted: List[Decision | ActionItem],
    dropped: List[DroppedClaim],
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """
    Persist accepted and dropped claims into SQLite within a single atomic transaction.
    If ANY insert raises IntegrityError or any failure, rolls back the ENTIRE batch and re-raises.
    Also mirrors claims, dropped rows, and stats to:
      storage/<meeting_id>/intelligence/claims.json
    Returns:
      {"accepted": n, "dropped": n, "by_reason": {...}, "drop_rate": float}
    """
    # 0. Deterministic owner & deadline resolution for ActionItems
    meeting = _db.get_meeting(meeting_id, db_path=db_path)
    meeting_date = date.today()
    if meeting and meeting.get("created_at"):
        try:
            meeting_date = datetime.fromisoformat(meeting["created_at"]).date()
        except Exception:
            meeting = {**meeting, "created_at": None}
    if meeting and not meeting.get("created_at") and meeting.get("filename"):
        try:
            meeting_date = datetime.fromtimestamp(
                Path(meeting["filename"]).stat().st_mtime
            ).date()
        except OSError:
            pass

    all_utts = _db.get_utterances(meeting_id, db_path=db_path)

    resolved_accepted: List[Decision | ActionItem] = []
    for claim in accepted:
        if claim.claim_type == "action" or isinstance(claim, ActionItem):
            owner, owner_type, owner_conf = resolve_owner(claim, all_utts)
            resolved_date, deadline_type, deadline_conf = resolve_deadline(
                claim.deadline_text, meeting_date
            )
            updated_claim = claim.model_copy(
                update={
                    "owner": owner,
                    "owner_type": owner_type,
                    "deadline_type": deadline_type,
                    "deadline_resolved": resolved_date,
                    "resolution_conf": {
                        "owner": round(owner_conf, 3),
                        "deadline": round(deadline_conf, 3),
                    },
                }
            )
            resolved_accepted.append(updated_claim)
        else:
            resolved_accepted.append(claim)

    # 1. Atomic DB persistence
    with _db.get_conn(db_path) as conn:
        # Idempotent clean of prior extraction state for this meeting
        _db.delete_claims_for_meeting(meeting_id, conn=conn)

        # Insert accepted claims strictly via insert_claim_with_evidence
        for claim in resolved_accepted:
            _db.insert_claim_with_evidence(claim, claim.evidence, conn=conn)

        # Insert all dropped rows
        _db.insert_dropped_claims(meeting_id, dropped, conn=conn)

    # 2. Compute stats
    by_reason: Dict[str, int] = {}
    for d in dropped:
        r = d.reason.value if hasattr(d.reason, "value") else str(d.reason)
        by_reason[r] = by_reason.get(r, 0) + 1

    total = len(accepted) + len(dropped)
    drop_rate = float(len(dropped) / total) if total > 0 else 0.0

    stats = {
        "accepted": len(accepted),
        "dropped": len(dropped),
        "by_reason": by_reason,
        "drop_rate": drop_rate,
    }

    # 3. Write human-readable mirror JSON
    mirror_dir = STORAGE_ROOT / meeting_id / "intelligence"
    mirror_dir.mkdir(parents=True, exist_ok=True)
    mirror_file = mirror_dir / "claims.json"

    dropped_list = []
    for d in dropped:
        if hasattr(d, "meeting_id"):
            reason_str = d.reason.value if hasattr(d.reason, "value") else str(d.reason)
            dropped_list.append(
                {
                    "meeting_id": d.meeting_id,
                    "window_id": d.window_id,
                    "claim_type": d.claim_type,
                    "text": d.text,
                    "cited_evidence": d.cited_evidence,
                    "reason": reason_str,
                    "detail": d.detail,
                }
            )
        elif isinstance(d, dict):
            dropped_list.append(d)

    mirror_data = {
        "claims": [c.model_dump() for c in resolved_accepted],
        "dropped": dropped_list,
        "stats": stats,
    }

    try:
        mirror_file.write_text(
            json.dumps(mirror_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Failed to write mirror JSON to %s: %s", mirror_file, e)

    return stats
