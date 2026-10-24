"""
Phase 8.3: scheduled meetings with multiple invitees.

A host schedules a future meeting and invites several people at once. The
meeting is 'scheduled' (no room, recording or extraction), each invitee gets
their own meeting-scoped, expiring, revocable link, and only the owner can
edit, invite, cancel or start it. No email is sent or claimed to be sent.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.api import live_routes, meeting_routes
from app.api.main import app
from app.core import auth, config, db

SECRET = "unit_test_secret_0123456789abcdefghij"
ZONE = "Asia/Kolkata"


@pytest.fixture(autouse=True)
def auth_on(monkeypatch):
    monkeypatch.setattr(config, "AUTH_REQUIRED", True)
    monkeypatch.setattr(config, "LIVEKIT_URL", "wss://unit-test.livekit.invalid")
    monkeypatch.setattr(config, "LIVEKIT_API_KEY", "APIunittest")
    monkeypatch.setattr(config, "LIVEKIT_API_SECRET", SECRET)
    created_rooms = []

    async def fake_room(**kwargs):
        created_rooms.append(kwargs.get("room_name"))
        return {"status": "ok", "room_name": kwargs.get("room_name"), "sid": "RM_test"}

    monkeypatch.setattr(live_routes, "create_live_room", fake_room)
    monkeypatch.setattr(meeting_routes, "run_pipeline", lambda *a, **k: None)
    return created_rooms


def _addr(name):
    """A unique invitee address per test (accounts are unique per email)."""
    return f"{name.lower()}-{uuid4().hex[:8]}@example.com"


def _account(name, email=None):
    """An account; pass the invited address so the invitation's email matches."""
    client = TestClient(app)
    email = email or _addr(name)
    res = client.post("/api/auth/register", json={"email": email, "password": "correct horse 1", "display_name": name})
    assert res.status_code == 200, res.text
    return client, res.json()["user"]


def _future(days=3, hour=10, minute=30):
    local = (datetime.now(ZoneInfo(ZONE)) + timedelta(days=days)).replace(hour=hour, minute=minute)
    return local.strftime("%Y-%m-%dT%H:%M")


def _schedule(client, invitees=(), **extra):
    body = {"title": "Sprint planning", "agenda": "Release scope\nOwners", "start_local": _future(),
            "timezone": ZONE, "duration_minutes": 45, "invitees": list(invitees), **extra}
    return client.post("/api/schedule", json=body)


def _accept(client, token):
    return client.post("/api/auth/invitations/accept", json={"token": token})


def _token(client, room_name, **extra):
    return client.post("/api/live/token", json={"room_name": room_name, "participant_name": "x", **extra})


def _meeting_state(mid):
    with db.get_conn() as conn:
        status = conn.execute("SELECT status FROM meetings WHERE meeting_id = ?", (mid,)).fetchone()[0]
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE meeting_id = ?", (mid,)).fetchone()[0]
                  for t in ("live_meetings", "recording_segments", "jobs", "claims", "utterances")}
    return status, counts


# ---------------------------------------------------------------------------
# Creating
# ---------------------------------------------------------------------------

def test_scheduling_three_invitees_creates_a_scheduled_meeting_and_one_link_each(auth_on):
    host_client, host = _account("Hana")
    res = _schedule(host_client, ["asha@example.com", "Ben@Example.COM ", "chen@example.com", "ben@example.com"])
    assert res.status_code == 200, res.text
    body = res.json()
    mid = body["meeting"]["meeting_id"]

    assert body["email_sent"] is False
    assert body["duplicates_ignored"] == ["ben@example.com"]
    assert [l["email"] for l in body["links"]] == ["asha@example.com", "ben@example.com", "chen@example.com"]
    assert len({l["token"] for l in body["links"]}) == 3                       # one distinct link each
    assert all(l["delivery"]["sent"] is False and "not emailed" in l["delivery"]["message"]
               for l in body["links"])
    assert all(l["link_path"] == f"/?room={body['meeting']['room_name']}&invite={l['token']}" for l in body["links"])

    meeting = body["meeting"]
    assert meeting["status"] == "scheduled" and meeting["timezone"] == ZONE
    assert meeting["scheduled_start_local"] == _future()
    start = datetime.fromisoformat(meeting["scheduled_start_utc"])
    assert start.utcoffset() == timedelta(0)
    assert start.astimezone(ZoneInfo(ZONE)).strftime("%Y-%m-%dT%H:%M") == _future()
    assert [i["status"] for i in meeting["invitees"]] == ["pending"] * 3

    # Nothing live, recorded or extracted; no LiveKit room was opened.
    status, counts = _meeting_state(mid)
    assert status == "scheduled"
    assert counts == {"live_meetings": 0, "recording_segments": 0, "jobs": 0, "claims": 0, "utterances": 0}
    assert auth_on == []
    assert db.get_member_role(mid, host["user_id"]) == "owner"

    listed = {m["meeting_id"]: m for m in host_client.get("/api/meetings").json()}
    assert listed[mid]["status"] == "scheduled"
    assert listed[mid]["schedule"]["title"] == "Sprint planning" and listed[mid]["schedule"]["invitee_count"] == 3


