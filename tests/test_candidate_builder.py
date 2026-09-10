from __future__ import annotations

from uuid import uuid4

from app.core import db
from app.schemas.claim import Decision
from app.services.verification.candidate_builder import (
    Provenance,
    build_candidates,
    build_query,
)
from app.services.verification.retrieval_index import (
    EvidenceSpan,
    RetrievalHit,
    RetrievalIndex,
)


def _decision(meeting_id: str, claim_id: str, text: str, evidence: list[str]):
    db.insert_claim_with_evidence(
        Decision(
            claim_id=f"{claim_id}-{uuid4().hex}",
            meeting_id=meeting_id,
            text=text,
            evidence=evidence,
            state="accepted",
        )
    )


def test_union_contains_both_sources(monkeypatch):
    meeting_id = uuid4().hex
    db.insert_meeting(meeting_id, filename="synthetic.wav")
    from tests.test_retrieval_index import _meeting
    source = _meeting()
    rows = db.get_utterances(source)
    for row in rows:
        row.meeting_id = meeting_id
    db.bulk_insert_utterances(meeting_id, rows)
    _decision(meeting_id, "c1", "deployment moved to Friday", ["u00005"])
    monkeypatch.setattr(RetrievalIndex, "_load_model", lambda self: __import__(
        "tests.test_retrieval_index", fromlist=["_DeterministicModel"]
    )._DeterministicModel())
    candidates = build_candidates(meeting_id, k=8)
    assert any(c.provenance == Provenance.BOTH for c in candidates[0].candidates)
    assert any(c.provenance == Provenance.RETRIEVED_ONLY for c in candidates[0].candidates)


def test_cited_only_preserved(monkeypatch):
    meeting_id = uuid4().hex
    db.insert_meeting(meeting_id, filename="synthetic.wav")
    from tests.test_retrieval_index import _meeting
    source = _meeting()
    rows = db.get_utterances(source)
    for row in rows:
        row.meeting_id = meeting_id
    db.bulk_insert_utterances(meeting_id, rows)
    _decision(meeting_id, "c2", "completely unrelated query", ["u00018"])
    span = EvidenceSpan("s", meeting_id, ["u00001"], "unrelated query", 8, 12, ["SPK_01"])
    fake = RetrievalIndex(meeting_id)
    fake.retrieve = lambda query, k=8: [RetrievalHit(span, 0.1, 0.1, 0.1)]
    result = build_candidates(meeting_id, index=fake)[0]
    cited = [c for c in result.candidates if c.provenance == Provenance.CITED_ONLY]
    assert cited and cited[0].span.utt_ids == ["u00018"] and cited[0].retrieval_score == 0.0


def test_cited_recall_zero_logs_warning(caplog):
    meeting_id = uuid4().hex
    db.insert_meeting(meeting_id, filename="synthetic.wav")
    from tests.test_retrieval_index import _meeting
    source = _meeting()
    rows = db.get_utterances(source)
    for row in rows:
        row.meeting_id = meeting_id
    db.bulk_insert_utterances(meeting_id, rows)
    _decision(meeting_id, "c3", "penguin migration", ["u00005"])
    fake = RetrievalIndex(meeting_id)
    fake.retrieve = lambda query, k=8: []
    with caplog.at_level("WARNING"):
        result = build_candidates(meeting_id, index=fake)[0]
    assert result.cited_recall_strict == 0.0
    assert "zero strict cited recall" in caplog.text


def test_no_substitution_on_unresolvable(monkeypatch, caplog):
    meeting_id = uuid4().hex
    db.insert_meeting(meeting_id, filename="synthetic.wav")
    from tests.test_retrieval_index import _meeting
    source = _meeting()
    rows = db.get_utterances(source)
    for row in rows:
        row.meeting_id = meeting_id
    db.bulk_insert_utterances(meeting_id, rows)
    _decision(meeting_id, "c4", "missing evidence", ["u00005"])
    fake = RetrievalIndex(meeting_id)
    fake.retrieve = lambda query, k=8: []
    monkeypatch.setattr(
        "app.services.verification.candidate_builder.resolve_spans",
        lambda meeting_id, ids: ([], ids),
    )
    with caplog.at_level("ERROR"):
        result = build_candidates(meeting_id, index=fake)[0]
    assert not result.candidates
    assert "unresolvable" in caplog.text


