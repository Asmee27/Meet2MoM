"""
Regression tests for app.core.jobs.run_pipeline terminal states (Phase 0 P0-1)
and the Phase 1 background integration: after extraction, the job runs the
DB-backed claim verification (deterministic + semantic + fusion) and persists
one result per claim and method.

The expensive stages (ffmpeg/Whisper/pyannote subprocess, topic segmentation)
are stubbed; indexing, claim persistence, verification and fusion are real.
The embedding/NLI models are deterministic fakes (tests/semantic_fakes.py).
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

import app.services.pipeline.recorded_pipeline_adapter as adapter
from app.core import db
from app.core.jobs import run_pipeline
from app.core.storage import MeetingStorage
from app.schemas.claim import ActionItem, Decision
from app.services.transcript.transcript_index import build_transcript_index, persist_transcript_index
from tests.semantic_fakes import install_semantic_fakes

SEGMENTS = [
    {"start": 0.0, "end": 3.0, "speaker": "SPEAKER_00",
     "text": "The coffee machine on the third floor is broken again.",
     "avg_logprob": -0.1, "speaker_attribution_confidence": 0.9},
    {"start": 3.5, "end": 6.0, "speaker": "SPEAKER_00",
     "text": "We decided to use PostgreSQL for the main database.",
     "avg_logprob": -0.1, "speaker_attribution_confidence": 0.9},
    {"start": 6.5, "end": 9.0, "speaker": "SPEAKER_01",
     "text": "Bob will set up the database cluster by Friday.",
     "avg_logprob": -0.1, "speaker_attribution_confidence": 0.9},
    {"start": 9.5, "end": 12.0, "speaker": "SPEAKER_02",
     "text": "Traffic was terrible this morning because of the rain.",
     "avg_logprob": -0.1, "speaker_attribution_confidence": 0.9},
]


def _meeting() -> tuple[str, MeetingStorage]:
    meeting_id = uuid4().hex
    storage = MeetingStorage(meeting_id)
    storage.ensure_dirs()
    db.insert_meeting(meeting_id, filename="synthetic-job.wav")
    return meeting_id, storage


def _stub_stages(monkeypatch, storage: MeetingStorage, *, core_error=None,
                 post_error=None, extraction_status="completed"):
    def fake_core(meeting_id, file_path, run_post_transcript=True):
        if core_error:
            raise core_error
        storage.path_for("transcript", "meeting_transcript_ready.json").write_text(
            json.dumps({"segments": SEGMENTS}), encoding="utf-8"
        )
        return {"segments": SEGMENTS}

    def fake_post(meeting_id, mode=None):
        if post_error:
            raise post_error
        persist_transcript_index(build_transcript_index({"segments": SEGMENTS}, meeting_id), meeting_id)
        if extraction_status == "failed":
            db.upsert_extraction_run(meeting_id, status="failed", reason="No indexed transcript utterances")
            return {"status": "failed", "reason": "No indexed transcript utterances", "accepted": 0}
        db.insert_claim_with_evidence(Decision(
            claim_id=f"{meeting_id}_d00000", meeting_id=meeting_id,
            text="Use PostgreSQL for the main database", evidence=["u00001"], state="accepted",
        ))
        db.insert_claim_with_evidence(ActionItem(
            claim_id=f"{meeting_id}_a00000", meeting_id=meeting_id,
            text="Set up the database cluster", evidence=["u00002"], owner="Bob",
            owner_type="explicit_third_person", deadline_text="by Friday",
        ))
        db.upsert_extraction_run(meeting_id, status="completed", mode_used="rules", accepted=2, dropped=0)
        return {"status": "completed", "accepted": 2, "dropped": 0, "mode_used": "rules"}

    monkeypatch.setattr(adapter, "run_recorded_pipeline", fake_core)
    monkeypatch.setattr(adapter, "run_post_transcript_stages", fake_post)


def test_successful_job_reaches_done_and_persists_verification(monkeypatch):
    install_semantic_fakes(monkeypatch)
    meeting_id, storage = _meeting()
    _stub_stages(monkeypatch, storage)

    run_pipeline(meeting_id, Path("recording.mp4"))

    job = db.get_job_status(meeting_id)
    assert job.state == "done"
    assert job.stage == "done"
    assert job.progress == 1.0
    assert job.error is None

    run = db.get_verification_run(meeting_id)
    assert run["status"] == "completed"
    assert run["claims_total"] == 2
    results = db.get_verification_results(meeting_id)
    assert set(results) == {f"{meeting_id}_d00000", f"{meeting_id}_a00000"}
    for methods in results.values():
        assert set(methods) == {"deterministic", "semantic_nli", "fusion"}
    assert results[f"{meeting_id}_d00000"]["semantic_nli"]["label"] == "supported"
    assert storage.path_for("verification", "verified_intelligence.json").exists()


def test_core_stage_failure_is_recorded_as_failed(monkeypatch):
    meeting_id, storage = _meeting()
    _stub_stages(monkeypatch, storage, core_error=RuntimeError("ffmpeg exploded"))

    run_pipeline(meeting_id, Path("recording.mp4"))

    job = db.get_job_status(meeting_id)
    assert job.state == "failed"
    assert job.stage == "merge"
    assert "ffmpeg exploded" in job.error


def test_failed_extraction_fails_the_job_instead_of_reporting_success(monkeypatch):
    meeting_id, storage = _meeting()
    _stub_stages(monkeypatch, storage, extraction_status="failed")

    run_pipeline(meeting_id, Path("recording.mp4"))

    job = db.get_job_status(meeting_id)
    assert job.state == "failed"
    assert job.stage == "extraction"
    assert "No indexed transcript utterances" in job.error


def test_verification_crash_fails_the_job_at_verification(monkeypatch):
    meeting_id, storage = _meeting()
    _stub_stages(monkeypatch, storage)

    def _crash(meeting_id, **kwargs):
        raise RuntimeError("verifier exploded")

    monkeypatch.setattr("app.services.verification.claim_verification.verify_meeting", _crash)

    run_pipeline(meeting_id, Path("recording.mp4"))

    job = db.get_job_status(meeting_id)
    assert job.state == "failed"
    assert job.stage == "verification"
    assert "verifier exploded" in job.error


def test_unavailable_semantic_model_still_completes_with_explicit_status(monkeypatch):
    from app.services.verification.retrieval_index import RetrievalIndex

    def _missing(self):
        raise OSError("model not downloaded")

    monkeypatch.setattr(RetrievalIndex, "_load_model", _missing)
    meeting_id, storage = _meeting()
    _stub_stages(monkeypatch, storage)

    run_pipeline(meeting_id, Path("recording.mp4"))

    assert db.get_job_status(meeting_id).state == "done"
    run = db.get_verification_run(meeting_id)
    assert run["status"] == "semantic_unavailable"
    assert "model not downloaded" in run["reason"]
    labels = {m["fusion"]["label"] for m in db.get_verification_results(meeting_id).values()}
    assert labels == {"review"}  # nothing is verified without the semantic check


@pytest.mark.parametrize("error", [KeyError("boom"), ValueError("bad transcript")])
def test_unwrapped_exception_cannot_leave_job_running(monkeypatch, error):
    # run_post_transcript_stages is not wrapped by _run_stage; before the fix
    # any exception other than PostTranscriptStageError escaped run_pipeline
    # and the job stayed "running" forever.
    meeting_id, storage = _meeting()
    _stub_stages(monkeypatch, storage, post_error=error)

    run_pipeline(meeting_id, Path("recording.mp4"))

    job = db.get_job_status(meeting_id)
    assert job.state == "failed"
    assert job.stage == "extraction"
    assert type(error).__name__ in job.error