@pytest.mark.parametrize("override,message", [
    ({"start_local": "2020-01-01T10:00"}, "in the past"),
    ({"timezone": "Mars/Olympus"}, "Unknown timezone"),
    ({"timezone": "America/New_York", "start_local": "2030-03-10T02:30"}, "does not exist"),   # DST gap
    ({"start_local": "next tuesday"}, "YYYY-MM-DDTHH:MM"),
    ({"title": "   "}, "Enter a title"),
    ({"duration_minutes": 2}, "Duration"),
    ({"invitees": ["asha@example.com", "not-an-email"]}, "not-an-email"),
])
def test_invalid_schedules_are_refused_and_create_nothing(override, message):
    host_client, host = _account("Hana")
    before = len(db.get_member_meeting_ids(host["user_id"]))
    res = _schedule(host_client, **{"invitees": ["asha@example.com"], **override})
    assert res.status_code == 422 and message in res.json()["detail"]
    assert len(db.get_member_meeting_ids(host["user_id"])) == before


def test_the_host_cannot_invite_themselves():
    host_client, host = _account("Hana")
    res = _schedule(host_client, [host["email"].upper()])
    assert res.status_code == 422 and "host" in res.json()["detail"]


def test_scheduling_needs_sign_in(monkeypatch):
    assert _schedule(TestClient(app), ["asha@example.com"]).status_code == 401
    monkeypatch.setattr(config, "AUTH_REQUIRED", False)
    assert _schedule(TestClient(app), ["asha@example.com"]).status_code == 409


def test_a_repeated_submit_creates_one_meeting():
    host_client, host = _account("Hana")
    first = _schedule(host_client, ["asha@example.com"], request_id="form-123").json()
    again = _schedule(host_client, ["asha@example.com"], request_id="form-123").json()
    assert again["duplicate_request"] is True and again["links"] == []
    assert again["meeting"]["meeting_id"] == first["meeting"]["meeting_id"]
    assert len(again["meeting"]["invitees"]) == 1


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------

def test_an_invitation_admits_to_its_own_meeting_only_and_records_who_accepted():
    host_client, _ = _account("Hana")
    asha = _addr("asha")
    a = _schedule(host_client, [asha]).json()
    b = _schedule(host_client, [asha]).json()
    guest_client, guest = _account("Asha", asha)

    accepted = _accept(guest_client, a["links"][0]["token"])
    assert accepted.status_code == 200
    assert accepted.json()["meeting_id"] == a["meeting"]["meeting_id"]
    assert accepted.json()["room_name"] == a["meeting"]["room_name"]
    assert db.get_member_role(a["meeting"]["meeting_id"], guest["user_id"]) == "member"
    assert db.get_member_role(b["meeting"]["meeting_id"], guest["user_id"]) is None

    invitee = host_client.get(f"/api/meetings/{a['meeting']['meeting_id']}/schedule").json()["meeting"]["invitees"][0]
    assert invitee["status"] == "accepted"
    assert invitee["accepted_by"]["display_name"] == "Asha"
    assert invitee["accepted_by"]["email_matches_invite"] is True

    # Meeting B's data stays closed to this guest.
    assert guest_client.get(f"/api/meetings/{b['meeting']['meeting_id']}/schedule").status_code == 404


def test_another_account_waits_for_the_hosts_explicit_approval():
    host_client, _ = _account("Hana")
    body = _schedule(host_client, [_addr("asha")]).json()
    mid, link = body["meeting"]["meeting_id"], body["links"][0]
    other_client, other = _account("Mallory")             # holds the link, different email

    refused = _accept(other_client, link["token"])
    assert refused.status_code == 409 and "sent to a***@example.com" in refused.json()["detail"]
    assert db.get_member_role(mid, other["user_id"]) is None
    invitee = host_client.get(f"/api/meetings/{mid}/schedule").json()["meeting"]["invitees"][0]
    assert invitee["status"] == "pending" and invitee["approval_request"]["email"] == other["email"]

    outsider, _ = _account("Olga")
    assert outsider.post(f"/api/meetings/{mid}/schedule/invitees/{link['invitee_id']}/approve-account",
                         json={"approve": True}).status_code == 404
    approved = host_client.post(f"/api/meetings/{mid}/schedule/invitees/{link['invitee_id']}/approve-account",
                                json={"approve": True})
    assert approved.status_code == 200
    assert db.get_member_role(mid, other["user_id"]) == "member"          # a participant, never an owner
    assert approved.json()["meeting"]["invitees"][0]["accepted_by"]["email_matches_invite"] is False


