from __future__ import annotations

import logging
import threading
from typing import Any

_lock = threading.Lock()
_models: dict[str, Any] = {}
logger = logging.getLogger(__name__)


def get_sentence_transformer(name: str):
    key = f"st::{name}"
    with _lock:
        if key not in _models:
            import time
            from sentence_transformers import SentenceTransformer

            started = time.perf_counter()
            _models[key] = SentenceTransformer(name)
            logger.info(
                "Loaded SentenceTransformer %s in %.2fs",
                name,
                time.perf_counter() - started,
            )
        return _models[key]


def get_hf_pipeline(task: str, model_name: str, **kw):
    key = f"hf::{task}::{model_name}"
    with _lock:
        if key not in _models:
            import time
            import torch
            from transformers import pipeline as hf_pipeline

            # transformers 5 keeps a checkpoint's stored precision ("auto"). The
            # NLI checkpoint is float16, which CPUs without fp16 units run ~17x
            # slower than float32 (measured 8.8 s vs 0.5 s per pair) with the
            # same labels. Load float32 unless a caller asks otherwise.
            kw.setdefault("dtype", torch.float32)
            started = time.perf_counter()
            _models[key] = hf_pipeline(task, model=model_name, **kw)
            logger.info(
                "Loaded HF pipeline %s/%s in %.2fs",
                task,
                model_name,
                time.perf_counter() - started,
            )
        return _models[key]


def is_loaded(key: str) -> bool:
    with _lock:
        return key in _models


def get_cached_model(key: str, factory):
    """
    Build a model once per process and reuse it (Phase 6). A long-lived worker
    therefore loads Whisper and pyannote once, not once per meeting.
    """
    with _lock:
        if key not in _models:
            import time

            started = time.perf_counter()
            _models[key] = factory()
            logger.info("Loaded %s in %.2fs", key, time.perf_counter() - started)
        return _models[key]


def clear_models() -> None:
    with _lock:
        _models.clear()
