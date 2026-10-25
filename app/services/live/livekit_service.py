from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import jwt
from livekit import api

from app.core import config

logger = logging.getLogger("livekit_service")


def is_livekit_configured() -> tuple[bool, str]:
    """
    Check if LiveKit Cloud credentials are configured and valid.
    Returns (is_valid, error_message).
    """
    url = getattr(config, "LIVEKIT_URL", None)
    if url is None:
        url = os.getenv("LIVEKIT_URL", "")
    url = (url or "").strip()

    key = getattr(config, "LIVEKIT_API_KEY", None)
    if key is None:
        key = os.getenv("LIVEKIT_API_KEY", "")
    key = (key or "").strip()

    secret = getattr(config, "LIVEKIT_API_SECRET", None)
    if secret is None:
        secret = os.getenv("LIVEKIT_API_SECRET", "")
    secret = (secret or "").strip()

    if not url:
        return False, "LIVEKIT_URL is not configured in environment or .env."
    if not key:
        return False, "LIVEKIT_API_KEY is not configured in environment or .env."
    if not secret:
        return False, "LIVEKIT_API_SECRET is not configured in environment or .env."

    # Detect if secret was copied as masked UI bullets (e.g., U+2022)
    if "•" in secret or any(ord(c) > 127 for c in secret):
        return False, (
            "LIVEKIT_API_SECRET in .env appears to contain masked bullet characters (•). "
            "Please copy the actual plain text secret from your LiveKit Cloud dashboard."
        )

    return True, ""


def sanitize_room_name(name: str) -> str:
    """Sanitize room name into a URL and LiveKit safe slug."""
    clean = re.sub(r"[^a-zA-Z0-9_\-]", "-", name.strip().lower())
    clean = re.sub(r"-+", "-", clean).strip("-")
    return clean or "m2m-room"


def generate_livekit_token(
    room_name: str,
    identity: str,
    name: str,
    is_host: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
    ttl_hours: int = 6,
) -> str:
    """
    Generate a signed JWT access token for LiveKit WebRTC signaling.
    Applies clock skew leeway to prevent 'invalid token' caused by local vs server NTP drift.
    """
    valid, err_msg = is_livekit_configured()
    if not valid:
        raise ValueError(err_msg)

    key = getattr(config, "LIVEKIT_API_KEY", "") or os.getenv("LIVEKIT_API_KEY", "")
    secret = getattr(config, "LIVEKIT_API_SECRET", "") or os.getenv("LIVEKIT_API_SECRET", "")

    grants = api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
        room_admin=is_host,
        room_create=is_host,
    )

    tok = (
        api.AccessToken(key, secret)
        .with_identity(identity)
        .with_name(name)
        .with_grants(grants)
    )

    meta_payload = {
        "is_host": is_host,
        "name": name,
        **(metadata or {}),
    }
    tok.with_metadata(json.dumps(meta_payload))

    # Apply 2-minute clock skew leeway to prevent premature token rejections
    now = datetime.now(timezone.utc)
    jwt_claims = tok.claims.asdict()
    jwt_claims.update(
        {
            "sub": identity,
            "iss": key,
            "nbf": calendar.timegm((now - timedelta(minutes=2)).utctimetuple()),
            "exp": calendar.timegm((now + timedelta(hours=ttl_hours)).utctimetuple()),
        }
    )

    return jwt.encode(jwt_claims, secret, algorithm="HS256")


def generate_recorder_token(room_name: str, identity: str, ttl_hours: int = 6) -> str:
    """
    Token for the server-side recorder: hidden from other participants,
    subscribe-only, and unable to publish media or data.
    """
    valid, err_msg = is_livekit_configured()
    if not valid:
        raise ValueError(err_msg)

    key = getattr(config, "LIVEKIT_API_KEY", "") or os.getenv("LIVEKIT_API_KEY", "")
    secret = getattr(config, "LIVEKIT_API_SECRET", "") or os.getenv("LIVEKIT_API_SECRET", "")

    grants = api.VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=False,
        can_subscribe=True,
        can_publish_data=False,
        hidden=True,
        recorder=True,
    )
    tok = (
        api.AccessToken(key, secret)
        .with_identity(identity)
        .with_name("Meet2MoM Recorder")
        .with_grants(grants)
    )
    now = datetime.now(timezone.utc)
    jwt_claims = tok.claims.asdict()
    jwt_claims.update(
        {
            "sub": identity,
            "iss": key,
            "nbf": calendar.timegm((now - timedelta(minutes=2)).utctimetuple()),
            "exp": calendar.timegm((now + timedelta(hours=ttl_hours)).utctimetuple()),
        }
    )
    return jwt.encode(jwt_claims, secret, algorithm="HS256")


