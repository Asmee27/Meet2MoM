"""
Scheduled meetings with multiple invitees (Phase 8.3).

Scheduling creates a meetings row in the 'scheduled' state, a schedule with a
reserved room code, and one meeting-scoped invitation link per invitee. It
creates no recording, runs no extraction and opens no live room; the room
opens only when the owner starts the meeting (live_routes.create_room).

* Only the owner schedules, edits, cancels and manages invitees.
* A link works only for its own meeting, expires, and is revoked by
  replacing it, revoking the invitee or cancelling the meeting.
* An invitee's email is how the host keeps track of them; it is never proof
  of who opens the link. The account that accepted is shown beside it.
* No email is sent (see app/services/invitations/delivery.py).
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core import auth, db
from app.services.invitations.delivery import get_delivery

router = APIRouter(prefix="/api", tags=["Scheduling"])

MAX_TITLE = 120
MAX_AGENDA = 2000
MAX_INVITEES = 50
MIN_DURATION, MAX_DURATION = 5, 480
LINK_GRACE = timedelta(days=1)           # links stay valid a day after the scheduled end
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LOCAL_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")


class ScheduleRequest(BaseModel):
    title: str
    agenda: Optional[str] = None
    start_local: str                     # "YYYY-MM-DDTHH:MM" wall-clock time in `timezone`
    timezone: str                        # IANA name, e.g. "Asia/Kolkata"
    duration_minutes: Optional[int] = None
    invitees: List[str] = []
    request_id: Optional[str] = None     # repeat submissions with the same id create one meeting


class ScheduleUpdate(BaseModel):
    title: Optional[str] = None
    agenda: Optional[str] = None
    start_local: Optional[str] = None
    timezone: Optional[str] = None
    duration_minutes: Optional[int] = None


class InviteesRequest(BaseModel):
    emails: List[str]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _signed_in_owner(request: Request) -> Dict[str, Any]:
    user = auth.current_user(request)
    if not auth.auth_required() or user is None:
        raise HTTPException(status_code=409, detail="Scheduling needs sign-in (AUTH_REQUIRED=true).")
    return user


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo((name or "").strip())
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=422, detail=f"Unknown timezone \"{name}\". Choose one from the list.")


def _start_utc(start_local: str, zone_name: str) -> datetime:
    """Wall-clock time in an IANA zone to aware UTC; times skipped by a clock change are refused."""
    zone = _zone(zone_name)
    if not _LOCAL_TIME.match((start_local or "").strip()):
        raise HTTPException(status_code=422, detail="Enter the start as a date and time (YYYY-MM-DDTHH:MM).")
    try:
        naive = datetime.fromisoformat(start_local.strip())
    except ValueError:
        raise HTTPException(status_code=422, detail="That date or time does not exist.")
    local = naive.replace(tzinfo=zone)
    as_utc = local.astimezone(timezone.utc)
    if as_utc.astimezone(zone).replace(tzinfo=None) != naive:
        raise HTTPException(status_code=422,
                            detail="That time does not exist in this timezone (the clocks change). Pick another time.")
    if as_utc <= auth._now():
        raise HTTPException(status_code=422, detail="The start time is in the past. Pick a future time.")
    return as_utc


def _title(value: Optional[str]) -> str:
    title = (value or "").strip()
    if not title or len(title) > MAX_TITLE:
        raise HTTPException(status_code=422, detail=f"Enter a title (up to {MAX_TITLE} characters).")
    return title


def _agenda(value: Optional[str]) -> Optional[str]:
    agenda = (value or "").strip()
    if len(agenda) > MAX_AGENDA:
        raise HTTPException(status_code=422, detail=f"Keep the agenda under {MAX_AGENDA} characters.")
    return agenda or None


def _duration(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    if not MIN_DURATION <= value <= MAX_DURATION:
        raise HTTPException(status_code=422,
                            detail=f"Duration must be between {MIN_DURATION} and {MAX_DURATION} minutes.")
    return value


def clean_emails(raw: List[str], owner_email: str) -> Dict[str, List[str]]:
    """Normalised unique emails, plus duplicates removed. Invalid entries and the owner's own email are errors."""
    emails: List[str] = []
    duplicates: List[str] = []
    invalid: List[str] = []
    for item in raw or []:
        email = (item or "").strip().lower()
        if not email:
            continue
        if not _EMAIL.match(email) or len(email) > 254:
            invalid.append(item.strip())
        elif email in emails:
            duplicates.append(email)
        else:
            emails.append(email)
    if invalid:
        raise HTTPException(status_code=422, detail="Not a valid email address: " + ", ".join(invalid))
    if owner_email and owner_email.lower() in emails:
        raise HTTPException(status_code=422, detail="You are the host; you do not need to invite yourself.")
    return {"emails": emails, "duplicates": duplicates}


