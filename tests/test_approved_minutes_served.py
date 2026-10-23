"""
Phase 8: once minutes are approved, the minutes page and the PDF come from the
approved snapshot. Regenerating a draft from (possibly held) claims must not
replace the approved minutes or their PDF.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import meeting_routes
from app.api.main import app
from app.core import db
from tests.scenario_helpers import FILLER, add_action, make_meeting, record_extraction
from tests.semantic_fakes import install_semantic_fakes


def _approved_meeting(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [("SPEAKER_01", "I will write the release notes by Friday.")])
    add_action(mid, 0, "Write the release notes", ["u00002"], owner="SPEAKER_01",
               owner_type="explicit_first_person", deadline_text="Friday")
    record_extraction(mid)
    client = TestClient(app)
    assert client.post(f"/api/verify/{mid}").status_code == 200
    # A reviewer confirms the item by editing it, then approves.
    item_id = client.get(f"/api/meetings/{mid}/review").json()["items"][0]["item_id"]
    assert client.put(f"/api/meetings/{mid}/review/items/{item_id}",
                      json={"text": "Write the release notes (reviewed)", "state": "accepted"}).status_code == 200
    assert client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Rita"}).status_code == 200
    return client, mid


def test_generate_mom_returns_the_approved_snapshot(monkeypatch):
    client, mid = _approved_meeting(monkeypatch)
    built = []
    monkeypatch.setattr(meeting_routes, "build_pdf", lambda file_id, mom: built.append(mom) or _fake_pdf(file_id))

    res = client.post(f"/api/generate-mom/{mid}")
    assert res.status_code == 200, res.text
    body = res.json()
    snapshot = db.get_latest_approved_snapshot(mid)["mom_payload"]
    assert body["status"] == "approved_snapshot"
    assert [a["task"] for a in body["mom"]["action_items"]] == ["Write the release notes (reviewed)"]
    assert body["mom"]["action_items"] == snapshot["action_items"]
    assert built and built[-1]["status"] == "approved"


def test_download_builds_the_pdf_from_the_snapshot_not_a_stale_file(monkeypatch):
    client, mid = _approved_meeting(monkeypatch)
    built = []
    monkeypatch.setattr(meeting_routes, "build_pdf", lambda file_id, mom: built.append(mom) or _fake_pdf(file_id))

    assert client.get(f"/api/download-mom/{mid}").status_code == 200
    assert built[-1]["status"] == "approved"
    assert [a["task"] for a in built[-1]["action_items"]] == ["Write the release notes (reviewed)"]


def test_reopened_minutes_are_generated_as_a_draft_again(monkeypatch):
    client, mid = _approved_meeting(monkeypatch)
    assert client.post(f"/api/meetings/{mid}/reopen",
                       json={"reviewer": "Rita", "reason": "Fix a date"}).status_code == 200
    body = client.post(f"/api/generate-mom/{mid}").json()
    assert body["status"] == "mom_generated"


def _fake_pdf(file_id):
    from app.core.storage import MeetingStorage

    path = MeetingStorage(file_id).path_for("mom", "Meet2MoM-Meeting-Minutes.pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4 test")
    return path