class TokenVerificationError(Exception):
    """The caller did not present a valid participant token for this room."""


def verify_participant_token(token: str, room_name: str) -> Dict[str, Any]:
    """
    Verify a LiveKit access token this server issued and return the caller's
    identity and role. Used to authenticate consent and recording actions.
    """
    valid, err_msg = is_livekit_configured()
    if not valid:
        raise ValueError(err_msg)
    if not token:
        raise TokenVerificationError("Missing participant token.")

    secret = getattr(config, "LIVEKIT_API_SECRET", "") or os.getenv("LIVEKIT_API_SECRET", "")
    key = getattr(config, "LIVEKIT_API_KEY", "") or os.getenv("LIVEKIT_API_KEY", "")
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            leeway=120,
            options={"verify_aud": False},
        )
    except jwt.PyJWTError as exc:
        raise TokenVerificationError("Invalid or expired participant token.") from exc

    video = claims.get("video") or {}
    if claims.get("iss") != key or video.get("room") != room_name or not video.get("roomJoin"):
        raise TokenVerificationError("Participant token is not valid for this room.")
    if video.get("hidden") or video.get("recorder"):
        raise TokenVerificationError("Recorder tokens cannot perform participant actions.")
    return {
        "identity": claims.get("sub", ""),
        "name": claims.get("name") or claims.get("sub", ""),
        "room_admin": bool(video.get("roomAdmin")),
    }


async def get_livekit_api() -> api.LiveKitAPI:
    """Return an authenticated LiveKitAPI instance."""
    valid, err_msg = is_livekit_configured()
    if not valid:
        raise ValueError(err_msg)

    url = getattr(config, "LIVEKIT_URL", "") or os.getenv("LIVEKIT_URL", "")
    key = getattr(config, "LIVEKIT_API_KEY", "") or os.getenv("LIVEKIT_API_KEY", "")
    secret = getattr(config, "LIVEKIT_API_SECRET", "") or os.getenv("LIVEKIT_API_SECRET", "")

    return api.LiveKitAPI(url=url, api_key=key, api_secret=secret)


class LiveKitAuthError(Exception):
    """LiveKit refused this server's credentials (HTTP 401/403)."""


def livekit_error_status(exc: BaseException) -> Optional[int]:
    """HTTP status of a LiveKit server API error, if it has one."""
    status = getattr(exc, "status", None)
    return status if isinstance(status, int) else None


_skew_cache: Dict[str, Any] = {"at": 0.0, "value": None}
_SKEW_CACHE_SECONDS = 600


def measure_clock_skew(timeout: float = 5.0) -> Optional[float]:
    """
    Seconds this computer's clock is behind (+) or ahead (-) of the LiveKit
    server, from the server's HTTP Date header; None if it cannot be measured.

    Phase 11: LiveKit tokens carry not-before/expiry times signed with this
    computer's clock. A badly wrong clock (an unsynced Windows clock was 8.5 h
    behind) makes LiveKit reject every token as "invalid token", which looked
    like a broken room lifecycle.
    """
    import email.utils
    import time as _time
    import urllib.request

    if _time.monotonic() - _skew_cache["at"] < _SKEW_CACHE_SECONDS and _skew_cache["at"]:
        return _skew_cache["value"]
    url = getattr(config, "LIVEKIT_URL", "") or ""
    host = url.split("://", 1)[-1].split("/", 1)[0]
    value = None
    if host:
        try:
            request = urllib.request.Request(f"https://{host}", method="HEAD")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                server_date = response.headers.get("Date")
            if server_date:
                server_now = email.utils.parsedate_to_datetime(server_date)
                value = round((server_now - datetime.now(timezone.utc)).total_seconds(), 1)
        except Exception as exc:
            logger.info("Clock check against LiveKit skipped: %s", type(exc).__name__)
    _skew_cache.update(at=_time.monotonic(), value=value)
    return value


def clock_warning(skew: Optional[float]) -> Optional[str]:
    if skew is None or abs(skew) < 60:
        return None
    hours = abs(skew) / 3600
    amount = f"{hours:.1f} hours" if hours >= 1 else f"{abs(skew) / 60:.0f} minutes"
    direction = "behind" if skew > 0 else "ahead of"
    return (f"This computer's clock is {amount} {direction} the real time, so LiveKit rejects its tokens "
            "(\"invalid token\"). Turn on Settings > Time & language > Date & time > Set time automatically, "
            "click Sync now, then restart the backend.")


