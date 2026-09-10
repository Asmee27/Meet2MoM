from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from app.core import db
from app.core.storage import MeetingStorage

logger = logging.getLogger(__name__)

EMBED_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@dataclass(frozen=True)
class EvidenceSpan:
    span_id: str
    meeting_id: str
    utt_ids: list[str]
    text: str
    start: float
    end: float
    speakers: list[str]


@dataclass(frozen=True)
class RetrievalHit:
    span: EvidenceSpan
    dense_score: float
    bm25_score: float
    hybrid_score: float


def build_spans(
    meeting_id: str,
    group_size: int = 3,
    stride: int = 1,
    prefix: str | None = None,
) -> list[EvidenceSpan]:
    utts = db.get_utterances(meeting_id)
    if not utts:
        return []
    if group_size < 1 or stride < 1:
        raise ValueError("group_size and stride must be positive")
    label = prefix or f"s{group_size}"
    last_start = max(1, len(utts) - group_size + 1)
    spans: list[EvidenceSpan] = []
    for i in range(0, last_start, stride):
        group = utts[i : i + group_size]
        spans.append(
            EvidenceSpan(
                span_id=f"{label}_{len(spans):03d}",
                meeting_id=meeting_id,
                utt_ids=[u.utt_id for u in group],
                text=" ".join(u.text.strip() for u in group if u.text),
                start=group[0].start,
                end=group[-1].end,
                speakers=list(dict.fromkeys(u.speaker_id for u in group)),
            )
        )
    return spans


class RetrievalIndex:
    def __init__(self, meeting_id: str):
        self.meeting_id = meeting_id
        self.spans: list[EvidenceSpan] = []
        self._emb: np.ndarray | None = None
        self._bm25: BM25Okapi | None = None
        self._model = None
        self._utterance_count = 0

    def _load_model(self):
        if self._model is None:
            from app.core.model_registry import get_sentence_transformer

            self._model = get_sentence_transformer(EMBED_MODEL_NAME)
        return self._model

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return [t for t in re.split(r"\W+", text.lower(), flags=re.UNICODE) if t]

    def build(self) -> None:
        started = time.perf_counter()
        utts = db.get_utterances(self.meeting_id)
        self._utterance_count = len(utts)
        self.spans = build_spans(self.meeting_id, 1, prefix="s1")
        self.spans.extend(build_spans(self.meeting_id, 3, prefix="s3"))
        texts = [s.text for s in self.spans]
        if texts:
            emb = self._load_model().encode(
                texts, normalize_embeddings=True, show_progress_bar=False
            )
            self._emb = np.asarray(emb, dtype=np.float32)
            self._bm25 = BM25Okapi([self._tokenize(t) for t in texts])
        else:
            self._emb = np.empty((0, 0), dtype=np.float32)
            self._bm25 = BM25Okapi([])
        dim = int(self._emb.shape[1]) if self._emb.ndim == 2 else 0
        logger.info(
            "Built retrieval index for %s: spans=%d embedding_dim=%d seconds=%.3f",
            self.meeting_id, len(self.spans), dim, time.perf_counter() - started,
        )

    def retrieve(self, query: str, k: int = 8, alpha: float = 0.65) -> list[RetrievalHit]:
        if self._emb is None or self._bm25 is None:
            raise RuntimeError("RetrievalIndex.build() or load_or_build() must be called first")
        if not self.spans or k <= 0:
            return []
        q = self._load_model().encode(
            [query], normalize_embeddings=True, show_progress_bar=False
        )
        dense = (self._emb @ np.asarray(q, dtype=np.float32).T).ravel()
        dense = np.clip((dense + 1.0) / 2.0, 0.0, 1.0)
        raw_bm = np.asarray(self._bm25.get_scores(self._tokenize(query)), dtype=np.float32)
        rng = float(raw_bm.max() - raw_bm.min())
        bm = (raw_bm - raw_bm.min()) / rng if rng > 1e-9 else np.zeros_like(raw_bm)
        hybrid = np.clip(alpha * dense + (1.0 - alpha) * bm, 0.0, 1.0)
        order = np.argsort(-hybrid, kind="stable")[:k]
        return [
            RetrievalHit(
                self.spans[i], float(dense[i]), float(bm[i]), float(hybrid[i])
            )
            for i in order
        ]

    def save(self) -> None:
        storage = MeetingStorage(self.meeting_id)
        storage.ensure_dirs()
        retrieval_dir = storage.path_for("verification", "retrieval")
        retrieval_dir.mkdir(parents=True, exist_ok=True)
        storage.path_for("verification", "retrieval/spans.json").write_text(
            json.dumps(
                {
                    "utterance_count": self._utterance_count,
                    "spans": [asdict(span) for span in self.spans],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        np.save(storage.path_for("verification", "retrieval/embeddings.npy"), self._emb)

    @classmethod
    def load_or_build(cls, meeting_id: str) -> "RetrievalIndex":
        index = cls(meeting_id)
        storage = MeetingStorage(meeting_id)
        spans_path = storage.path_for("verification", "retrieval/spans.json")
        emb_path = storage.path_for("verification", "retrieval/embeddings.npy")
        current_count = len(db.get_utterances(meeting_id))
        if spans_path.exists() and emb_path.exists():
            payload = json.loads(spans_path.read_text(encoding="utf-8"))
            saved_count = payload.get("utterance_count")
            raw_spans = payload.get("spans", payload if isinstance(payload, list) else [])
            if saved_count == current_count and isinstance(raw_spans, list):
                emb = np.load(emb_path)
                if len(raw_spans) == len(emb):
                    index._utterance_count = current_count
                    index.spans = [EvidenceSpan(**span) for span in raw_spans]
                    index._emb = np.asarray(emb, dtype=np.float32)
                    index._bm25 = BM25Okapi(
                        [index._tokenize(span.text) for span in index.spans]
                    )
                    return index
        index.build()
        index.save()
        return index
