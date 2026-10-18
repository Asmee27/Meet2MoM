from __future__ import annotations

from pathlib import Path
from uuid import uuid4
import json
import re
import shutil

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import (
    getSampleStyleSheet,
    ParagraphStyle,
)
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
    KeepTogether,
)

from app.services.pipeline.recorded_pipeline_adapter import (
    RecordedPipelineError,
    get_pipeline_status,
    run_recorded_pipeline,
)

from pydantic import BaseModel
from typing import Any, Dict, List, Literal, Optional

from app.services.pipeline.intelligence_service import (
    ensure_extraction,
    ensure_verification,
    verification_is_current,
)
from app.services.verification.claims_view import build_claims_view


router = APIRouter(
    prefix="/api",
    tags=["Meetings"],
)


# ============================================================
# PROJECT PATHS
# ============================================================

from datetime import datetime, timezone
from app.core import config
from app.core.config import STORAGE_ROOT
from app.core.storage import MeetingStorage, is_valid_meeting_id
from app.core import db as _db
from app.core.jobs import run_pipeline, STAGES
from app.schemas.job import JobStatus

PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parent
    .parent
    .parent
)

STORAGE_DIR = STORAGE_ROOT

STORAGE_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# ============================================================
# UPLOAD CONFIGURATION
# ============================================================

ALLOWED_EXTENSIONS = {
    ".mp4",
    ".wav",
    ".mp3",
    ".m4a",
    ".webm",
    ".mov",
    ".avi",
}

MAX_FILE_SIZE = (
    2
    * 1024
    * 1024
    * 1024
)

CHUNK_SIZE = (
    1024
    * 1024
)

# ============================================================
# GENERIC UTILITIES
# ============================================================

def validate_file_id(
    file_id: str,
):
    if not is_valid_meeting_id(file_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid meeting ID.",
        )


def get_storage(
    file_id: str,
) -> MeetingStorage:

    validate_file_id(
        file_id
    )

    storage = MeetingStorage(
        file_id
    )

    storage.ensure_dirs()

    return storage


def meeting_dir(
    file_id: str,
) -> Path:

    return get_storage(
        file_id
    ).root


def find_uploaded_file(
    file_id: str,
) -> Path:

    storage = get_storage(
        file_id
    )

    # Check storage/meeting_id/recording first, then root fallback
    candidates = [
        path
        for path
        in storage.recording.glob(
            "recording.*"
        )
        if (
            path.is_file()
            and path.suffix.lower()
            in ALLOWED_EXTENSIONS
        )
    ]

    if not candidates:
        candidates = [
            path
            for path
            in storage.root.glob(
                "recording.*"
            )
            if (
                path.is_file()
                and path.suffix.lower()
                in ALLOWED_EXTENSIONS
            )
        ]

    if not candidates:
        raise HTTPException(
            status_code=404,
            detail=(
                "Uploaded recording "
                "not found."
            ),
        )

    return candidates[0]


def read_json(
    path: Path,
    detail: str,
):

    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=detail,
        )

    try:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid JSON: "
                f"{path.name}"
            ),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Unable to read "
                f"{path.name}"
            ),
        ) from exc


def write_json(
    path: Path,
    data,
):

    try:
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        path.write_text(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Unable to save "
                f"{path.name}"
            ),
        ) from exc


def clean_text(
    value,
) -> str:

    return re.sub(
        r"\s+",
        " ",
        str(
            value or ""
        ),
    ).strip(
        " ,.-"
    )


# ============================================================
# UPLOAD
# ============================================================

@router.post("/upload")
@router.post("/meetings/upload")
async def upload_meeting(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
):
    """
    Upload a meeting recording and immediately queue the full pipeline.
    Returns 202 within ~2 s — processing continues in the background.
    Poll GET /api/meetings/{meeting_id}/status to track progress.
    """

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="No file was provided.",
        )

    original_name = Path(file.filename).name
    extension = Path(original_name).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        await file.close()
        raise HTTPException(
            status_code=400,
            detail="Supported formats: MP4, WAV, MP3, M4A, WEBM, MOV, AVI.",
        )

    file_id = uuid4().hex
    storage = get_storage(file_id)
    destination = storage.path_for("recording", f"recording{extension}")

    total_size = 0
    try:
        with destination.open("wb") as buffer:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > MAX_FILE_SIZE:
                    if destination.exists():
                        destination.unlink()
                    raise HTTPException(
                        status_code=413,
                        detail="Recording is too large. Maximum size is 2 GB.",
                    )
                buffer.write(chunk)
    finally:
        await file.close()

    metadata = {
        "file_id": file_id,
        "original_filename": original_name,
        "stored_filename": destination.name,
        "size_bytes": total_size,
        "status": "uploaded",
    }

    write_json(storage.path_for("recording", "meeting.json"), metadata)
    write_json(storage.path_for("root", "meeting.json"), metadata)

    # Persist meetings row so FK constraints on utterances are satisfied
    try:
        _db.init_db()
        _db.insert_meeting(
            file_id,
            filename=original_name,
            status="queued",
        )
        # Always register queued job status
        _db.upsert_job_status(
            JobStatus(
                meeting_id=file_id,
                stage="uploaded",
                progress=0.0,
                state="queued",
                error=None,
                updated_at=datetime.now(timezone.utc),
                file_path=str(destination),
            )
        )
    except Exception:
        pass  # DB write failures must not block upload response

    # When JOB_RUNNER is worker, only enqueue without starting inline BackgroundTasks
    if (getattr(config, "JOB_RUNNER", "inline") or "inline").strip().lower() != "worker":
        background_tasks.add_task(run_pipeline, file_id, destination)


    return JSONResponse(
        status_code=202,
        content={
            **metadata,
            "meeting_id": file_id,
            "status": "queued",
            "file_id": file_id,
            "message": "Recording queued for processing.",
        },
    )


# ============================================================
# ASYNC JOB STATUS  (new, stable URL)
# ============================================================

