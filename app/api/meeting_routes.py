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

# ============================================================
# REAL INTELLIGENCE SERVICES
# ============================================================

from app.services.intelligence.topic_segmenter import (
    TopicSegmenter,
)

from app.services.intelligence.decision_extractor import (
    DecisionExtractor,
)

from app.services.intelligence.action_item_extractor import (
    ActionItemExtractor,
)

# ============================================================
# TRANSCRIPT-GROUNDED VERIFICATION
# ============================================================

from app.services.verification.evidence_verifier import (
    EvidenceVerifier,
)


router = APIRouter(
    prefix="/api",
    tags=["Meetings"],
)


# ============================================================
# PROJECT PATHS
# ============================================================

from app.core.config import STORAGE_ROOT
from app.core.storage import MeetingStorage, is_valid_meeting_id
from app.core import db as _db
from app.core.jobs import run_pipeline, STAGES

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
    except Exception:
        pass  # DB write failures must not block upload response

    # Queue background pipeline — returns immediately
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
# REAL MEETING INTELLIGENCE
# ============================================================

@router.post(
    "/extract/{file_id}"
)
def extract_meeting_intelligence(
    file_id: str,
):

    storage = get_storage(
        file_id
    )

    transcript_path = storage.path_for(
        "transcript",
        "frontend_transcript.json"
    )

    if not transcript_path.exists():
        transcript_path = (
            storage.root
            / "frontend_transcript.json"
        )

    if not transcript_path.exists():

        raise HTTPException(
            status_code=404,
            detail=(
                "Transcript not found. "
                "Transcribe the meeting first."
            ),
        )

    transcript = read_json(
        transcript_path,
        "Transcript not found.",
    )

    segments = transcript.get(
        "segments",
        [],
    )

    if (
        not isinstance(
            segments,
            list,
        )
        or not segments
    ):

        raise HTTPException(
            status_code=422,
            detail=(
                "Transcript contains "
                "no usable segments."
            ),
        )

    # --------------------------------------------------------
    # Every meeting gets its OWN intelligence directory.
    # Routed through MeetingStorage.
    # --------------------------------------------------------

    intelligence_dir = storage.intelligence
    intelligence_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:

        # ====================================================
        # 1. SEMANTIC TOPIC SEGMENTATION
        # ====================================================

        topic_segmenter = TopicSegmenter(
            output_dir=str(
                intelligence_dir
            )
        )

        topics_path = (
            topic_segmenter.process(
                str(transcript_path)
            )
        )

        # ====================================================
        # 2. CONTEXT-AWARE DECISION EXTRACTION
        # ====================================================

        decision_extractor = (
            DecisionExtractor(
                output_dir=str(
                    intelligence_dir
                )
            )
        )

        decisions_path = (
            decision_extractor.process(
                str(topics_path)
            )
        )

        # ====================================================
        # 3. CONTEXT-AWARE ACTION ITEM EXTRACTION
        # ====================================================

        action_extractor = (
            ActionItemExtractor(
                output_dir=str(
                    intelligence_dir
                )
            )
        )

        actions_path = (
            action_extractor.process(
                str(transcript_path),
                str(topics_path),
            )
        )

    except FileNotFoundError as exc:

        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except ValueError as exc:

        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Meeting intelligence "
                f"processing failed: {exc}"
            ),
        ) from exc

    # --------------------------------------------------------
    # Load strong intelligence results
    # --------------------------------------------------------

    topics_data = read_json(
        Path(topics_path),
        "Topics output not found.",
    )

    decisions_data = read_json(
        Path(decisions_path),
        "Decisions output not found.",
    )

    actions_data = read_json(
        Path(actions_path),
        "Action items output not found.",
    )

    topics = topics_data.get(
        "topics",
        [],
    )

    final_decisions = (
        decisions_data.get(
            "final_decisions",
            [],
        )
    )

    raw_actions = (
        actions_data.get(
            "action_items",
            [],
        )
    )

    # --------------------------------------------------------
    # Frontend compatibility
    #
    # The frontend/PDF currently expects `dueDate`.
    # Strong ActionItemExtractor stores deadline_text.
    #
    # Preserve all intelligence fields AND expose dueDate.
    # --------------------------------------------------------

    action_items = []

    for item in raw_actions:

        frontend_item = dict(
            item
        )

        frontend_item[
            "dueDate"
        ] = (
            item.get(
                "due_date"
            )
            or item.get(
                "deadline_text"
            )
        )

        action_items.append(
            frontend_item
        )

    extraction = {
        "file_id":
            file_id,

        "topic_count":
            len(topics),

        "decision_count":
            len(
                final_decisions
            ),

        "action_item_count":
            len(
                action_items
            ),

        "topics":
            topics,

        "decisions":
            final_decisions,

        "action_items":
            action_items,

        # Full audit information stays available
        # for future verification/RAG and review UI.
        "decision_metadata":
            decisions_data.get(
                "metadata",
                {},
            ),

        "action_metadata":
            actions_data.get(
                "metadata",
                {},
            ),

        "decision_records":
            decisions_data.get(
                "all_records",
                [],
            ),

        "proposals":
            decisions_data.get(
                "proposals",
                [],
            ),

        "status":
            "extracted",
    }

    write_json(
        storage.path_for(
            "intelligence",
            "extraction.json"
        ),
        extraction,
    )
    write_json(
        storage.root
        / "extraction.json",
        extraction,
    )

    # Any previous verification/MoM is now stale because
    # intelligence has been regenerated.
    stale_files = [
        storage.path_for("verification", "verified_intelligence.json"),
        storage.path_for("verification", "verification_summary.json"),
        storage.path_for("mom", "mom.json"),
        storage.path_for("mom", "Meet2MoM-Meeting-Minutes.pdf"),
        storage.root / "mom.json",
        storage.root / "Meet2MoM-Meeting-Minutes.pdf",
    ]

    for stale_file in stale_files:
        try:
            if stale_file.exists():
                stale_file.unlink()
        except OSError:
            # Do not fail a successful extraction only because an old
            # derived artifact could not be removed.
            pass

    return extraction


