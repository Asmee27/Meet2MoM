from __future__ import annotations

import hashlib
from uuid import uuid4

import numpy as np

from app.core import db
from app.schemas.utterance import Utterance
from app.services.verification.retrieval_index import RetrievalIndex


class _DeterministicModel:
    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        rows = []
        for text in texts:
            vector = np.zeros(128, dtype=np.float32)
            for token in text.lower().split():
                digest = hashlib.sha256(token.encode()).digest()
                vector[int.from_bytes(digest[:2], "big") % len(vector)] += 1.0
            norm = np.linalg.norm(vector)
            rows.append(vector / norm if norm else vector)
        return np.asarray(rows, dtype=np.float32)


def _meeting() -> str:
    meeting_id = uuid4().hex
    db.insert_meeting(meeting_id, filename="synthetic.wav")
    planted = {
        5: "we should move the deployment to friday",
        6: "yes agreed, friday works",
        12: "the budget for the marketing campaign is forty thousand",
        18: "मैं शुक्रवार तक script ready कर दूँगी",
    }
    rows = [
        Utterance(
            meeting_id=meeting_id,
            utt_id=f"u{i:05d}",
            start=i * 8.0,
            end=i * 8.0 + 4.0,
            speaker_id=f"SPK_{i % 4:02d}",
            text=planted.get(i, f"general meeting discussion item {i}"),
            asr_conf=0.8,
            spk_conf=0.8,
            conf_source="numeric",
        )
        for i in range(20)
    ]
    db.bulk_insert_utterances(meeting_id, rows)
    return meeting_id


def _index(monkeypatch, meeting_id: str) -> RetrievalIndex:
    monkeypatch.setattr(
        RetrievalIndex,
        "_load_model",
        lambda self: setattr(self, "_test_model", getattr(self, "_test_model", _DeterministicModel()))
        or self._test_model,
    )
    index = RetrievalIndex(meeting_id)
    index.build()
    return index


def test_spans_cover_all_utterances(monkeypatch):
    index = _index(monkeypatch, _meeting())
    assert set().union(
        *(set(span.utt_ids) for span in index.spans if span.span_id.startswith("s1_"))
    ) == {f"u{i:05d}" for i in range(20)}


def test_retrieve_finds_planted_span(monkeypatch):
    hits = _index(monkeypatch, _meeting()).retrieve("deployment moved to Friday", k=3)
    assert any("u00005" in hit.span.utt_ids for hit in hits)


def test_bm25_catches_exact_number(monkeypatch):
    hits = _index(monkeypatch, _meeting()).retrieve("forty thousand budget", k=3)
    assert any("u00012" in hit.span.utt_ids for hit in hits)


def test_codeswitch_retrieval(monkeypatch):
    hits = _index(monkeypatch, _meeting()).retrieve(
        "script will be ready by Friday", k=5
    )
    assert any("u00018" in hit.span.utt_ids for hit in hits)


def test_irrelevant_query_low_score(monkeypatch, capsys):
    hit = _index(monkeypatch, _meeting()).retrieve(
        "quarterly penguin migration", k=1
    )[0]
    print(f"irrelevant hybrid_score={hit.hybrid_score:.6f}")
    assert hit.hybrid_score < 0.55


def test_save_load_roundtrip(monkeypatch):
    meeting_id = _meeting()
    index = _index(monkeypatch, meeting_id)
    index.save()
    loaded = RetrievalIndex.load_or_build(meeting_id)
    loaded._model = _DeterministicModel()
    first = index.retrieve("deployment moved to Friday", k=5)
    second = loaded.retrieve("deployment moved to Friday", k=5)
    assert [hit.span.span_id for hit in first] == [hit.span.span_id for hit in second]


def test_deterministic(monkeypatch):
    index = _index(monkeypatch, _meeting())
    first = index.retrieve("deployment moved to Friday", k=5)
    second = index.retrieve("deployment moved to Friday", k=5)
    assert [(h.span.span_id, h.hybrid_score) for h in first] == [
        (h.span.span_id, h.hybrid_score) for h in second
    ]
