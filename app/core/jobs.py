"""
app/core/jobs.py
================
JobRunner: orchestrates the full recorded-meeting pipeline in a background
thread, reporting progress after every stage via DB upserts.

Stages (in order):
  uploaded → audio → quality → asr → diarization → merge →
  index → topics → extraction → verification → mom → done

Usage:
    from app.core.jobs import run_pipeline
    run_pipeline(meeting_id, file_path)     # call from BackgroundTasks
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.core.db import get_job_status, init_db, upsert_job_status
from app.schemas.job import JobStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------

STAGES = [
    "uploaded",
    "audio",
    "quality",
    "asr",
    "diarization",
    "merge",
    "index",
    "topics",
    "extraction",
    "verification",
    "mom",
    "done",
]

# Map each stage name to a [0, 1] progress value at completion
_STAGE_PROGRESS: dict[str, float] = {
    stage: round(i / (len(STAGES) - 1), 4)
    for i, stage in enumerate(STAGES)
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _emit(meeting_id: str, stage: str, state: str, error: str | None = None) -> None:
    """Write a JobStatus row to the DB."""
    job = JobStatus(
        meeting_id=meeting_id,
        stage=stage,
        progress=_STAGE_PROGRESS.get(stage, 0.0),
        state=state,
        error=error,
        updated_at=_now(),
    )
    try:
        upsert_job_status(job)
    except Exception as exc:
        logger.error("Failed to persist job status for %s/%s: %s", meeting_id, stage, exc)


# ---------------------------------------------------------------------------
# PUBLIC: run_pipeline
# ---------------------------------------------------------------------------

def run_pipeline(meeting_id: str, file_path: Path) -> None:
    """
    Orchestrate the full recorded-meeting pipeline.

    Designed to be called inside FastAPI BackgroundTasks.  On any exception
    the state is set to "failed" at the failing stage and the function returns
    without re-raising so the worker thread stays alive.

    Progress is emitted to the DB after every stage completes.
    """
    # Ensure the DB schema exists (idempotent)
    try:
        init_db()
    except Exception as exc:
        logger.warning("init_db() failed: %s", exc)

    _emit(meeting_id, "uploaded", "running")

    try:
        _run_all_stages(meeting_id, file_path)
    except _StageError as exc:
        _emit(meeting_id, exc.stage, "failed", str(exc.cause))
        logger.error(
            "Pipeline failed for meeting %s at stage '%s': %s",
            meeting_id,
            exc.stage,
            exc.cause,
        )
    except Exception as exc:
        # Anything raised outside a wrapped stage must still end the job,
        # otherwise the status row stays "running" forever.
        stage = _last_stage(meeting_id)
        _emit(meeting_id, stage, "failed", f"{type(exc).__name__}: {exc}")
        logger.exception(
            "Pipeline failed for meeting %s at stage '%s'", meeting_id, stage
        )


def _last_stage(meeting_id: str) -> str:
    """Return the most recently emitted stage for a meeting."""
    try:
        job = get_job_status(meeting_id)
    except Exception:
        job = None
    return job.stage if job is not None else "uploaded"


class _StageError(Exception):
    def __init__(self, stage: str, cause: Exception):
        self.stage = stage
        self.cause = cause
        super().__init__(str(cause))


def _run_stage(stage: str, fn):
    """Call fn(), wrapping any exception in _StageError."""
    try:
        return fn()
    except Exception as exc:
        raise _StageError(stage, exc) from exc


def _run_all_stages(meeting_id: str, file_path: Path) -> None:
    """
    Execute each pipeline stage sequentially, emitting DB progress after each.

    We import the pipeline adapter lazily inside the function so that import
    errors at module-load time don't crash the whole API.
    """
    # Lazy imports to avoid crash at import time (sentence_transformers DLL issue)
    from app.services.pipeline.recorded_pipeline_adapter import (
        run_recorded_pipeline,
        run_post_transcript_stages,
        PostTranscriptStageError,
    )

    # ------------------------------------------------------------------ #
    # Stages 1-6: audio → asr → diarization → merge                      #
    # These are all handled by run_recorded_pipeline in one subprocess.   #
    # We emit intermediate stage labels as the subprocess runs.           #
    # ------------------------------------------------------------------ #

    for stage in ("audio", "quality", "asr", "diarization", "merge"):
        _emit(meeting_id, stage, "running")

    def _run_core_pipeline():
        return run_recorded_pipeline(meeting_id, file_path, run_post_transcript=False)

    transcript = _run_stage("merge", _run_core_pipeline)
    _emit(meeting_id, "merge", "running")  # completed

    for stage in ("index", "topics", "extraction"):
        _emit(meeting_id, stage, "running")
    try:
        extraction_stats = run_post_transcript_stages(meeting_id)
    except PostTranscriptStageError as exc:
        raise _StageError(exc.stage, exc.cause) from exc
    if extraction_stats.get("status") == "failed":
        raise _StageError(
            "extraction",
            RuntimeError(extraction_stats.get("reason") or "Extraction failed"),
        )
    logger.info(
        "Extraction for %s: status=%s mode_used=%s accepted=%d dropped=%d",
        meeting_id,
        extraction_stats.get("status"),
        extraction_stats.get("mode_used"),
        extraction_stats.get("accepted", 0),
        extraction_stats.get("dropped", 0),
    )
    _emit(meeting_id, "extraction", "running")

    # ------------------------------------------------------------------ #
    # Stage 10: verification                                               #
    # Deterministic + semantic (retrieval + NLI) + fusion, persisted per   #
    # claim in verification_results.                                       #
    # ------------------------------------------------------------------ #

    _emit(meeting_id, "verification", "running")

    def _run_verification():
        from app.services.pipeline.intelligence_service import ensure_verification

        return ensure_verification(meeting_id, force=True)

    verification = _run_stage("verification", _run_verification)
    logger.info(
        "Verification for %s: status=%s counts=%s",
        meeting_id,
        verification.get("status"),
        verification.get("final_counts"),
    )
    _emit(meeting_id, "verification", "running")

    # ------------------------------------------------------------------ #
    # Stage 11: mom                                                        #
    # ------------------------------------------------------------------ #

    _emit(meeting_id, "mom", "running")

    # MoM generation is handled by the /generate-mom endpoint on demand;
    # mark stage done here so status polling advances correctly.
    _emit(meeting_id, "mom", "running")

    # ------------------------------------------------------------------ #
    # Done                                                                 #
    # ------------------------------------------------------------------ #

    _emit(meeting_id, "done", "done")
    logger.info("Pipeline completed successfully for meeting %s", meeting_id)