# ============================================================
# REAL TRANSCRIPT-GROUNDED VERIFICATION
# ============================================================

@router.post(
    "/verify/{file_id}"
)
def verify_meeting_intelligence(
    file_id: str,
):
    """
    Verify extracted decisions and action items against the original
    speaker-attributed transcript.

    The verifier does not invent new claims. It retrieves transcript
    evidence, recovers speaker/timestamps, applies reliability penalties,
    and classifies claims as:
        - verified
        - verified_with_warning
        - review
        - rejected

    Every meeting gets its own verification directory.
    """

    storage = get_storage(
        file_id
    )

    transcript_path = storage.path_for(
        "transcript",
        "frontend_transcript.json"
    )

    if not transcript_path.exists():
        transcript_path = (
            storage.root
            / "frontend_transcript.json"
        )

    decisions_path = storage.path_for(
        "intelligence",
        "decisions.json"
    )

    action_items_path = storage.path_for(
        "intelligence",
        "action_items.json"
    )

    if not transcript_path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "Transcript not found. "
                "Transcribe the meeting first."
            ),
        )

    if not decisions_path.exists():
        raise HTTPException(
            status_code=409,
            detail=(
                "Decision extraction not found. "
                "Run /api/extract/{file_id} first."
            ),
        )

    if not action_items_path.exists():
        raise HTTPException(
            status_code=409,
            detail=(
                "Action-item extraction not found. "
                "Run /api/extract/{file_id} first."
            ),
        )

    verification_dir = storage.verification
    verification_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        verifier = EvidenceVerifier(
            output_dir=str(
                verification_dir
            )
        )

        verified_path = verifier.process(
            transcript_path=str(
                transcript_path
            ),
            decisions_path=str(
                decisions_path
            ),
            action_items_path=str(
                action_items_path
            ),
        )

    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "Meeting verification failed: "
                f"{exc}"
            ),
        ) from exc

    verified_data = read_json(
        Path(
            verified_path
        ),
        "Verified intelligence output not found.",
    )

    metadata = verified_data.get(
        "metadata",
        {},
    )

    verified_decisions = (
        verified_data.get(
            "verified_decisions",
            [],
        )
        or []
    )

    verified_actions = (
        verified_data.get(
            "verified_action_items",
            [],
        )
        or []
    )

    # Evidence-supported means claims that can be used in the final MoM.
    supported_statuses = {
        "verified",
        "verified_with_warning",
    }

    supported_decisions = [
        item
        for item
        in verified_decisions
        if item.get(
            "verification_status"
        )
        in supported_statuses
    ]

    supported_actions = [
        item
        for item
        in verified_actions
        if item.get(
            "verification_status"
        )
        in supported_statuses
    ]

    warning_count = (
        metadata.get(
            "decisions_verified_with_warning",
            0,
        )
        or 0
    ) + (
        metadata.get(
            "action_items_verified_with_warning",
            0,
        )
        or 0
    )

    overall_status = (
        "verified_with_warnings"
        if warning_count
        else "verified"
    )

    # A new verification invalidates any previously generated MoM/PDF.
    for stale_file in (
        storage.path_for("mom", "mom.json"),
        storage.path_for("mom", "Meet2MoM-Meeting-Minutes.pdf"),
        storage.root / "mom.json",
        storage.root / "Meet2MoM-Meeting-Minutes.pdf",
    ):
        try:
            if stale_file.exists():
                stale_file.unlink()
        except OSError:
            pass

    response = {
        "message":
            "Meeting intelligence verified "
            "against transcript evidence",

        "file_id":
            file_id,

        "status":
            overall_status,

        "decisions": {
            "total":
                len(
                    verified_decisions
                ),

            "evidence_supported":
                len(
                    supported_decisions
                ),

            "verified_clean":
                sum(
                    1
                    for item
                    in verified_decisions
                    if item.get(
                        "verification_status"
                    )
                    == "verified"
                ),

            "with_warnings":
                sum(
                    1
                    for item
                    in verified_decisions
                    if item.get(
                        "verification_status"
                    )
                    == "verified_with_warning"
                ),

            "review":
                sum(
                    1
                    for item
                    in verified_decisions
                    if item.get(
                        "verification_status"
                    )
                    == "review"
                ),

            "rejected":
                sum(
                    1
                    for item
                    in verified_decisions
                    if item.get(
                        "verification_status"
                    )
                    == "rejected"
                ),
        },

        "action_items": {
            "total":
                len(
                    verified_actions
                ),

            "evidence_supported":
                len(
                    supported_actions
                ),

            "verified_clean":
                sum(
                    1
                    for item
                    in verified_actions
                    if item.get(
                        "verification_status"
                    )
                    == "verified"
                ),

            "with_warnings":
                sum(
                    1
                    for item
                    in verified_actions
                    if item.get(
                        "verification_status"
                    )
                    == "verified_with_warning"
                ),

            "review":
                sum(
                    1
                    for item
                    in verified_actions
                    if item.get(
                        "verification_status"
                    )
                    == "review"
                ),

            "rejected":
                sum(
                    1
                    for item
                    in verified_actions
                    if item.get(
                        "verification_status"
                    )
                    == "rejected"
                ),
        },

        "metadata":
            metadata,

        "verified_decisions":
            verified_decisions,

        "verified_action_items":
            verified_actions,
    }

    write_json(
        storage.path_for(
            "verification",
            "verification_summary.json"
        ),
        response,
    )
    write_json(
        storage.root
        / "verification_summary.json",
        response,
    )

    return response


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
# DETERMINISTIC SUMMARY
# ============================================================

