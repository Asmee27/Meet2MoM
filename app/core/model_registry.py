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
            from transformers import pipeline as hf_pipeline

            started = time.perf_counter()
            _models[key] = hf_pipeline(task, model=model_name, **kw)
            logger.info(
                "Loaded HF pipeline %s/%s in %.2fs",
                task,
                model_name,
                time.perf_counter() - started,
            )
        return _models[key]


def clear_models() -> None:
    with _lock:
        _models.clear()
