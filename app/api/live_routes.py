from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from uuid import uuid4
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from app.core import auth
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core import config
from app.core import db
from app.core.jobs import run_pipeline
from app.core.stage_timings import timings_view
from app.core.storage import MeetingStorage, is_valid_meeting_id
from app.services.live.livekit_service import (
    TokenVerificationError,
    create_live_room,
    end_live_room,
    generate_livekit_token,
    get_live_room_participants,
    is_livekit_configured,
    sanitize_room_name,
    update_live_participant_consent,
    verify_participant_token,
)
from app.services.live.recorder import (
    CONSENT_STOP_REASONS,
    is_recorder_identity,
    get_recording_manager,
)
from app.services.live.recording_audio import (
    MANIFEST_FILENAME,
    RECORDING_FILENAME,
    attribute_utterances,
    wav_info,
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
# Helpers
# ---------------------------------------------------------------------------

def _require_configured() -> None:
    configured, err_msg = is_livekit_configured()
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LiveKit Cloud is not configured: {err_msg}",
        )


def _caller(authorization: Optional[str], room_name: str) -> Dict[str, Any]:
    """Authenticate the caller with the LiveKit token this server issued them."""
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    try:
        return verify_participant_token(token, room_name)
    except TokenVerificationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LiveKit Cloud is not configured: {exc}",
        ) from exc