async def create_live_room(
    room_name: str,
    title: str = "",
    host_name: str = "",
    empty_timeout_seconds: int = 300,
    max_participants: int = 50,
) -> Dict[str, Any]:
    """
    Register/create a room on LiveKit Cloud.
    If the room already exists or cannot be created ahead of time, returns metadata.
    """
    valid, err_msg = is_livekit_configured()
    if not valid:
        raise ValueError(err_msg)

    url = getattr(config, "LIVEKIT_URL", "") or os.getenv("LIVEKIT_URL", "")
    metadata_json = json.dumps({
        "title": title or room_name,
        "host_name": host_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "consent_required": True,
    })

    try:
        lk = await get_livekit_api()
        try:
            req = api.CreateRoomRequest(
                name=room_name,
                empty_timeout=empty_timeout_seconds,
                max_participants=max_participants,
                metadata=metadata_json,
            )
            created = await lk.room.create_room(req)
            return {
                "room_name": created.name,
                "sid": created.sid,
                "title": title or room_name,
                "livekit_url": url,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "empty_timeout": empty_timeout_seconds,
            }
        finally:
            await lk.aclose()
    except Exception as exc:
        if livekit_error_status(exc) in (401, 403):
            # Not a "create on join later" case: every token from this server
            # would be refused too (wrong key/secret, or a wrong clock).
            raise LiveKitAuthError(f"LiveKit rejected this server's credentials (HTTP {livekit_error_status(exc)}).") from exc
        logger.warning("LiveKit explicit room creation returned: %s. Using lazy creation on join.", exc)
        return {
            "room_name": room_name,
            "title": title or room_name,
            "livekit_url": url,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "empty_timeout": empty_timeout_seconds,
            "note": "Room will be initialized on first participant join.",
        }


async def get_live_room_participants(room_name: str) -> Dict[str, Any]:
    """Fetch active participants in a LiveKit room."""
    valid, err_msg = is_livekit_configured()
    if not valid:
        raise ValueError(err_msg)

    lk = await get_livekit_api()
    try:
        resp = await lk.room.list_participants(api.ListParticipantsRequest(room=room_name))
        participants: List[Dict[str, Any]] = []

        for p in resp.participants:
            parsed_meta = {}
            if p.metadata:
                try:
                    parsed_meta = json.loads(p.metadata)
                except Exception:
                    pass

            participants.append({
                "sid": p.sid,
                "identity": p.identity,
                "name": p.name or p.identity,
                "state": str(p.state),
                "is_publisher": p.is_publisher,
                "joined_at": p.joined_at,
                "consented": parsed_meta.get("consented", False),
                "is_host": parsed_meta.get("is_host", False),
                "metadata": parsed_meta,
            })

        return {
            "room_name": room_name,
            "participant_count": len(participants),
            "participants": participants,
        }
    finally:
        await lk.aclose()


async def end_live_room(room_name: str) -> Dict[str, Any]:
    """Forcefully end the room on LiveKit and disconnect all active participants."""
    valid, err_msg = is_livekit_configured()
    if not valid:
        raise ValueError(err_msg)

    lk = await get_livekit_api()
    try:
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
        except Exception as exc:
            # LiveKit closes a room by itself shortly after its last
            # participant leaves. Deleting it afterwards answers 404 "requested
            # room does not exist": the room is already closed, which is the
            # state ending was meant to reach. Any other error is real.
            if livekit_error_status(exc) != 404:
                raise
            return {
                "status": "already_closed",
                "room_name": room_name,
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "note": "LiveKit had already closed this room (everyone had left).",
            }
        return {
            "status": "ended",
            "room_name": room_name,
            "ended_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        await lk.aclose()


async def update_live_participant_consent(
    room_name: str,
    identity: str,
    consented: bool,
) -> Dict[str, Any]:
    """Update participant metadata in LiveKit room to sync consent across all participants."""
    valid, err_msg = is_livekit_configured()
    if not valid:
        raise ValueError(err_msg)

    lk = await get_livekit_api()
    try:
        # Merge into the existing metadata so is_host/name/role survive.
        existing: Dict[str, Any] = {}
        try:
            current = await lk.room.get_participant(
                api.RoomParticipantIdentity(room=room_name, identity=identity)
            )
            if current.metadata:
                existing = json.loads(current.metadata)
        except Exception:
            existing = {}
        meta_update = {
            **existing,
            "consented": consented,
            "consent_time": datetime.now(timezone.utc).isoformat() if consented else None,
        }
        await lk.room.update_participant(
            api.UpdateParticipantRequest(
                room=room_name,
                identity=identity,
                metadata=json.dumps(meta_update),
            )
        )
        return {
            "status": "updated",
            "room_name": room_name,
            "identity": identity,
            "consented": consented,
        }
    finally:
        await lk.aclose()
