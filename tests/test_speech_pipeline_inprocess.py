"""
Phase 6: run the speech pipeline inside a long-lived worker and reuse models.

The heavy speech stages are replaced by a small fake module that has the same
run() signature as scripts/run_recorded_pipeline.py, so these tests check the
lifecycle (mode selection, import once, failure recovery, cache flag) without
loading Whisper or pyannote.
"""
from __future__ import annotations

import json
import sys
import threading
import types
from pathlib import Path
from uuid import uuid4

import pytest

from app.core import config, db, model_registry
from app.core.storage import MeetingStorage
from app.services.pipeline import recorded_pipeline_adapter as adapter


@pytest.fixture(autouse=True)
def _clean_registry():
    model_registry.clear_models()
    yield
    model_registry.clear_models()


# ---------------------------------------------------------------------------
# Model reuse
# ---------------------------------------------------------------------------

def test_cached_model_is_built_once_even_under_concurrency():
    built = []

    def factory():
        built.append(1)
        return object()

    results = []
    threads = [threading.Thread(target=lambda: results.append(model_registry.get_cached_model("k", factory)))
               for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(built) == 1 and len({id(r) for r in results}) == 1
    assert model_registry.is_loaded("k")


def test_whisper_model_is_loaded_once_per_process(monkeypatch, tmp_path):
    from app.services.transcription import transcriber

    created = []

    class FakeWhisper:
        def __init__(self, *args, **kwargs):
            created.append(args)

    monkeypatch.setattr(transcriber, "WhisperModel", FakeWhisper)
    monkeypatch.setattr(transcriber, "TRANSCRIPT_DIR", tmp_path)
    first = transcriber.MeetingTranscriber()
    second = transcriber.MeetingTranscriber()
    assert len(created) == 1
    assert first.model is second.model
    assert (first.model_reused, second.model_reused) == (False, True)


def test_pyannote_pipeline_is_loaded_once_per_process(monkeypatch):
    from app.services.diarization import diarizer

    loaded = []

    class FakePipeline:
        @classmethod
        def from_pretrained(cls, name, token=None):
            loaded.append(name)
            return cls()

        def to(self, device):
            return self

    monkeypatch.setattr(diarizer, "Pipeline", FakePipeline)
    monkeypatch.setattr(diarizer.config, "get_hf_token", lambda: "hf-test-token")
    a, b = diarizer.SpeakerDiarizer(), diarizer.SpeakerDiarizer()
    assert len(loaded) == 1 and a.pipeline is b.pipeline
    assert (a.model_reused, b.model_reused) == (False, True)


# ---------------------------------------------------------------------------
# Mode selection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("setting, role, expected", [
    ("auto", None, "subprocess"),          # the API process keeps isolation
    ("auto", "worker", "inprocess"),       # the long-lived worker reuses models
    ("inprocess", None, "inprocess"),
    ("subprocess", "worker", "subprocess"),
    ("bogus", "worker", "subprocess"),
])
def test_speech_pipeline_mode(monkeypatch, setting, role, expected):
    monkeypatch.setattr(config, "SPEECH_PIPELINE", setting)
    if role:
        monkeypatch.setenv(adapter.PROCESS_ROLE_ENV, role)
    else:
        monkeypatch.delenv(adapter.PROCESS_ROLE_ENV, raising=False)
    assert adapter.speech_pipeline_mode() == expected


# ---------------------------------------------------------------------------
# In-process lifecycle with a fake speech module
# ---------------------------------------------------------------------------

def _fake_speech_module(monkeypatch, name, *, fail_for=()):
    calls = []

    def run(meeting_id, input_file, *, skip_post_transcript=False, startup_seconds=None, startup_note=None):
        calls.append({"meeting_id": meeting_id, "skip": skip_post_transcript,
                      "startup_seconds": startup_seconds, "note": startup_note})
        if meeting_id in fail_for:
            raise RuntimeError("asr crashed")
        storage = MeetingStorage(meeting_id)
        storage.path_for("transcript", "meeting_transcript_ready.json").write_text(
            json.dumps({"segments": [{"start": 0.0, "end": 2.0, "speaker": "S0", "text": "Hello there."}]}),
            encoding="utf-8",
        )

    module = types.ModuleType(name)
    module.run = run
    monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(adapter, "_SPEECH_MODULE", name)
    monkeypatch.setattr(config, "SPEECH_PIPELINE", "inprocess")
    monkeypatch.setattr(adapter, "load_frontend_transcript",
                        lambda mid: {"segments": [{"text": "Hello there."}]})
    return calls


def _meeting():
    mid = uuid4().hex
    storage = MeetingStorage(mid)
    storage.ensure_dirs()
    db.insert_meeting(mid, filename="inprocess.wav")
    wav = storage.recording / "recording.wav"
    wav.write_bytes(b"RIFF fake audio for hashing only")
    return mid, storage, wav