def _require_meeting(meeting_id: str) -> Dict[str, Any]:
    if not is_valid_meeting_id(meeting_id):
        raise HTTPException(status_code=400, detail="Invalid meeting ID.")
    db.init_db()
    meeting = db.get_live_meeting(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Live meeting not found.")
    return meeting


def _require_host(meeting: Dict[str, Any], authorization: Optional[str]) -> Dict[str, Any]:
    caller = _caller(authorization, meeting["room_name"])
    if not caller["room_admin"] or caller["identity"] != meeting.get("host_identity"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the meeting host can control recording.",
        )
    return caller


async def _fetch_roster(room_name: str) -> List[Dict[str, Any]]:
    """Participants LiveKit reports as connected, excluding the recorder."""
    room = await get_live_room_participants(room_name)
    return [
        {"identity": p["identity"], "name": p.get("name") or p["identity"]}
        for p in room.get("participants", [])
        if not is_recorder_identity(p["identity"])
    ]


_last_roster_sync: Dict[str, float] = {}
_ROSTER_SYNC_INTERVAL = 2.0


async def _maybe_sync_roster(meeting: Dict[str, Any]) -> None:
    """Keep connected flags and join/leave audit events current (throttled)."""
    if meeting["status"] != "active":
        return
    now = time.monotonic()
    if now - _last_roster_sync.get(meeting["meeting_id"], 0.0) < _ROSTER_SYNC_INTERVAL:
        return
    _last_roster_sync[meeting["meeting_id"]] = now
    try:
        roster = await _fetch_roster(meeting["room_name"])
    except Exception as exc:
        logger.warning("Roster sync skipped for %s: %s", meeting["meeting_id"], type(exc).__name__)
        return
    db.sync_live_roster(meeting["meeting_id"], roster)


def _read_manifest(meeting_id: str) -> Optional[Dict[str, Any]]:
    path = MeetingStorage(meeting_id).recording / MANIFEST_FILENAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _segment_view(segment: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "segment_id": segment["segment_id"],
        "seq": segment["seq"],
        "state": segment["state"],
        "started_by": segment.get("started_by"),
        "started_at": segment.get("started_at"),
        "capture_started_at": segment.get("capture_started_at"),
        "stopped_at": segment.get("stopped_at"),
        "stop_reason": segment.get("stop_reason"),
        "duration_seconds": segment.get("duration_seconds"),
        "participants": [p.get("identity") for p in segment.get("participants") or []],
        "tracks": [
            {
                "identity": t.get("identity"),
                "name": t.get("name"),
                "track_sid": t.get("track_sid"),
                "start_offset_seconds": t.get("start_offset_seconds"),
                "duration_seconds": t.get("duration_seconds"),
            }
            for t in segment.get("tracks") or []
        ],
        "error": segment.get("error"),
    }


def recording_state(meeting: Dict[str, Any]) -> Dict[str, Any]:
    """The authoritative recording/consent/processing state for a live meeting."""
    meeting_id = meeting["meeting_id"]
    session = get_recording_manager().active_session(meeting_id)
    segments = db.get_recording_segments(meeting_id)
    participants = db.get_live_participants(meeting_id)
    connected = [p for p in participants if p["connected"]]
    blocked = [p for p in connected if not p["consented"]]
    job = db.get_job_status(meeting_id)

    recording_path = MeetingStorage(meeting_id).recording / RECORDING_FILENAME
    artifact = None
    info = wav_info(recording_path) if recording_path.exists() else None
    if info and info["frames"] > 0:
        manifest = _read_manifest(meeting_id) or {}
        artifact = {
            **info,
            "segments": len(manifest.get("segments", [])),
            "participants": sorted({
                t.get("name") or t.get("identity")
                for seg in manifest.get("segments", [])
                for t in seg.get("tracks", [])
            }),
        }

    live = session.stats() if session else None
    if live is not None and live["stop_reason"] is None:
        # REC is only reported once real audio frames have been written.
        overall = "recording" if live["state"] == "recording" and live["capturing"] else "starting"
    elif live is not None:
        overall = "stopping"
    elif any(s["state"] in db.ACTIVE_SEGMENT_STATES for s in segments):
        overall = "stopping"
    elif job is not None:
        overall = {"queued": "processing", "running": "processing", "done": "completed"}.get(job.state, "failed")
    elif artifact is not None:
        overall = "recorded"
    elif segments and segments[-1]["state"] == "failed":
        overall = "failed"
    else:
        overall = "idle"

    last = segments[-1] if segments else None
    last_stop_reason = last.get("stop_reason") if last else None
    consent_blocked = bool(blocked) or (
        overall in ("idle", "recorded") and last_stop_reason in CONSENT_STOP_REASONS
    )

    return {
        "meeting_id": meeting_id,
        "room_name": meeting["room_name"],
        "meeting_status": meeting["status"],
        "host_identity": meeting.get("host_identity"),
        "status": overall,
        "capture_active": overall == "recording",
        "live": live,
        "consent": {
            "all_connected_consented": bool(connected) and not blocked,
            "blocked": [{"identity": p["identity"], "name": p["name"]} for p in blocked],
            "consent_blocked": consent_blocked,
        },
        "participants": participants,
        "last_stop_reason": last_stop_reason,
        "segments": [_segment_view(s) for s in segments],
        "artifact": artifact,
        "processing": (
            {**job.model_dump(mode="json"), "stage_timings": timings_view(meeting_id)}
            if job else None
        ),
        "can_start": (
            meeting["status"] == "active"
            and live is None
            and job is None
            and bool(connected)
            and not blocked
        ),
        "can_process": live is None and job is None and artifact is not None,
    }


# ---------------------------------------------------------------------------
# Endpoints (Phase 4.1)
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
async def create_room(payload: CreateRoomRequest, request: Request):
    """
    Create or initialize a LiveKit room.
    Generates a shareable room identifier. With sign-in required, the caller
    becomes the meeting's owner (and therefore its host).
    """
    _require_configured()
    user = auth.current_user(request)

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
    except Exception as exc:
        logger.error("Failed to create LiveKit room: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create LiveKit room: {exc}",
        )

    db.init_db()
    meeting = db.get_or_create_live_meeting(
        room_name,
        title=payload.title or "Live Meeting",
        room_sid=room_info.get("sid"),
    )
    if auth.auth_required():
        owner = (db.get_meeting(meeting["meeting_id"]) or {}).get("owner_user_id")
        if owner and owner != user["user_id"]:
            raise HTTPException(status_code=409, detail="This room belongs to another meeting owner.")
        db.add_meeting_member(meeting["meeting_id"], user["user_id"], "owner")
    return {**room_info, "meeting_id": meeting["meeting_id"]}


@router.post("/token")
def create_token(payload: TokenRequest, request: Request):
    """
    Generate an authenticated LiveKit participant token.
    Grants publishing, subscription, and data messaging rights.

    Consent ticked on the join form is recorded here as an explicit grant.
    The token's `consented` metadata always reflects the server's record,
    never the client's claim.
    """
    _require_configured()

    clean_room = sanitize_room_name(payload.room_name)
    client_meta = dict(payload.metadata or {})
    requested_consent = client_meta.pop("consented", None)
    db.init_db()

    if auth.auth_required():
        # The server chooses identity, name and host role from the session and
        # the meeting membership; the client's values are ignored.
        user = auth.current_user(request)
        meeting = db.get_active_live_meeting(clean_room)
        if meeting is None:
            raise HTTPException(status_code=404, detail="Meeting not found.")
        role = db.get_member_role(meeting["meeting_id"], user["user_id"])
        if role is None:
            raise HTTPException(status_code=403, detail="Ask the host for an invitation to join this meeting.")
        identity, name, is_host = user["user_id"], user["display_name"], role == "owner"
    else:
        identity = payload.participant_identity or f"user_{uuid4().hex[:6]}"
        name = payload.participant_name.strip() or "Guest Participant"
        is_host = payload.is_host
        meeting = db.get_or_create_live_meeting(clean_room, title=client_meta.get("topic"))
    if is_recorder_identity(identity):
        raise HTTPException(status_code=400, detail="Reserved participant identity.")

    meeting_id = meeting["meeting_id"]
    if is_host and not db.claim_live_meeting_host(meeting_id, identity):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This meeting already has a host. Join as a participant instead.",
        )
    db.upsert_live_participant(meeting_id, identity, name=name, is_host=is_host)

    previously = db.is_participant_consented(meeting_id, identity)
    if requested_consent is True and not previously:
        db.set_participant_consent(meeting_id, identity, True, source="join_form", name=name)
    elif requested_consent is False and previously:
        db.set_participant_consent(meeting_id, identity, False, source="join_form", name=name)
    consented = db.is_participant_consented(meeting_id, identity)

    try:
        jwt_token = generate_livekit_token(
            room_name=clean_room,
            identity=identity,
            name=name,
            is_host=is_host,
            metadata={**client_meta, "consented": consented, "meeting_id": meeting_id},
        )
        return {
            "token": jwt_token,
            "livekit_url": getattr(config, "LIVEKIT_URL", ""),
            "room_name": clean_room,
            "participant_identity": identity,
            "participant_name": name,
            "is_host": is_host,
            "meeting_id": meeting_id,
            "consented": consented,
        }
    except Exception as exc:
        logger.error("Token generation error: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token generation failed.",
        )


