"""
Processing-time safeguards found while diagnosing a 22-minute job (Oct 2026):

* the NLI verifier must load in float32 (its float16 checkpoint ran ~17x
  slower on CPU under transformers 5, which keeps the stored precision);
* a worker must not run beside an inline API, where it could process the
  same meeting a second time;
* every pipeline stage logs how long it took.
"""
from __future__ import annotations

import logging
import sys
from types import SimpleNamespace

import pytest
import torch

from app.core import config, jobs, model_registry


@pytest.fixture
def fake_transformers(monkeypatch):
    calls = []

    def pipeline(task, model=None, **kw):
        calls.append({"task": task, "model": model, **kw})
        return SimpleNamespace(model=SimpleNamespace(config=SimpleNamespace(
            id2label={0: "entailment", 1: "neutral", 2: "contradiction"})))

    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(pipeline=pipeline))
    model_registry.clear_models()
    yield calls
    model_registry.clear_models()


def test_hf_pipelines_load_in_float32_by_default(fake_transformers):
    model_registry.get_hf_pipeline("text-classification", "some/model", top_k=None)
    assert fake_transformers[0]["dtype"] is torch.float32


def test_an_explicit_dtype_is_respected(fake_transformers):
    model_registry.get_hf_pipeline("text-classification", "some/model", dtype=torch.bfloat16)
    assert fake_transformers[0]["dtype"] is torch.bfloat16


def test_nli_verifier_model_is_float32(fake_transformers):
    from app.services.verification.nli_scorer import NLI_MODEL, NLIScorer

    NLIScorer()._pipe_or_load()
    (call,) = fake_transformers
    assert call["model"] == NLI_MODEL
    assert call["dtype"] is torch.float32
    assert call["truncation"] is True and call["top_k"] is None        # unchanged settings


def test_worker_refuses_to_run_beside_an_inline_api(monkeypatch, caplog):
    from scripts import worker

    monkeypatch.setattr(config, "JOB_RUNNER", "inline")
    monkeypatch.setattr(worker, "run_worker_loop", lambda *a, **k: pytest.fail("worker must not poll"))
    with caplog.at_level(logging.ERROR, logger="worker"), pytest.raises(SystemExit) as info:
        worker.main()
    assert info.value.code == 2
    assert "JOB_RUNNER=inline" in caplog.text


def test_worker_runs_in_worker_mode(monkeypatch):
    from scripts import worker
    from app.services.pipeline.recorded_pipeline_adapter import PROCESS_ROLE_ENV

    started, preloaded = [], []
    monkeypatch.setenv(PROCESS_ROLE_ENV, "test")          # restored after the test
    monkeypatch.setattr(config, "JOB_RUNNER", "worker")
    monkeypatch.setattr(config, "SPEECH_PIPELINE", "auto")
    monkeypatch.setattr(worker, "preload_models", lambda: preloaded.append(True))
    monkeypatch.setattr(worker, "run_worker_loop", lambda *a, **k: started.append(True))
    worker.main()
    assert started == [True]
    assert preloaded == [True]                            # auto -> in-process in the worker


def test_stages_log_their_duration(caplog):
    with caplog.at_level(logging.INFO, logger=jobs.logger.name):
        assert jobs._run_stage("verification", lambda: 42) == 42
        with pytest.raises(jobs._StageError):
            jobs._run_stage("merge", lambda: 1 / 0)
    assert "Stage verification finished in" in caplog.text
    assert "Stage merge failed after" in caplog.text
