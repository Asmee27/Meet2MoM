"""
Phase 5: per-stage processing timings.

The recorder uses time.monotonic(); tests replace it with a fake clock so
durations are exact. The speech pipeline subprocess is faked by writing the
same stage file the real script writes.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.core import db, jobs
from app.core import stage_timings as st
from app.core.jobs import run_pipeline
from app.core.storage import MeetingStorage
from app.services.pipeline import recorded_pipeline_adapter as adapter
from tests.semantic_fakes import install_semantic_fakes
from tests.test_job_runner import SEGMENTS, _meeting, _stub_stages


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(st.time, "monotonic", c)
    return c


def _rows(meeting_id):
    return {r["stage"]: r for r in db.get_stage_timings(meeting_id)}


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------

def test_completed_stage_is_timed_with_the_monotonic_clock(clock):
    mid, _ = _meeting()
    rec = st.StageRecorder.for_database(mid)
    with rec.stage("transcript_index") as h:
        assert _rows(mid)["transcript_index"]["status"] == "running"   # visible while running
        clock.now += 4.25
        h.detail["utterances"] = 12
    row = _rows(mid)["transcript_index"]
    assert row["status"] == "completed" and row["duration_seconds"] == 4.25
    assert row["detail"] == {"utterances": 12} and row["finished_at"]


def test_failed_stage_is_recorded_and_the_error_propagates(clock):
    mid, _ = _meeting()
    rec = st.StageRecorder.for_database(mid)
    with pytest.raises(RuntimeError, match="disk full"):
        with rec.stage("topic_segmentation"):
            clock.now += 1.5
            raise RuntimeError("disk full")
    row = _rows(mid)["topic_segmentation"]
    assert row["status"] == "failed" and row["duration_seconds"] == 1.5
    assert "RuntimeError: disk full" in row["detail"]["error"]


def test_skipped_stage_never_gets_a_duration(clock):
    mid, _ = _meeting()
    rec = st.StageRecorder.for_database(mid)
    with rec.stage("extraction") as h:
        clock.now += 9
        h.status = "skipped"
    rec.record("pdf_export", "skipped", duration_seconds=0.0)
    rows = _rows(mid)
    assert rows["extraction"]["duration_seconds"] is None
    assert rows["pdf_export"]["duration_seconds"] is None


def test_a_broken_sink_never_interrupts_processing(clock):
    def sink(_rec):
        raise sqlite3.OperationalError("database is locked")

    rec = st.StageRecorder("m", sink=sink)
    with rec.stage("verification"):
        clock.now += 2
    assert rec.stages[-1]["status"] == "completed" and rec.stages[-1]["duration_seconds"] == 2


def test_unknown_status_is_rejected():
    mid, _ = _meeting()
    with pytest.raises(ValueError):
        db.upsert_stage_timing(mid, "r1", "x", "done", seq=1)


def test_schema_v4_adds_the_timings_table(tmp_path):
    path = tmp_path / "old.db"
    db.init_db(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE processing_stage_timings")
        conn.execute("PRAGMA user_version = 3")
    db.init_db(path)
    assert db.get_schema_version(path) == db.SCHEMA_VERSION >= 4
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM processing_stage_timings").fetchone()[0] == 0


def test_subprocess_stage_file_is_imported_under_its_parent(tmp_path):
    mid, _ = _meeting()
    child = st.StageRecorder(mid, file_path=tmp_path / "stage_timings.json")
    child.record("pipeline_startup", "completed", duration_seconds=31.0)
    child.record("transcription", "cached")
    child.begin("speaker_attribution", method="pyannote_diarization")   # subprocess died here

    parent = st.StageRecorder.for_database(mid)
    parent.record("speech_pipeline", "failed", duration_seconds=40.0)
    assert parent.import_file(tmp_path / "stage_timings.json", parent_stage="speech_pipeline") == 3
    assert parent.import_file(tmp_path / "missing.json", parent_stage="speech_pipeline") == 0

    rows = _rows(mid)
    assert rows["pipeline_startup"]["parent_stage"] == "speech_pipeline"
    assert rows["pipeline_startup"]["duration_seconds"] == 31.0
    assert rows["transcription"]["status"] == "cached"
    assert rows["speaker_attribution"]["status"] == "failed"
    assert "stopped during this stage" in rows["speaker_attribution"]["detail"]["error"]


# ---------------------------------------------------------------------------
# Job runner
# ---------------------------------------------------------------------------

def _fake_core_with_stage_file(monkeypatch, storage, *, error=None, cache_hit=False):
    def fake_core(meeting_id, file_path, run_post_transcript=True):
        child = st.StageRecorder(meeting_id, file_path=storage.path_for("metadata", st.STAGE_FILE))
        child.record("pipeline_startup", "completed", duration_seconds=12.0)
        child.record("audio_preparation", "completed", duration_seconds=3.0)
        if error:
            child.begin("transcription", mode="mixed_recording")
            raise error
        child.record("transcription", "completed", duration_seconds=20.0, detail={"model_load_seconds": 4.0})
        child.record("speaker_attribution", "completed", duration_seconds=30.0)
        child.record("transcript_merge", "completed", duration_seconds=1.0)
        storage.path_for("transcript", "meeting_transcript_ready.json").write_text(
            json.dumps({"segments": SEGMENTS}), encoding="utf-8"
        )
        if cache_hit:
            storage.path_for("root", "pipeline_status.json").write_text(
                json.dumps({"cache_hit": True}), encoding="utf-8"
            )
        return {"segments": SEGMENTS}

    monkeypatch.setattr(adapter, "run_recorded_pipeline", fake_core)


def test_successful_job_persists_every_stage(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid, storage = _meeting()
    _stub_stages(monkeypatch, storage)
    _fake_core_with_stage_file(monkeypatch, storage)

    run_pipeline(mid, Path("recording.wav"))

    assert db.get_job_status(mid).state == "done"
    rows = _rows(mid)
    assert rows["speech_pipeline"]["status"] == "completed"
    for child in ("pipeline_startup", "audio_preparation", "transcription", "speaker_attribution", "transcript_merge"):
        assert rows[child]["parent_stage"] == "speech_pipeline"
    assert rows["transcription"]["detail"]["model_load_seconds"] == 4.0
    assert rows["verification"]["status"] == "completed"
    for child in ("evidence_retrieval", "nli_model_load", "nli_verification"):
        assert rows[child]["parent_stage"] == "verification"
        assert rows[child]["status"] == "completed" and rows[child]["duration_seconds"] is not None
    for on_demand in ("minutes_generation", "pdf_export"):
        assert rows[on_demand]["status"] == "skipped" and rows[on_demand]["duration_seconds"] is None


def test_failed_speech_pipeline_stops_the_timeline_honestly(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid, storage = _meeting()
    _stub_stages(monkeypatch, storage)
    _fake_core_with_stage_file(monkeypatch, storage, error=RuntimeError("asr crashed"))

    run_pipeline(mid, Path("recording.wav"))

    job = db.get_job_status(mid)
    assert job.state == "failed"
    rows = _rows(mid)
    assert rows["speech_pipeline"]["status"] == "failed"
    assert rows["audio_preparation"]["status"] == "completed"
    assert rows["transcription"]["status"] == "failed"
    # Nothing after the failure is reported, let alone as completed.
    assert not {"transcript_merge", "verification", "minutes_generation"} & set(rows)


def test_stale_stage_file_from_an_earlier_run_is_never_imported(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid, storage = _meeting()
    stale = storage.path_for("metadata", st.STAGE_FILE)
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(json.dumps([{"stage": "transcription", "status": "completed", "seq": 1,
                                  "duration_seconds": 999.0}]), encoding="utf-8")
    _stub_stages(monkeypatch, storage)               # this fake core writes no stage file

    run_pipeline(mid, Path("recording.wav"))
    assert "transcription" not in _rows(mid)


def test_pipeline_cache_hit_marks_the_speech_pipeline_cached(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid, storage = _meeting()
    _stub_stages(monkeypatch, storage)
    _fake_core_with_stage_file(monkeypatch, storage, cache_hit=True)

    run_pipeline(mid, Path("recording.wav"))
    assert _rows(mid)["speech_pipeline"]["status"] == "cached"


def test_status_endpoint_adds_stage_timings_without_changing_existing_fields(monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.main import app

    install_semantic_fakes(monkeypatch)
    mid, storage = _meeting()
    _stub_stages(monkeypatch, storage)
    _fake_core_with_stage_file(monkeypatch, storage)
    run_pipeline(mid, Path("recording.wav"))

    body = TestClient(app).get(f"/api/meetings/{mid}/status").json()
    assert body["state"] == "done" and body["stage"] == "done" and body["progress"] == 1.0
    timings = body["stage_timings"]
    names = [s["stage"] for s in timings["stages"]]
    assert names.index("speech_pipeline") < names.index("transcription") < names.index("verification")
    top = [s for s in timings["stages"]
           if not s["parent_stage"] and s["duration_seconds"] is not None
           and s["stage"] not in ("minutes_generation", "pdf_export")]
    assert timings["processing_seconds"] == pytest.approx(sum(s["duration_seconds"] for s in top))
    assert timings["running"] is False


def test_status_endpoint_without_timings_stays_backward_compatible():
    from fastapi.testclient import TestClient
    from app.api.main import app

    mid, _ = _meeting()
    db.upsert_job_status(jobs.JobStatus(
        meeting_id=mid, stage="asr", progress=0.3, state="running", error=None,
        updated_at=jobs._now(),
    ))
    body = TestClient(app).get(f"/api/meetings/{mid}/status").json()
    assert body["stage"] == "asr" and body["stage_timings"] is None


def test_on_demand_minutes_and_pdf_attach_to_the_latest_run(monkeypatch, clock):
    mid, _ = _meeting()
    rec = st.StageRecorder.for_database(mid)
    rec.record("pdf_export", "skipped", seq=st.ON_DEMAND_SEQ["pdf_export"])
    with st.on_demand_stage(mid, "pdf_export"):
        clock.now += 0.8
    row = _rows(mid)["pdf_export"]
    assert row["status"] == "completed" and row["duration_seconds"] == 0.8

    other, _ = _meeting()                            # processed before Phase 5: no run
    with st.on_demand_stage(other, "pdf_export"):
        pass
    assert db.get_stage_timings(other) == []


# ---------------------------------------------------------------------------
# Stage outcome descriptions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stats, status", [
    ({"status": "completed", "mode_requested": "llm", "mode_used": "llm",
      "llm_provider": "gemini", "llm_model": "gemini-3.8-flash", "llm_requests": 2,
      "llm_provider_seconds": 41.5}, None),
    ({"status": "completed", "mode_requested": "llm", "mode_used": "hybrid",
      "fallback_reason": "LLM extraction failed: LLM request timed out after 60s"}, "fallback"),
    ({"status": "failed", "mode_requested": "llm", "reason": "No indexed transcript utterances"}, "failed"),
    ({"status": "completed", "mode_requested": "rules", "mode_used": "rules"}, None),
])
def test_extraction_stage_states_the_extractor_actually_used(stats, status):
    h = st.StageHandle()
    adapter._describe_extraction(h, stats)
    assert h.status == status
    if stats.get("mode_used") == "llm":
        assert h.detail["llm_model"] == "gemini-3.8-flash" and h.detail["llm_provider_seconds"] == 41.5
    if status == "fallback":
        assert "timed out" in h.detail["fallback_reason"]


def test_unavailable_semantic_verifier_is_a_fallback_not_a_success():
    mid, _ = _meeting()
    rec = st.StageRecorder.for_database(mid)
    with st.use_recorder(rec):
        with rec.stage("verification") as h:
            jobs._describe_verification(h, {
                "status": "semantic_unavailable", "reason": "OSError: model missing",
                "timings": {"retrieval_index_seconds": 1.0},
            })
    rows = _rows(mid)
    assert rows["verification"]["status"] == "fallback"
    assert rows["nli_verification"]["status"] == "skipped"
    assert rows["nli_verification"]["duration_seconds"] is None