@router.get("/rooms/{room_name}")
async def get_room(room_name: str):
    """Retrieve real-time active participants and status for a LiveKit room."""
    _require_configured()

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
async def end_room_endpoint(room_name: str, authorization: Optional[str] = Header(None)):
    """
    Host terminates the meeting room for all participants.
    Stops and finalizes any active recording first, then closes the room.
    """
    _require_configured()

    clean_room = sanitize_room_name(room_name)
    db.init_db()
    meeting = db.get_active_live_meeting(clean_room)
    segment = None
    if meeting is not None:
        _require_host(meeting, authorization)
        segment = await get_recording_manager().stop(meeting["meeting_id"], reason="meeting_ended")
        db.end_live_meeting(meeting["meeting_id"])
    else:
        caller = _caller(authorization, clean_room)
        if not caller["room_admin"]:
            raise HTTPException(status_code=403, detail="Only the meeting host can end the meeting.")

    try:
        result = await end_live_room(clean_room)
    except Exception as exc:
        logger.error("Failed to end LiveKit room: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to end LiveKit room: {exc}",
        )
    return {
        **result,
        "meeting_id": meeting["meeting_id"] if meeting else None,
        "recording_segment": _segment_view(segment) if segment else None,
    }


@router.post("/rooms/{room_name}/consent")
async def update_consent_endpoint(
    room_name: str,
    payload: ConsentRequest,
    authorization: Optional[str] = Header(None),
):
    """
    Record a participant's own explicit consent decision on the server.

    A revocation halts any active capture before it is persisted. A grant is
    persisted but never resumes a halted recording. LiveKit metadata is then
    updated so other participants see the change.
    """
    _require_configured()

    clean_room = sanitize_room_name(room_name)
    caller = _caller(authorization, clean_room)
    if payload.identity != caller["identity"]:
        raise HTTPException(status_code=403, detail="Participants can only change their own consent.")

    db.init_db()
    meeting = db.get_active_live_meeting(clean_room)
    if meeting is None:
        raise HTTPException(status_code=404, detail="No active live meeting for this room.")
    meeting_id = meeting["meeting_id"]

    manager = get_recording_manager()
    if not payload.consented:
        manager.notify_consent(meeting_id, payload.identity, False)
    db.set_participant_consent(
        meeting_id, payload.identity, payload.consented, source="in_room", name=caller.get("name"),
    )
    if payload.consented:
        manager.notify_consent(meeting_id, payload.identity, True)

    try:
        await update_live_participant_consent(
            room_name=clean_room,
            identity=payload.identity,
            consented=payload.consented,
        )
    except Exception as exc:
        # Metadata only drives other clients' badges; the database is authoritative.
        logger.warning("Consent metadata sync failed: %s", type(exc).__name__)

    return {
        "status": "updated",
        "room_name": clean_room,
        "identity": payload.identity,
        "consented": payload.consented,
        "meeting_id": meeting_id,
        "recording": recording_state(db.get_live_meeting(meeting_id)),
    }


