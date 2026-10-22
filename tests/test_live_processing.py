"""
Phase 4.3: a finalized LiveKit recording through the existing pipeline,
verification, review, approval and export, plus failure/retry/interruption.

run_pipeline runs for real; only its expensive stages (ffmpeg/Whisper/pyannote
subprocess and extraction) are stubbed exactly as in tests/test_job_runner.py.
Verification uses the deterministic semantic fakes.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.api import live_routes
from app.core import db
from app.core.jobs import run_pipeline
from app.core.storage import MeetingStorage
from app.services.live.recording_audio import (
    RECORDING_FILENAME,
    attribute_utterances,
    build_meeting_recording,
    finalize_segment,
)
from app.services.live.recorder import RecordingSession
from tests.semantic_fakes import install_semantic_fakes
from tests.test_job_runner import _stub_stages
from tests.test_live_recording import (  # noqa: F401  (fixture)
    FRAME,
    SR,
    FakeClock,
    feed,
    join,
    live_api,
    tone,
    wait_until,
)


def record_and_stop(client, roster, manager, room):
    mid, host = join(client, roster, room, "Hana", host=True)
    join(client, roster, room, "Gus")
    assert client.post(f"/api/live/meetings/{mid}/recording/start", headers=host).status_code == 202
    assert wait_until(lambda: manager.active_session(mid) and manager.active_session(mid).capturing)
    feed(manager.active_session(mid), "hana", "TR_H", 440)
    feed(manager.active_session(mid), "gus", "TR_G", 1000)
    state = client.post(f"/api/live/meetings/{mid}/recording/stop", headers=host).json()
    assert state["status"] == "recorded"
    return mid, host


def use_real_pipeline(monkeypatch, mid, **stub_kwargs):
    install_semantic_fakes(monkeypatch)
    _stub_stages(monkeypatch, MeetingStorage(mid), **stub_kwargs)
    calls = []

    def tracked(meeting_id, file_path):
        calls.append(meeting_id)
        run_pipeline(meeting_id, file_path)

    monkeypatch.setattr(live_routes, "run_pipeline", tracked)
    return calls


def test_live_recording_processes_through_review_approval_and_export(live_api, monkeypatch):
    client, roster, manager, _ = live_api
    mid, host = record_and_stop(client, roster, manager, "room-e2e")
    calls = use_real_pipeline(monkeypatch, mid)

    res = client.post(f"/api/live/meetings/{mid}/recording/process", headers=host)
    assert res.status_code == 202
    assert calls == [mid]

    state = client.get(f"/api/live/meetings/{mid}/recording", headers=host).json()
    assert state["status"] == "completed"
    assert state["processing"]["state"] == "done" and state["processing"]["progress"] == 1.0
    assert state["can_process"] is False and state["can_start"] is False
    # Phase 5: per-stage timings travel with the processing state.
    stages = {s["stage"]: s for s in state["processing"]["stage_timings"]["stages"]}
    assert stages["speech_pipeline"]["status"] == "completed"
    assert stages["verification"]["status"] == "completed"
    assert stages["verification"]["duration_seconds"] is not None
    assert stages["pdf_export"]["status"] == "skipped" and stages["pdf_export"]["duration_seconds"] is None
    assert db.get_meeting(mid)["status"] == "completed"
    assert client.get(f"/api/meetings/{mid}/status").json()["state"] == "done"

    # Transcript, extraction and verification are persisted once, for this meeting.
    utts = client.get(f"/api/meetings/{mid}/transcript").json()
    assert "PostgreSQL" in " ".join(u["text"] for u in utts)
    results = db.get_verification_results(mid)
    assert set(results) == {f"{mid}_d00000", f"{mid}_a00000"}
    assert all(set(m) == {"deterministic", "semantic_nli", "fusion"} for m in results.values())
    assert client.get(f"/api/meetings/{mid}/claims").status_code == 200

    # A second Process request never re-runs the pipeline.
    again = client.post(f"/api/live/meetings/{mid}/recording/process", headers=host)
    assert again.status_code == 200 and again.json()["already_submitted"] is True
    retry_done = client.post(f"/api/live/meetings/{mid}/recording/process?retry=true", headers=host)
    assert retry_done.json()["already_submitted"] is True
    assert calls == [mid]

    # Phase 3 review, immutable approval and evidence-backed export.
    review = client.get(f"/api/meetings/{mid}/review")
    assert review.status_code == 200
    assert {i["original_claim_id"] for i in review.json()["items"]} >= {f"{mid}_d00000", f"{mid}_a00000"}
    approve = client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Hana"})
    assert approve.status_code == 200
    mom = client.get(f"/api/meetings/{mid}/export/json").json()
    assert mom["evidence_appendix"]
    assert any("u0000" in str(row) for row in mom["evidence_appendix"])
    pdf = client.get(f"/api/download-mom/{mid}")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    on_demand = {
        s["stage"]: s
        for s in client.get(f"/api/meetings/{mid}/status").json()["stage_timings"]["stages"]
    }
    assert on_demand["minutes_generation"]["status"] == "completed"
    assert on_demand["pdf_export"]["status"] == "completed"

    # Participant mapping is available for the processed meeting.
    speakers = client.get(f"/api/live/meetings/{mid}/speakers", headers=host).json()
    assert len(speakers["utterances"]) == len(utts)


def test_failed_processing_is_visible_and_retry_runs_once(live_api, monkeypatch):
    client, roster, manager, _ = live_api
    mid, host = record_and_stop(client, roster, manager, "room-retry")
    calls = use_real_pipeline(monkeypatch, mid, core_error=RuntimeError("asr crashed"))

    assert client.post(f"/api/live/meetings/{mid}/recording/process", headers=host).status_code == 202
    state = client.get(f"/api/live/meetings/{mid}/recording", headers=host).json()
    assert state["status"] == "failed"
    assert state["processing"]["state"] == "failed"
    assert "asr crashed" in state["processing"]["error"]
    assert db.get_meeting(mid)["status"] == "failed"
    failed = {s["stage"]: s for s in state["processing"]["stage_timings"]["stages"]}
    assert failed["speech_pipeline"]["status"] == "failed"
    assert "asr crashed" in failed["speech_pipeline"]["detail"]["error"]
    assert "verification" not in failed                   # never claimed after a failure

    # Without retry=true a failed job is not resubmitted.
    plain = client.post(f"/api/live/meetings/{mid}/recording/process", headers=host)
    assert plain.status_code == 200 and plain.json()["already_submitted"] is True
    assert calls == [mid]

    _stub_stages(monkeypatch, MeetingStorage(mid))       # the cause is fixed
    retry = client.post(f"/api/live/meetings/{mid}/recording/process?retry=true", headers=host)
    assert retry.status_code == 202
    assert calls == [mid, mid]
    state = client.get(f"/api/live/meetings/{mid}/recording", headers=host).json()
    assert state["status"] == "completed"
    assert state["processing"]["error"] is None
    assert db.get_meeting(mid)["status"] == "completed"
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE meeting_id = ?", (mid,)).fetchone()[0] == 1


def test_inline_job_interrupted_by_restart_becomes_retryable(live_api, monkeypatch):
    client, roster, manager, _ = live_api
    mid, host = record_and_stop(client, roster, manager, "room-interrupt")
    recording = MeetingStorage(mid).recording / RECORDING_FILENAME
    assert db.enqueue_job_once(mid, str(recording))
    db.upsert_job_status(db.get_job_status(mid).model_copy(update={"state": "running", "stage": "asr"}))

    # A process that died mid-job: the next inline API start fails it truthfully.
    assert mid in db.fail_interrupted_inline_jobs()
    state = client.get(f"/api/live/meetings/{mid}/recording", headers=host).json()
    assert state["status"] == "failed"
    assert state["processing"]["error"] == db.INTERRUPTED_JOB_ERROR

    calls = use_real_pipeline(monkeypatch, mid)
    assert client.post(f"/api/live/meetings/{mid}/recording/process?retry=true", headers=host).status_code == 202
    assert calls == [mid]
    assert client.get(f"/api/live/meetings/{mid}/recording", headers=host).json()["status"] == "completed"


def test_worker_mode_startup_leaves_running_jobs_to_the_worker(live_api, monkeypatch):
    from app.api.main import app
    from app.core import config
    from fastapi.testclient import TestClient

    client, roster, manager, _ = live_api
    mid, host = record_and_stop(client, roster, manager, "room-worker-owned")
    assert db.enqueue_job_once(mid, str(MeetingStorage(mid).recording / RECORDING_FILENAME))
    db.upsert_job_status(db.get_job_status(mid).model_copy(update={"state": "running"}))

    monkeypatch.setattr(config, "JOB_RUNNER", "worker")
    with TestClient(app):                                # runs the startup lifespan
        pass
    assert db.get_job_status(mid).state == "running"
    assert db.get_job_status(mid).error is None

    # The worker's own startup recovery still handles it (requeue + retry count).
    recovered = db.recover_stale_jobs(stale_timeout_seconds=0, max_retries=3)
    assert {"meeting_id": mid, "action": "requeued", "retries": 1} in recovered
    job = db.get_job_status(mid)
    assert job.state == "queued" and job.retry_count == 1
    db.upsert_job_status(job.model_copy(update={"state": "done"}))


def test_inline_restart_lifespan_fails_queued_and_running_jobs_only(live_api, monkeypatch):
    from app.api.main import app
    from fastapi.testclient import TestClient

    client, roster, manager, _ = live_api
    mid, host = record_and_stop(client, roster, manager, "room-restart")
    recording = str(MeetingStorage(mid).recording / RECORDING_FILENAME)
    assert db.enqueue_job_once(mid, recording)                       # queued, never started

    others = {}
    for state in ("running", "done", "failed"):
        other = f"restart-{state}-{mid[:8]}"
        db.insert_meeting(other, filename="upload.wav", status="processing")
        db.enqueue_job_once(other, "upload.wav")
        db.upsert_job_status(db.get_job_status(other).model_copy(update={"state": state}))
        others[state] = other

    with TestClient(app):                                # inline-mode API restart
        pass

    for meeting_id in (mid, others["running"]):
        job = db.get_job_status(meeting_id)
        assert job.state == "failed" and job.error == db.INTERRUPTED_JOB_ERROR
        assert db.get_meeting(meeting_id)["status"] == "failed"
    assert db.get_job_status(others["done"]).state == "done"
    assert db.get_job_status(others["failed"]).error is None

    calls = use_real_pipeline(monkeypatch, mid)
    assert client.post(f"/api/live/meetings/{mid}/recording/process?retry=true", headers=host).status_code == 202
    assert calls == [mid]
    assert db.get_job_status(mid).state == "done"
    assert db.get_meeting(mid)["status"] == "completed"


# ---------------------------------------------------------------------------
# meetings.status follows run_pipeline's terminal state (uploads and live)
# ---------------------------------------------------------------------------

def _upload_meeting():
    from uuid import uuid4

    meeting_id = uuid4().hex
    storage = MeetingStorage(meeting_id)
    storage.ensure_dirs()
    db.insert_meeting(meeting_id, filename="upload.wav", status="queued")
    return meeting_id, storage


def test_run_pipeline_marks_meeting_completed(monkeypatch):
    install_semantic_fakes(monkeypatch)
    meeting_id, storage = _upload_meeting()
    _stub_stages(monkeypatch, storage)
    run_pipeline(meeting_id, Path("upload.wav"))
    assert db.get_job_status(meeting_id).state == "done"
    assert db.get_meeting(meeting_id)["status"] == "completed"


def test_run_pipeline_marks_meeting_failed_on_stage_error(monkeypatch):
    meeting_id, storage = _upload_meeting()
    _stub_stages(monkeypatch, storage, core_error=RuntimeError("ffmpeg exploded"))
    run_pipeline(meeting_id, Path("upload.wav"))
    assert db.get_job_status(meeting_id).state == "failed"
    assert db.get_meeting(meeting_id)["status"] == "failed"


def test_run_pipeline_marks_meeting_failed_on_unexpected_error(monkeypatch):
    import app.core.jobs as jobs

    meeting_id, _ = _upload_meeting()

    def boom(*_args):
        raise ValueError("unexpected")

    monkeypatch.setattr(jobs, "_run_all_stages", boom)
    run_pipeline(meeting_id, Path("upload.wav"))
    job = db.get_job_status(meeting_id)
    assert job.state == "failed" and "ValueError: unexpected" in job.error
    assert db.get_meeting(meeting_id)["status"] == "failed"


def test_missing_or_deleted_recording_cannot_be_processed(live_api):
    client, roster, manager, pipeline_calls = live_api
    mid, host = record_and_stop(client, roster, manager, "room-missing")
    (MeetingStorage(mid).recording / RECORDING_FILENAME).unlink()
    res = client.post(f"/api/live/meetings/{mid}/recording/process", headers=host)
    assert res.status_code == 409
    assert db.get_job_status(mid) is None and pipeline_calls == []


def test_truncated_recording_cannot_be_processed(live_api):
    client, roster, manager, pipeline_calls = live_api
    mid, host = record_and_stop(client, roster, manager, "room-truncated")
    path = MeetingStorage(mid).recording / RECORDING_FILENAME
    path.write_bytes(path.read_bytes()[:20])             # corrupt header, no frames
    assert client.post(f"/api/live/meetings/{mid}/recording/process", headers=host).status_code == 409
    assert pipeline_calls == []


def test_speakers_endpoint_requires_processing(live_api):
    client, roster, manager, _ = live_api
    mid, host = record_and_stop(client, roster, manager, "room-speakers")
    assert client.get(f"/api/live/meetings/{mid}/speakers", headers=host).status_code == 404


# ---------------------------------------------------------------------------
# Speaker mapping edge cases (manifest-based attribution)
# ---------------------------------------------------------------------------

def _two_track_recording(tmp_path, alice_frames, bob_frames):
    clock = FakeClock()
    seg_dir = tmp_path / "segments" / "001"
    session = RecordingSession(
        meeting_id="m1", segment_id="s1", segment_dir=seg_dir,
        consent_lookup=lambda _i: True, clock=clock,
    )
    session.arm([("alice", "Alice"), ("bob", "Bob")])
    for i in range(len(alice_frames)):
        clock.t = 100.0 + (i + 1) * 0.01
        session.audio_frame("TA", "alice", alice_frames[i], SR, 1)
        session.audio_frame("TB", "bob", bob_frames[i], SR, 1)
    session.halt("host_stopped")
    session.close_writers()
    result = finalize_segment(seg_dir)
    return build_meeting_recording(
        tmp_path, [{"segment_id": "s1", "seq": 1, "dir": seg_dir, **result}], "m1"
    )


def test_overlapping_speech_is_left_unattributed(tmp_path):
    speech_a = [tone(440, phase=i * FRAME) for i in range(100)]
    speech_b = [tone(1000, phase=i * FRAME) for i in range(100)]
    manifest = _two_track_recording(tmp_path, speech_a, speech_b)
    out = attribute_utterances(tmp_path, manifest, [{"utt_id": "u1", "start": 0.1, "end": 0.9}])
    assert out[0]["participant_identity"] is None


def test_silence_and_out_of_range_spans_are_left_unattributed(tmp_path):
    silence = bytes(FRAME * 2)
    quiet = [silence] * 100
    loud = [tone(440, phase=i * FRAME) for i in range(100)]
    manifest = _two_track_recording(tmp_path, loud[:50] + quiet[:50], quiet)
    out = attribute_utterances(tmp_path, manifest, [
        {"utt_id": "speech", "start": 0.05, "end": 0.45},
        {"utt_id": "silence", "start": 0.6, "end": 0.95},
        {"utt_id": "beyond", "start": 5.0, "end": 6.0},
    ])
    assert [u["participant_identity"] for u in out] == ["alice", None, None]
    assert out[0]["participant_name"] == "Alice"


def test_weakly_dominant_speaker_is_not_guessed(tmp_path):
    a = [tone(440, amp=6000, phase=i * FRAME) for i in range(100)]
    b = [tone(1000, amp=4000, phase=i * FRAME) for i in range(100)]   # 1.5x, below the 2x bar
    manifest = _two_track_recording(tmp_path, a, b)
    out = attribute_utterances(tmp_path, manifest, [{"utt_id": "u", "start": 0.1, "end": 0.9}])
    assert out[0]["participant_identity"] is None
    assert np.isclose(out[0]["start"], 0.1)
