"""
Phase 9: evidence-linked commitment ledger, two-meeting vertical slice.

Meeting 1 is processed (real verification on deterministic model fakes) and
approved; its approved action item becomes a commitment. Meeting 2's
transcript produces a cited proposal (done / delayed / no_update) that changes
nothing until a reviewer confirms or corrects it.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core import config, db
from app.core.jobs import run_pipeline
from tests.scenario_helpers import make_meeting
from tests.semantic_fakes import install_semantic_fakes
from tests.test_job_runner import _meeting, _stub_stages


@pytest.fixture
def approved_meeting(monkeypatch):
    """Meeting 1: processed, reviewed and approved. Returns (client, meeting_id, commitment)."""
    install_semantic_fakes(monkeypatch)
    mid, storage = _meeting()
    _stub_stages(monkeypatch, storage)
    run_pipeline(mid, Path("recording.wav"))
    client = TestClient(app)
    review = client.get(f"/api/meetings/{mid}/review").json()
    action = next(i for i in review["items"] if i["item_type"] == "action")
    if action.get("review_status") != "accepted_from_held" and action.get("is_held"):
        client.put(f"/api/meetings/{mid}/review/items/{action['item_id']}",
                   json={"review_status": "accepted_from_held", "reviewer": "Rita"})
    approved = client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Rita Reviewer"}).json()
    assert approved["status"] == "approved" and approved["commitments_recorded"] >= 1
    created = client.get(f"/api/meetings/{mid}/commitments").json()["created_here"]
    commitment = next(c for c in created if c["text"] == "Set up the database cluster")
    return client, mid, commitment


def test_approved_action_item_becomes_an_evidence_linked_commitment(approved_meeting):
    _, mid, commitment = approved_meeting
    assert commitment["status"] == "open"
    assert commitment["source_meeting_id"] == mid
    assert commitment["evidence_ids"] == ["u00002"]                 # the cited utterance
    assert commitment["assignee"] == "Bob"
    assert commitment["assignee_user_id"] is None                   # never linked by name
    assert commitment["deadline"]                                   # carried from the minutes


def _follow_up(lines):
    return make_meeting(lines)


@pytest.mark.parametrize("lines, proposed", [
    ([("Bob", "Quick update: the database cluster set up is done now."),
      ("Ann", "Great, thanks.")], "done"),
    ([("Bob", "The database cluster set up is not done, it slipped to Monday."),
      ("Ann", "Okay.")], "delayed"),
    ([("Ann", "Let us talk about the marketing budget."),
      ("Bob", "The office coffee machine is broken again.")], "no_update"),
])
def test_follow_up_meeting_proposes_a_cited_status_without_changing_it(approved_meeting, lines, proposed):
    client, _, commitment = approved_meeting
    mid2 = _follow_up(lines)
    follow_ups = client.post(f"/api/meetings/{mid2}/commitments/propose").json()["follow_ups"]
    update = next(f for f in follow_ups if f["commitment_id"] == commitment["commitment_id"])
    assert update["proposed_status"] == proposed
    assert update["review_status"] == "proposed"
    if proposed == "no_update":
        assert update["evidence"] == []
    else:
        assert update["evidence"] and update["evidence"][0]["speaker"] == "Bob"   # cited utterance
        assert set(update["evidence_ids"]) <= {u.utt_id for u in db.get_utterances(mid2)}
    # A proposal alone never changes the commitment.
    ledger = client.get("/api/commitments").json()["commitments"]
    assert next(c for c in ledger if c["commitment_id"] == commitment["commitment_id"])["status"] == "open"


def test_reviewer_confirms_or_corrects_and_history_is_kept(approved_meeting):
    client, _, commitment = approved_meeting
    mid2 = _follow_up([("Bob", "Quick update: the database cluster set up is done now.")])
    client.post(f"/api/meetings/{mid2}/commitments/propose")

    # The reviewer disagrees: the work is only delayed.
    res = client.post(f"/api/meetings/{mid2}/commitments/{commitment['commitment_id']}/review",
                      json={"status": "delayed", "note": "Only partly done", "reviewer": "Rita"}).json()
    assert res["review_status"] == "corrected" and res["final_status"] == "delayed"

    ledger = client.get("/api/commitments").json()["commitments"]
    entry = next(c for c in ledger if c["commitment_id"] == commitment["commitment_id"])
    assert entry["status"] == "delayed"
    (history,) = entry["history"]
    assert history["proposed_status"] == "done" and history["final_status"] == "delayed"
    assert history["review_status"] == "corrected" and history["reviewer"] == "Rita"
    assert history["note"] == "Only partly done"

    # A delayed commitment is still followed up in the next meeting.
    mid3 = _follow_up([("Bob", "Final update: the database cluster set up is done now.")])
    follow = client.post(f"/api/meetings/{mid3}/commitments/propose").json()["follow_ups"]
    assert any(f["commitment_id"] == commitment["commitment_id"] for f in follow)


def test_no_update_review_keeps_the_commitment_open(approved_meeting):
    client, _, commitment = approved_meeting
    mid2 = _follow_up([("Ann", "Let us talk about the marketing budget.")])
    client.post(f"/api/meetings/{mid2}/commitments/propose")
    res = client.post(f"/api/meetings/{mid2}/commitments/{commitment['commitment_id']}/review",
                      json={"status": "no_update", "reviewer": "Rita"}).json()
    assert res["review_status"] == "confirmed"
    ledger = client.get("/api/commitments").json()["commitments"]
    assert next(c for c in ledger if c["commitment_id"] == commitment["commitment_id"])["status"] == "open"


def test_invalid_reviews_are_rejected(approved_meeting):
    client, _, commitment = approved_meeting
    mid2 = _follow_up([("Ann", "Nothing relevant here.")])
    client.post(f"/api/meetings/{mid2}/commitments/propose")
    url = f"/api/meetings/{mid2}/commitments/{commitment['commitment_id']}/review"
    assert client.post(url, json={"status": "finished", "reviewer": "Rita"}).status_code == 422
    assert client.post(url, json={"status": "done"}).status_code == 422                     # no reviewer
    assert client.post(f"/api/meetings/{mid2}/commitments/c_missing/review",
                       json={"status": "done", "reviewer": "Rita"}).status_code == 404


def test_ledger_is_private_to_its_owner(monkeypatch):
    from uuid import uuid4

    from app.services.ledger import commitment_ledger

    monkeypatch.setattr(config, "AUTH_REQUIRED", True)
    owner_client, other_client = TestClient(app), TestClient(app)
    owner = owner_client.post("/api/auth/register", json={
        "email": f"o-{uuid4().hex[:6]}@example.test", "password": "long-password", "display_name": "Olga"}).json()["user"]
    other_client.post("/api/auth/register", json={
        "email": f"x-{uuid4().hex[:6]}@example.test", "password": "long-password", "display_name": "Xavi"})

    mid = make_meeting([("Bob", "I will set up the database cluster by Friday.")])
    db.add_meeting_member(mid, owner["user_id"], "owner")
    mom = {"action_items": [{"claim_id": f"{mid}_a00000", "task": "Set up the database cluster",
                             "owner": "Bob", "dueDate": "Friday", "evidence_ids": ["u00000"]}]}
    assert commitment_ledger.record_commitments(mid, mom, owner["user_id"]) == 1

    assert len(owner_client.get("/api/commitments").json()["commitments"]) == 1
    assert other_client.get("/api/commitments").json()["commitments"] == []
    assert other_client.get(f"/api/meetings/{mid}/commitments").status_code == 404
    assert TestClient(app).get("/api/commitments").status_code == 401


def test_an_utterance_about_another_task_is_never_cited():
    """Real-model regression: "the mobile check is not done" was cited as the login page being delayed."""
    from app.services.ledger.commitment_ledger import _on_topic

    login = "update the login page with loading and error states by Friday"
    mobile = "check the mobile layout before the demo"
    assert _on_topic(login, "The login page update with loading and error states is done now.")
    assert _on_topic(mobile, "The mobile layout check is not done yet, it slipped to Monday.")
    assert not _on_topic(login, "The mobile layout check is not done yet, it slipped to Monday.")
    assert not _on_topic(mobile, "The login page update with loading and error states is done now.")


def test_hypotheses_leave_out_the_deadline():
    """A trailing deadline made the real NLI model answer neutral to finished work."""
    from app.services.ledger.commitment_ledger import _hypotheses

    assert _hypotheses("Update the login page by Friday.")["done"] == "Update the login page is done."
    assert _hypotheses("check the mobile layout before the demo")["delayed"] == "check the mobile layout is delayed."
    assert _hypotheses("share the results at the next meeting")["done"] == "share the results is done."
    assert _hypotheses("by Friday")["done"] == "by Friday is done."     # never empties the task
