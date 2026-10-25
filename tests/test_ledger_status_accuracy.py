"""
Phase 9.1: commitment-status proposals must follow what the follow-up meeting
actually says.

Reported: "I will finalize the backend API deployment and email the access
links" was proposed as Delayed. Cause: "Delayed" was proposed whenever
"<task> is done." was contradicted, and a promise of future work contradicts
"done" without saying anything about a delay.

The NLI scores below are the real model's (mDeBERTa, measured 25 Oct 2026 on
these sentences) so the classification rules are tested on realistic values
without loading the model. The slow test at the end runs the real model.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core import db
from app.services.ledger import commitment_ledger as ledger
from tests.scenario_helpers import make_meeting
from tests.semantic_fakes import install_semantic_fakes

REPORT = "Submit the front end validation report"
BACKEND = "Finalize the backend API deployment and email the access links"

# premise -> entailment of: task-specific done, postponed/late, future, not finished
REAL_SCORES = {
    "I have submitted the front end validation report.": (0.98, 0.00, 0.00, 0.00),
    "The front end validation report is postponed until next week.": (0.00, 0.99, 0.04, 0.86),
    "I will submit the front end validation report by Friday.": (0.13, 0.45, 0.47, 0.18),
    "I will finalize the backend API deployment and email the access links.": (0.45, 0.00, 0.02, 0.01),
    "I have not started the front end validation report yet.": (0.00, 0.05, 0.00, 0.72),
    "The front end validation report is not finished yet.": (0.00, 0.00, 0.00, 0.94),
    "The backend API deployment is postponed until next week.": (0.00, 0.99, 0.04, 0.86),
}


class ScriptedScorer:
    """Returns the measured real-model entailment for each (premise, hypothesis kind)."""

    def __init__(self):
        self.meetings = set()
        self.premises = []

    @staticmethod
    def _kind(hypothesis):
        if hypothesis in ledger.POSTPONED_HYPOTHESES:
            return 1
        if hypothesis == ledger.FUTURE_HYPOTHESIS:
            return 2
        if hypothesis == ledger.NOT_FINISHED_HYPOTHESIS:
            return 3
        return 0                                       # "<task> is done." / "<task> has been completed."

    def score_pairs_cached(self, meeting_id, pairs):
        self.meetings.add(meeting_id)
        out = []
        for premise, hypothesis in pairs:
            self.premises.append(premise)
            value = REAL_SCORES.get(premise, (0.0, 0.0, 0.0, 0.0))[self._kind(hypothesis)]
            out.append({"entailment": value, "neutral": 1 - value, "contradiction": 0.0})
        return out


@pytest.fixture
def ledger_meeting(monkeypatch):
    """Meeting 1 with two approved commitments (open)."""
    install_semantic_fakes(monkeypatch)
    m1 = make_meeting([
        ("Asmee", "I will submit the front end validation report by Friday."),
        ("Samarth", "I will finalize the backend API deployment and email the access links."),
    ])
    mom = {"action_items": [
        {"claim_id": f"{m1}_a00000", "task": REPORT, "owner": "Asmee", "dueDate": "2026-10-30", "evidence_ids": ["u00000"]},
        {"claim_id": f"{m1}_a00001", "task": BACKEND, "owner": "Samarth", "dueDate": None, "evidence_ids": ["u00001"]},
    ]}
    assert ledger.record_commitments(m1, mom, None) == 2
    with db.get_conn() as conn:
        ids = {r["text"]: r["commitment_id"] for r in conn.execute(
            "SELECT commitment_id, text FROM commitments WHERE source_meeting_id = ?", (m1,))}
    return m1, ids


def _propose(m2):
    scorer = ScriptedScorer()
    proposals = {p["commitment_id"]: p for p in ledger.propose_updates(m2, scorer=scorer)}
    return proposals, scorer


def _status(cid):
    with db.get_conn() as conn:
        return conn.execute("SELECT status FROM commitments WHERE commitment_id = ?", (cid,)).fetchone()[0]


@pytest.mark.parametrize("statement, expected", [
    ("I have submitted the front end validation report.", "done"),
    ("The front end validation report is postponed until next week.", "delayed"),
    ("I will submit the front end validation report by Friday.", "no_update"),          # repeated promise
    ("I have not started the front end validation report yet.", "no_update"),           # not done, no delay
])
def test_each_statement_gets_the_status_it_supports(ledger_meeting, statement, expected):
    m1, ids = ledger_meeting
    m2 = make_meeting([("Samarth", "Good afternoon, let us check last week's items."), ("Asmee", statement)])
    proposals, _ = _propose(m2)
    proposal = proposals[ids[REPORT]]
    assert proposal["proposed_status"] == expected
    assert proposal["scores"]["needs_review"] is False
    if expected in ("done", "delayed"):
        assert proposal["evidence_ids"] == ["u00001"]                    # exactly the statement, no neighbour
    else:
        assert proposal["evidence_ids"] == []                            # nothing supports a change
    assert _status(ids[REPORT]) == "open"                                # a proposal changes nothing


def test_a_repeated_future_promise_is_never_delayed(ledger_meeting):
    """The reported case."""
    _, ids = ledger_meeting
    m2 = make_meeting([("Samarth", "I will finalize the backend API deployment and email the access links.")])
    proposal = _propose(m2)[0][ids[BACKEND]]
    assert proposal["proposed_status"] == "no_update"
    assert proposal["scores"]["delayed"] < ledger.PROPOSAL_MIN
    assert "without progress information" in proposal["scores"]["reason"]
    assert proposal["scores"]["context_ids"] == ["u00000"]


def test_evidence_about_another_commitment_is_rejected(ledger_meeting):
    _, ids = ledger_meeting
    m2 = make_meeting([("Samarth", "The backend API deployment is postponed until next week.")])
    proposals, scorer = _propose(m2)
    assert proposals[ids[BACKEND]]["proposed_status"] == "delayed"
    report = proposals[ids[REPORT]]
    assert report["proposed_status"] == "no_update" and report["evidence_ids"] == []
    assert report["scores"]["candidate_ids"] == []                        # never even scored for the report
    assert "not discussed" in report["scores"]["reason"]


def test_conflicting_statements_are_held_for_review(ledger_meeting):
    _, ids = ledger_meeting
    m2 = make_meeting([
        ("Asmee", "I have submitted the front end validation report."),
        ("Samarth", "The front end validation report is not finished yet."),
    ])
    proposal = _propose(m2)[0][ids[REPORT]]
    assert proposal["scores"]["needs_review"] is True
    assert proposal["proposed_status"] == "no_update"                    # nothing is asserted either way
    assert set(proposal["evidence_ids"]) == {"u00000", "u00001"}


def test_citations_name_the_follow_up_meeting_even_when_ids_repeat(ledger_meeting):
    m1, ids = ledger_meeting
    # Meeting 2 reuses u00000/u00001, like every meeting does.
    m2 = make_meeting([("Samarth", "Let us start."), ("Asmee", "I have submitted the front end validation report.")])
    proposals, scorer = _propose(m2)
    assert scorer.meetings == {m2}                                        # only meeting 2 text was scored
    assert "I will submit the front end validation report by Friday." not in scorer.premises   # meeting 1's line

    follow_ups = TestClient(app).get(f"/api/meetings/{m2}/commitments").json()["follow_ups"]
    report = next(f for f in follow_ups if f["commitment_id"] == ids[REPORT])
    (evidence,) = report["evidence"]
    assert evidence["meeting_id"] == m2 and evidence["ref"] == f"{m2}:u00001"
    assert evidence["text"] == "I have submitted the front end validation report."
    assert report["source_evidence"] == [{"meeting_id": m1, "utt_id": "u00000", "ref": f"{m1}:u00000"}]
    assert report["source_meeting_id"] == m1


def test_review_is_separate_from_the_proposal_and_keeps_previous_status(ledger_meeting):
    _, ids = ledger_meeting
    m2 = make_meeting([("Asmee", "I have submitted the front end validation report.")])
    _propose(m2)
    client = TestClient(app)
    res = client.post(f"/api/meetings/{m2}/commitments/{ids[REPORT]}/review",
                      json={"status": "delayed", "note": "Only the draft was sent", "reviewer": "Rita"})
    assert res.status_code == 200
    assert _status(ids[REPORT]) == "delayed"
    entry = next(c for c in client.get("/api/commitments").json()["commitments"] if c["commitment_id"] == ids[REPORT])
    (history,) = entry["history"]
    assert history["proposed_status"] == "done"                           # the AI's proposal stays recorded
    assert history["final_status"] == "delayed" and history["review_status"] == "corrected"
    assert history["previous_status"] == "open" and history["reviewer"] == "Rita"
    assert history["reviewed_at"] and history["meeting_id"] == m2 and history["evidence_ids"] == ["u00000"]


def test_a_pending_proposal_never_overrides_a_status_confirmed_elsewhere(ledger_meeting):
    _, ids = ledger_meeting
    m2 = make_meeting([("Asmee", "Nothing new from my side.")])
    m3 = make_meeting([("Asmee", "I have submitted the front end validation report.")])
    _propose(m2)                                                          # no_update, left pending
    _propose(m3)
    client = TestClient(app)
    client.post(f"/api/meetings/{m3}/commitments/{ids[REPORT]}/review", json={"status": "done", "reviewer": "Rita"})
    pending = next(f for f in client.get(f"/api/meetings/{m2}/commitments").json()["follow_ups"]
                   if f["commitment_id"] == ids[REPORT])
    assert pending["proposed_status"] == "no_update" and pending["review_status"] == "proposed"
    assert pending["commitment_status"] == "done"                        # both facts are reported
    assert _status(ids[REPORT]) == "done"


@pytest.mark.slow
def test_real_model_scenarios():
    """Real NLI and embedding models (downloaded/cached); run with -m slow."""
    m1 = make_meeting([("Asmee", "I will submit the front end validation report by Friday.")])
    mom = {"action_items": [{"claim_id": f"{m1}_a00000", "task": REPORT, "owner": "Asmee",
                             "dueDate": "2026-10-30", "evidence_ids": ["u00000"]}]}
    ledger.record_commitments(m1, mom, None)
    with db.get_conn() as conn:
        cid = conn.execute("SELECT commitment_id FROM commitments WHERE source_meeting_id = ?", (m1,)).fetchone()[0]
    for statement, expected in [
        ("I have submitted the front end validation report.", "done"),
        ("The front end validation report is postponed until next week.", "delayed"),
        ("I will submit the front end validation report by Friday.", "no_update"),
        ("I have not started the front end validation report yet.", "no_update"),
    ]:
        m2 = make_meeting([("Samarth", "Let us check the open items."), ("Asmee", statement)])
        proposal = next(p for p in ledger.propose_updates(m2) if p["commitment_id"] == cid)
        assert proposal["proposed_status"] == expected, (statement, proposal["scores"])