def test_inprocess_run_reuses_loaded_libraries(monkeypatch):
    calls = _fake_speech_module(monkeypatch, "fake_speech_reuse")
    mid, storage, wav = _meeting()
    adapter.run_recorded_pipeline(mid, wav, run_post_transcript=False)

    assert calls == [{"meeting_id": mid, "skip": True, "startup_seconds": None, "note": None}]
    status = json.loads(storage.path_for("root", "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "completed"


def test_first_inprocess_import_is_timed(monkeypatch, tmp_path):
    package = tmp_path / "speechpkg_first"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "mod.py").write_text(
        "CALLS = []\n"
        "def run(meeting_id, input_file, **kw):\n"
        "    CALLS.append(kw)\n"
        "    raise RuntimeError('stop after recording the call')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(adapter, "_SPEECH_MODULE", "speechpkg_first.mod")
    with pytest.raises(RuntimeError):
        adapter._run_speech_pipeline_inprocess("m", Path("x.wav"))
    kw = sys.modules["speechpkg_first.mod"].CALLS[0]
    assert kw["startup_seconds"] is not None and kw["startup_seconds"] >= 0
    assert "first import" in kw["startup_note"]


def test_inprocess_failure_is_reported_and_the_next_job_still_runs(monkeypatch):
    from app.core.jobs import run_pipeline
    from tests.semantic_fakes import install_semantic_fakes

    install_semantic_fakes(monkeypatch)
    failing, f_storage, f_wav = _meeting()
    ok, ok_storage, ok_wav = _meeting()
    calls = _fake_speech_module(monkeypatch, "fake_speech_fail", fail_for={failing})
    monkeypatch.setattr(adapter, "run_post_transcript_stages",
                        lambda mid, mode=None: {"status": "completed", "accepted": 0, "dropped": 0,
                                                "mode_used": "rules"})

    run_pipeline(failing, f_wav)
    job = db.get_job_status(failing)
    assert job.state == "failed" and "asr crashed" in job.error
    assert db.get_meeting(failing)["status"] == "failed"         # never marked processed
    status = json.loads(f_storage.path_for("root", "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed" and status["speech_pipeline"] == "inprocess"
    stages = {s["stage"]: s for s in db.get_stage_timings(failing)}
    assert stages["speech_pipeline"]["status"] == "failed"

    run_pipeline(ok, ok_wav)                                       # the same process keeps working
    assert db.get_job_status(ok).state == "done"
    assert [c["meeting_id"] for c in calls] == [failing, ok]


def test_disabled_cache_is_neither_read_nor_written(monkeypatch):
    calls = _fake_speech_module(monkeypatch, "fake_speech_cache")
    saved = []
    monkeypatch.setattr(config, "ENABLE_PIPELINE_CACHE", False)
    monkeypatch.setattr(adapter, "_cache_is_complete", lambda h: True)       # would be a hit
    monkeypatch.setattr(adapter, "_save_results_to_cache", lambda *a: saved.append(a) or True)
    mid, storage, wav = _meeting()
    adapter.run_recorded_pipeline(mid, wav, run_post_transcript=False)
    assert len(calls) == 1 and saved == []
    status = json.loads(storage.path_for("root", "pipeline_status.json").read_text(encoding="utf-8"))
    assert status["cache_hit"] is False and status["cache_saved"] is False


def test_enabled_cache_still_short_circuits(monkeypatch):
    calls = _fake_speech_module(monkeypatch, "fake_speech_cache_on")
    monkeypatch.setattr(config, "ENABLE_PIPELINE_CACHE", True)
    monkeypatch.setattr(adapter, "_cache_is_complete", lambda h: True)
    monkeypatch.setattr(adapter, "_restore_cached_results", lambda mid, h: None)
    mid, storage, wav = _meeting()
    adapter.run_recorded_pipeline(mid, wav, run_post_transcript=False)
    assert calls == []                                             # reused, not recomputed


# ---------------------------------------------------------------------------
# Worker preload
# ---------------------------------------------------------------------------

def test_preload_failures_are_reported_not_fatal(monkeypatch):
    from scripts import worker

    monkeypatch.setattr(config, "get_hf_token", lambda: None)
    monkeypatch.setitem(sys.modules, "scripts.run_recorded_pipeline", types.ModuleType("stub"))
    from app.services.transcription import transcriber
    from app.services.verification import nli_scorer

    monkeypatch.setattr(transcriber, "load_whisper_model", lambda: None)
    monkeypatch.setattr(model_registry, "get_sentence_transformer", lambda name: None)
    monkeypatch.setattr(nli_scorer.NLIScorer, "_pipe_or_load", lambda self: (_ for _ in ()).throw(OSError("no model")))
    results = worker.preload_models()
    assert results["speech_libraries"]["status"] == "loaded"
    assert results["whisper"]["status"] == "loaded"
    assert results["nli"]["status"] == "failed" and "no model" in results["nli"]["error"]
    assert results["pyannote"]["status"] == "failed" and "Hugging Face" in results["pyannote"]["error"]


def test_worker_does_not_preload_when_using_subprocesses(monkeypatch):
    from scripts import worker

    preloaded = []
    monkeypatch.setenv(adapter.PROCESS_ROLE_ENV, "test")
    monkeypatch.setattr(config, "JOB_RUNNER", "worker")
    monkeypatch.setattr(config, "SPEECH_PIPELINE", "subprocess")
    monkeypatch.setattr(worker, "preload_models", lambda: preloaded.append(True))
    monkeypatch.setattr(worker, "run_worker_loop", lambda *a, **k: None)
    worker.main()
    assert preloaded == []


# ---------------------------------------------------------------------------
# The real script raises in-process instead of exiting
# ---------------------------------------------------------------------------

def test_real_pipeline_script_raises_and_records_the_failed_stage(tmp_path):
    from app.core.stage_timings import STAGE_FILE
    from scripts import run_recorded_pipeline as script

    mid = uuid4().hex
    MeetingStorage(mid).ensure_dirs()
    with pytest.raises(Exception):
        script.run(mid, tmp_path / "missing.wav", skip_post_transcript=True, startup_seconds=None)
    stages = {s["stage"]: s for s in json.loads(
        MeetingStorage(mid).path_for("metadata", STAGE_FILE).read_text(encoding="utf-8"))}
    assert stages["pipeline_startup"]["status"] == "cached"
    assert stages["audio_preparation"]["status"] == "failed"
