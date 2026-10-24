"""
Delete a meeting and everything derived from it (Phase 10).

Refused while the meeting is being processed or recorded. A queued job is
removed in the same transaction as the check, so a worker can no longer claim
it; the job runner also skips meetings that no longer exist.

Removed: every database row of the meeting (transcript, claims, verification,
review, approvals, live-meeting records, stage timings, membership,
invitations, and ledger commitments created from it, with their history), the
meeting's storage folder (recording, tracks, transcripts, minutes, PDFs, LLM /
NLI / retrieval caches), and the shared pipeline-cache entry of its recording
unless another meeting has the same recording.
"""
from __future__ import annotations

import json
import logging
import shutil
from typing import Any, Dict

from app.core import db
from app.core.storage import MeetingStorage

logger = logging.getLogger(__name__)

# Order matters: evidence_links references utterances (ON DELETE RESTRICT).
_MEETING_TABLES = (
    "evidence_links", "verification_results", "evidence_corrections", "claims", "dropped_claims",
    "utterances", "extraction_runs", "verification_runs", "review_items", "review_audit_log",
    "approved_snapshots", "review_drafts", "processing_stage_timings", "live_events",
    "live_participants", "recording_segments", "live_meetings", "meeting_members",
    "meeting_invitations", "commitment_updates", "transcript_corrections", "jobs",
    "meeting_invitees", "scheduled_meetings", "email_outbox",
)


class DeletionRefused(Exception):
    pass


def _content_hash(storage: MeetingStorage):
    try:
        status = json.loads(storage.path_for("root", "pipeline_status.json").read_text(encoding="utf-8"))
        return status.get("content_hash")
    except Exception:
        return None


def _hash_used_elsewhere(content_hash: str, meeting_id: str) -> bool:
    from app.core import config

    for status_file in config.STORAGE_ROOT.glob("*/pipeline_status.json"):
        if status_file.parent.name == meeting_id:
            continue
        try:
            if json.loads(status_file.read_text(encoding="utf-8")).get("content_hash") == content_hash:
                return True
        except Exception:
            continue
    return False


def delete_meeting(meeting_id: str) -> Dict[str, Any]:
    from app.services.live.recorder import get_recording_manager

    if get_recording_manager().is_active(meeting_id):
        raise DeletionRefused("The meeting is being recorded. Stop the recording first.")

    storage = MeetingStorage(meeting_id)
    content_hash = _content_hash(storage)
    removed_rows = {}
    with db.get_conn() as conn:
        # Remove a queued job atomically with the check; a running job blocks deletion.
        conn.execute("DELETE FROM jobs WHERE meeting_id = ? AND state != 'running'", (meeting_id,))
        if conn.execute("SELECT 1 FROM jobs WHERE meeting_id = ?", (meeting_id,)).fetchone():
            raise DeletionRefused("The meeting is being processed. Wait until processing finishes.")
        if conn.execute(
            "SELECT 1 FROM recording_segments WHERE meeting_id = ? AND state IN ('starting', 'recording', 'stopping')",
            (meeting_id,),
        ).fetchone():
            raise DeletionRefused("A recording segment is still open. Stop the recording first.")

        commitment_ids = [r[0] for r in conn.execute(
            "SELECT commitment_id FROM commitments WHERE source_meeting_id = ?", (meeting_id,))]
        for cid in commitment_ids:
            conn.execute("DELETE FROM commitment_updates WHERE commitment_id = ?", (cid,))
        removed_rows["commitments"] = conn.execute(
            "DELETE FROM commitments WHERE source_meeting_id = ?", (meeting_id,)).rowcount
        removed_rows["email_events"] = conn.execute(
            "DELETE FROM email_events WHERE outbox_id IN (SELECT outbox_id FROM email_outbox WHERE meeting_id = ?)",
            (meeting_id,)).rowcount
        for table in _MEETING_TABLES:
            removed_rows[table] = conn.execute(f"DELETE FROM {table} WHERE meeting_id = ?", (meeting_id,)).rowcount
        removed_rows["meetings"] = conn.execute("DELETE FROM meetings WHERE meeting_id = ?", (meeting_id,)).rowcount

    removed_files = storage.root.exists()
    if removed_files:
        shutil.rmtree(storage.root)
    cache_removed = False
    if content_hash and not _hash_used_elsewhere(content_hash, meeting_id):
        from app.services.pipeline.recorded_pipeline_adapter import _cache_directory

        cache_dir = _cache_directory(content_hash)
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            cache_removed = True
    logger.info("Deleted meeting %s (%d rows)", meeting_id, sum(removed_rows.values()))
    return {
        "meeting_id": meeting_id,
        "deleted": True,
        "rows_removed": {k: v for k, v in removed_rows.items() if v},
        "storage_folder_removed": removed_files,
        "pipeline_cache_removed": cache_removed,
    }
