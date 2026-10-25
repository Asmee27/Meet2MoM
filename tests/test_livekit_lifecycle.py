"""
Phase 11: LiveKit room lifecycle and clock diagnosis.

Found on 25 Oct 2026: this PC's Windows clock had never synced and was 8.5 h
behind real UTC. LiveKit rejected every token ("401 invalid token"); room
creation's 401 was swallowed as "lazy creation on join"; and ending a room
LiveKit had already closed (everyone gone) answered "404 requested room does
not exist", which the API reported as a 500 after the meeting had in fact
ended correctly. LiveKit itself is faked here; nothing touches the network.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from livekit.api import ServerError

from app.api import live_routes
from app.api.main import app
from app.core import config, db
from app.services.live import livekit_service

SECRET = "unit_test_secret_0123456789abcdefghij"


@pytest.fixture(autouse=True)
def livekit_configured(monkeypatch):
    monkeypatch.setattr(config, "LIVEKIT_URL", "wss://unit-test.livekit.cloud")
    monkeypatch.setattr(config, "LIVEKIT_API_KEY", "APIunittest")
    monkeypatch.setattr(config, "LIVEKIT_API_SECRET", SECRET)
    livekit_service._skew_cache.update(at=0.0, value=None)


class _FakeRoomService:
    def __init__(self, error=None):
        self.error = error

    async def delete_room(self, request):
        if self.error:
            raise self.error

    async def create_room(self, request):
        raise self.error


class _FakeLiveKit:
    def __init__(self, error=None):
        self.room = _FakeRoomService(error)

    async def aclose(self):
        pass


def _use_fake_livekit(monkeypatch, error=None):
    async def fake_api():
        return _FakeLiveKit(error)

    monkeypatch.setattr(livekit_service, "get_livekit_api", fake_api)


def test_ending_a_room_livekit_already_closed_is_reported_as_closed(monkeypatch):
    _use_fake_livekit(monkeypatch, ServerError("not_found", "requested room does not exist", status=404))
    result = asyncio.run(livekit_service.end_live_room("room-a"))
    assert result["status"] == "already_closed" and "already closed" in result["note"]


@pytest.mark.parametrize("status", [401, 500])
def test_other_end_room_errors_are_not_hidden(monkeypatch, status):
    _use_fake_livekit(monkeypatch, ServerError("unknown", "boom", status=status))
    with pytest.raises(ServerError):
        asyncio.run(livekit_service.end_live_room("room-a"))


def test_ending_the_meeting_after_livekit_closed_the_room_keeps_the_recording_state(monkeypatch):
    _use_fake_livekit(monkeypatch, ServerError("not_found", "requested room does not exist", status=404))
    room = f"lc-{uuid4().hex[:6]}"
    meeting = db.create_live_meeting(room, title="Lifecycle")
    host = "host-1"
    assert db.claim_live_meeting_host(meeting["meeting_id"], host)
    token = livekit_service.generate_livekit_token(room_name=room, identity=host, name="Host", is_host=True)

    response = TestClient(app).post(f"/api/live/rooms/{room}/end", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "already_closed"
    assert db.get_live_meeting(meeting["meeting_id"])["status"] == "ended"


def test_rejected_credentials_are_not_silently_turned_into_lazy_creation(monkeypatch):
    _use_fake_livekit(monkeypatch, ServerError("unauthenticated", "", status=401))
    with pytest.raises(livekit_service.LiveKitAuthError):
        asyncio.run(livekit_service.create_live_room("room-b"))

    monkeypatch.setattr(live_routes, "measure_clock_skew", lambda: 30691.0)
    response = TestClient(app).post("/api/live/rooms", json={"room_name": "room-b"})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "HTTP 401" in detail and "8.5 hours behind" in detail and "Set time automatically" in detail


def test_other_creation_errors_still_fall_back_to_creation_on_join(monkeypatch):
    _use_fake_livekit(monkeypatch, ServerError("unavailable", "", status=503))
    result = asyncio.run(livekit_service.create_live_room("room-c"))
    assert result["room_name"] == "room-c" and "first participant join" in result["note"]


def test_clock_skew_is_measured_from_the_livekit_server_date(monkeypatch):
    server_now = datetime.now(timezone.utc) + timedelta(hours=8, minutes=31)

    class _Response:
        headers = {"Date": format_datetime(server_now, usegmt=True)}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=None: _Response())
    skew = livekit_service.measure_clock_skew()
    assert 30600 < skew < 30700
    warning = livekit_service.clock_warning(skew)
    assert "8.5 hours behind" in warning and "invalid token" in warning

    config_view = TestClient(app).get("/api/live/config").json()
    assert config_view["clock_warning"] == warning
    assert livekit_service.clock_warning(20) is None and livekit_service.clock_warning(None) is None


def test_an_unreachable_clock_check_never_breaks_the_config(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    view = TestClient(app).get("/api/live/config").json()
    assert view["configured"] is True and view["clock_skew_seconds"] is None and view["clock_warning"] is None