@router.get("/meetings")
def list_meetings():
    """List previously processed meetings that have indexed utterances."""
    _db.init_db()
    with _db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                m.meeting_id,
                m.filename,
                m.created_at,
                m.status,
                (
                    SELECT COUNT(*)
                    FROM utterances u
                    WHERE u.meeting_id = m.meeting_id
                ) AS utterance_count,
                (
                    SELECT COUNT(*)
                    FROM claims c
                    WHERE c.meeting_id = m.meeting_id
                ) AS claim_count
            FROM meetings m
            WHERE m.filename IS NOT NULL
              AND m.filename <> 'synthetic.wav'
              AND m.meeting_id NOT LIKE 'background-%'
              AND m.meeting_id NOT LIKE 'mismatch-%'
              AND m.meeting_id NOT LIKE 'cache-check-%'
              AND m.meeting_id NOT LIKE 'cited-none-%'
              AND m.meeting_id NOT LIKE 'expand-%'
              AND m.meeting_id NOT LIKE 'clamp-%'
            ORDER BY m.created_at DESC
            LIMIT 50
            """
        ).fetchall()
    return [
        dict(row)
        for row in rows
        if row["utterance_count"] > 0
    ]


@router.get("/meetings/{meeting_id}/status")
def get_meeting_status(meeting_id: str):
    """
    Return the current JobStatus for a meeting.
    Fields: meeting_id, stage, progress (0-1), state, error, updated_at.
    """
    validate_file_id(meeting_id)
    job = _db.get_job_status(meeting_id)
    if job is None:
        # Fall back to the file-based status written by the adapter
        storage = MeetingStorage(meeting_id)
        status_path = storage.path_for("root", "pipeline_status.json")
        if status_path.exists():
            try:
                return json.loads(status_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        meeting = _db.get_meeting(meeting_id)
        if meeting is not None and _db.get_utterances(meeting_id):
            return {
                "meeting_id": meeting_id,
                "stage": "done",
                "progress": 1.0,
                "state": "done",
                "error": None,
                "updated_at": meeting.get("created_at"),
            }
        raise HTTPException(status_code=404, detail="No status found for this meeting.")
    return job.model_dump()


# ============================================================
# UTTERANCES FROM DB  (new endpoint)
# ============================================================

@router.get("/meetings/{meeting_id}/transcript")
def get_meeting_transcript(meeting_id: str):
    """
    Return utterances from the DB for a meeting.
    404 if the index stage has not completed yet.
    """
    validate_file_id(meeting_id)
    job = _db.get_job_status(meeting_id)
    if job is not None:
        reached_index = STAGES.index(job.stage) >= STAGES.index("index")
        if not reached_index:
            raise HTTPException(
                status_code=404,
                detail=f"Transcript not yet available (current stage: {job.stage}).",
            )
    utterances = _db.get_utterances(meeting_id)
    if not utterances:
        # Fallback: check for transcript_index.json on disk
        storage = MeetingStorage(meeting_id)
        index_path = storage.path_for("transcript", "transcript_index.json")
        if index_path.exists():
            try:
                return json.loads(index_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        raise HTTPException(
            status_code=404,
            detail="Transcript index not found. Processing may still be in progress.",
        )
    return [u.model_dump() for u in utterances]


# ============================================================
# RECORDED PIPELINE
# ============================================================

@router.post("/process/{file_id}")
def process_meeting(
    file_id: str,
):

    input_file = find_uploaded_file(
        file_id
    )

    try:

        transcript = (
            run_recorded_pipeline(
                file_id,
                input_file,
            )
        )

    except RecordedPipelineError as exc:

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc

    return {
        "message":
            "Recorded meeting pipeline "
            "completed successfully",
        **transcript,
    }


@router.get("/status/{file_id}")
def pipeline_status(
    file_id: str,
):

    find_uploaded_file(
        file_id
    )

    return get_pipeline_status(
        file_id
    )



# ============================================================
# FRONTEND COMPATIBILITY — AUDIO EXTRACTION
# ============================================================

@router.post(
    "/extract-audio/{file_id}"
)
def extract_audio_compatibility(
    file_id: str,
):

    input_file = find_uploaded_file(
        file_id
    )

    return {
        "message":
            "Recording is ready for "
            "the Meet2MoM audio pipeline",

        "file_id":
            file_id,

        "input_file":
            input_file.name,

        "status":
            "ready_for_processing",
    }


# ============================================================
# TRANSCRIPTION + DIARIZATION
# ============================================================

@router.post(
    "/transcribe/{file_id}"
)
def transcribe_with_real_pipeline(
    file_id: str,
):

    input_file = find_uploaded_file(
        file_id
    )

    storage = get_storage(
        file_id
    )

    cached_frontend_transcript = storage.path_for(
        "transcript",
        "frontend_transcript.json"
    )

    if not cached_frontend_transcript.exists():
        cached_frontend_transcript = (
            storage.root
            / "frontend_transcript.json"
        )

    if (
        cached_frontend_transcript
        .exists()
    ):

        return read_json(
            cached_frontend_transcript,
            "Saved meeting transcript "
            "not found.",
        )

    try:

        return run_recorded_pipeline(
            file_id,
            input_file,
        )

    except RecordedPipelineError as exc:

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc


# ============================================================
# AUTHORITATIVE MEETING INTELLIGENCE (Phase 1)
#
# One source of truth: claims in the DB (claims + evidence_links), produced by
# the validated extraction pipeline, and their persisted verification
# (verification_results). /extract, /verify, /meetings/{id}/claims and the MoM
# export all read the same records through build_claims_view().
# ============================================================

def _stale_mom_files(storage: MeetingStorage):
    return (
        storage.path_for("mom", "mom.json"),
        storage.path_for("mom", "Meet2MoM-Meeting-Minutes.pdf"),
        storage.root / "mom.json",
        storage.root / "Meet2MoM-Meeting-Minutes.pdf",
    )


def _remove_files(paths) -> None:
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            # A stale derived artifact must not fail the request.
            pass


def _load_topics(storage: MeetingStorage) -> list:
    path = storage.path_for("intelligence", "topics.json")
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    topics = data.get("topics", data) if isinstance(data, dict) else data
    return topics if isinstance(topics, list) else []


def _warnings(claim: dict) -> list:
    return [
        reason["message"]
        for reason in claim["verification"]["final"]["reasons"]
        if reason.get("severity") == "warning"
    ]


def _decision_item(claim: dict) -> dict:
    return {
        "claim_id": claim["claim_id"],
        "text": claim["text"],
        "state": claim["state"],
        "speaker": claim.get("proposer") or claim.get("speaker"),
        "timestamp": claim.get("timestamp"),
        "evidence_ids": claim["evidence_ids"],
        "verification_status": claim["verification"]["final"]["label"],
        "warnings": _warnings(claim),
    }


def _action_item(claim: dict) -> dict:
    evidence = claim.get("evidence") or []
    return {
        "claim_id": claim["claim_id"],
        "task": claim["text"],
        "owner": claim.get("owner"),
        "owner_type": claim.get("owner_type"),
        "dueDate": claim.get("deadline_resolved") or claim.get("deadline_text"),
        "deadline_text": claim.get("deadline_text"),
        "deadline_resolved": claim.get("deadline_resolved"),
        "source_start": evidence[0]["start"] if evidence else None,
        "source_end": evidence[-1]["end"] if evidence else None,
        "source_text": " ".join(item["text"] for item in evidence),
        "evidence_ids": claim["evidence_ids"],
        "verification_status": claim["verification"]["final"]["label"],
        "warnings": _warnings(claim),
    }


_EXTRACTION_HTTP_ERRORS = {
    "failed": 422,
    "extractor_unavailable": 503,
}


@router.post(
    "/extract/{file_id}"
)
def extract_meeting_intelligence(
    file_id: str,
    force: bool = False,
):
    """
    Run (or reuse) the authoritative claims extraction for a meeting.

    Zero claims is returned as a normal result only when an extractor actually
    ran ("no_claims"). A missing transcript, an unavailable extractor or a
    failed extraction is reported as an error, never as an empty success.
    """
    storage = get_storage(file_id)

    try:
        result = ensure_extraction(file_id, force=force)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Transcript not found. Transcribe the meeting first.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Meeting intelligence processing failed: {exc}",
        ) from exc

    view = build_claims_view(file_id)
    extraction = view["extraction"] or {}
    status = extraction.get("status")
    if status in _EXTRACTION_HTTP_ERRORS:
        raise HTTPException(
            status_code=_EXTRACTION_HTTP_ERRORS[status],
            detail=f"Extraction {status.replace('_', ' ')}: {extraction.get('reason')}",
        )

    topics = _load_topics(storage)
    decisions = [_decision_item(c) for c in view["claims"] if c["claim_type"] == "decision"]
    action_items = [_action_item(c) for c in view["claims"] if c["claim_type"] == "action"]

    response = {
        "file_id": file_id,
        "status": "extracted",
        "extraction": extraction,
        "reused_existing": bool(result.get("reused")),
        "topic_count": len(topics),
        "decision_count": len(decisions),
        "action_item_count": len(action_items),
        "topics": topics,
        "decisions": decisions,
        "action_items": action_items,
        "claims": view["claims"],
    }

    write_json(storage.path_for("intelligence", "extraction.json"), response)
    if not result.get("reused"):
        _remove_files(_stale_mom_files(storage))
    return response


def _legacy_stats(claims: list, claim_type: str) -> dict:
    labels = [c["verification"]["final"]["label"] for c in claims if c["claim_type"] == claim_type]
    return {
        "total": len(labels),
        "evidence_supported": sum(1 for label in labels if label in ("verified", "verified_with_warning")),
        "verified_clean": labels.count("verified"),
        "with_warnings": labels.count("verified_with_warning"),
        "review": labels.count("review") + labels.count("unverified"),
        "rejected": labels.count("rejected"),
    }


def _verification_response(file_id: str, view: dict) -> dict:
    claims = view["claims"]
    counts = view["counts"]
    run = view["verification_run"] or {}
    if run.get("status") == "semantic_unavailable":
        overall = "semantic_verification_unavailable"
    elif counts.get("review") or counts.get("unverified"):
        overall = "needs_review"
    elif counts.get("verified_with_warning"):
        overall = "verified_with_warnings"
    elif claims:
        overall = "verified"
    else:
        overall = "no_claims"
    return {
        "message": "Claims verified against transcript evidence",
        "file_id": file_id,
        "status": overall,
        "counts": counts,
        "extraction": view["extraction"],
        "verification_run": run,
        "decisions": _legacy_stats(claims, "decision"),
        "action_items": _legacy_stats(claims, "action"),
        "claims": claims,
    }


@router.post(
    "/verify/{file_id}"
)
def verify_meeting_intelligence(
    file_id: str,
    force: bool = False,
):
    """
    Verify every extracted claim (deterministic + semantic + fusion) and return
    the persisted results. The verifier never creates claims; it only judges
    the authoritative ones.
    """
    storage = get_storage(file_id)

    if _db.get_extraction_run(file_id) is None:
        raise HTTPException(
            status_code=409,
            detail="Extraction not found. Run /api/extract/{file_id} first.",
        )

    try:
        result = ensure_verification(file_id, force=force)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Meeting verification failed: {exc}",
        ) from exc

    response = _verification_response(file_id, build_claims_view(file_id))
    response["reused_existing"] = bool(result.get("reused"))

    write_json(storage.path_for("verification", "verification_summary.json"), response)
    if not result.get("reused"):
        _remove_files(_stale_mom_files(storage))
    return response


@router.get("/meetings/{meeting_id}/claims")
def get_meeting_claims(meeting_id: str):
    """Claims with their persisted verification payload (read-only, no side effects)."""
    validate_file_id(meeting_id)
    if _db.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="Meeting not found.")
    return _verification_response(meeting_id, build_claims_view(meeting_id))


class EvidenceSuggestionDecision(BaseModel):
    action: Literal["accept", "reject"]


@router.post("/meetings/{meeting_id}/claims/{claim_id}/evidence-suggestion")
def review_evidence_suggestion(
    meeting_id: str,
    claim_id: str,
    decision: EvidenceSuggestionDecision,
):
    """
    Accept or reject the evidence span the semantic verifier suggested.

    Accepting replaces the claim's evidence links (FK-checked) and re-runs
    verification; rejecting is recorded and shown as a reason. Either decision
    is persisted in evidence_corrections. Full reviewer sign-off and audit
    history are Phase 3.
    """
    validate_file_id(meeting_id)
    view = build_claims_view(meeting_id)
    claim = next((c for c in view["claims"] if c["claim_id"] == claim_id), None)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found.")
    if claim["correction"] is not None:
        raise HTTPException(status_code=409, detail="This suggestion has already been reviewed.")
    if not claim["suggestion"]:
        raise HTTPException(status_code=409, detail="No evidence suggestion exists for this claim.")

    action = "accepted" if decision.action == "accept" else "rejected"
    try:
        _db.record_evidence_correction(
            claim_id,
            meeting_id,
            action,
            original_utt_ids=claim["evidence_ids"],
            proposed_utt_ids=claim["suggestion"]["utt_ids"],
        )
        ensure_verification(meeting_id, force=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not apply the evidence decision: {exc}",
        ) from exc

    _remove_files(_stale_mom_files(MeetingStorage(meeting_id)))
    response = _verification_response(meeting_id, build_claims_view(meeting_id))
    response["claim"] = next(c for c in response["claims"] if c["claim_id"] == claim_id)
    return response


# ============================================================
# PHASE 3: REVIEW DRAFTS, APPROVALS, AUDIT LOG, AND EXPORTS
# ============================================================

class ReviewItemUpdate(BaseModel):
    text: Optional[str] = None
    owner: Optional[str] = None
    owner_type: Optional[str] = None
    deadline_text: Optional[str] = None
    deadline_resolved: Optional[str] = None
    state: Optional[str] = None
    is_excluded: Optional[bool] = None
    evidence_ids: Optional[List[str]] = None
    reviewer: Optional[str] = None
    reason: Optional[str] = None


class ReviewItemCreate(BaseModel):
    item_type: Literal["decision", "action"]
    text: str
    owner: Optional[str] = None
    owner_type: Optional[str] = "unassigned"
    deadline_text: Optional[str] = None
    deadline_resolved: Optional[str] = None
    state: Optional[str] = "accepted"
    is_excluded: Optional[bool] = False
    evidence_ids: Optional[List[str]] = []
    reviewer: Optional[str] = None
    reason: Optional[str] = None


class ApproveReviewRequest(BaseModel):
    reviewer: str
    notes: Optional[str] = None


class ReopenReviewRequest(BaseModel):
    reviewer: str
    reason: str


def _build_review_response(meeting_id: str) -> Dict[str, Any]:
    validate_file_id(meeting_id)
    if _db.get_meeting(meeting_id) is None:
        raise HTTPException(status_code=404, detail="Meeting not found.")

    draft = _db.init_review_draft(meeting_id)
    claims_view = build_claims_view(meeting_id)
    claims_by_id = {c["claim_id"]: c for c in claims_view["claims"]}

    utterances = _db.get_utterances(meeting_id)
    utts_by_id = {u.utt_id: u for u in utterances}

    items = []
    for item in draft.get("items") or []:
        orig_id = item.get("original_claim_id")
        orig_claim = claims_by_id.get(orig_id) if orig_id else None

        evidence_models = []
        for uid in item.get("evidence_ids") or []:
            if uid in utts_by_id:
                u = utts_by_id[uid]
                evidence_models.append({
                    "utt_id": u.utt_id,
                    "speaker": u.speaker_name or u.speaker_id,
                    "speaker_id": u.speaker_id,
                    "start": u.start,
                    "end": u.end,
                    "text": u.text,
                    "asr_conf": u.asr_conf,
                    "spk_conf": u.spk_conf,
                    "overlap": bool(u.overlap),
                })

        # Determine display status
        if item["is_excluded"]:
            display_status = "excluded"
        elif item["review_status"] == "edited":
            display_status = "human_edited"
        elif item["review_status"] == "added":
            display_status = "added"
        elif item["review_status"] == "rejected":
            display_status = "rejected"
        elif orig_claim:
            display_status = orig_claim["verification"]["final"]["label"]
        else:
            display_status = "unverified"

        items.append({
            **item,
            "evidence": evidence_models,
            "display_status": display_status,
            "verification": orig_claim["verification"] if orig_claim else None,
            "suggestion": orig_claim.get("suggestion") if orig_claim else None,
            "contradiction": orig_claim.get("contradiction") if orig_claim else None,
            "correction": orig_claim.get("correction") if orig_claim else None,
        })

    approved_snapshot = _db.get_latest_approved_snapshot(meeting_id)
    approval_history = _db.get_all_approved_snapshots(meeting_id)

    counts = {
        "total_items": len(items),
        "original": sum(1 for it in items if it["review_status"] == "original"),
        "edited": sum(1 for it in items if it["review_status"] == "edited"),
        "added": sum(1 for it in items if it["review_status"] == "added"),
        "excluded": sum(1 for it in items if it["is_excluded"]),
        "confirmed": sum(
            1 for it in items
            if not it["is_excluded"] and (
                it["review_status"] in ("edited", "added") or
                it["display_status"] in ("verified", "verified_with_warning")
            ) and (it["item_type"] != "decision" or it.get("state") == "accepted")
        ),
        "needs_review": sum(
            1 for it in items
            if not it["is_excluded"] and
            it["review_status"] == "original" and
            it["display_status"] not in ("verified", "verified_with_warning")
        ),
        "rejected": sum(
            1 for it in items
            if it["review_status"] == "rejected" or it["display_status"] == "rejected"
        ),
    }

    return {
        "meeting_id": meeting_id,
        "draft_status": draft["draft_status"],
        "meeting_title": draft.get("meeting_title") or "Meeting Minutes",
        "summary": draft.get("summary"),
        "approved_snapshot": approved_snapshot,
        "latest_approved_snapshot": approved_snapshot,
        "approval_history": approval_history,
        "counts": counts,
        "items": items,
        "updated_at": draft["updated_at"],
    }


@router.get("/meetings/{meeting_id}/review")
def get_meeting_review(meeting_id: str):
    """Retrieve the interactive review draft with items, verification, and approval status."""
    return _build_review_response(meeting_id)


@router.put("/meetings/{meeting_id}/review/items/{item_id}")
def update_review_item_endpoint(
    meeting_id: str,
    item_id: str,
    request: ReviewItemUpdate,
):
    """
    Update a decision or action item in the review draft.
    Records field-level diffs in the review audit log.
    Rejects changes if the draft is currently approved (must be reopened first).
    """
    validate_file_id(meeting_id)
    draft = _db.init_review_draft(meeting_id)
    if draft["draft_status"] == "approved":
        raise HTTPException(
            status_code=409,
            detail="Cannot edit an approved MoM. Please reopen review first.",
        )

    updates = request.model_dump(exclude_unset=True, exclude={"reviewer", "reason"})
    try:
        updated_item = _db.update_review_item(
            meeting_id,
            item_id,
            updates,
            reviewer=request.reviewer,
            reason=request.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    review = _build_review_response(meeting_id)
    return {
        "item": updated_item,
        "review": review,
    }


@router.post("/meetings/{meeting_id}/review/items")
def add_review_item_endpoint(
    meeting_id: str,
    request: ReviewItemCreate,
):
    """
    Add a new human-curated decision or action item to the review draft.
    Rejects additions if the draft is currently approved.
    """
    validate_file_id(meeting_id)
    draft = _db.init_review_draft(meeting_id)
    if draft["draft_status"] == "approved":
        raise HTTPException(
            status_code=409,
            detail="Cannot add items to an approved MoM. Please reopen review first.",
        )

    item_data = request.model_dump(exclude={"reviewer", "reason"})
    try:
        new_item = _db.add_review_item(
            meeting_id,
            item_data,
            reviewer=request.reviewer,
            reason=request.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    review = _build_review_response(meeting_id)
    return {
        "item": new_item,
        "review": review,
    }


def build_evidence_appendix(
    meeting_id: str,
    mom: dict,
    db_path: Optional[Path | str] = None,
) -> list[dict]:
    """
    Build a deterministic evidence appendix array grounding each item's decisions
    and actions in verbatim utterances from the immutable database.

    - Excluded items do not appear in the appendix.
    - Reviewer-added items with no cited utterances appear with evidence_role: "none"
      and quote: "Reviewer-added; no transcript evidence".
    - Sorted deterministically: by item order, then by utterance start time.
    """
    utterances = _db.get_utterances(meeting_id, db_path=db_path)
    utt_map = {u.utt_id: u for u in utterances}

    v_info = mom.get("verification") or {}
    det_ver = v_info.get("config_version") or "evidence-v1"
    sem_ver = v_info.get("model_version") or "dialogue-v1"
    thresh = v_info.get("thresholds") or "fused-v1"
    verifier_versions = {
        "deterministic": det_ver,
        "semantic": sem_ver,
        "thresholds": thresh,
    }

    appendix = []

    # Gather items deterministically: decisions first, then actions
    items_to_process = []
    for d in mom.get("decisions", []):
        if not (d.get("excluded") or d.get("is_excluded")):
            items_to_process.append(("decision", d))

    for a in mom.get("action_items", []):
        if not (a.get("excluded") or a.get("is_excluded")):
            items_to_process.append(("action", a))

    for item_type, item in items_to_process:
        item_id = item.get("claim_id") or item.get("item_id")
        item_status = item.get("review_status") or item.get("verification_status") or "verified"
        verification_label = item.get("verification_status") or "verified"
        evidence_ids = item.get("evidence_ids") or []

        if not evidence_ids:
            appendix.append({
                "item_id": item_id,
                "item_type": item_type,
                "item_status": item_status,
                "utt_id": None,
                "speaker": None,
                "start_s": None,
                "end_s": None,
                "start_label": None,
                "end_label": None,
                "quote": "Reviewer-added; no transcript evidence",
                "evidence_role": "none",
                "verification_label": verification_label,
                "verifier_versions": verifier_versions,
            })
            continue

        cited_utts = [utt_map[uid] for uid in evidence_ids if uid in utt_map]
        cited_utts.sort(key=lambda u: (u.start, u.end, u.utt_id))

        if not cited_utts:
            appendix.append({
                "item_id": item_id,
                "item_type": item_type,
                "item_status": item_status,
                "utt_id": None,
                "speaker": None,
                "start_s": None,
                "end_s": None,
                "start_label": None,
                "end_label": None,
                "quote": "Reviewer-added; no transcript evidence",
                "evidence_role": "none",
                "verification_label": verification_label,
                "verifier_versions": verifier_versions,
            })
            continue

        for idx, u in enumerate(cited_utts):
            if item_type == "decision" and len(cited_utts) > 1 and idx == len(cited_utts) - 1:
                role = "confirmation"
            else:
                role = "evidence"

            start_s = round(u.start, 2)
            end_s = round(u.end, 2)
            s_min, s_rem = divmod(int(round(u.start)), 60)
            e_min, e_rem = divmod(int(round(u.end)), 60)
            start_label = f"{s_min:02d}:{s_rem:02d}"
            end_label = f"{e_min:02d}:{e_rem:02d}"

            appendix.append({
                "item_id": item_id,
                "item_type": item_type,
                "item_status": item_status,
                "utt_id": u.utt_id,
                "speaker": u.speaker_name or u.speaker_id or "SPEAKER_UNKNOWN",
                "start_s": start_s,
                "end_s": end_s,
                "start_label": start_label,
                "end_label": end_label,
                "quote": u.text,
                "evidence_role": role,
                "verification_label": verification_label,
                "verifier_versions": verifier_versions,
            })

    return appendix


@router.post("/meetings/{meeting_id}/approve")
def approve_meeting_review(
    meeting_id: str,
    request: ApproveReviewRequest,
):
    """
    Explicitly approve and lock the Minutes of Meeting.
    Freezes the approved snapshot into the database, records the reviewer identity
    in the audit log, and generates the approved PDF artifact.
    Idempotent: re-approving an already-approved draft returns the current snapshot.
    """
    validate_file_id(meeting_id)
    if not request.reviewer or not request.reviewer.strip():
        raise HTTPException(status_code=422, detail="Reviewer name is required.")

    draft = _db.init_review_draft(meeting_id)

    # Idempotent return
    if draft["draft_status"] == "approved":
        latest = _db.get_latest_approved_snapshot(meeting_id)
        if latest:
            return {
                "status": "approved",
                "message": "Meeting is already approved.",
                "snapshot": latest,
                "mom": latest["mom_payload"],
            }

    storage = get_storage(meeting_id)
    view = build_claims_view(meeting_id)
    claims_by_id = {c["claim_id"]: c for c in view["claims"]}
    utterances = _db.get_utterances(meeting_id)
    utts_by_id = {u.utt_id: u for u in utterances}
    duration = max((u.end for u in utterances), default=0.0)
    participants = sorted({
        str(u.speaker_name or u.speaker_id)
        for u in utterances
        if (u.speaker_name or u.speaker_id) not in {None, "", "SPEAKER_UNKNOWN", "UNKNOWN"}
    })
    topics = _load_topics(storage)
    extraction = view["extraction"] or {}
    verification_run = view["verification_run"] or {}

    items = draft.get("items") or []

    decisions = []
    action_items = []
    excluded_items = []

    for it in items:
        if it.get("is_excluded"):
            excluded_items.append(it)
            continue

        orig = claims_by_id.get(it["original_claim_id"]) if it.get("original_claim_id") else None
        is_edited = it.get("review_status") == "edited"
        is_added = it.get("review_status") == "added"

        evidence_ids = it.get("evidence_ids") or []
        first_ev = utts_by_id.get(evidence_ids[0]) if evidence_ids else None
        spk = (first_ev.speaker_name or first_ev.speaker_id) if first_ev else None
        tstamp = first_ev.start if first_ev else None

        if is_edited:
            status_label = "Human Edited / Reviewer Approved"
            is_exportable = it.get("state") == "accepted"
        elif is_added:
            status_label = "Reviewer Added & Approved"
            is_exportable = it.get("state") == "accepted"
        else:
            status_label = orig["verification"]["final"]["label"] if orig else "unverified"
            is_exportable = orig["exportable"] if orig else False

        warnings = [
            r["message"] for r in (orig["verification"]["final"]["reasons"] if orig and orig.get("verification") else [])
            if r.get("severity") == "warning"
        ]

        if it["item_type"] == "decision":
            if is_exportable:
                decisions.append({
                    "claim_id": it["item_id"],
                    "text": it["text"],
                    "state": it.get("state") or "accepted",
                    "speaker": spk,
                    "timestamp": tstamp,
                    "verification_status": status_label,
                    "evidence_ids": evidence_ids,
                    "warnings": warnings,
                    "review_status": it.get("review_status"),
                })
            else:
                excluded_items.append(it)
        elif it["item_type"] == "action":
            if is_exportable:
                action_items.append({
                    "claim_id": it["item_id"],
                    "task": it["text"],
                    "owner": it.get("owner") or "Not assigned",
                    "owner_type": it.get("owner_type") or "unassigned",
                    "dueDate": it.get("deadline_resolved") or it.get("deadline_text") or "Not specified",
                    "speaker": spk,
                    "timestamp": tstamp,
                    "verification_status": status_label,
                    "evidence_ids": evidence_ids,
                    "warnings": warnings,
                    "review_status": it.get("review_status"),
                })
            else:
                excluded_items.append(it)

    follow_ups = [
        f"Deadline not specified for '{item['task']}'."
        for item in action_items
        if not item.get("dueDate") or item.get("dueDate") == "Not specified"
    ]
    follow_ups += [
        f"Owner not assigned for '{item['task']}'."
        for item in action_items
        if not item.get("owner") or item.get("owner") == "Not assigned"
    ]

    mom = {
        "file_id": meeting_id,
        "meeting_title": draft.get("meeting_title") or "Meeting Minutes",
        "meeting_date": None,
        "duration_seconds": round(duration, 2),
        "language": _meeting_language(storage, utterances),
        "participants": participants,
        "summary": draft.get("summary") or build_summary(topics, decisions, action_items, len(excluded_items), extraction),
        "summary_method": "reviewer_approved" if draft.get("summary") else "deterministic_overview",
        "discussion_points": build_discussion_points(topics),
        "decisions": decisions,
        "action_items": action_items,
        "needs_review": [],
        "excluded_from_mom": [{"claim_id": ex["item_id"], "text": ex["text"]} for ex in excluded_items],
        "follow_ups": follow_ups,
        "verification": {
            "strategy": "deterministic + retrieval/NLI semantic verification + human reviewer approval",
            "approved": True,
            "approved_by": request.reviewer,
            "approved_at": _db._now_iso(),
            "run_status": (verification_run or {}).get("status"),
            "config_version": (verification_run or {}).get("config_version"),
            "model_version": (verification_run or {}).get("model_version"),
            "confirmed_decisions": len(decisions),
            "reliable_action_items": len(action_items),
            "excluded_items": len(excluded_items),
        },
        "status": "approved",
    }

    mom["evidence_appendix"] = build_evidence_appendix(meeting_id, mom)

    snapshot = _db.approve_review_draft(
        meeting_id,
        reviewer=request.reviewer,
        notes=request.notes,
        mom_payload=mom,
    )

    write_json(storage.path_for("mom", "mom.json"), mom)
    write_json(storage.root / "mom.json", mom)
    pdf_path = build_pdf(meeting_id, mom)

    return {
        "status": "approved",
        "message": "Minutes of Meeting approved and locked successfully.",
        "snapshot_id": snapshot["snapshot_id"],
        "version": snapshot["version"],
        "approved_by": snapshot["approved_by"],
        "approved_at": snapshot["approved_at"],
        "pdf_file": pdf_path.name,
        "mom": mom,
    }


@router.post("/meetings/{meeting_id}/reopen")
def reopen_meeting_review(
    meeting_id: str,
    request: ReopenReviewRequest,
):
    """
    Reopen an approved meeting review for further corrections.
    Preserves previously approved snapshot rows in approved_snapshots.
    """
    validate_file_id(meeting_id)
    if not request.reviewer or not request.reviewer.strip():
        raise HTTPException(status_code=422, detail="Reviewer name is required.")
    if not request.reason or not request.reason.strip():
        raise HTTPException(status_code=422, detail="Reopening reason is required.")

    draft = _db.get_review_draft(meeting_id)
    if not draft or draft["draft_status"] != "approved":
        raise HTTPException(
            status_code=409,
            detail="Review draft is not approved; cannot reopen.",
        )

    try:
        updated_draft = _db.reopen_review_draft(
            meeting_id,
            reviewer=request.reviewer,
            reason=request.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return _build_review_response(meeting_id)


@router.get("/meetings/{meeting_id}/audit-log")
def get_meeting_audit_log(meeting_id: str):
    """Retrieve full chronological audit history of review actions."""
    validate_file_id(meeting_id)
    return {
        "meeting_id": meeting_id,
        "audit_log": _db.get_review_audit_log(meeting_id),
    }


@router.get("/meetings/{meeting_id}/export/json")
def export_mom_json(meeting_id: str, preview: bool = False):
    """Export the approved MoM in JSON format from the persisted snapshot."""
    validate_file_id(meeting_id)
    snapshot = _db.get_latest_approved_snapshot(meeting_id)
    if snapshot is not None:
        mom = dict(snapshot["mom_payload"])
        mom["evidence_appendix"] = build_evidence_appendix(meeting_id, mom)
        return mom

    if preview:
        mom = create_mom(meeting_id)
        if "evidence_appendix" not in mom:
            mom["evidence_appendix"] = build_evidence_appendix(meeting_id, mom)
        return mom

    raise HTTPException(
        status_code=409,
        detail="MoM has not been approved yet. Approve minutes before exporting, or pass ?preview=true to preview.",
    )


@router.get("/meetings/{meeting_id}/audio")
def get_meeting_audio(meeting_id: str):
    """Stream or download audio file for the meeting with Range support."""
    validate_file_id(meeting_id)
    storage = get_storage(meeting_id)

    candidates = [
        p for p in storage.recording.glob("recording.*")
        if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS
    ]
    if not candidates:
        candidates = [
            p for p in storage.audio.glob("*.wav")
            if p.is_file() and not p.name.startswith("chunk_")
        ]
    if not candidates:
        candidates = [
            p for p in storage.root.glob("recording.*")
            if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS
        ]

    if not candidates:
        raise HTTPException(
            status_code=404,
            detail="Audio recording not available for this meeting.",
        )

    audio_file = candidates[0]
    ext = audio_file.suffix.lower()
    media_type = "audio/wav" if ext == ".wav" else "audio/mp4"
    if ext == ".mp3":
        media_type = "audio/mpeg"

    return FileResponse(
        path=str(audio_file),
        media_type=media_type,
        filename=audio_file.name,
    )


# ============================================================
# TOPIC → DISCUSSION POINTS
# ============================================================

TOPIC_FILLER_WORDS = {
    "exactly",
    "okay",
    "ok",
    "yes",
    "yeah",
    "right",
    "well",
    "sure",
    "actually",
    "basically",
    "general discussion",
}


def clean_topic_label(
    label: str,
) -> str:

    if not label:
        return "General Discussion"

    parts = [
        clean_text(part)
        for part
        in str(label).split("/")
    ]

    useful_parts = []

    for part in parts:

        if not part:
            continue

        if (
            part.lower()
            in TOPIC_FILLER_WORDS
        ):
            continue

        useful_parts.append(
            part
        )

    if not useful_parts:
        return "General Discussion"

    return " / ".join(
        useful_parts
    )


def build_discussion_points(
    topics,
):

    """
    Key Discussion Points are derived from semantic topics.

    IMPORTANT:
    We intentionally do NOT copy the first six transcript
    sentences anymore.
    """

    points = []

    seen = set()

    for topic in topics:

        label = clean_topic_label(
            topic.get(
                "topic_label",
                "",
            )
        )

        key = label.lower()

        if (
            not label
            or key in seen
        ):
            continue

        seen.add(
            key
        )

        points.append(
            {
                "text":
                    label,

                "topic_id":
                    topic.get(
                        "topic_id"
                    ),

                "source_start":
                    topic.get(
                        "start"
                    ),

                "source_end":
                    topic.get(
                        "end"
                    ),

                "speakers":
                    topic.get(
                        "speakers",
                        [],
                    ),

                "segment_count":
                    topic.get(
                        "segment_count",
                        0,
                    ),

                "evidence_type":
                    "semantic_topic",
            }
        )

    return points


# ============================================================
# DETERMINISTIC OVERVIEW
# ============================================================

def build_summary(
    topics,
    decisions,
    action_items,
    review_count=0,
    extraction=None,
):
    """
    A short, deterministic overview built only from topic labels and verified
    counts. It is not an abstractive summary and never invents content.
    """

    topic_labels = []
    for topic in topics:
        label = clean_topic_label(topic.get("topic_label", ""))
        if label and label not in topic_labels and label != "General Discussion":
            topic_labels.append(label)

    parts = []
    if topic_labels:
        if len(topic_labels) == 1:
            parts.append(f"The meeting focused on {topic_labels[0]}.")
        else:
            parts.append(
                "The meeting covered "
                + ", ".join(topic_labels[:-1])
                + " and "
                + topic_labels[-1]
                + "."
            )
    else:
        parts.append("The meeting contained general discussion.")

    status = (extraction or {}).get("status")
    if status not in ("completed", "no_claims"):
        parts.append(
            "Decision and action-item extraction did not complete"
            + (f" ({(extraction or {}).get('reason')})." if (extraction or {}).get("reason") else ".")
        )
        return " ".join(parts)

    if decisions:
        parts.append(
            "One decision was confirmed by transcript evidence."
            if len(decisions) == 1
            else f"{len(decisions)} decisions were confirmed by transcript evidence."
        )
    else:
        parts.append("No decisions could be confirmed from transcript evidence.")

    if action_items:
        parts.append(
            "One action item was confirmed."
            if len(action_items) == 1
            else f"{len(action_items)} action items were confirmed."
        )
    else:
        parts.append("No action items could be confirmed.")

    if review_count:
        parts.append(
            f"{review_count} item{'s' if review_count != 1 else ''} "
            f"{'were' if review_count != 1 else 'was'} held back for human review."
        )

    if (extraction or {}).get("fallback_reason"):
        parts.append(f"[Degraded extraction: {(extraction or {}).get('fallback_reason')}].")

    return " ".join(parts)


# ============================================================
# CREATE MOM
# ============================================================

def _meeting_language(storage: MeetingStorage, utterances) -> str | None:
    for path in (
        storage.path_for("transcript", "frontend_transcript.json"),
        storage.root / "frontend_transcript.json",
    ):
        if path.exists():
            try:
                language = json.loads(path.read_text(encoding="utf-8")).get("language")
                if language:
                    return language
            except (OSError, json.JSONDecodeError, AttributeError):
                pass
    tags = [tag for u in utterances for tag in (u.lang_tags or [])]
    return max(set(tags), key=tags.count) if tags else None


def create_mom(
    file_id: str,
):
    """
    Create the MoM only from authoritative, verified claim records.

    - verified / verified_with_warning accepted decisions -> confirmed items
      (warnings are kept visible)
    - review / unverified claims -> "held for review", never as confirmed
    - rejected claims -> excluded (counted)
    """

    storage = get_storage(file_id)
    view = build_claims_view(file_id)
    extraction = view["extraction"]
    verification_run = view["verification_run"]

    if extraction is None:
        raise HTTPException(
            status_code=409,
            detail="Extraction has not run. Run /api/extract/{file_id} first.",
        )
    if extraction["status"] in ("completed", "no_claims") and not verification_is_current(file_id):
        raise HTTPException(
            status_code=409,
            detail=(
                "Verified intelligence not found or out of date. "
                "Run /api/verify/{file_id} before generating the MoM."
            ),
        )

    claims = view["claims"]
    decisions = [_decision_item(c) for c in claims if c["claim_type"] == "decision" and c["exportable"]]
    action_items = [_action_item(c) for c in claims if c["claim_type"] == "action" and c["exportable"]]
    needs_review = [
        {
            "claim_id": c["claim_id"],
            "claim_type": c["claim_type"],
            "text": c["text"],
            "state": c["state"],
            "status": c["verification"]["final"]["label"],
            "reasons": [
                r["message"] for r in c["verification"]["final"]["reasons"]
                if r.get("severity") in ("review", "reject")
            ],
            "evidence_ids": c["evidence_ids"],
        }
        for c in claims
        if not c["exportable"] and c["verification"]["final"]["label"] != "rejected"
    ]
    rejected_count = sum(1 for c in claims if c["verification"]["final"]["label"] == "rejected")

    utterances = _db.get_utterances(file_id)
    duration = max((u.end for u in utterances), default=0.0)
    participants = sorted({
        str(u.speaker_name or u.speaker_id)
        for u in utterances
        if (u.speaker_name or u.speaker_id) not in {None, "", "SPEAKER_UNKNOWN", "UNKNOWN"}
    })
    topics = _load_topics(storage)

    follow_ups = [
        f"Deadline not specified for '{item['task']}'."
        for item in action_items
        if not item.get("dueDate")
    ]
    follow_ups += [
        f"Owner not assigned for '{item['task']}'."
        for item in action_items
        if not item.get("owner")
    ]

    counts = view["counts"]
    mom = {
        "file_id": file_id,
        "meeting_title": "Meeting Minutes",
        "meeting_date": None,
        "duration_seconds": round(duration, 2),
        "language": _meeting_language(storage, utterances),
        "participants": participants,
        "summary": build_summary(topics, decisions, action_items, len(needs_review), extraction),
        "summary_method": "deterministic_overview",
        "discussion_points": build_discussion_points(topics),
        "decisions": decisions,
        "action_items": action_items,
        "needs_review": needs_review,
        "follow_ups": follow_ups,
        "extraction": {
            "status": extraction.get("status"),
            "mode_requested": extraction.get("mode_requested"),
            "mode_used": extraction.get("mode_used"),
            "fallback_reason": extraction.get("fallback_reason"),
            "degraded_extraction": bool(extraction.get("fallback_reason")),
            "reason": extraction.get("reason"),
        },
        "intelligence": {
            "semantic_topics": len(topics),
            "confirmed_decisions": len(decisions),
            "reliable_action_items": len(action_items),
        },
        "verification": {
            "strategy": "deterministic + retrieval/NLI semantic verification + fusion",
            "required_for_mom": True,
            "run_status": (verification_run or {}).get("status"),
            "run_reason": (verification_run or {}).get("reason"),
            "config_version": (verification_run or {}).get("config_version"),
            "model_version": (verification_run or {}).get("model_version"),
            "counts": counts,
            "evidence_supported_decisions": len(decisions),
            "evidence_supported_action_items": len(action_items),
            "decisions_with_warnings": sum(1 for d in decisions if d["verification_status"] == "verified_with_warning"),
            "action_items_with_warnings": sum(1 for a in action_items if a["verification_status"] == "verified_with_warning"),
            "held_for_review": len(needs_review),
            "rejected": rejected_count,
            # Kept for older clients of this payload.
            "decisions_excluded_from_mom": sum(1 for c in claims if c["claim_type"] == "decision" and not c["exportable"]),
            "action_items_excluded_from_mom": sum(1 for c in claims if c["claim_type"] == "action" and not c["exportable"]),
        },
        "status": "mom_generated_from_verified_claims",
    }

    mom["evidence_appendix"] = build_evidence_appendix(file_id, mom)

    write_json(storage.path_for("mom", "mom.json"), mom)
    write_json(storage.root / "mom.json", mom)
    return mom


# ============================================================
# PDF UTILITIES
# ============================================================

def safe_pdf(
    value,
) -> str:

    if value is None:
        return "Not specified"

    return (
        str(value)
        .replace(
            "&",
            "&amp;",
        )
        .replace(
            "<",
            "&lt;",
        )
        .replace(
            ">",
            "&gt;",
        )
    )


def format_duration(
    seconds,
) -> str:

    try:

        total = max(
            0,
            int(
                round(
                    float(
                        seconds or 0
                    )
                )
            ),
        )

    except (
        TypeError,
        ValueError,
    ):

        return "Not available"

    minutes, seconds = divmod(
        total,
        60,
    )

    hours, minutes = divmod(
        minutes,
        60,
    )

    if hours:

        return (
            f"{hours}h "
            f"{minutes}m "
            f"{seconds}s"
        )

    return (
        f"{minutes}m "
        f"{seconds}s"
    )


# ============================================================
# PDF GENERATION
# ============================================================

def build_pdf(
    file_id: str,
    mom: dict,
):
    """Build a polished, dynamic Meet2MoM PDF from verified MoM data."""

    storage = get_storage(file_id)
    pdf_path = storage.path_for("mom", "Meet2MoM-Meeting-Minutes.pdf")

    # Brand palette
    NAVY = colors.HexColor("#172554")
    INDIGO = colors.HexColor("#4F46E5")
    INDIGO_LIGHT = colors.HexColor("#EEF2FF")
    SLATE = colors.HexColor("#475569")
    DARK = colors.HexColor("#0F172A")
    LIGHT = colors.HexColor("#F8FAFC")
    BORDER = colors.HexColor("#E2E8F0")
    GREEN = colors.HexColor("#047857")
    GREEN_LIGHT = colors.HexColor("#ECFDF5")
    AMBER = colors.HexColor("#B45309")
    AMBER_LIGHT = colors.HexColor("#FFFBEB")

    styles = getSampleStyleSheet()

    brand_style = ParagraphStyle(
        "Meet2MoMBrand", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=23, leading=27, textColor=colors.white,
        alignment=TA_LEFT, spaceAfter=2,
    )
    brand_subtitle = ParagraphStyle(
        "Meet2MoMBrandSubtitle", parent=styles["BodyText"],
        fontName="Helvetica", fontSize=9, leading=12,
        textColor=colors.HexColor("#C7D2FE"),
    )
    header_right = ParagraphStyle(
        "Meet2MoMHeaderRight", parent=styles["BodyText"],
        fontName="Helvetica-Bold", fontSize=8.5, leading=11,
        textColor=colors.HexColor("#C7D2FE"), alignment=TA_CENTER,
    )
    heading_style = ParagraphStyle(
        "Meet2MoMSection", parent=styles["Heading2"],
        fontName="Helvetica-Bold", fontSize=13, leading=16,
        textColor=NAVY, spaceBefore=11, spaceAfter=7,
    )
    body_style = ParagraphStyle(
        "Meet2MoMBody", parent=styles["BodyText"],
        fontName="Helvetica", fontSize=9.3, leading=13.5,
        textColor=DARK,
    )
    small_style = ParagraphStyle(
        "Meet2MoMSmall", parent=styles["BodyText"],
        fontName="Helvetica", fontSize=8, leading=10.5,
        textColor=SLATE,
    )
    card_label = ParagraphStyle(
        "Meet2MoMCardLabel", parent=styles["BodyText"],
        fontName="Helvetica-Bold", fontSize=7.5, leading=9,
        textColor=SLATE,
    )
    card_value = ParagraphStyle(
        "Meet2MoMCardValue", parent=styles["BodyText"],
        fontName="Helvetica-Bold", fontSize=10.5, leading=13,
        textColor=NAVY,
    )

    story = []

    # Header
    meeting_title = safe_pdf(mom.get("meeting_title") or "Minutes of Meeting")
    header = Table([[ 
        [Paragraph("Meet2MoM", brand_style),
         Paragraph("AI-Powered Meeting Intelligence", brand_subtitle)],
        Paragraph(meeting_title.upper(), header_right),
    ]], colWidths=[125 * mm, 34 * mm])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 11),
        ("RIGHTPADDING", (-1, 0), (-1, 0), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.extend([header, Spacer(1, 8)])

    participants = mom.get("participants") or []
    decisions = mom.get("decisions") or []
    actions = mom.get("action_items") or []
    verification = mom.get("verification") or {}
    language = safe_pdf(mom.get("language") or "Not specified")

    # KPI cards
    metrics = [
        ("DURATION", format_duration(mom.get("duration_seconds"))),
        ("LANGUAGE", language),
        ("PARTICIPANTS", f"{len(participants)} speakers" if participants else "Not specified"),
        ("DECISIONS", f"{len(decisions)} confirmed"),
    ]
    metric_cells = []
    for label, value in metrics:
        metric_cells.append([
            Paragraph(safe_pdf(label), card_label),
            Paragraph(safe_pdf(value), card_value),
        ])
    metrics_table = Table([metric_cells], colWidths=[39.75 * mm] * 4)
    metrics_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.7, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.extend([metrics_table, Spacer(1, 7)])

    # Participants
    participant_text = "  •  ".join(safe_pdf(x) for x in participants) or "Not specified"
    participant_box = Table([[
        Paragraph("<b>Participants</b>", small_style),
        Paragraph(participant_text, small_style),
    ]], colWidths=[27 * mm, 132 * mm])
    participant_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), INDIGO_LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#C7D2FE")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(participant_box)

    # Executive summary
    story.append(Paragraph("Executive Summary", heading_style))
    summary_box = Table([[
        Paragraph(safe_pdf(mom.get("summary") or "No summary available."), body_style)
    ]], colWidths=[159 * mm])
    summary_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(summary_box)

    # Discussion points
    story.append(Paragraph("Key Discussion Points", heading_style))
    discussion_points = mom.get("discussion_points") or []
    if discussion_points:
        rows = []
        cells = []
        for index, item in enumerate(discussion_points, start=1):
            value = item.get("text") if isinstance(item, dict) else item
            cell = Table([[
                Paragraph(f"<b>{index:02d}</b>", ParagraphStyle(
                    f"TopicNumber{index}", parent=small_style,
                    fontName="Helvetica-Bold", textColor=INDIGO,
                )),
                Paragraph(safe_pdf(value), body_style),
            ]], colWidths=[10 * mm, 65 * mm])
            cell.setStyle(TableStyle([
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            cells.append(cell)
        for i in range(0, len(cells), 2):
            row = [cells[i]]
            row.append(cells[i + 1] if i + 1 < len(cells) else "")
            rows.append(row)
        topic_table = Table(rows, colWidths=[79.5 * mm, 79.5 * mm])
        topic_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(topic_table)
    else:
        story.append(Paragraph("No semantic discussion topics were detected.", body_style))

    # Confirmed decisions
    story.append(Paragraph("Confirmed Decisions", heading_style))
    if decisions:
        for index, item in enumerate(decisions, start=1):
            value = item.get("text") if isinstance(item, dict) else item
            status = item.get("verification_status") if isinstance(item, dict) else None
            speaker = item.get("speaker") if isinstance(item, dict) else None
            timestamp = item.get("timestamp") if isinstance(item, dict) else None
            warnings = (item.get("warnings") or []) if isinstance(item, dict) else []

            meta = []
            if speaker:
                meta.append(str(speaker))
            if timestamp is not None:
                meta.append(format_duration(timestamp))
            if item.get("evidence_ids") if isinstance(item, dict) else None:
                meta.append("evidence " + ", ".join(item["evidence_ids"]))
            if status == "verified_with_warning":
                meta.append("verified with warning")
            elif status == "verified":
                meta.append("verified")

            content = safe_pdf(value)
            if meta:
                content += "<br/><font color='#475569' size='8'>" + safe_pdf(" • ".join(meta)) + "</font>"
            for warning in warnings:
                content += "<br/><font color='#B45309' size='8'>⚠ " + safe_pdf(warning) + "</font>"

            decision_box = Table([[
                Paragraph(str(index), ParagraphStyle(
                    f"DecisionBadge{index}", parent=small_style,
                    fontName="Helvetica-Bold", textColor=colors.white,
                    alignment=TA_CENTER,
                )),
                Paragraph(content, body_style),
            ]], colWidths=[10 * mm, 149 * mm])
            decision_box.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), INDIGO),
                ("BACKGROUND", (1, 0), (1, 0), INDIGO_LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#C7D2FE")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.extend([KeepTogether([decision_box]), Spacer(1, 4)])
    else:
        story.append(Paragraph("No confirmed final decisions detected.", body_style))

    # Action items
    story.append(Paragraph("Action Items", heading_style))
    if actions:
        rows = [[
            Paragraph("Task", small_style),
            Paragraph("Owner", small_style),
            Paragraph("Due Date", small_style),
        ]]
        for item in actions:
            due_date = item.get("dueDate") or item.get("deadline_text") or item.get("due_date")
            task = safe_pdf(item.get("task"))
            for warning in item.get("warnings") or []:
                task += "<br/><font color='#B45309' size='7.5'>⚠ " + safe_pdf(warning) + "</font>"
            rows.append([
                Paragraph(task, body_style),
                Paragraph(safe_pdf(item.get("owner") or "Not assigned"), body_style),
                Paragraph(safe_pdf(due_date or "Not specified"), body_style),
            ])
        action_table = Table(rows, colWidths=[88 * mm, 38 * mm, 33 * mm], repeatRows=1)
        action_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(action_table)
    else:
        empty_action = Table([[
            Paragraph("✓", ParagraphStyle(
                "NoActionCheck", parent=body_style,
                fontName="Helvetica-Bold", fontSize=12, textColor=GREEN,
            )),
            Paragraph(
                "<b>No reliable action items detected.</b><br/>"
                "<font color='#475569'>No sufficiently supported task assignment was included in the verified MoM.</font>",
                body_style,
            ),
        ]], colWidths=[10 * mm, 149 * mm])
        empty_action.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), GREEN_LIGHT),
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#A7F3D0")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.append(empty_action)

    # Items held for review: shown, but explicitly NOT part of the confirmed minutes.
    needs_review = mom.get("needs_review") or []
    if needs_review:
        story.append(Paragraph("Held for Review (not confirmed)", heading_style))
        story.append(Paragraph(
            "These items were extracted but could not be confirmed from transcript evidence. "
            "They are not decisions or tasks of record until a person checks them.",
            small_style,
        ))
        for item in needs_review:
            kind = "Decision" if item.get("claim_type") == "decision" else "Action item"
            line = f"<b>{safe_pdf(kind)}:</b> {safe_pdf(item.get('text'))}"
            reasons = item.get("reasons") or []
            if reasons:
                line += "<br/><font color='#475569' size='8'>" + safe_pdf("; ".join(reasons)) + "</font>"
            story.append(Paragraph(line, body_style))
            story.append(Spacer(1, 3))

    # Extraction outcome, when it was not a normal run.
    extraction = mom.get("extraction") or {}
    if extraction.get("status") not in ("completed", "no_claims") or extraction.get("fallback_reason"):
        note = f"Extraction status: {extraction.get('status')}"
        if extraction.get("mode_used"):
            note += f" (extractor used: {extraction.get('mode_used')})"
        if extraction.get("fallback_reason"):
            note += f". Fallback reason: {extraction.get('fallback_reason')}"
        if extraction.get("reason"):
            note += f". {extraction.get('reason')}"
        story.append(Spacer(1, 4))
        story.append(Paragraph(safe_pdf(note), small_style))

    # Verification snapshot
    story.append(Paragraph("Verification Snapshot", heading_style))
    warning_count = int(verification.get("decisions_with_warnings") or 0) + int(verification.get("action_items_with_warnings") or 0)
    held = int(verification.get("held_for_review") or 0)
    rejected = int(verification.get("rejected") or 0)
    verification_text = (
        f"Every item was checked by a deterministic evidence match and a semantic (retrieval + NLI) verifier. "
        f"{len(decisions)} decision(s) and {len(actions)} action item(s) were confirmed; "
        f"{warning_count} of them carry warnings. "
        f"{held} item(s) were held for review and {rejected} rejected; none of those appear as confirmed. "
        f"Verifier status: {verification.get('run_status') or 'unknown'}."
    )
    if verification.get("run_reason"):
        verification_text += f" {verification.get('run_reason')}"
    verification_box = Table([[
        Paragraph(safe_pdf(verification_text), body_style)
    ]], colWidths=[159 * mm])
    verification_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), AMBER_LIGHT if (warning_count or held) else GREEN_LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#FDE68A") if (warning_count or held) else colors.HexColor("#A7F3D0")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(verification_box)

    # Follow-ups, only when present
    follow_ups = mom.get("follow_ups") or []
    if follow_ups:
        story.append(Paragraph("Follow-ups", heading_style))
        for index, item in enumerate(follow_ups, start=1):
            story.append(Paragraph(f"{index}. {safe_pdf(item)}", body_style))

    # Evidence Appendix Section
    appendix = mom.get("evidence_appendix")
    if appendix is None:
        appendix = build_evidence_appendix(file_id, mom)

    if appendix:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Evidence Appendix", heading_style))
        story.append(Paragraph(
            "Verbatim transcript evidence grounding confirmed decisions and action items.",
            small_style,
        ))
        story.append(Spacer(1, 6))

        appendix_col_widths = [18 * mm, 16 * mm, 22 * mm, 24 * mm, 56 * mm, 20 * mm, 18 * mm]
        app_header_style = ParagraphStyle(
            "AppendixHeader", parent=small_style,
            fontName="Helvetica-Bold", textColor=colors.white, fontSize=7.5, leading=9,
        )
        app_cell_style = ParagraphStyle(
            "AppendixCell", parent=small_style,
            fontName="Helvetica", textColor=DARK, fontSize=7.5, leading=9.5,
        )
        app_quote_style = ParagraphStyle(
            "AppendixQuote", parent=small_style,
            fontName="Helvetica-Oblique", textColor=DARK, fontSize=7.5, leading=9.5,
        )

        app_rows = [[
            Paragraph("Item", app_header_style),
            Paragraph("Utterance", app_header_style),
            Paragraph("Speaker", app_header_style),
            Paragraph("Time", app_header_style),
            Paragraph("Quote", app_header_style),
            Paragraph("Verification", app_header_style),
            Paragraph("Verifier", app_header_style),
        ]]

        for row in appendix:
            item_lbl = str(row.get("item_id") or "")
            if len(item_lbl) > 12:
                item_lbl = item_lbl[:10] + "…"
            utt_lbl = str(row.get("utt_id") or "—")
            spk_lbl = str(row.get("speaker") or "—")
            t_lbl = (
                f"{row.get('start_label')}–{row.get('end_label')}"
                if row.get("start_label")
                else "—"
            )
            q_text = safe_pdf(row.get("quote") or "")
            v_lbl = safe_pdf(row.get("verification_label") or "")
            ver_versions = row.get("verifier_versions") or {}
            v_ver = safe_pdf(ver_versions.get("semantic") or ver_versions.get("deterministic") or "v1")

            app_rows.append([
                Paragraph(safe_pdf(item_lbl), app_cell_style),
                Paragraph(safe_pdf(utt_lbl), app_cell_style),
                Paragraph(safe_pdf(spk_lbl), app_cell_style),
                Paragraph(safe_pdf(t_lbl), app_cell_style),
                Paragraph(q_text, app_quote_style),
                Paragraph(v_lbl, app_cell_style),
                Paragraph(v_ver, app_cell_style),
            ])

        app_table = Table(app_rows, colWidths=appendix_col_widths, repeatRows=1)
        app_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]),
            ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(app_table)

    story.extend([
        Spacer(1, 10),
        HRFlowable(width="100%", thickness=0.6, color=BORDER),
        Spacer(1, 4),
        Paragraph(
            "Generated by Meet2MoM • Transcript-grounded meeting intelligence",
            ParagraphStyle(
                "Meet2MoMFooter", parent=small_style,
                fontSize=7.5, alignment=TA_CENTER,
            ),
        ),
    ])

    def add_page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(SLATE)
        canvas.drawRightString(
            A4[0] - 18 * mm,
            9 * mm,
            f"Page {doc.page}",
        )
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=A4,
        rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
        title="Meet2MoM - Minutes of Meeting",
        author="Meet2MoM",
    )

    try:
        doc.build(
            story,
            onFirstPage=add_page_number,
            onLaterPages=add_page_number,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"PDF generation failed: {exc}",
        ) from exc

    if pdf_path.exists():
        try:
            shutil.copy2(pdf_path, storage.root / "Meet2MoM-Meeting-Minutes.pdf")
        except OSError:
            pass

    return pdf_path


# ============================================================
# GENERATE MOM
# ============================================================

@router.post(
    "/generate-mom/{file_id}"
)
def generate_mom(
    file_id: str,
):

    mom = create_mom(
        file_id
    )

    pdf_path = build_pdf(
        file_id,
        mom,
    )

    return {
        "message":
            "Minutes of Meeting "
            "generated successfully",

        "file_id":
            file_id,

        "mom":
            mom,

        "pdf_file":
            pdf_path.name,

        "status":
            "mom_generated",
    }


# ============================================================
# DOWNLOAD MOM
# ============================================================

@router.get(
    "/download-mom/{file_id}"
)
def download_mom(
    file_id: str,
):

    storage = get_storage(file_id)
    snapshot = _db.get_latest_approved_snapshot(file_id)
    pdf_path = storage.path_for("mom", "Meet2MoM-Meeting-Minutes.pdf")

    if snapshot is not None:
        if not pdf_path.exists():
            pdf_path = build_pdf(file_id, snapshot["mom_payload"])
    else:
        if not pdf_path.exists():
            pdf_path = storage.root / "Meet2MoM-Meeting-Minutes.pdf"

        if not pdf_path.exists():
            mom_path = storage.path_for("mom", "mom.json")
            if not mom_path.exists():
                mom_path = storage.root / "mom.json"

            if mom_path.exists():
                mom = read_json(mom_path, "MoM not found.")
            else:
                mom = create_mom(file_id)

            pdf_path = build_pdf(file_id, mom)

    return FileResponse(
        path=str(
            pdf_path
        ),
        media_type=(
            "application/pdf"
        ),
        filename=(
            "Meet2MoM-Meeting-Minutes.pdf"
        ),
    )