def build_summary(
    topics,
    decisions,
    action_items,
):

    """
    Generates a structured high-level summary without copying
    a long transcript paragraph.

    This is deterministic for now.
    Later this can be replaced by a verified LLM summarizer.
    """

    topic_labels = []

    for topic in topics:

        label = clean_topic_label(
            topic.get(
                "topic_label",
                "",
            )
        )

        if (
            label
            and label
            not in topic_labels
            and label
            != "General Discussion"
        ):
            topic_labels.append(
                label
            )

    summary_parts = []

    if topic_labels:

        if len(topic_labels) == 1:

            summary_parts.append(
                "The meeting focused on "
                f"{topic_labels[0]}."
            )

        else:

            formatted_topics = (
                ", ".join(
                    topic_labels[:-1]
                )
                + " and "
                + topic_labels[-1]
            )

            summary_parts.append(
                "The meeting covered "
                f"{formatted_topics}."
            )

    else:

        summary_parts.append(
            "The meeting contained "
            "general discussion."
        )

    # Add confirmed decision context
    if decisions:

        if len(decisions) == 1:

            summary_parts.append(
                "One confirmed decision "
                "was identified."
            )

        else:

            summary_parts.append(
                f"{len(decisions)} confirmed "
                "decisions were identified."
            )

    else:

        summary_parts.append(
            "No confirmed final decisions "
            "were detected."
        )

    # Add action context
    if action_items:

        if len(action_items) == 1:

            summary_parts.append(
                "One reliable action item "
                "was identified."
            )

        else:

            summary_parts.append(
                f"{len(action_items)} reliable "
                "action items were identified."
            )

    else:

        summary_parts.append(
            "No reliable action items "
            "were detected."
        )

    return " ".join(
        summary_parts
    )


# ============================================================
# CREATE MOM
# ============================================================

