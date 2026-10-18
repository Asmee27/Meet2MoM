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


async def get_livekit_api() -> api.LiveKitAPI:
    """Return an authenticated LiveKitAPI instance."""
    valid, err_msg = is_livekit_configured()
    if not valid:
        raise ValueError(err_msg)

    url = getattr(config, "LIVEKIT_URL", "") or os.getenv("LIVEKIT_URL", "")
    key = getattr(config, "LIVEKIT_API_KEY", "") or os.getenv("LIVEKIT_API_KEY", "")
    secret = getattr(config, "LIVEKIT_API_SECRET", "") or os.getenv("LIVEKIT_API_SECRET", "")

    return api.LiveKitAPI(url=url, api_key=key, api_secret=secret)


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
        await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
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
        meta_update = {
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
