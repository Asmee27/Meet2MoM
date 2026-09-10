from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sys
from types import SimpleNamespace

from app.core import model_registry


class _FakeModel:
    pass


def test_sentence_transformer_is_cached(monkeypatch):
    model_registry.clear_models()
    created = []

    class FakeSentenceTransformer:
        def __new__(cls, name):
            instance = super().__new__(cls)
            created.append(instance)
            return instance

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    first = model_registry.get_sentence_transformer("test-model")
    second = model_registry.get_sentence_transformer("test-model")
    assert first is second
    assert len(created) == 1
    model_registry.clear_models()


def test_sentence_transformer_cache_is_thread_safe(monkeypatch):
    model_registry.clear_models()
    created = []

    class FakeSentenceTransformer:
        def __new__(cls, name):
            instance = super().__new__(cls)
            created.append(instance)
            return instance

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                model_registry.get_sentence_transformer,
                ["thread-model"] * 4,
            )
        )
    assert len({id(result) for result in results}) == 1
    assert len(created) == 1
    model_registry.clear_models()
