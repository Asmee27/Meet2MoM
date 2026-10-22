"""
Per-stage processing timings (Phase 5).

StageRecorder times each pipeline stage with time.monotonic() and saves it as
soon as it starts and ends, so a running or failed stage is visible too.
Statuses:

    running    the stage has started and not finished
    completed  the stage ran and succeeded
    failed     the stage raised; the job fails at this stage
    fallback   the stage finished in a degraded way (for example extraction
               used the local extractor instead of the LLM)
    cached     the stage's result was reused instead of recomputed
    skipped    the stage did not run; it has no duration (never a fake 0 s)

Saving is best effort: a database or file error is logged and never
interrupts processing, recording or verification.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

# The recorded-pipeline subprocess writes its stages here; the job runner
# imports them into the database after the subprocess ends.
STAGE_FILE = "stage_timings.json"
RUN_ID_ENV = "M2M_STAGE_RUN_ID"

_MAX_ERROR_CHARS = 300


def new_run_id() -> str:
    """Unique per run and sortable by start time."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:6]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StageHandle:
    """Lets the timed code mark its outcome and add details."""

    def __init__(self) -> None:
        self.status: Optional[str] = None
        self.detail: Dict[str, Any] = {}


class StageRecorder:
    def __init__(
        self,
        meeting_id: str,
        run_id: Optional[str] = None,
        *,
        sink: Optional[Callable[[Dict[str, Any]], None]] = None,
        file_path: Optional[Path] = None,
        seq_start: int = 0,
    ) -> None:
        self.meeting_id = meeting_id
        self.run_id = run_id or new_run_id()
        self.sink = sink
        self.file_path = Path(file_path) if file_path else None
        self.stages: List[Dict[str, Any]] = []
        self._seq = seq_start
        self._current = None

    @classmethod
    def for_database(cls, meeting_id: str, run_id: Optional[str] = None, **kwargs: Any) -> "StageRecorder":
        from app.core import db

        def sink(rec: Dict[str, Any]) -> None:
            db.upsert_stage_timing(meeting_id, rec["run_id"], rec["stage"], rec["status"], **{
                k: rec.get(k) for k in (
                    "seq", "parent_stage", "started_at", "finished_at", "duration_seconds", "detail",
                )
            })

        return cls(meeting_id, run_id, sink=sink, **kwargs)

    # ------------------------------------------------------------------
    def _save(self, rec: Dict[str, Any]) -> None:
        self.stages = [s for s in self.stages if s["stage"] != rec["stage"]] + [rec]
        if self.file_path is not None:
            try:
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
                self.file_path.write_text(json.dumps(self.stages, indent=2), encoding="utf-8")
            except Exception as exc:
                logger.warning("Could not write stage timings file: %s", type(exc).__name__)
        if self.sink is not None:
            try:
                self.sink(rec)
            except Exception as exc:
                logger.warning(
                    "Could not save timing for stage %s of %s: %s",
                    rec["stage"], self.meeting_id, type(exc).__name__,
                )

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def record(
        self,
        stage: str,
        status: str,
        *,
        duration_seconds: Optional[float] = None,
        parent_stage: Optional[str] = None,
        detail: Optional[Dict[str, Any]] = None,
        started_at: Optional[str] = None,
        finished_at: Optional[str] = None,
        seq: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Record a stage that was timed elsewhere (or skipped)."""
        if status in ("skipped",):
            duration_seconds = None
        rec = {
            "run_id": self.run_id,
            "stage": stage,
            "status": status,
            "seq": seq if seq is not None else self._next_seq(),
            "parent_stage": parent_stage,
            "started_at": started_at,
            "finished_at": finished_at or (None if status == "running" else _now_iso()),
            "duration_seconds": None if duration_seconds is None else round(float(duration_seconds), 3),
            "detail": detail or {},
        }
        self._save(rec)
        return rec

    @contextmanager
    def stage(
        self, name: str, *, parent_stage: Optional[str] = None, seq: Optional[int] = None, **detail: Any,
    ) -> Iterator[StageHandle]:
        """Time the block. Marks it failed if it raises, then re-raises."""
        handle = StageHandle()
        handle.detail.update(detail)
        seq = seq if seq is not None else self._next_seq()
        started_at = _now_iso()
        start = time.monotonic()
        self.record(name, "running", parent_stage=parent_stage, detail=dict(handle.detail),
                    started_at=started_at, seq=seq)
        try:
            yield handle
        except BaseException as exc:
            handle.detail["error"] = f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS]
            self.record(
                name, "failed", duration_seconds=time.monotonic() - start, parent_stage=parent_stage,
                detail=handle.detail, started_at=started_at, seq=seq,
            )
            raise
        status = handle.status or "completed"
        self.record(
            name, status,
            duration_seconds=None if status == "skipped" else time.monotonic() - start,
            parent_stage=parent_stage, detail=handle.detail, started_at=started_at, seq=seq,
        )

    # Begin/end form for long scripts where a `with` block is impractical.
    def begin(self, name: str, *, parent_stage: Optional[str] = None, **detail: Any) -> StageHandle:
        if getattr(self, "_current", None):
            self.end()
        handle = StageHandle()
        handle.detail.update(detail)
        seq, started_at = self._next_seq(), _now_iso()
        self._current = (name, seq, time.monotonic(), started_at, handle, parent_stage)
        self.record(name, "running", parent_stage=parent_stage, detail=dict(handle.detail),
                    started_at=started_at, seq=seq)
        return handle

    def end(self, status: Optional[str] = None) -> None:
        current, self._current = getattr(self, "_current", None), None
        if not current:
            return
        name, seq, start, started_at, handle, parent = current
        status = status or handle.status or "completed"
        self.record(
            name, status,
            duration_seconds=None if status == "skipped" else time.monotonic() - start,
            parent_stage=parent, detail=handle.detail, started_at=started_at, seq=seq,
        )

    def fail_current(self, exc: BaseException) -> None:
        current = getattr(self, "_current", None)
        if current:
            current[4].detail["error"] = f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_CHARS]
            self.end("failed")

    def import_file(self, path: Path, *, parent_stage: str) -> int:
        """Persist stages written by the recorded-pipeline subprocess."""
        try:
            items = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0
        except Exception as exc:
            logger.warning("Unreadable stage timings file %s: %s", path, type(exc).__name__)
            return 0
        count = 0
        for item in sorted(items, key=lambda i: i.get("seq", 0)):
            status = item.get("status")
            if status == "running":
                # The subprocess ended inside this stage: it did not complete.
                status = "failed"
                item.setdefault("detail", {})["error"] = "The recorded pipeline stopped during this stage."
            if status not in ("completed", "failed", "skipped", "cached", "fallback"):
                continue
            self.record(
                item["stage"], status,
                duration_seconds=item.get("duration_seconds"),
                parent_stage=parent_stage,
                detail=item.get("detail") or {},
                started_at=item.get("started_at"),
                finished_at=item.get("finished_at"),
            )
            count += 1
        return count


# ---------------------------------------------------------------------------
# The recorder of the run in progress, so that shared code (the post-transcript
# stages, MoM and PDF generation) can time itself without new parameters.
# ---------------------------------------------------------------------------

from contextvars import ContextVar  # noqa: E402

_CURRENT: "ContextVar[Optional[StageRecorder]]" = ContextVar("m2m_stage_recorder", default=None)


@contextmanager
def use_recorder(recorder: StageRecorder) -> Iterator[StageRecorder]:
    token = _CURRENT.set(recorder)
    try:
        yield recorder
    finally:
        _CURRENT.reset(token)


def current_recorder() -> Optional[StageRecorder]:
    return _CURRENT.get()


@contextmanager
def timed_stage(name: str, **detail: Any) -> Iterator[StageHandle]:
    """Time a stage of the current run; a no-op when no run is being recorded."""
    recorder = _CURRENT.get()
    if recorder is None:
        yield StageHandle()
        return
    with recorder.stage(name, **detail) as handle:
        yield handle


# Fixed positions for stages that also run on demand, after processing.
ON_DEMAND_SEQ = {"minutes_generation": 900, "pdf_export": 910}


@contextmanager
def on_demand_stage(meeting_id: str, name: str, **detail: Any) -> Iterator[StageHandle]:
    """
    Time MoM or PDF generation and attach it to the meeting's latest run.
    Meetings processed before Phase 5 have no run and are not recorded.
    """
    recorder = _CURRENT.get()
    if recorder is not None and recorder.meeting_id == meeting_id:
        with recorder.stage(name, seq=ON_DEMAND_SEQ.get(name), **detail) as handle:
            yield handle
        return
    run_id = None
    try:
        from app.core import db

        run_id = db.latest_stage_run_id(meeting_id)
    except Exception as exc:
        logger.warning("Could not look up the processing run for %s: %s", meeting_id, type(exc).__name__)
    if not run_id:
        yield StageHandle()
        return
    recorder = StageRecorder.for_database(meeting_id, run_id)
    with recorder.stage(name, seq=ON_DEMAND_SEQ.get(name), **detail) as handle:
        yield handle


def timings_view(meeting_id: str) -> Optional[Dict[str, Any]]:
    """
    The latest run's stages for API responses. processing_seconds adds up the
    top-level pipeline stages that actually ran (sub-stages are a breakdown
    of their parent, and on-demand MoM / PDF work is reported separately).
    """
    try:
        from app.core import db

        stages = db.get_stage_timings(meeting_id)
    except Exception as exc:
        logger.warning("Could not read stage timings for %s: %s", meeting_id, type(exc).__name__)
        return None
    if not stages:
        return None
    pipeline = [
        s for s in stages
        if not s["parent_stage"] and s["seq"] < min(ON_DEMAND_SEQ.values())
        and s["duration_seconds"] is not None
    ]
    return {
        "run_id": stages[0]["run_id"],
        "stages": [
            {k: s[k] for k in (
                "stage", "parent_stage", "status", "started_at", "finished_at", "duration_seconds", "detail",
            )}
            for s in stages
        ],
        "processing_seconds": round(sum(s["duration_seconds"] for s in pipeline), 3) if pipeline else None,
        "running": any(s["status"] == "running" for s in stages),
    }