def create_mom(
    file_id: str,
):
    """
    Create the final MoM only from transcript-grounded intelligence.

    Topics still come from semantic extraction, but decisions and action
    items must have passed EvidenceVerifier. Items with status `review`
    or `rejected` are intentionally excluded from the final MoM.
    """

    storage = get_storage(
        file_id
    )

    transcript_path = storage.path_for(
        "transcript",
        "frontend_transcript.json"
    )
    if not transcript_path.exists():
        transcript_path = (
            storage.root
            / "frontend_transcript.json"
        )

    transcript = read_json(
        transcript_path,
        "Transcript not found.",
    )

    extraction_path = storage.path_for(
        "intelligence",
        "extraction.json"
    )
    if not extraction_path.exists():
        extraction_path = (
            storage.root
            / "extraction.json"
        )

    extraction = read_json(
        extraction_path,
        (
            "Extraction not found. "
            "Run extraction first."
        ),
    )

    verification_path = storage.path_for(
        "verification",
        "verified_intelligence.json"
    )
    if not verification_path.exists():
        verification_path = (
            storage.root
            / "verification"
            / "verified_intelligence.json"
        )

    if not verification_path.exists():
        raise HTTPException(
            status_code=409,
            detail=(
                "Verified intelligence not found. "
                "Run /api/verify/{file_id} before "
                "generating the MoM."
            ),
        )

    verification = read_json(
        verification_path,
        "Verified intelligence not found.",
    )

    segments = transcript.get(
        "segments",
        [],
    )

    topics = extraction.get(
        "topics",
        [],
    )

    all_verified_decisions = (
        verification.get(
            "verified_decisions",
            [],
        )
        or []
    )

    all_verified_actions = (
        verification.get(
            "verified_action_items",
            [],
        )
        or []
    )

    supported_statuses = {
        "verified",
        "verified_with_warning",
    }

    # --------------------------------------------------------
    # Only transcript-supported decisions enter the MoM.
    # Also prefer verifier-recovered speaker/timestamp anchors
    # over approximate values from the flattened topic extractor.
    # --------------------------------------------------------

    decisions = []

    for item in all_verified_decisions:

        if item.get(
            "verification_status"
        ) not in supported_statuses:
            continue

        decision = dict(
            item
        )

        recovered_speaker = (
            item.get(
                "proposal_speaker"
            )
        )

        if recovered_speaker not in {
            None,
            "",
            "UNKNOWN",
            "SPEAKER_UNKNOWN",
        }:
            decision[
                "speaker"
            ] = recovered_speaker

        recovered_timestamp = (
            item.get(
                "proposal_timestamp"
            )
        )

        if (
            recovered_timestamp
            is not None
        ):
            decision[
                "timestamp"
            ] = recovered_timestamp

        decisions.append(
            decision
        )

    # --------------------------------------------------------
    # Only transcript-supported action items enter the MoM.
    # --------------------------------------------------------

    action_items = []

    for item in all_verified_actions:

        if item.get(
            "verification_status"
        ) not in supported_statuses:
            continue

        action = dict(
            item
        )

        action[
            "dueDate"
        ] = (
            item.get(
                "dueDate"
            )
            or item.get(
                "due_date"
            )
            or item.get(
                "deadline_text"
            )
        )

        action_items.append(
            action
        )

    # --------------------------------------------------------
    # Meeting duration
    # --------------------------------------------------------

    duration = 0.0

    for segment in segments:

        try:

            duration = max(
                duration,
                float(
                    segment.get(
                        "end",
                        0,
                    )
                    or 0
                ),
            )

        except (
            TypeError,
            ValueError,
        ):
            pass

    # --------------------------------------------------------
    # Participants
    # --------------------------------------------------------

    participants = sorted(
        {
            str(
                segment.get(
                    "speaker"
                )
            )

            for segment in segments

            if (
                segment.get(
                    "speaker"
                )

                and segment.get(
                    "speaker"
                )
                not in {
                    "SPEAKER_UNKNOWN",
                    "UNKNOWN",
                }
            )
        }
    )

    discussion_points = (
        build_discussion_points(
            topics
        )
    )

    summary = build_summary(
        topics,
        decisions,
        action_items,
    )

    follow_ups = []

    for item in action_items:

        task = item.get(
            "task"
        )

        due = (
            item.get(
                "dueDate"
            )
            or item.get(
                "deadline_text"
            )
            or item.get(
                "due_date"
            )
        )

        if (
            task
            and not due
        ):

            follow_ups.append(
                "Deadline not specified "
                f"for '{task}'."
            )

    verification_metadata = (
        verification.get(
            "metadata",
            {},
        )
        or {}
    )

    warning_decisions = sum(
        1
        for item
        in decisions
        if item.get(
            "verification_status"
        )
        == "verified_with_warning"
    )

    warning_actions = sum(
        1
        for item
        in action_items
        if item.get(
            "verification_status"
        )
        == "verified_with_warning"
    )

    excluded_decisions = (
        len(
            all_verified_decisions
        )
        - len(
            decisions
        )
    )

    excluded_actions = (
        len(
            all_verified_actions
        )
        - len(
            action_items
        )
    )

    mom = {
        "file_id":
            file_id,

        "meeting_title":
            "Meeting Minutes",

        "meeting_date":
            None,

        "duration_seconds":
            round(
                duration,
                2,
            ),

        "language":
            transcript.get(
                "language"
            ),

        "participants":
            participants,

        "summary":
            summary,

        "discussion_points":
            discussion_points,

        "decisions":
            decisions,

        "action_items":
            action_items,

        "follow_ups":
            follow_ups,

        "intelligence": {
            "semantic_topics":
                len(
                    topics
                ),

            "confirmed_decisions":
                len(
                    decisions
                ),

            "reliable_action_items":
                len(
                    action_items
                ),

            "decision_metadata":
                extraction.get(
                    "decision_metadata",
                    {},
                ),

            "action_metadata":
                extraction.get(
                    "action_metadata",
                    {},
                ),
        },

        "verification": {
            "strategy":
                verification_metadata.get(
                    "verification_strategy",
                    "transcript_grounded_evidence_matching",
                ),

            "required_for_mom":
                True,

            "evidence_supported_decisions":
                len(
                    decisions
                ),

            "evidence_supported_action_items":
                len(
                    action_items
                ),

            "decisions_with_warnings":
                warning_decisions,

            "action_items_with_warnings":
                warning_actions,

            "decisions_excluded_from_mom":
                excluded_decisions,

            "action_items_excluded_from_mom":
                excluded_actions,

            "metadata":
                verification_metadata,
        },

        "status":
            "mom_generated_from_verified_intelligence",
    }

    write_json(
        storage.path_for("mom", "mom.json"),
        mom,
    )
    write_json(
        storage.root / "mom.json",
        mom,
    )

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
            confidence = item.get("verification_confidence") if isinstance(item, dict) else None
            speaker = item.get("speaker") if isinstance(item, dict) else None
            timestamp = item.get("timestamp") if isinstance(item, dict) else None

            meta = []
            if speaker:
                meta.append(str(speaker))
            if timestamp is not None:
                meta.append(format_duration(timestamp))
            if confidence is not None:
                try:
                    meta.append(f"{float(confidence) * 100:.0f}% evidence confidence")
                except (TypeError, ValueError):
                    pass
            if status == "verified_with_warning":
                meta.append("verified with warning")
            elif status == "verified":
                meta.append("verified")

            content = safe_pdf(value)
            if meta:
                content += "<br/><font color='#475569' size='8'>" + safe_pdf(" • ".join(meta)) + "</font>"

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
            rows.append([
                Paragraph(safe_pdf(item.get("task")), body_style),
                Paragraph(safe_pdf(item.get("owner")), body_style),
                Paragraph(safe_pdf(due_date), body_style),
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

    # Verification snapshot
    story.append(Paragraph("Verification Snapshot", heading_style))
    warning_count = int(verification.get("decisions_with_warnings") or 0) + int(verification.get("action_items_with_warnings") or 0)
    excluded_count = int(verification.get("decisions_excluded_from_mom") or 0) + int(verification.get("action_items_excluded_from_mom") or 0)
    verification_text = (
        f"Transcript-grounded verification was required for this MoM. "
        f"{len(decisions)} decision(s) and {len(actions)} action item(s) were evidence-supported. "
        f"{warning_count} included item(s) carried reliability warnings; "
        f"{excluded_count} unsupported/review item(s) were excluded from the final MoM."
    )
    verification_box = Table([[
        Paragraph(safe_pdf(verification_text), body_style)
    ]], colWidths=[159 * mm])
    verification_box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), AMBER_LIGHT if warning_count else GREEN_LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#FDE68A") if warning_count else colors.HexColor("#A7F3D0")),
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

    storage = get_storage(
        file_id
    )

    pdf_path = storage.path_for(
        "mom",
        "Meet2MoM-Meeting-Minutes.pdf"
    )

    if not pdf_path.exists():
        pdf_path = storage.root / "Meet2MoM-Meeting-Minutes.pdf"

    if not pdf_path.exists():

        mom_path = storage.path_for(
            "mom",
            "mom.json"
        )
        if not mom_path.exists():
            mom_path = storage.root / "mom.json"

        if mom_path.exists():

            mom = read_json(
                mom_path,
                "MoM not found.",
            )

        else:

            mom = create_mom(
                file_id
            )

        pdf_path = build_pdf(
            file_id,
            mom,
        )

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