def test_copying_a_link_replaces_the_previous_one():
    host_client, _ = _account("Hana")
    asha = _addr("asha")
    body = _schedule(host_client, [asha]).json()
    mid, old = body["meeting"]["meeting_id"], body["links"][0]
    new = host_client.post(f"/api/meetings/{mid}/schedule/invitees/{old['invitee_id']}/link").json()
    assert new["previous_link_revoked"] is True and new["token"] != old["token"]
    guest_client, _ = _account("Asha", asha)
    assert _accept(guest_client, old["token"]).status_code == 404
    assert _accept(guest_client, new["token"]).status_code == 200


def test_revoking_an_invitee_closes_the_link_and_removes_access():
    host_client, _ = _account("Hana")
    asha_email, ben_email = _addr("asha"), _addr("ben")
    body = _schedule(host_client, [asha_email, ben_email]).json()
    mid = body["meeting"]["meeting_id"]
    asha, ben = body["links"]
    asha_client, asha_user = _account("Asha", asha_email)
    assert _accept(asha_client, asha["token"]).status_code == 200

    for link in (asha, ben):
        assert host_client.delete(f"/api/meetings/{mid}/schedule/invitees/{link['invitee_id']}").status_code == 200
    assert db.get_member_role(mid, asha_user["user_id"]) is None
    ben_client, _ = _account("Ben", ben_email)
    assert _accept(ben_client, ben["token"]).status_code == 404
    statuses = [i["status"] for i in host_client.get(f"/api/meetings/{mid}/schedule").json()["meeting"]["invitees"]]
    assert statuses == ["revoked", "revoked"]


def test_adding_invitees_later_skips_people_already_invited():
    host_client, _ = _account("Hana")
    mid = _schedule(host_client, ["asha@example.com"]).json()["meeting"]["meeting_id"]
    res = host_client.post(f"/api/meetings/{mid}/schedule/invitees",
                           json={"emails": ["ASHA@example.com", "dev@example.com", "dev@example.com"]}).json()
    assert [l["email"] for l in res["links"]] == ["dev@example.com"]
    assert res["already_invited"] == ["asha@example.com"] and res["duplicates_ignored"] == ["dev@example.com"]
    assert res["email_sent"] is False and res["meeting"]["invitee_count"] == 2


def test_links_expire_after_the_meeting(monkeypatch):
    host_client, _ = _account("Hana")
    asha = _addr("asha")
    body = _schedule(host_client, [asha]).json()
    end = datetime.fromisoformat(body["meeting"]["scheduled_start_utc"]) + timedelta(minutes=45)
    assert datetime.fromisoformat(body["links"][0]["expires_at"]) >= end + timedelta(days=1)

    guest_client, _ = _account("Asha", asha)
    later = datetime.now(timezone.utc) + timedelta(days=6)     # sessions last 7 days
    monkeypatch.setattr(auth, "_now", lambda: later)
    assert _accept(guest_client, body["links"][0]["token"]).status_code == 404


# ---------------------------------------------------------------------------
# Owner-only actions
# ---------------------------------------------------------------------------

def test_only_the_owner_edits_invites_or_cancels():
    host_client, _ = _account("Hana")
    asha = _addr("asha")
    body = _schedule(host_client, [asha]).json()
    mid, link = body["meeting"]["meeting_id"], body["links"][0]
    guest_client, _ = _account("Asha", asha)
    assert _accept(guest_client, link["token"]).status_code == 200
    outsider, _ = _account("Olga")

    member_view = guest_client.get(f"/api/meetings/{mid}/schedule").json()
    assert member_view["is_owner"] is False and "invitees" not in member_view["meeting"]
    assert member_view["meeting"]["title"] == "Sprint planning"

    for client, expected in ((guest_client, 403), (outsider, 404)):
        assert client.patch(f"/api/meetings/{mid}/schedule", json={"title": "Mine"}).status_code == expected
        assert client.post(f"/api/meetings/{mid}/schedule/invitees",
                           json={"emails": ["x@example.com"]}).status_code == expected
        assert client.post(f"/api/meetings/{mid}/schedule/invitees/{link['invitee_id']}/link").status_code == expected
        assert client.delete(f"/api/meetings/{mid}/schedule/invitees/{link['invitee_id']}").status_code == expected
        assert client.post(f"/api/meetings/{mid}/schedule/cancel").status_code == expected
    assert outsider.get(f"/api/meetings/{mid}/schedule").status_code == 404

    edited = host_client.patch(f"/api/meetings/{mid}/schedule",
                               json={"title": "Sprint planning (moved)", "start_local": _future(days=4, hour=15)})
    assert edited.status_code == 200
    assert edited.json()["meeting"]["title"] == "Sprint planning (moved)"
    assert edited.json()["meeting"]["scheduled_start_local"] == _future(days=4, hour=15)


