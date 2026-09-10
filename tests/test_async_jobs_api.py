"""
tests/test_async_jobs_api.py
============================
Tests for:
- POST /api/upload (or /api/meetings/upload) returns 202 within 2 seconds
- Non-blocking job execution
- GET /api/meetings/{id}/status returns JobStatus
- GET /api/meetings/{id}/transcript returns 404 before index, 200 with utterances after index
- Corrupt/invalid file handling
"""
from __future__ import annotations

import sys
sys.modules["sentence_transformers"] = None

import asyncio
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.api.meeting_routes import (
    upload_meeting,
    get_meeting_status,
    get_meeting_transcript,
)
from app.core import db as _db
from app.core.jobs import STAGES
from app.schemas.job import JobStatus
from app.schemas.utterance import Utterance


@pytest.fixture(autouse=True)
def init_database():
    _db.init_db()


def test_upload_returns_202_under_two_seconds():
    bg_tasks = BackgroundTasks()
    file_content = b"fake-audio-content-for-upload-test"
    upload_file = UploadFile(
        filename="test_audio.mp3",
        file=io.BytesIO(file_content),
    )

    t0 = time.perf_counter()
    response = asyncio.run(upload_meeting(background_tasks=bg_tasks, file=upload_file))
    elapsed = time.perf_counter() - t0

    assert elapsed < 2.0, f"Upload took too long: {elapsed:.3f}s"
    assert isinstance(response, JSONResponse)
    assert response.status_code == 202

    data = json.loads(response.body)
    assert "meeting_id" in data
    assert data["status"] == "queued"
    assert len(data["meeting_id"]) == 32


def test_upload_invalid_extension_rejected():
    bg_tasks = BackgroundTasks()
    upload_file = UploadFile(
        filename="malicious.exe",
        file=io.BytesIO(b"binary"),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(upload_meeting(background_tasks=bg_tasks, file=upload_file))

    assert exc_info.value.status_code == 400


def test_status_endpoint_returns_job_status():
    mid = uuid4().hex
    job = JobStatus(
        meeting_id=mid,
        stage="asr",
        progress=0.33,
        state="running",
        updated_at=datetime.now(timezone.utc),
    )
    _db.upsert_job_status(job)

    result = get_meeting_status(mid)
    assert result["meeting_id"] == mid
    assert result["stage"] == "asr"
    assert result["state"] == "running"
    assert result["progress"] == 0.33


def test_transcript_endpoint_404_before_index():
    mid = uuid4().hex
    # stage is "asr", which is before "index"
    job = JobStatus(
        meeting_id=mid,
        stage="asr",
        progress=0.33,
        state="running",
        updated_at=datetime.now(timezone.utc),
    )
    _db.upsert_job_status(job)

    with pytest.raises(HTTPException) as exc_info:
        get_meeting_transcript(mid)

    assert exc_info.value.status_code == 404
    assert "not yet available" in exc_info.value.detail


def test_transcript_endpoint_200_after_index():
    mid = uuid4().hex
    # Insert meeting & job status at "done"
    _db.insert_meeting(mid, filename="test.mp4")
    job = JobStatus(
        meeting_id=mid,
        stage="done",
        progress=1.0,
        state="done",
        updated_at=datetime.now(timezone.utc),
    )
    _db.upsert_job_status(job)

    # Insert utterances
    utts = [
        Utterance(
            meeting_id=mid,
            utt_id="u00000",
            start=0.0,
            end=2.0,
            speaker_id="Alice",
            text="Hello world",
            lang_tags=["en"],
            script="latin",
            asr_conf=0.95,
            spk_conf=0.90,
        )
    ]
    _db.bulk_insert_utterances(mid, utts)

    result = get_meeting_transcript(mid)
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["utt_id"] == "u00000"
    assert result[0]["text"] == "Hello world"
