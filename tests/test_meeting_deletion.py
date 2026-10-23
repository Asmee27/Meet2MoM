"""Phase 10: deleting a meeting removes everything derived from it, safely."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core import config, db
from app.core.jobs import run_pipeline
from app.core.storage import MeetingStorage
from app.schemas.job import JobStatus
from app.services.pipeline import recorded_pipeline_adapter as adapter
from tests.scenario_helpers import make_meeting


def _with_files(mid, content_hash=None):
    storage = MeetingStorage(mid)
    storage.ensure_dirs()
    (storage.recording / "recording.wav").write_bytes(b"RIFF fake")
    storage.path_for("intelligence", "llm_cache/x.json").parent.mkdir(parents=True, exist_ok=True)
    storage.path_for("intelligence", "llm_cache/x.json").write_text("{}", encoding="utf-8")
    if content_hash:
        storage.path_for("root", "pipeline_status.json").write_text(
            json.dumps({"content_hash": content_hash}), encoding="utf-8")
        cache = adapter._cache_directory(content_hash)
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "marker").write_text("x", encoding="utf-8")
    return storage


def _count(table, mid, column="meeting_id"):
    with db.get_conn() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (mid,)).fetchone()[0]


def test_delete_removes_rows_files_caches_and_commitments():
    from app.services.ledger import commitment_ledger

    mid = make_meeting([("A", "We decided to ship."), ("B", "I will write the notes by Friday.")])
    h = uuid4().hex
    storage = _with_files(mid, h)
    db.upsert_stage_timing(mid, "r1", "transcription", "completed", seq=1, duration_seconds=1.0)
    commitment_ledger.record_commitments(mid, {"action_items": [
        {"claim_id": f"{mid}_a1", "task": "Write the notes", "evidence_ids": ["u00001"]}]}, None)

    res = TestClient(app).delete(f"/api/meetings/{mid}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["deleted"] and body["storage_folder_removed"] and body["pipeline_cache_removed"]
    assert db.get_meeting(mid) is None and db.get_utterances(mid) == []
    for table in ("processing_stage_timings", "jobs"):
        assert _count(table, mid) == 0
    assert _count("commitments", mid, "source_meeting_id") == 0
    assert not storage.root.exists() and not adapter._cache_directory(h).exists()
    assert TestClient(app).delete(f"/api/meetings/{mid}").status_code == 404


def test_shared_pipeline_cache_is_kept_for_the_other_meeting():
    h = uuid4().hex
    a, b = make_meeting([("A", "Hello.")]), make_meeting([("A", "Hello.")])
    _with_files(a, h)
    _with_files(b, h)
    assert TestClient(app).delete(f"/api/meetings/{a}").json()["pipeline_cache_removed"] is False
    assert adapter._cache_directory(h).exists()


def test_running_job_blocks_deletion():
    mid = make_meeting([("A", "Hello.")])
    db.upsert_job_status(JobStatus(meeting_id=mid, stage="asr", progress=0.3, state="running",
                                   error=None, updated_at=datetime.now(timezone.utc)))
    res = TestClient(app).delete(f"/api/meetings/{mid}")
    assert res.status_code == 409 and "processed" in res.json()["detail"]
    assert db.get_meeting(mid) is not None


def test_queued_job_is_cancelled_and_cannot_be_claimed_or_rerun():
    mid = make_meeting([("A", "Hello.")])
    db.enqueue_job_once(mid, "x.wav")
    assert TestClient(app).delete(f"/api/meetings/{mid}").status_code == 200
    assert db.get_job_status(mid) is None                  # nothing left for a worker to claim
    run_pipeline(mid, Path("x.wav"))                       # a late inline task does nothing
    assert db.get_job_status(mid) is None and db.get_meeting(mid) is None
    assert not MeetingStorage(mid).root.exists()


def test_open_recording_segment_blocks_deletion():
    mid = make_meeting([("A", "Hello.")])
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO live_meetings (meeting_id, room_name, status, created_at) VALUES (?, ?, 'active', ?)",
            (mid, f"room-{mid[:6]}", datetime.now(timezone.utc).isoformat()))
        conn.execute(
            "INSERT INTO recording_segments (segment_id, meeting_id, seq, state, started_at, updated_at) "
            "VALUES (?, ?, 1, 'recording', ?, ?)",
            (uuid4().hex, mid, "now", "now"))
    assert TestClient(app).delete(f"/api/meetings/{mid}").status_code == 409


def test_only_the_owner_can_delete(monkeypatch):
    monkeypatch.setattr(config, "AUTH_REQUIRED", True)

    def client(name):
        c = TestClient(app)
        user = c.post("/api/auth/register", json={
            "email": f"{name}-{uuid4().hex[:6]}@example.test", "password": "long-password",
            "display_name": name}).json()["user"]
        return c, user

    owner_client, owner = client("owner")
    member_client, member = client("member")
    stranger_client, _ = client("stranger")
    mid = make_meeting([("A", "Hello.")])
    db.add_meeting_member(mid, owner["user_id"], "owner")
    db.add_meeting_member(mid, member["user_id"], "member")

    assert TestClient(app).delete(f"/api/meetings/{mid}").status_code == 401
    assert stranger_client.delete(f"/api/meetings/{mid}").status_code == 404
    assert member_client.delete(f"/api/meetings/{mid}").status_code == 403
    assert owner_client.delete(f"/api/meetings/{mid}").status_code == 200
