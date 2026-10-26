"""
Worker presence: lets the API tell whether a scripts/worker.py process is alive.

With JOB_RUNNER=worker the API only queues jobs. If no worker is running, a
queued job never starts and the status page used to look the same as a slow
run. The worker rewrites a small heartbeat file every few seconds for its whole
lifetime (model preload, idle polling and running jobs alike), and the status
endpoints report the job as stalled when the file is missing or old.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

HEARTBEAT_FILE = "worker_heartbeat.json"
HEARTBEAT_INTERVAL_SECONDS = 5.0
# A worker unseen for this long counts as not running.
WORKER_ALIVE_SECONDS = 30.0

NO_WORKER_MESSAGE = (
    "No processing worker is running, so this meeting is waiting in the queue. "
    "Start it with: .\\.venv\\Scripts\\python.exe scripts/worker.py"
)


def heartbeat_path() -> Path:
    from app.core import config

    return Path(config.STORAGE_ROOT) / HEARTBEAT_FILE


def write_heartbeat(path: Optional[Path] = None) -> None:
    path = path or heartbeat_path()
    payload = {"pid": os.getpid(), "seen_at": time.time(),
               "seen_at_iso": datetime.now(timezone.utc).isoformat()}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("Worker heartbeat write failed: %s", exc)


def start_heartbeat_thread(stop: threading.Event, path: Optional[Path] = None) -> threading.Thread:
    """Write the heartbeat now and then every few seconds until stop is set."""
    write_heartbeat(path)

    def loop() -> None:
        while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            write_heartbeat(path)

    thread = threading.Thread(target=loop, name="worker-presence", daemon=True)
    thread.start()
    return thread


def clear_heartbeat(path: Optional[Path] = None) -> None:
    try:
        (path or heartbeat_path()).unlink(missing_ok=True)
    except OSError:
        pass


def worker_status(path: Optional[Path] = None) -> Dict[str, Any]:
    """{"alive": bool, "last_seen": iso or None}."""
    try:
        data = json.loads((path or heartbeat_path()).read_text(encoding="utf-8"))
        seen = float(data.get("seen_at") or 0)
    except (OSError, ValueError, TypeError):
        return {"alive": False, "last_seen": None}
    return {"alive": time.time() - seen <= WORKER_ALIVE_SECONDS,
            "last_seen": data.get("seen_at_iso")}


def queue_notice(state: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    The worker field for a job status response: None unless JOB_RUNNER=worker
    and the job is waiting for a worker (queued).
    """
    from app.core import config

    if (getattr(config, "JOB_RUNNER", "inline") or "inline").strip().lower() != "worker":
        return None
    if state != "queued":
        return None
    status = worker_status()
    if not status["alive"]:
        status["message"] = NO_WORKER_MESSAGE
    return status
