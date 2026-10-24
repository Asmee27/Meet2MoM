"""
Phase 7: accounts, sessions and meeting access, with AUTH_REQUIRED on.

Covers: signed-out users get 401 on meeting data; non-members get 404 for
another user's meeting; the uploader and room creator become owners; the
LiveKit identity, name and host role come from the session and membership,
never from the client; invitations grant one meeting only; the reviewer in the
audit log is the signed-in user.
"""
from __future__ import annotations

import io

import jwt
import pytest
from fastapi.testclient import TestClient

from app.api import live_routes, meeting_routes
from app.api.main import app
from app.core import config, db
from tests.scenario_helpers import make_meeting

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


def _client(email, name="User", password="correct horse 1"):
    client = TestClient(app)
    res = client.post("/api/auth/register", json={"email": email, "password": password, "display_name": name})
    assert res.status_code == 200, res.text
    return client, res.json()["user"]


def _unique(prefix):
    from uuid import uuid4
    return f"{prefix}-{uuid4().hex[:8]}@example.test"


def _owned_meeting(user):
    mid = make_meeting([("SPEAKER_00", "We decided to ship on Friday."), ("SPEAKER_01", "I will write the notes.")])
    db.add_meeting_member(mid, user["user_id"], "owner")
    return mid


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

def test_register_login_logout_and_password_hashing():
    email = _unique("alice")
    client, user = _client(email, "Alice", "s3cret-password")
    stored = db.get_user_by_email(email)
    assert stored["password_hash"].startswith("scrypt$") and "s3cret-password" not in stored["password_hash"]
    assert client.get("/api/auth/me").json()["user"]["email"] == email

    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401

    fresh = TestClient(app)
    assert fresh.post("/api/auth/login", json={"email": email, "password": "wrong-password"}).status_code == 401
    assert fresh.post("/api/auth/login", json={"email": email.upper(), "password": "s3cret-password"}).status_code == 200
    assert fresh.get("/api/auth/me").json()["user"]["user_id"] == user["user_id"]


@pytest.mark.parametrize("payload, status", [
    ({"email": "not-an-email", "password": "long-enough", "display_name": "X"}, 422),
    ({"email": "short@example.test", "password": "short", "display_name": "X"}, 422),
    ({"email": "noname@example.test", "password": "long-enough", "display_name": " "}, 422),
])
def test_registration_is_validated(payload, status):
    assert TestClient(app).post("/api/auth/register", json=payload).status_code == status


def test_duplicate_email_is_rejected():
    email = _unique("dup")
    _client(email)
    res = TestClient(app).post("/api/auth/register", json={"email": email, "password": "another-one", "display_name": "Y"})
    assert res.status_code == 409


# ---------------------------------------------------------------------------
# Meeting access
# ---------------------------------------------------------------------------

PROTECTED = [
    ("get", "/api/meetings/{mid}/status"),
    ("get", "/api/meetings/{mid}/transcript"),
    ("get", "/api/meetings/{mid}/claims"),
    ("get", "/api/meetings/{mid}/review"),
    ("get", "/api/meetings/{mid}/audit-log"),
    ("get", "/api/meetings/{mid}/export/json?preview=true"),
    ("get", "/api/meetings/{mid}/audio"),
    ("get", "/api/download-mom/{mid}"),
    ("post", "/api/meetings/{mid}/approve"),
]


@pytest.mark.parametrize("method, path", PROTECTED)
def test_signed_out_users_cannot_read_meeting_data(method, path):
    _, owner = _client(_unique("owner"))
    mid = _owned_meeting(owner)
    res = getattr(TestClient(app), method)(path.format(mid=mid), **({"json": {"reviewer": "x"}} if method == "post" else {}))
    assert res.status_code == 401


@pytest.mark.parametrize("method, path", PROTECTED)
def test_non_members_cannot_see_another_users_meeting(method, path):
    _, owner = _client(_unique("owner"))
    mid = _owned_meeting(owner)
    stranger, _ = _client(_unique("stranger"))
    res = getattr(stranger, method)(path.format(mid=mid), **({"json": {"reviewer": "x"}} if method == "post" else {}))
    assert res.status_code == 404


