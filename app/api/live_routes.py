from __future__ import annotations

import logging
from uuid import uuid4
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core import config
from app.services.live.livekit_service import (
    create_live_room,
    end_live_room,
    generate_livekit_token,
    get_live_room_participants,
    is_livekit_configured,
    sanitize_room_name,
    update_live_participant_consent,
)

logger = logging.getLogger("live_routes")

router = APIRouter(
    prefix="/api/live",
    tags=["Live Meetings (LiveKit)"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class CreateRoomRequest(BaseModel):
    room_name: Optional[str] = None
    title: Optional[str] = "Live Meeting"
    host_name: Optional[str] = "Host"
    empty_timeout: Optional[int] = 300
    max_participants: Optional[int] = 50


class TokenRequest(BaseModel):
    room_name: str
    participant_name: str
    participant_identity: Optional[str] = None
    is_host: bool = False
    metadata: Optional[Dict[str, Any]] = None


class ConsentRequest(BaseModel):
    identity: str
    consented: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/config")
def get_live_config():
    """
    Check if LiveKit Cloud is configured and return the public WebSocket URL.
    Secrets are strictly protected server-side and never returned.
    """
    configured, err_msg = is_livekit_configured()
    livekit_url = getattr(config, "LIVEKIT_URL", "")
    return {
        "configured": configured,
        "livekit_url": livekit_url if configured else "",
        "message": "LiveKit Cloud ready" if configured else err_msg,
    }


@router.post("/rooms")
async def create_room(payload: CreateRoomRequest):
    """
    Create or initialize a LiveKit room.
    Generates a shareable room identifier.
    """
    configured, err_msg = is_livekit_configured()
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LiveKit Cloud is not configured: {err_msg}",
        )

    room_name = payload.room_name
    if not room_name:
        room_name = f"m2m-{uuid4().hex[:8]}"
    else:
        room_name = sanitize_room_name(room_name)

    try:
        room_info = await create_live_room(
            room_name=room_name,
            title=payload.title or "Live Meeting",
            host_name=payload.host_name or "Host",
            empty_timeout_seconds=payload.empty_timeout or 300,
            max_participants=payload.max_participants or 50,
        )
        return room_info
    except Exception as exc:
        logger.error("Failed to create LiveKit room: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create LiveKit room: {exc}",
        )


@router.post("/token")
def create_token(payload: TokenRequest):
    """
    Generate an authenticated LiveKit participant token.
    Grants publishing, subscription, and data messaging rights.
    """
    configured, err_msg = is_livekit_configured()
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LiveKit Cloud is not configured: {err_msg}",
        )

    clean_room = sanitize_room_name(payload.room_name)
    identity = payload.participant_identity
    if not identity:
        identity = f"user_{uuid4().hex[:6]}"

    name = payload.participant_name.strip() or "Guest Participant"

    try:
        jwt_token = generate_livekit_token(
            room_name=clean_room,
            identity=identity,
            name=name,
            is_host=payload.is_host,
            metadata=payload.metadata,
        )
        return {
            "token": jwt_token,
            "livekit_url": getattr(config, "LIVEKIT_URL", ""),
            "room_name": clean_room,
            "participant_identity": identity,
            "participant_name": name,
            "is_host": payload.is_host,
        }
    except Exception as exc:
        logger.error("Token generation error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Token generation failed: {exc}",
        )


@router.get("/rooms/{room_name}")
async def get_room(room_name: str):
    """Retrieve real-time active participants and status for a LiveKit room."""
    configured, err_msg = is_livekit_configured()
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LiveKit Cloud is not configured: {err_msg}",
        )

    clean_room = sanitize_room_name(room_name)
    try:
        return await get_live_room_participants(clean_room)
    except Exception as exc:
        logger.error("Failed to query LiveKit room: %s", exc)
        # Non-fatal: if room hasn't had participants yet, return empty list
        return {
            "room_name": clean_room,
            "participant_count": 0,
            "participants": [],
            "status": "idle",
        }


@router.post("/rooms/{room_name}/end")
async def end_room_endpoint(room_name: str):
    """
    Host terminates the meeting room for all participants.
    Closes the room and triggers disconnect for all clients.
    """
    configured, err_msg = is_livekit_configured()
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LiveKit Cloud is not configured: {err_msg}",
        )

    clean_room = sanitize_room_name(room_name)
    try:
        return await end_live_room(clean_room)
    except Exception as exc:
        logger.error("Failed to end LiveKit room: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to end LiveKit room: {exc}",
        )


@router.post("/rooms/{room_name}/consent")
async def update_consent_endpoint(room_name: str, payload: ConsentRequest):
    """
    Update participant's explicit recording consent in LiveKit metadata.
    Syncs across all connected participants via WebRTC data channel / metadata events.
    """
    configured, err_msg = is_livekit_configured()
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LiveKit Cloud is not configured: {err_msg}",
        )

    clean_room = sanitize_room_name(room_name)
    try:
        return await update_live_participant_consent(
            room_name=clean_room,
            identity=payload.identity,
            consented=payload.consented,
        )
    except Exception as exc:
        logger.error("Failed to update participant consent: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update consent: {exc}",
        )
