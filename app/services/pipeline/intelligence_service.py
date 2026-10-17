"""
Post-transcript intelligence stages shared by the background job, the API and
the CLI runner (Phase 1).

Every stage consumes the normalized transcript index (utterances in the DB), so
it works the same for recordings and for future imported transcripts.

The ensure_* helpers are idempotent: they reuse existing results unless they
are missing, stale or explicitly forced, so a normal processing attempt runs
extraction and verification once.
"""
from __future__ import annotations

import io
import json
import logging
from contextlib import redirect_stdout
from typing import Any, Optional

from app.core import db
from app.core.storage import MeetingStorage

logger = logging.getLogger(__name__)

_TRANSCRIPT_CANDIDATES = (
    ("transcript", "meeting_transcript_ready.json"),
    ("transcript", "frontend_transcript.json"),
    ("root", "frontend_transcript.json"),
)


def ensure_transcript_index(meeting_id: str) -> int:
    """Index the meeting transcript into the DB if it is not indexed yet."""
    from app.services.transcript.transcript_index import (
        build_transcript_index,
        persist_transcript_index,
    )

    db.init_db()
    existing = db.get_utterances(meeting_id)
    if existing:
        return len(existing)
    storage = MeetingStorage(meeting_id)
    for category, name in _TRANSCRIPT_CANDIDATES:
        path = storage.path_for(category, name)
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        utterances = build_transcript_index(payload, meeting_id)
        if utterances:
            persist_transcript_index(utterances, meeting_id)
            return len(utterances)
    raise FileNotFoundError("No transcript with usable utterances was found for this meeting.")


def ensure_topics(meeting_id: str) -> None:
    """Run topic segmentation once; topics.json has a single writer."""
    from app.services.intelligence.topic_segmenter import TopicSegmenter

    storage = MeetingStorage(meeting_id)
    storage.ensure_dirs()
    if storage.path_for("intelligence", "topics.json").exists():
        return
    transcript = storage.path_for("transcript", "frontend_transcript.json")
    if not transcript.exists():
        transcript.write_text(
            json.dumps(
                {
                    "segments": [
                        {
                            "start": u.start, "end": u.end, "speaker": u.speaker_name or u.speaker_id,
                            "text": u.text, "speaker_attribution_confidence": u.spk_conf,
                        }
                        for u in db.get_utterances(meeting_id)
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    with redirect_stdout(io.StringIO()):
        TopicSegmenter(output_dir=str(storage.intelligence)).process(str(transcript))


def extraction_is_current(meeting_id: str) -> bool:
    run = db.get_extraction_run(meeting_id)
    if not run or run["status"] not in ("completed", "no_claims"):
        return False
    return len(db.get_claims(meeting_id)) == (run.get("accepted") or 0)


def ensure_extraction(meeting_id: str, force: bool = False, mode: Optional[str] = None) -> dict[str, Any]:
    from app.services.intelligence.extraction_pipeline import run_extraction

    ensure_transcript_index(meeting_id)
    ensure_topics(meeting_id)
    if not force and extraction_is_current(meeting_id):
        return {"reused": True, **(db.get_extraction_run(meeting_id) or {})}
    with redirect_stdout(io.StringIO()):
        stats = run_extraction(meeting_id, mode=mode)
    return {"reused": False, **stats}


def verification_is_current(meeting_id: str) -> bool:
    run = db.get_verification_run(meeting_id)
    extraction = db.get_extraction_run(meeting_id)
    if not run or not extraction:
        return False
    if run["status"] not in ("completed", "no_claims", "semantic_unavailable"):
        return False
    if (extraction.get("finished_at") or "") > (run.get("created_at") or ""):
        return False
    results = db.get_verification_results(meeting_id)
    return all("fusion" in results.get(c["claim_id"], {}) for c in db.get_claims(meeting_id))


def ensure_verification(meeting_id: str, force: bool = False) -> dict[str, Any]:
    from app.services.verification.claim_verification import verify_meeting

    if not force and verification_is_current(meeting_id):
        return {"reused": True, **(db.get_verification_run(meeting_id) or {})}
    return {"reused": False, **verify_meeting(meeting_id)}