# ---------------------------------------------------------------------------
# Views and links
# ---------------------------------------------------------------------------

def _link_expiry(schedule: Dict[str, Any]) -> datetime:
    start = datetime.fromisoformat(schedule["scheduled_start_utc"])
    end = start + timedelta(minutes=schedule.get("duration_minutes") or 60)
    return max(auth._now() + timedelta(days=auth.INVITATION_DAYS), end + LINK_GRACE)


def _issue_link(schedule: Dict[str, Any], invitee_id: int, owner_id: str) -> Dict[str, Any]:
    """A fresh link for one invitee; the previous one stops working. The token is returned once, never stored."""
    expires = _link_expiry(schedule)
    token = auth.create_invitation(schedule["meeting_id"], owner_id, expires_at=expires)
    db.set_invitee_token(invitee_id, auth.token_hash(token))
    return {"token": token, "room_name": schedule["room_name"],
            "link_path": f"/?room={schedule['room_name']}&invite={token}", "expires_at": expires.isoformat()}


def _invitee_view(row: Dict[str, Any]) -> Dict[str, Any]:
    view = {"invitee_id": row["invitee_id"], "email": row["email"], "status": row["status"],
            "delivery": row["delivery"], "accepted_by": None}
    if row.get("accepted_user_id"):
        user = db.get_user(row["accepted_user_id"]) or {}
        view["accepted_by"] = {
            "display_name": user.get("display_name"),
            "email": user.get("email"),
            # The invited address and the accepting account can differ (a
            # forwarded link). Shown to the host, never used as identity proof.
            "email_matches_invite": (user.get("email") or "").lower() == row["email"].lower(),
        }
    return view


def schedule_view(schedule: Dict[str, Any], *, include_invitees: bool) -> Dict[str, Any]:
    zone = ZoneInfo(schedule["timezone"])
    start = datetime.fromisoformat(schedule["scheduled_start_utc"])
    view = {
        "meeting_id": schedule["meeting_id"],
        "title": schedule["title"],
        "agenda": schedule["agenda"],
        "status": schedule["status"],
        "scheduled_start_utc": schedule["scheduled_start_utc"],
        "scheduled_start_local": start.astimezone(zone).strftime("%Y-%m-%dT%H:%M"),
        "timezone": schedule["timezone"],
        "duration_minutes": schedule["duration_minutes"],
        "room_name": schedule["room_name"],
        "created_at": schedule["created_at"],
        "cancelled_at": schedule["cancelled_at"],
    }
    invitees = db.list_invitees(schedule["meeting_id"])
    view["invitee_count"] = sum(1 for i in invitees if i["status"] != "revoked")
    if include_invitees:
        view["invitees"] = [_invitee_view(i) for i in invitees]
    return view


