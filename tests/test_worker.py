"""
Phase 3 Task 3: Standalone background worker process tests:
1. Atomic job claims (two claims never return the same job, concurrent claims are distinct).
2. Stale job recovery and retry threshold failure.
3. Upload endpoint with JOB_RUNNER=worker enqueues without running inline.
4. Worker processing execution and heartbeat updates.
5. Job release on interruption.
"""
from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core import config, db
from app.core.storage import MeetingStorage
from app.schemas.job import JobStatus
from scripts.worker import run_worker_loop


@pytest.fixture()
def client():
    return TestClient(app)


def test_atomic_claim_in_sequence_never_returns_same_job(tmp_path):
    test_db = tmp_path / "atomic_claim.db"
    db.init_db(test_db)

    # Insert 3 queued jobs
    for i in range(1, 4):
        db.upsert_job_status(
            JobStatus(
                meeting_id=f"m_{i}",
                stage="uploaded",
                progress=0.0,
                state="queued",
                updated_at=datetime.now(timezone.utc) + timedelta(seconds=i),
                file_path=f"storage/m_{i}/recording.wav",
            ),
            db_path=test_db,
        )

    # Claim 1
    job1 = db.claim_next_job(worker_id="w1", db_path=test_db)
    assert job1 is not None
    assert job1.meeting_id == "m_1"
    assert job1.state == "running"
    assert job1.claim_token is not None
    assert "w1_" in job1.claim_token

    # Claim 2
    job2 = db.claim_next_job(worker_id="w2", db_path=test_db)
    assert job2 is not None
    assert job2.meeting_id == "m_2"
    assert job2.state == "running"
    assert job2.meeting_id != job1.meeting_id

    # Claim 3
    job3 = db.claim_next_job(worker_id="w1", db_path=test_db)
    assert job3 is not None
    assert job3.meeting_id == "m_3"
    assert job3.meeting_id not in (job1.meeting_id, job2.meeting_id)

    # Claim 4 (empty queue)
    job4 = db.claim_next_job(worker_id="w2", db_path=test_db)
    assert job4 is None


def test_concurrent_atomic_claims(tmp_path):
    test_db = tmp_path / "concurrent_claim.db"
    db.init_db(test_db)

    # Enqueue 10 jobs
    for i in range(10):
        db.upsert_job_status(
            JobStatus(
                meeting_id=f"concurrent_m_{i}",
                stage="uploaded",
                progress=0.0,
                state="queued",
                updated_at=datetime.now(timezone.utc) + timedelta(milliseconds=i * 10),
            ),
            db_path=test_db,
        )

    def _worker_claim(worker_idx: int):
        return db.claim_next_job(worker_id=f"worker_{worker_idx}", db_path=test_db)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(_worker_claim, range(20)))

    claimed_jobs = [r for r in results if r is not None]
    claimed_ids = [j.meeting_id for j in claimed_jobs]

    # Exactly 10 jobs should have been claimed, with zero duplicates
    assert len(claimed_jobs) == 10
    assert len(set(claimed_ids)) == 10
    # The remaining 10 claims returned None
    assert results.count(None) == 10