def test_action_query_includes_owner_deadline():
    query = build_query({
        "claim_type": "action",
        "text": "Prepare the script",
        "owner": "Priya",
        "deadline_text": "Friday",
    })
    assert "Priya" in query and "Friday" in query


def test_ordering_and_dedup(monkeypatch):
    meeting_id = uuid4().hex
    db.insert_meeting(meeting_id, filename="synthetic.wav")
    from tests.test_retrieval_index import _meeting
    source = _meeting()
    rows = db.get_utterances(source)
    for row in rows:
        row.meeting_id = meeting_id
    db.bulk_insert_utterances(meeting_id, rows)
    _decision(meeting_id, "c5", "deployment moved to Friday", ["u00005"])
    monkeypatch.setattr(RetrievalIndex, "_load_model", lambda self: __import__(
        "tests.test_retrieval_index", fromlist=["_DeterministicModel"]
    )._DeterministicModel())
    result = build_candidates(meeting_id, k=8)[0]
    assert len([
        c for c in result.candidates if c.provenance == Provenance.BOTH
    ]) >= 1
    ranks = {
        Provenance.BOTH: 0,
        Provenance.RETRIEVED_ONLY: 1,
        Provenance.CITED_ONLY: 2,
    }
    assert [ranks[c.provenance] for c in result.candidates] == sorted(
        ranks[c.provenance] for c in result.candidates
    )


def test_cited_recall_strict_penalises_offset_citation(caplog):
    meeting_id = uuid4().hex
    db.insert_meeting(meeting_id, filename="synthetic.wav")
    from tests.test_retrieval_index import _meeting
    source = _meeting()
    rows = db.get_utterances(source)
    for row in rows:
        row.meeting_id = meeting_id
    db.bulk_insert_utterances(meeting_id, rows)
    _decision(meeting_id, "strict", "look at the report together", ["u00013"])
    fake = RetrievalIndex(meeting_id)
    fake.retrieve = lambda query, k=8: [fake.retrieve(query, k)] if False else [
        RetrievalHit(
            EvidenceSpan("s3", meeting_id, ["u00013", "u00014", "u00015"],
                         "full report look at the report together", 264, 284,
                         ["SPK_01"]),
            0.9, 0.9, 0.9,
        ),
        RetrievalHit(
            EvidenceSpan("s1_support", meeting_id, ["u00014"],
                         "look at the report together", 272, 276, ["SPK_02"]),
            0.8, 0.8, 0.8,
        ),
    ]
    with caplog.at_level("WARNING"):
        result = build_candidates(meeting_id, index=fake)[0]
    assert result.cited_overlap_any == 1.0
    assert result.cited_recall_strict == 0.0
    assert result.cited_span is not None
    assert "zero strict cited recall" in caplog.text


def test_cited_span_built_from_cited_ids_only():
    meeting_id = uuid4().hex
    db.insert_meeting(meeting_id, filename="synthetic.wav")
    from tests.test_retrieval_index import _meeting
    source = _meeting()
    rows = db.get_utterances(source)
    for row in rows:
        row.meeting_id = meeting_id
    db.bulk_insert_utterances(meeting_id, rows)
    _decision(meeting_id, "span", "deployment moved to Friday", ["u00006", "u00005"])
    fake = RetrievalIndex(meeting_id)
    fake.retrieve = lambda query, k=8: []
    result = build_candidates(meeting_id, index=fake)[0]
    assert result.cited_span is not None
    assert result.cited_span.utt_ids == ["u00005", "u00006"]


def test_cited_span_none_when_unresolvable(monkeypatch, caplog):
    meeting_id = uuid4().hex
    db.insert_meeting(meeting_id, filename="synthetic.wav")
    from tests.test_retrieval_index import _meeting
    source = _meeting()
    rows = db.get_utterances(source)
    for row in rows:
        row.meeting_id = meeting_id
    db.bulk_insert_utterances(meeting_id, rows)
    _decision(meeting_id, "span-missing", "missing evidence", ["u00005"])
    fake = RetrievalIndex(meeting_id)
    fake.retrieve = lambda query, k=8: []
    monkeypatch.setattr(
        "app.services.verification.candidate_builder.resolve_spans",
        lambda meeting_id, ids: ([], ids),
    )
    with caplog.at_level("ERROR"):
        result = build_candidates(meeting_id, index=fake)[0]
    assert result.cited_span is None
    assert not any(candidate.span.utt_ids == ["u00005"] for candidate in result.candidates)
    assert "unresolvable" in caplog.text
