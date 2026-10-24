"""
Phase 8.1: an invited guest joins a live meeting the host is already in.

Reported: after copying the invite link, a new guest account saw the host's
state ("host already joined") instead of joining. The server side must give
each account its own identity, give the host role only to the owner, and let
an invited member join whether or not the host is already connected.
"""
from __future__ import annotations

from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api import live_routes, meeting_routes
from app.api.main import app
from app.core import config, db

SECRET = "unit_test_secret_0123456789abcdefghij"


@pytest.fixture(autouse=True)
def auth_on(monkeypatch):
    monkeypatch.setattr(config, "AUTH_REQUIRED", True)
    monkeypatch.setattr(config, "LIVEKIT_URL", "wss://unit-test.livekit.invalid")
    monkeypatch.setattr(config, "LIVEKIT_API_KEY", "APIunittest")
    monkeypatch.setattr(config, "LIVEKIT_API_SECRET", SECRET)

    async def fake_room(**kwargs):
        return {"status": "ok", "room_name": kwargs.get("room_name"), "sid": "RM_test"}

    monkeypatch.setattr(live_routes, "create_live_room", fake_room)
    monkeypatch.setattr(meeting_routes, "run_pipeline", lambda *a, **k: None)


def _account(name):
    client = TestClient(app)
    res = client.post("/api/auth/register", json={
        "email": f"{name.lower()}-{uuid4().hex[:8]}@example.test",
        "password": "correct horse 1", "display_name": name,
    })
    assert res.status_code == 200, res.text
    return client, res.json()["user"]


def _token(client, room_name, **extra):
    return client.post("/api/live/token", json={"room_name": room_name, "participant_name": "x", **extra})


def _sub(token):
    return jwt.decode(token, SECRET, algorithms=["HS256"], options={"verify_aud": False})["sub"]


def _hosted_room(host_client):
    room_name = f"livekit-m2m-{uuid4().hex[:6]}"
    room = host_client.post("/api/live/rooms", json={"room_name": room_name, "title": "Sync"})
    assert room.status_code == 200, room.text
    return room.json()["meeting_id"], room_name


def test_invited_guest_joins_while_the_host_is_in_the_room():
    host_client, host = _account("Hana")
    mid, room = _hosted_room(host_client)

    host_join = _token(host_client, room, is_host=True)
    assert host_join.status_code == 200 and host_join.json()["is_host"] is True
    assert db.get_live_meeting(mid)["host_identity"] == host["user_id"]      # host is "in the room"

    invite = host_client.post(f"/api/auth/meetings/{mid}/invitations").json()
    guest_client, guest = _account("Gus")
    accepted = guest_client.post("/api/auth/invitations/accept", json={"token": invite["token"]})
    assert accepted.status_code == 200
    assert accepted.json() == {"meeting_id": mid, "room_name": room}         # the client joins this room

    # The client asked for host (as the old join form did); the server refuses
    # quietly by role, not with "already has a host".
    guest_join = _token(guest_client, room, is_host=True, participant_identity=host["user_id"])
    assert guest_join.status_code == 200, guest_join.text
    body = guest_join.json()
    assert body["is_host"] is False
    assert body["participant_identity"] == guest["user_id"] != host["user_id"]
    assert _sub(body["token"]) == guest["user_id"]
    assert body["participant_name"] == "Gus"

    # The host refreshing or reconnecting stays the host, with the same identity.
    again = _token(host_client, room).json()
    assert again["is_host"] is True and again["participant_identity"] == host["user_id"]

    roster = {p["identity"]: p for p in db.get_live_participants(mid)}
    assert roster[host["user_id"]]["is_host"] and not roster[guest["user_id"]]["is_host"]


def test_a_guest_cannot_take_the_room_over_by_creating_it():
    host_client, host = _account("Hana")
    mid, room = _hosted_room(host_client)
    guest_client, guest = _account("Gus")

    # The old join form called "create room" for guests; it must not grant anything.
    assert guest_client.post("/api/live/rooms", json={"room_name": room, "title": "x"}).status_code == 409
    assert db.get_member_role(mid, guest["user_id"]) is None
    assert db.get_member_role(mid, host["user_id"]) == "owner"


def test_a_guest_listed_as_second_owner_by_the_old_bug_joins_as_participant():
    host_client, host = _account("Hana")
    mid, room = _hosted_room(host_client)
    assert _token(host_client, room).json()["is_host"] is True
    guest_client, guest = _account("Gus")
    db.add_meeting_member(mid, guest["user_id"], "owner")          # state left by the old check

    res = _token(guest_client, room, is_host=True)
    assert res.status_code == 200, res.text                        # not "already has a host"
    assert res.json()["is_host"] is False
    assert _token(host_client, room).json()["is_host"] is True


def test_a_room_code_alone_does_not_admit_a_third_account():
    host_client, _ = _account("Hana")
    mid, room = _hosted_room(host_client)
    assert _token(host_client, room).status_code == 200
    invite = host_client.post(f"/api/auth/meetings/{mid}/invitations").json()
    guest_client, _ = _account("Gus")
    guest_client.post("/api/auth/invitations/accept", json={"token": invite["token"]})
    assert _token(guest_client, room).status_code == 200

    stranger_client, _ = _account("Sam")
    res = _token(stranger_client, room, is_host=True)
    assert res.status_code == 403 and "invitation" in res.json()["detail"]
    assert _token(TestClient(app), room).status_code == 401


def test_an_invitation_admits_to_its_own_meeting_only():
    host_client, _ = _account("Hana")
    mid_a, room_a = _hosted_room(host_client)
    _, room_b = _hosted_room(host_client)
    invite_a = host_client.post(f"/api/auth/meetings/{mid_a}/invitations").json()

    guest_client, _ = _account("Gus")
    guest_client.post("/api/auth/invitations/accept", json={"token": invite_a["token"]})
    assert _token(guest_client, room_a).status_code == 200
    assert _token(guest_client, room_b).status_code == 403


def test_invalid_or_repeated_invitations():
    host_client, host = _account("Hana")
    mid, room = _hosted_room(host_client)
    invite = host_client.post(f"/api/auth/meetings/{mid}/invitations").json()
    guest_client, guest = _account("Gus")

    bad = guest_client.post("/api/auth/invitations/accept", json={"token": invite["token"] + "x"})
    assert bad.status_code == 404 and "invalid or has expired" in bad.json()["detail"]

    for _ in range(3):                                     # repeated clicks / refreshes
        assert guest_client.post("/api/auth/invitations/accept", json={"token": invite["token"]}).status_code == 200
    with db.get_conn() as conn:
        rows = conn.execute("SELECT role FROM meeting_members WHERE meeting_id = ? AND user_id = ?",
                            (mid, guest["user_id"])).fetchall()
    assert [r["role"] for r in rows] == ["member"]

    # The host opening their own invite link stays the owner and host.
    host_client.post("/api/auth/invitations/accept", json={"token": invite["token"]})
    assert db.get_member_role(mid, host["user_id"]) == "owner"
    assert _token(host_client, room).json()["is_host"] is True