def test_stale_job_recovery_and_failure_after_max_retries(tmp_path):
    test_db = tmp_path / "stale_recovery.db"
    db.init_db(test_db)

    now = datetime.now(timezone.utc)
    old_heartbeat = now - timedelta(seconds=400)  # > 300s timeout

    # Enqueue a stuck running job
    db.upsert_job_status(
        JobStatus(
            meeting_id="stuck_meeting",
            stage="asr",
            progress=0.3,
            state="running",
            heartbeat_at=old_heartbeat,
            updated_at=old_heartbeat,
            retry_count=0,
            file_path="storage/stuck/rec.wav",
        ),
        db_path=test_db,
    )

    # First recovery attempt: max_retries = 2
    recovered1 = db.recover_stale_jobs(
        stale_timeout_seconds=300.0,
        max_retries=2,
        db_path=test_db,
    )
    assert len(recovered1) == 1
    assert recovered1[0]["meeting_id"] == "stuck_meeting"
    assert recovered1[0]["action"] == "requeued"
    assert recovered1[0]["retries"] == 1

    job = db.get_job_status("stuck_meeting", db_path=test_db)
    assert job.state == "queued"
    assert job.retry_count == 1
    assert job.heartbeat_at is None
    assert "Recovered from stale running state" in (job.error or "")

    # Worker claims it again
    claimed = db.claim_next_job(worker_id="w1", db_path=test_db)
    assert claimed.meeting_id == "stuck_meeting"

    # Simulate worker crash: make heartbeat old again
    with db.get_conn(test_db) as conn:
        conn.execute(
            "UPDATE jobs SET heartbeat_at = ? WHERE meeting_id = 'stuck_meeting'",
            (old_heartbeat.isoformat(),),
        )

    # Second recovery attempt: should requeue with retry_count = 2
    recovered2 = db.recover_stale_jobs(
        stale_timeout_seconds=300.0,
        max_retries=2,
        db_path=test_db,
    )
    assert len(recovered2) == 1
    assert recovered2[0]["action"] == "requeued"
    assert recovered2[0]["retries"] == 2

    # Worker claims it again
    claimed2 = db.claim_next_job(worker_id="w2", db_path=test_db)
    assert claimed2.meeting_id == "stuck_meeting"

    # Simulate worker crash again: make heartbeat old again
    with db.get_conn(test_db) as conn:
        conn.execute(
            "UPDATE jobs SET heartbeat_at = ? WHERE meeting_id = 'stuck_meeting'",
            (old_heartbeat.isoformat(),),
        )

    # Third recovery attempt: retry_count (3) > max_retries (2) -> must fail
    recovered3 = db.recover_stale_jobs(
        stale_timeout_seconds=300.0,
        max_retries=2,
        db_path=test_db,
    )
    assert len(recovered3) == 1
    assert recovered3[0]["action"] == "failed"
    assert recovered3[0]["retries"] == 3

    final_job = db.get_job_status("stuck_meeting", db_path=test_db)
    assert final_job.state == "failed"
    assert "exceeded maximum retries" in final_job.error


def test_job_runner_worker_upload_enqueues_without_running_inline(client, monkeypatch):
    # Configure JOB_RUNNER = worker
    monkeypatch.setattr(config, "JOB_RUNNER", "worker")

    dummy_content = b"RIFFdummydataforupload1234567890"
    file_payload = ("test_audio.wav", io.BytesIO(dummy_content), "audio/wav")

    response = client.post(
        "/api/upload",
        files={"file": file_payload},
    )
    assert response.status_code == 202
    body = response.json()
    mid = body["meeting_id"]
    assert body["status"] == "queued"

    # Check that job status row exists in DB and is strictly 'queued' (NOT running)
    job = db.get_job_status(mid)
    assert job is not None
    assert job.state == "queued"
    assert job.stage == "uploaded"
    assert job.progress == 0.0


def test_worker_release_job(tmp_path):
    test_db = tmp_path / "release_test.db"
    db.init_db(test_db)

    db.upsert_job_status(
        JobStatus(
            meeting_id="m_release",
            stage="uploaded",
            progress=0.0,
            state="queued",
            updated_at=datetime.now(timezone.utc),
        ),
        db_path=test_db,
    )

    job = db.claim_next_job(worker_id="w1", db_path=test_db)
    assert job.state == "running"

    db.release_job("m_release", db_path=test_db)
    released = db.get_job_status("m_release", db_path=test_db)
    assert released.state == "queued"
    assert released.heartbeat_at is None
    assert released.claim_token is None


def test_worker_loop_run_once(tmp_path, monkeypatch):
    test_db = tmp_path / "worker_loop.db"
    db.init_db(test_db)

    mid = "worker_meeting_run"
    storage = MeetingStorage(mid)
    storage.ensure_dirs()
    dummy_wav = storage.recording / "recording.wav"
    dummy_wav.write_bytes(b"RIFFdummydata1234")

    db.upsert_job_status(
        JobStatus(
            meeting_id=mid,
            stage="uploaded",
            progress=0.0,
            state="queued",
            updated_at=datetime.now(timezone.utc),
            file_path=str(dummy_wav),
        ),
        db_path=test_db,
    )

    mock_pipeline = MagicMock()
    monkeypatch.setattr("scripts.worker.run_pipeline", mock_pipeline)

    run_worker_loop(run_once=True, db_path=test_db)

    assert mock_pipeline.called
    assert mock_pipeline.call_args[0][0] == mid
    assert Path(mock_pipeline.call_args[0][1]) == dummy_wav