# ---------------------------------------------------------------------------
# Endpoints (Phase 4.2: server-side recording)
# ---------------------------------------------------------------------------

@router.get("/meetings/{meeting_id}/recording")
async def get_recording_state(meeting_id: str, authorization: Optional[str] = Header(None)):
    """True recording, consent and processing state. Any participant may read it."""
    meeting = _require_meeting(meeting_id)
    _caller(authorization, meeting["room_name"])
    await _maybe_sync_roster(meeting)
    return recording_state(meeting)


@router.post("/meetings/{meeting_id}/recording/start")
async def start_recording(meeting_id: str, authorization: Optional[str] = Header(None)):
    """Start a new recording segment after validating every connected participant's consent."""
    _require_configured()
    meeting = _require_meeting(meeting_id)
    caller = _require_host(meeting, authorization)
    manager = get_recording_manager()

    if meeting["status"] != "active":
        raise HTTPException(status_code=409, detail="The meeting has ended.")
    if db.get_job_status(meeting_id) is not None:
        raise HTTPException(status_code=409, detail="This meeting has already been submitted for processing.")
    if manager.is_active(meeting_id):
        return recording_state(meeting)

    try:
        roster = await _fetch_roster(meeting["room_name"])
    except Exception as exc:
        logger.error("Roster lookup failed for %s: %s", meeting_id, type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Could not verify the participant roster with LiveKit, so recording was not started.",
        )
    db.sync_live_roster(meeting_id, roster)
    if not roster:
        raise HTTPException(status_code=409, detail="No participants are connected.")

    blocked = [p for p in roster if not db.is_participant_consented(meeting_id, p["identity"])]
    if blocked:
        db.record_live_event(
            meeting_id,
            "recording_blocked",
            identity=caller["identity"],
            detail={"unconsented": [p["identity"] for p in blocked]},
        )
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Recording requires explicit consent from every connected participant.",
                "blocked": blocked,
            },
        )

    try:
        await manager.start(meeting, caller["identity"], roster)
    except db.ActiveSegmentExists:
        raise HTTPException(status_code=409, detail="A recording segment is already active.")
    return JSONResponse(status_code=202, content=recording_state(meeting))