def test_cancelling_revokes_every_link_and_is_safe_to_repeat(auth_on):
    host_client, _ = _account("Hana")
    asha = _addr("asha")
    body = _schedule(host_client, [asha, _addr("ben")]).json()
    mid, room = body["meeting"]["meeting_id"], body["meeting"]["room_name"]

    assert host_client.post(f"/api/meetings/{mid}/schedule/cancel").json()["meeting"]["status"] == "cancelled"
    assert host_client.post(f"/api/meetings/{mid}/schedule/cancel").status_code == 200      # repeat is harmless
    guest_client, _ = _account("Asha", asha)
    assert _accept(guest_client, body["links"][0]["token"]).status_code == 404
    assert host_client.post(f"/api/meetings/{mid}/schedule/invitees",
                            json={"emails": ["dev@example.com"]}).status_code == 409
    assert host_client.post("/api/live/rooms", json={"room_name": room}).status_code == 410
    assert auth_on == []
    status, counts = _meeting_state(mid)
    assert status == "cancelled" and counts["live_meetings"] == 0
    assert {m["meeting_id"]: m for m in host_client.get("/api/meetings").json()}[mid]["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Starting
# ---------------------------------------------------------------------------

def test_the_owner_starts_the_scheduled_meeting_and_invitees_join_as_participants():
    host_client, host = _account("Hana")
    asha = _addr("asha")
    body = _schedule(host_client, [asha]).json()
    mid, room = body["meeting"]["meeting_id"], body["meeting"]["room_name"]
    guest_client, guest = _account("Asha", asha)
    assert _accept(guest_client, body["links"][0]["token"]).status_code == 200

    # Before the start: the guest is told it has not started; an outsider
    # holding only the room code learns nothing and cannot take the room.
    early = _token(guest_client, room)
    assert early.status_code == 409 and "not started" in early.json()["detail"]
    outsider, _ = _account("Olga")
    assert _token(outsider, room).status_code == 404
    assert outsider.post("/api/live/rooms", json={"room_name": room}).status_code == 409
    assert guest_client.post("/api/live/rooms", json={"room_name": room}).status_code == 409

    started = host_client.post("/api/live/rooms", json={"room_name": room, "title": "ignored"})
    assert started.status_code == 200 and started.json()["meeting_id"] == mid       # same meeting, not a new one
    assert host_client.post("/api/live/rooms", json={"room_name": room}).json()["meeting_id"] == mid
    status, counts = _meeting_state(mid)
    assert status == "live" and counts["live_meetings"] == 1 and counts["recording_segments"] == 0
    assert db.get_scheduled_meeting(mid)["status"] == "started"
    assert db.get_live_meeting(mid)["title"] == "Sprint planning"

    host_join = _token(host_client, room, is_host=True)
    assert host_join.status_code == 200 and host_join.json()["is_host"] is True
    guest_join = _token(guest_client, room, is_host=True)
    assert guest_join.status_code == 200
    assert guest_join.json()["is_host"] is False and guest_join.json()["meeting_id"] == mid
    assert guest_join.json()["consented"] is False                  # consent is still asked for, never assumed
    assert _token(outsider, room).status_code == 403                 # a room code alone never grants access

    assert host_client.post(f"/api/meetings/{mid}/schedule/cancel").status_code == 409
    assert host_client.patch(f"/api/meetings/{mid}/schedule", json={"title": "Late"}).status_code == 409
    assert db.get_meeting_owner_ids(mid)[0] == host["user_id"]
    assert db.get_member_role(mid, guest["user_id"]) == "member"


def test_deleting_a_scheduled_meeting_removes_its_schedule_and_invitees():
    from app.services.meeting_deletion import delete_meeting

    host_client, _ = _account("Hana")
    mid = _schedule(host_client, ["asha@example.com"]).json()["meeting"]["meeting_id"]
    delete_meeting(mid)
    assert db.get_scheduled_meeting(mid) is None and db.list_invitees(mid) == []
