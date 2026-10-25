"""
Phase 11: commitments belong to the meeting owner's ledger.

Found while preparing the demo: ledger_owner() read owner_user_id from
db.get_meeting(), which never returns it, so every commitment was stored with
no owner and a signed-in user's ledger (GET /api/commitments) was always
empty, and a follow-up meeting found nothing to propose.
"""
from __future__ import annotations

import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core import config, db
from app.schemas.utterance import Utterance
from app.services.ledger import commitment_ledger
from tests.scenario_helpers import add_action, record_extraction
from tests.semantic_fakes import install_semantic_fakes


@pytest.fixture(autouse=True)
def auth_on(monkeypatch):
    monkeypatch.setattr(config, "AUTH_REQUIRED", True)


def _account(name):
    client = TestClient(app)
    res = client.post("/api/auth/register", json={"email": f"{name.lower()}-{uuid4().hex[:8]}@example.com",
                                                  "password": "correct horse 1", "display_name": name})
    assert res.status_code == 200, res.text
    return client, res.json()["user"]


def _owned_meeting(owner_id, lines):
    mid = f"own-{uuid4().hex[:10]}"
    db.insert_meeting(mid, filename="meeting.wav")
    db.add_meeting_member(mid, owner_id, "owner")
    db.bulk_insert_utterances(mid, [
        Utterance(meeting_id=mid, utt_id=f"u{i:05d}", start=i * 5.0, end=i * 5.0 + 4, speaker_id=spk,
                  speaker_name=name, text=text, asr_conf=0.9, spk_conf=1.0, overlap=False, conf_source="numeric")
        for i, (spk, name, text) in enumerate(lines)
    ])
    return mid


def test_approved_action_items_appear_in_the_owners_ledger_only(monkeypatch):
    install_semantic_fakes(monkeypatch)
    host_client, host = _account("Samarth")
    other_client, _ = _account("Olga")
    mid = _owned_meeting(host["user_id"], [
        ("u_guest", "Asmee", "I will submit the front end validation report by Friday."),
    ])
    add_action(mid, 0, "Submit the front end validation report", ["u00000"], owner="Asmee",
               owner_type="explicit_first_person", deadline_text="by Friday", deadline_resolved="2026-10-30")
    record_extraction(mid)
    host_client.post(f"/api/verify/{mid}")
    approved = host_client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "x"})
    assert approved.status_code == 200 and approved.json()["commitments_recorded"] == 1

    assert commitment_ledger.ledger_owner(mid) == host["user_id"]
    mine = [c for c in host_client.get("/api/commitments").json()["commitments"] if c["source_meeting_id"] == mid]
    assert len(mine) == 1
    assert mine[0]["status"] == "open" and mine[0]["assignee"] == "Asmee" and mine[0]["evidence_ids"] == ["u00000"]
    assert all(c["source_meeting_id"] != mid for c in other_client.get("/api/commitments").json()["commitments"])


def test_migration_gives_ownerless_commitments_their_meetings_owner(tmp_path):
    path = tmp_path / "v9.db"
    db.init_db(path)
    with db.get_conn(path) as conn:
        for uid in ("u_owner", "u_member_owner"):
            conn.execute("INSERT INTO users (user_id, email, display_name, password_hash, created_at) "
                         "VALUES (?, ?, 'n', 'h', 'x')", (uid, f"{uid}@example.com"))
        conn.execute("INSERT INTO meetings (meeting_id, created_at, status, owner_user_id) VALUES ('m1', 'x', 'approved', 'u_owner')")
        conn.execute("INSERT INTO meetings (meeting_id, created_at, status) VALUES ('m2', 'x', 'approved')")
        conn.execute("INSERT INTO meeting_members (meeting_id, user_id, role, added_at) VALUES ('m2', 'u_member_owner', 'owner', 'x')")
        conn.execute("INSERT INTO meetings (meeting_id, created_at, status) VALUES ('m3', 'x', 'approved')")
        for cid, mid in (("c1", "m1"), ("c2", "m2"), ("c3", "m3")):
            conn.execute(
                "INSERT INTO commitments (commitment_id, ledger_owner_id, source_meeting_id, source_item_id, text, "
                "evidence_ids, status, created_at, updated_at) VALUES (?, NULL, ?, 'a1', 't', '[]', 'open', 'x', 'x')",
                (cid, mid))
        conn.execute("PRAGMA user_version = 9")
    raw = sqlite3.connect(path)
    db._migrate(raw)
    raw.commit()
    owners = dict(raw.execute("SELECT commitment_id, ledger_owner_id FROM commitments").fetchall())
    status = dict(raw.execute("SELECT commitment_id, status FROM commitments").fetchall())
    assert owners == {"c1": "u_owner", "c2": "u_member_owner", "c3": None}     # no owner known: left alone
    assert set(status.values()) == {"open"}                                     # nothing else changed
    assert raw.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    raw.close()
