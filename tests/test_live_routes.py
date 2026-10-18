import json
import pytest
from fastapi.testclient import TestClient
import jwt

from app.api.main import app
from app.services.live.livekit_service import (
    generate_livekit_token,
    is_livekit_configured,
    sanitize_room_name,
)

client = TestClient(app)


def test_sanitize_room_name():
    assert sanitize_room_name("Sprint Planning & Retrospective!") == "sprint-planning-retrospective"
    assert sanitize_room_name("---room--name---") == "room-name"
    assert sanitize_room_name("   ") == "m2m-room"


def test_is_livekit_configured_flags_missing_and_masked_secrets(monkeypatch):
    # Missing URL
    monkeypatch.setattr("app.core.config.LIVEKIT_URL", "")
    monkeypatch.setattr("app.core.config.LIVEKIT_API_KEY", "APIkey")
    monkeypatch.setattr("app.core.config.LIVEKIT_API_SECRET", "secret")
    valid, err = is_livekit_configured()
    assert not valid
    assert "LIVEKIT_URL" in err

    # Masked secret with bullet characters
    monkeypatch.setattr("app.core.config.LIVEKIT_URL", "wss://example.livekit.cloud")
    monkeypatch.setattr("app.core.config.LIVEKIT_API_KEY", "APIkey")
    monkeypatch.setattr("app.core.config.LIVEKIT_API_SECRET", "••••••••••••••••")
    valid, err = is_livekit_configured()
    assert not valid
    assert "masked bullet characters" in err

    # Valid mock keys
    monkeypatch.setattr("app.core.config.LIVEKIT_API_SECRET", "valid_secret_12345678901234567890")
    valid, err = is_livekit_configured()
    assert valid
    assert err == ""


def test_generate_livekit_token_creates_valid_jwt_with_clock_skew(monkeypatch):
    monkeypatch.setattr("app.core.config.LIVEKIT_URL", "wss://test.livekit.cloud")
    monkeypatch.setattr("app.core.config.LIVEKIT_API_KEY", "API_TEST_KEY_123")
    monkeypatch.setattr("app.core.config.LIVEKIT_API_SECRET", "TEST_SECRET_ABC123456789012345678")

    token = generate_livekit_token(
        room_name="test-room",
        identity="user_123",
        name="Alex Morgan",
        is_host=True,
        metadata={"consented": True},
    )
    assert isinstance(token, str)

    # Decode and verify claims
    decoded = jwt.decode(token, "TEST_SECRET_ABC123456789012345678", algorithms=["HS256"])
    assert decoded["iss"] == "API_TEST_KEY_123"
    assert decoded["sub"] == "user_123"
    assert decoded["video"]["room"] == "test-room"
    assert decoded["video"]["roomJoin"] is True
    assert decoded["video"]["roomAdmin"] is True
    assert decoded["nbf"] < decoded["exp"]

    meta = json.loads(decoded["metadata"])
    assert meta["is_host"] is True
    assert meta["name"] == "Alex Morgan"
    assert meta["consented"] is True


def test_api_live_config_endpoint(monkeypatch):
    monkeypatch.setattr("app.core.config.LIVEKIT_URL", "wss://test.livekit.cloud")
    monkeypatch.setattr("app.core.config.LIVEKIT_API_KEY", "API_KEY")
    monkeypatch.setattr("app.core.config.LIVEKIT_API_SECRET", "SECRET_KEY_12345678901234567890")

    response = client.get("/api/live/config")
    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is True
    assert data["livekit_url"] == "wss://test.livekit.cloud"
    # Ensure secrets are never leaked
    assert "secret" not in json.dumps(data).lower()
    assert "api_key" not in json.dumps(data).lower()


def test_api_live_token_endpoint(monkeypatch):
    monkeypatch.setattr("app.core.config.LIVEKIT_URL", "wss://test.livekit.cloud")
    monkeypatch.setattr("app.core.config.LIVEKIT_API_KEY", "API_KEY")
    monkeypatch.setattr("app.core.config.LIVEKIT_API_SECRET", "SECRET_KEY_12345678901234567890")

    payload = {
        "room_name": "Product Sync",
        "participant_name": "Priya Sharma",
        "is_host": False,
        "metadata": {"consented": True},
    }
    response = client.post("/api/live/token", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "token" in data
    assert data["room_name"] == "product-sync"
    assert data["participant_name"] == "Priya Sharma"
    assert data["is_host"] is False
    assert data["livekit_url"] == "wss://test.livekit.cloud"