def test_members_can_read_their_meeting_and_the_list_is_filtered():
    owner_client, owner = _client(_unique("owner"))
    mid = _owned_meeting(owner)
    other_client, other = _client(_unique("other"))
    _owned_meeting(other)

    assert owner_client.get(f"/api/meetings/{mid}/transcript").status_code == 200
    mine = {m["meeting_id"] for m in owner_client.get("/api/meetings").json()}
    assert mid in mine
    assert mid not in {m["meeting_id"] for m in other_client.get("/api/meetings").json()}


def test_uploader_becomes_the_owner():
    client, user = _client(_unique("uploader"))
    res = client.post("/api/upload", files={"file": ("talk.wav", io.BytesIO(b"RIFF....WAVEfmt "), "audio/wav")})
    assert res.status_code in (200, 202), res.text
    mid = res.json().get("meeting_id") or res.json().get("file_id")
    assert db.get_member_role(mid, user["user_id"]) == "owner"


def test_reviewer_in_the_audit_log_is_the_signed_in_user():
    client, user = _client(_unique("rev"), "Reviewer Ray")
    mid = _owned_meeting(user)
    res = client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Somebody Else"})
    assert res.status_code == 200, res.text
    snapshot = db.get_latest_approved_snapshot(mid)
    assert "Reviewer Ray" in snapshot["approved_by"] and user["email"] in snapshot["approved_by"]
    assert "Somebody Else" not in snapshot["approved_by"]


# ---------------------------------------------------------------------------
# LiveKit identity, host role and invitations
# ---------------------------------------------------------------------------

def _claims(token):
    return jwt.decode(token, SECRET, algorithms=["HS256"], options={"verify_aud": False})


def test_server_chooses_identity_and_host_role():
    host_client, host = _client(_unique("host"), "Hana Host")
    room = host_client.post("/api/live/rooms", json={"title": "Sync"}).json()
    mid, room_name = room["meeting_id"], room["room_name"] if "room_name" in room else None
    room_name = room_name or db.get_live_meeting(mid)["room_name"]
    assert db.get_member_role(mid, host["user_id"]) == "owner"

    token = host_client.post("/api/live/token", json={
        "room_name": room_name, "participant_name": "Someone Else", "participant_identity": "evil", "is_host": False,
    }).json()
    assert token["participant_identity"] == host["user_id"] and token["participant_name"] == "Hana Host"
    assert token["is_host"] is True                                  # role comes from ownership
    assert _claims(token["token"])["sub"] == host["user_id"]

    # A signed-in stranger without an invitation cannot join.
    guest_client, guest = _client(_unique("guest"), "Gus Guest")
    assert guest_client.post("/api/live/token", json={"room_name": room_name, "participant_name": "G"}).status_code == 403
    # Members cannot create invitations, signed-out users cannot get tokens.
    assert guest_client.post(f"/api/auth/meetings/{mid}/invitations").status_code == 403
    assert TestClient(app).post("/api/live/token", json={"room_name": room_name, "participant_name": "x"}).status_code == 401

    invite = host_client.post(f"/api/auth/meetings/{mid}/invitations").json()
    accepted = guest_client.post("/api/auth/invitations/accept", json={"token": invite["token"]}).json()
    assert accepted["meeting_id"] == mid and db.get_member_role(mid, guest["user_id"]) == "member"

    guest_token = guest_client.post("/api/live/token", json={
        "room_name": room_name, "participant_name": "Hana Host",
        "participant_identity": host["user_id"], "is_host": True,
    }).json()
    assert guest_token["participant_identity"] == guest["user_id"]   # cannot claim the host's identity
    assert guest_token["participant_name"] == "Gus Guest"
    assert guest_token["is_host"] is False                           # cannot make itself host

    # The invitation grants this meeting only.
    other_mid = _owned_meeting(host)
    assert guest_client.get(f"/api/meetings/{other_mid}/transcript").status_code == 404


def test_invalid_invitation_is_rejected():
    client, _ = _client(_unique("x"))
    assert client.post("/api/auth/invitations/accept", json={"token": "nope"}).status_code == 404


def test_legacy_open_mode_still_works_when_auth_is_off(monkeypatch):
    monkeypatch.setattr(config, "AUTH_REQUIRED", False)
    mid = make_meeting([("SPEAKER_00", "Hello.")])
    assert TestClient(app).get(f"/api/meetings/{mid}/transcript").status_code == 200
    assert TestClient(app).get("/api/auth/me").json() == {"auth_required": False, "user": None}