@router.post("/meetings/{meeting_id}/recording/stop")
async def stop_recording(meeting_id: str, authorization: Optional[str] = Header(None)):
    """Stop capture and wait for the segment and recording.wav to be finalized. Idempotent."""
    meeting = _require_meeting(meeting_id)
    _require_host(meeting, authorization)
    await get_recording_manager().stop(meeting_id, reason="host_stopped")
    return recording_state(db.get_live_meeting(meeting_id))


@router.post("/meetings/{meeting_id}/recording/process")
async def process_recording(
    meeting_id: str,
    background_tasks: BackgroundTasks,
    retry: bool = False,
    authorization: Optional[str] = Header(None),
):
    """
    Queue the finalized recording for the existing pipeline exactly once.
    Repeated calls return the existing job. `retry=true` re-queues a failed job.
    """
    meeting = _require_meeting(meeting_id)
    caller = _require_host(meeting, authorization)

    if get_recording_manager().is_active(meeting_id) or any(
        s["state"] in db.ACTIVE_SEGMENT_STATES for s in db.get_recording_segments(meeting_id)
    ):
        raise HTTPException(status_code=409, detail="Stop the recording before processing it.")

    recording_path = MeetingStorage(meeting_id).recording / RECORDING_FILENAME
    info = wav_info(recording_path) if recording_path.exists() else None
    if not info or info["frames"] == 0:
        raise HTTPException(status_code=409, detail="There is no finalized recording to process.")

    created = db.enqueue_job_once(meeting_id, str(recording_path))
    if not created:
        job = db.get_job_status(meeting_id)
        if retry and job is not None and job.state == "failed" and db.requeue_failed_job(meeting_id):
            created = True
        else:
            return {**recording_state(meeting), "already_submitted": True}

    db.insert_meeting(meeting_id, filename=f"live-{meeting['room_name']}.wav", status="queued")
    db.record_live_event(meeting_id, "processing_queued", identity=caller["identity"])
    if (getattr(config, "JOB_RUNNER", "inline") or "inline").strip().lower() != "worker":
        background_tasks.add_task(run_pipeline, meeting_id, Path(recording_path))
    return JSONResponse(status_code=202, content={**recording_state(meeting), "already_submitted": False})


@router.get("/meetings/{meeting_id}/speakers")
async def get_speaker_attribution(meeting_id: str, authorization: Optional[str] = Header(None)):
    """Map processed utterances to the LiveKit participant whose track carried them."""
    meeting = _require_meeting(meeting_id)
    _caller(authorization, meeting["room_name"])
    manifest = _read_manifest(meeting_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="No recording manifest for this meeting.")
    utterances = [u.model_dump() for u in db.get_utterances(meeting_id)]
    if not utterances:
        raise HTTPException(status_code=404, detail="The recording has not been transcribed yet.")
    return {
        "meeting_id": meeting_id,
        "utterances": attribute_utterances(MeetingStorage(meeting_id).recording, manifest, utterances),
    }


@router.get("/meetings/{meeting_id}/events")
async def get_live_audit_log(meeting_id: str, authorization: Optional[str] = Header(None)):
    """Consent, roster and recording audit trail (host only)."""
    meeting = _require_meeting(meeting_id)
    _require_host(meeting, authorization)
    return {"meeting_id": meeting_id, "events": db.get_live_events(meeting_id, limit=1000)}
