"""
Deterministic stand-ins for the embedding model and the NLI pipeline.

They let tests exercise retrieval -> NLI -> fusion -> persistence without
downloading models. They are NOT models: entailment is token coverage of the
hypothesis by the premise, and contradiction is coverage plus a negation /
reversal cue in the premise. Real-model behaviour is covered by the slow tests.
"""
from __future__ import annotations

import hashlib
import re

import numpy as np

STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by",
    "is", "are", "was", "were", "be", "will", "would", "we", "i", "you", "it", "this",
    "that", "our", "let", "lets", "s", "so", "group", "agreed", "following", "due",
    "responsible", "rejected", "ll",
}
NEGATION = {"not", "no", "never", "won't", "wont", "instead", "drop", "cancel", "cancelled", "don't", "dont"}
TEMPLATE = re.compile(
    r"^(the group agreed to:|the group rejected:|the following is due by [^:]*:)\s*", re.IGNORECASE
)


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^\w']+", text.lower()) if t]


def _content(text: str) -> set[str]:
    return {t for t in _tokens(text) if t not in STOP and len(t) > 1}


class FakeEmbedder:
    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False, **kwargs):
        rows = []
        for text in texts:
            vector = np.zeros(256, dtype=np.float32)
            for token in _content(text):
                digest = hashlib.sha256(token.encode()).digest()
                vector[int.from_bytes(digest[:2], "big") % 256] += 1.0
            norm = np.linalg.norm(vector)
            rows.append(vector / norm if norm else vector)
        return np.asarray(rows, dtype=np.float32)


class FakeNLIPipeline:
    class _Config:
        id2label = {0: "entailment", 1: "neutral", 2: "contradiction"}

    class _Model:
        def __init__(self):
            self.config = FakeNLIPipeline._Config()

    def __init__(self):
        self.model = self._Model()
        self.calls = 0

    @staticmethod
    def _neutral() -> list[dict]:
        return [
            {"label": "entailment", "score": 0.05},
            {"label": "neutral", "score": 0.90},
            {"label": "contradiction", "score": 0.05},
        ]

    def _score(self, premise: str, hypothesis: str) -> list[dict]:
        have = set(_tokens(premise))
        # Owner / deadline hypotheses are only entailed when the owner or the
        # deadline itself is in the premise, not just the task words.
        owner = re.match(r"^(.*?) is responsible for:\s*(.*)$", hypothesis, re.IGNORECASE)
        if owner:
            if not set(_tokens(owner.group(1))) <= have:
                return self._neutral()
            hypothesis = owner.group(2)
        deadline = re.match(r"^The following is due by (.*?):\s*(.*)$", hypothesis, re.IGNORECASE)
        if deadline:
            if not _content(deadline.group(1)) <= have:
                return self._neutral()
            hypothesis = deadline.group(2)
        hyp = TEMPLATE.sub("", hypothesis)
        wanted = _content(hyp)
        coverage = len(wanted & have) / len(wanted) if wanted else 0.0
        negated = bool(NEGATION & have)
        if coverage >= 0.6 and negated:
            e, n, x = 0.05, 0.10, 0.85
        elif coverage >= 0.6:
            e, n, x = 0.90, 0.08, 0.02
        else:
            e, n, x = 0.05, 0.90, 0.05
        return [
            {"label": "entailment", "score": e},
            {"label": "neutral", "score": n},
            {"label": "contradiction", "score": x},
        ]

    def __call__(self, inputs, batch_size=8):
        self.calls += len(inputs)
        return [self._score(item["text"], item["text_pair"]) for item in inputs]


def install_semantic_fakes(monkeypatch) -> FakeNLIPipeline:
    """Patch the model loaders used by RetrievalIndex and NLIScorer."""
    from app.services.verification.retrieval_index import RetrievalIndex

    embedder = FakeEmbedder()
    pipe = FakeNLIPipeline()
    monkeypatch.setattr(RetrievalIndex, "_load_model", lambda self: embedder)
    monkeypatch.setattr("app.core.model_registry.get_hf_pipeline", lambda *a, **k: pipe)
    monkeypatch.setattr("app.core.model_registry.get_sentence_transformer", lambda *a, **k: embedder)
    return pipe