def _owned_schedule(request: Request, meeting_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    user = _signed_in_owner(request)
    role = db.get_member_role(meeting_id, user["user_id"])
    schedule = db.get_scheduled_meeting(meeting_id)
    if role is None or schedule is None:
        raise HTTPException(status_code=404, detail="Meeting not found.")
    if role != "owner":
        raise HTTPException(status_code=403, detail="Only the meeting owner can do this.")
    return user, schedule


def _require_open(schedule: Dict[str, Any]) -> None:
    if schedule["status"] == "cancelled":
        raise HTTPException(status_code=409, detail="This meeting was cancelled.")
    if schedule["status"] != "scheduled":
        raise HTTPException(status_code=409, detail="This meeting has already started.")


def _invite(schedule: Dict[str, Any], emails: List[str], owner_id: str) -> Dict[str, Any]:
    delivery = get_delivery()
    links, already = [], []
    for email in emails:
        invitee_id = db.add_invitee(schedule["meeting_id"], email)
        if invitee_id is None:
            already.append(email)
            continue
        link = _issue_link(schedule, invitee_id, owner_id)
        links.append({"invitee_id": invitee_id, "email": email, **link,
                      "delivery": delivery.deliver(email, link["link_path"], schedule["title"]).as_dict()})
    return {"links": links, "already_invited": already}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/schedule")
def schedule_meeting(payload: ScheduleRequest, request: Request):
    """Schedule a future meeting and invite several people in one operation."""
    db.init_db()
    user = _signed_in_owner(request)
    request_id = (payload.request_id or "").strip()[:80] or None
    if request_id:
        existing = db.find_scheduled_by_request(user["user_id"], request_id)
        if existing:
            # A repeated submit (double click, retry) returns the meeting already
            # created. Links are not reissued here; copy them from the meeting page.
            return {"meeting": schedule_view(existing, include_invitees=True), "links": [],
                    "duplicate_request": True, "duplicates_ignored": [], "email_sent": False}

    title = _title(payload.title)
    agenda = _agenda(payload.agenda)
    start = _start_utc(payload.start_local, payload.timezone)
    duration = _duration(payload.duration_minutes)
    cleaned = clean_emails(payload.invitees, user["email"])
    if len(cleaned["emails"]) > MAX_INVITEES:
        raise HTTPException(status_code=422, detail=f"Invite at most {MAX_INVITEES} people at once.")

    meeting_id = uuid4().hex
    db.create_scheduled_meeting(
        meeting_id, title=title, agenda=agenda, scheduled_start_utc=start.isoformat(),
        timezone_name=payload.timezone.strip(), duration_minutes=duration,
        room_name=f"m2m-{secrets.token_hex(5)}", owner_id=user["user_id"], request_id=request_id,
    )
    schedule = db.get_scheduled_meeting(meeting_id)
    invited = _invite(schedule, cleaned["emails"], user["user_id"])
    return {"meeting": schedule_view(schedule, include_invitees=True), "links": invited["links"],
            "duplicate_request": False, "duplicates_ignored": cleaned["duplicates"], "email_sent": False}


@router.get("/meetings/{meeting_id}/schedule")
def get_schedule(meeting_id: str, request: Request):
    """Members see the schedule; only the owner sees the invitee list."""
    db.init_db()
    user = auth.current_user(request)
    role = db.get_member_role(meeting_id, user["user_id"]) if auth.auth_required() else "owner"
    schedule = db.get_scheduled_meeting(meeting_id)
    if role is None or schedule is None:
        raise HTTPException(status_code=404, detail="Meeting not found.")
    return {"meeting": schedule_view(schedule, include_invitees=role == "owner"), "is_owner": role == "owner"}


@router.patch("/meetings/{meeting_id}/schedule")
def update_schedule(meeting_id: str, payload: ScheduleUpdate, request: Request):
    db.init_db()
    _, schedule = _owned_schedule(request, meeting_id)
    _require_open(schedule)
    fields: Dict[str, Any] = {}
    if payload.title is not None:
        fields["title"] = _title(payload.title)
    if payload.agenda is not None:
        fields["agenda"] = _agenda(payload.agenda)
    if payload.duration_minutes is not None:
        fields["duration_minutes"] = _duration(payload.duration_minutes)
    if payload.start_local is not None or payload.timezone is not None:
        zone_name = (payload.timezone or schedule["timezone"]).strip()
        local = payload.start_local or datetime.fromisoformat(schedule["scheduled_start_utc"]).astimezone(
            _zone(zone_name)).strftime("%Y-%m-%dT%H:%M")
        fields["scheduled_start_utc"] = _start_utc(local, zone_name).isoformat()
        fields["timezone"] = zone_name
    if not db.update_scheduled_meeting(meeting_id, fields):
        raise HTTPException(status_code=409, detail="This meeting can no longer be edited.")
    return {"meeting": schedule_view(db.get_scheduled_meeting(meeting_id), include_invitees=True)}


@router.post("/meetings/{meeting_id}/schedule/invitees")
def add_invitees(meeting_id: str, payload: InviteesRequest, request: Request):
    db.init_db()
    user, schedule = _owned_schedule(request, meeting_id)
    _require_open(schedule)
    cleaned = clean_emails(payload.emails, user["email"])
    if not cleaned["emails"]:
        raise HTTPException(status_code=422, detail="Enter at least one email address.")
    if len(cleaned["emails"]) + len(db.list_invitees(meeting_id)) > MAX_INVITEES:
        raise HTTPException(status_code=422, detail=f"A meeting can have at most {MAX_INVITEES} invitees.")
    invited = _invite(schedule, cleaned["emails"], user["user_id"])
    return {"meeting": schedule_view(db.get_scheduled_meeting(meeting_id), include_invitees=True),
            "links": invited["links"], "already_invited": invited["already_invited"],
            "duplicates_ignored": cleaned["duplicates"], "email_sent": False}


@router.post("/meetings/{meeting_id}/schedule/invitees/{invitee_id}/link")
def copy_invitee_link(meeting_id: str, invitee_id: int, request: Request):
    """
    A link for one invitee. Only token hashes are stored, so the link cannot be
    shown again; a new one is issued and the previous one stops working.
    """
    db.init_db()
    user, schedule = _owned_schedule(request, meeting_id)
    if schedule["status"] == "cancelled":
        raise HTTPException(status_code=409, detail="This meeting was cancelled.")
    invitee = db.get_invitee(meeting_id, invitee_id)
    if invitee is None:
        raise HTTPException(status_code=404, detail="Invitee not found.")
    if invitee["status"] == "revoked":
        raise HTTPException(status_code=409, detail="This invitation was revoked. Invite the person again.")
    link = _issue_link(schedule, invitee_id, user["user_id"])
    return {"invitee_id": invitee_id, "email": invitee["email"], **link,
            "delivery": get_delivery().deliver(invitee["email"], link["link_path"], schedule["title"]).as_dict(),
            "previous_link_revoked": bool(invitee["token_hash"])}


@router.delete("/meetings/{meeting_id}/schedule/invitees/{invitee_id}")
def revoke_invitee(meeting_id: str, invitee_id: int, request: Request):
    """Revoke the link; an invitee who already joined loses access to this meeting."""
    db.init_db()
    _, schedule = _owned_schedule(request, meeting_id)
    invitee = db.get_invitee(meeting_id, invitee_id)
    if invitee is None:
        raise HTTPException(status_code=404, detail="Invitee not found.")
    db.revoke_invitee(invitee_id)
    if invitee["accepted_user_id"]:
        db.remove_meeting_member(meeting_id, invitee["accepted_user_id"])
    return {"meeting": schedule_view(db.get_scheduled_meeting(meeting_id), include_invitees=True)}


@router.post("/meetings/{meeting_id}/schedule/cancel")
def cancel_schedule(meeting_id: str, request: Request):
    """Cancel before it starts; every invitation link stops working. Repeating it is harmless."""
    db.init_db()
    _, schedule = _owned_schedule(request, meeting_id)
    if schedule["status"] == "started":
        raise HTTPException(status_code=409, detail="This meeting has already started and cannot be cancelled.")
    db.cancel_scheduled_meeting(meeting_id)
    return {"meeting": schedule_view(db.get_scheduled_meeting(meeting_id), include_invitees=True)}
