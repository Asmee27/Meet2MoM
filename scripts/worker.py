r"""
scripts/worker.py
=================
Standalone background worker for processing queued Meet2MoM meetings.
Polls the SQLite jobs table, claims jobs atomically, maintains a live heartbeat,
re-queues stale jobs on startup, and handles SIGINT / SIGTERM gracefully.

Usage (PowerShell):
    $env:JOB_RUNNER="worker"
    .\.venv\Scripts\python.exe scripts/worker.py
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ensure repository root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core import config, db
from app.core.jobs import run_pipeline
from app.core.storage import MeetingStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Worker-%(process)d] %(message)s",
)
logger = logging.getLogger("worker")

_stop_event = threading.Event()
_current_meeting_id: Optional[str] = None
_lock = threading.Lock()


def _signal_handler(signum, frame):
    logger.info("Termination signal %s received. Stopping worker gracefully...", signum)
    _stop_event.set()
    with _lock:
        mid = _current_meeting_id
        if mid:
            logger.warning("Releasing in-progress meeting %s back to queue...", mid)
            try:
                db.release_job(mid)
            except Exception as exc:
                logger.error("Failed to release job %s: %s", mid, exc)


def _heartbeat_loop(meeting_id: str, stop_hb: threading.Event, interval: float = 10.0, db_path: Optional[Path | str] = None):
    """Periodically write heartbeat timestamps to DB while pipeline is running."""
    while not stop_hb.wait(interval):
        try:
            db.update_job_heartbeat(meeting_id, db_path=db_path)
            logger.debug("Heartbeat updated for %s", meeting_id)
        except Exception as exc:
            logger.warning("Heartbeat failed for %s: %s", meeting_id, exc)


def resolve_recording_path(meeting_id: str, file_path_str: Optional[str]) -> Optional[Path]:
    """Find the recording audio file on disk for this meeting."""
    if file_path_str:
        p = Path(file_path_str)
        if p.exists():
            return p

    storage = MeetingStorage(meeting_id)
    # Check recording folder then root folder
    candidates = list(storage.recording.glob("recording.*"))
    if not candidates:
        candidates = list(storage.root.glob("recording.*"))
    if candidates:
        return candidates[0]
    return None


def run_worker_loop(
    poll_interval: Optional[float] = None,
    stale_timeout: Optional[float] = None,
    max_retries: Optional[int] = None,
    run_once: bool = False,
    db_path: Optional[Path | str] = None,
):
    """
    Main worker loop.
    1. Recovers any stale jobs left in 'running' state on worker start.
    2. Polls for queued jobs and atomically claims the next job.
    3. Runs the 12-stage pipeline via run_pipeline while maintaining a heartbeat.
    4. Handles shutdown signals by releasing in-progress work back to queue.
    """
    global _current_meeting_id

    poll_interval = poll_interval if poll_interval is not None else config.WORKER_POLL_INTERVAL_SECONDS
    stale_timeout = stale_timeout if stale_timeout is not None else config.WORKER_STALE_TIMEOUT_SECONDS
    max_retries = max_retries if max_retries is not None else config.WORKER_MAX_RETRIES

    db.init_db(db_path)

    # 1. On worker start, recover stale jobs
    recovered = db.recover_stale_jobs(
        stale_timeout_seconds=stale_timeout,
        max_retries=max_retries,
        db_path=db_path,
    )
    if recovered:
        logger.info("Worker startup: recovered %d stale job(s): %s", len(recovered), recovered)

    logger.info(
        "Worker started. Polling every %.1fs (stale_timeout=%.1fs, max_retries=%d)...",
        poll_interval,
        stale_timeout,
        max_retries,
    )

    worker_id = f"worker_{os.getpid()}"

    while not _stop_event.is_set():
        job = None
        try:
            job = db.claim_next_job(worker_id=worker_id, db_path=db_path)
        except Exception as exc:
            logger.error("Error claiming job: %s", exc)

        if job is None:
            if run_once:
                break
            _stop_event.wait(poll_interval)
            continue

        meeting_id = job.meeting_id
        logger.info("Claimed job for meeting %s (retries: %d)", meeting_id, job.retry_count)

        with _lock:
            _current_meeting_id = meeting_id

        # Find recording file
        recording_path = resolve_recording_path(meeting_id, job.file_path)
        if not recording_path or not recording_path.exists():
            err_msg = f"Recording file not found for meeting {meeting_id}"
            logger.error(err_msg)
            job_obj = db.get_job_status(meeting_id, db_path=db_path)
            if job_obj:
                job_obj.state = "failed"
                job_obj.error = err_msg
                job_obj.updated_at = datetime.now(timezone.utc)
                db.upsert_job_status(job_obj, db_path=db_path)
            with _lock:
                _current_meeting_id = None
            if run_once:
                break
            continue

        # Start background heartbeat thread for this job
        stop_hb = threading.Event()
        hb_thread = threading.Thread(
            target=_heartbeat_loop,
            args=(meeting_id, stop_hb, 5.0, db_path),
            daemon=True,
        )
        hb_thread.start()

        try:
            # Reusing the existing 12-stage pipeline runner
            run_pipeline(meeting_id, recording_path)
            logger.info("Finished processing meeting %s", meeting_id)
        except Exception as exc:
            logger.exception("Pipeline execution failed for %s: %s", meeting_id, exc)
        finally:
            stop_hb.set()
            hb_thread.join(timeout=2.0)
            with _lock:
                _current_meeting_id = None

        if run_once:
            break


def main():
    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    try:
        run_worker_loop()
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user.")
    logger.info("Worker stopped.")


if __name__ == "__main__":
    main()
