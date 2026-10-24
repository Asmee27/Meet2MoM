"""
Email delivery API (Phase 8.4): approved-minutes emails, delivery history,
retries, provider status and the Brevo delivery webhook.

Sending approved minutes:
* only when the minutes are approved, and only the current approved version
  (the client names the version it showed, so a reopened or re-approved
  meeting is never sent by mistake);
* only by the owner or the member who approved that version;
* only to recipients the meeting itself lists (members, invitees who have not
  joined); never to a typed address;
* a PDF attachment needs an explicit choice and a confirmation;
* each (version, recipient) is queued once; repeating the request reports
  "already sent" instead of emailing the person again.
Every request and every delivery result is written to the review audit log
(who, which version, recipients, result). No tokens or links are logged.
"""
from __future__ import annotations

import hmac
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.core import auth, config, db
from app.services.email import minutes as minutes_service
from app.services.email import outbox, providers

router = APIRouter(prefix="/api", tags=["Email"])
meeting_access = [Depends(auth.require_meeting_access)]


class MinutesEmailRequest(BaseModel):
    recipients: List[str]                 # keys from GET …/minutes-email, e.g. "user:u_…", "invitee:12"
    version: int
    attach_pdf: bool = False
    confirm_attachment: bool = False


def _signed_in(request: Request) -> Dict[str, Any]:
    user = auth.current_user(request)
    if not auth.auth_required() or user is None:
        raise HTTPException(status_code=409, detail="Sending email needs sign-in (AUTH_REQUIRED=true).")
    return user


def _identity(user: Dict[str, Any]) -> str:
    return f"{user['display_name']} <{user['email']}>"


def _history(meeting_id: str, kind: str) -> List[Dict[str, Any]]:
    rows = db.list_emails(meeting_id, kind)
    events = db.list_email_events([r["outbox_id"] for r in rows])
    return [outbox.email_view(r, events.get(r["outbox_id"])) for r in reversed(rows)]


@router.get("/email/status")
def email_status(request: Request):
    """Whether email is really sent (provider), so the UI never implies otherwise."""
    auth.current_user(request)
    return providers.provider_status()


@router.get("/meetings/{meeting_id}/minutes-email", dependencies=meeting_access)
def minutes_email_options(meeting_id: str, request: Request):
    db.init_db()
    user = _signed_in(request)
    snapshot = minutes_service.current_approved_snapshot(meeting_id)
    base = {"provider": providers.provider_status(), "history": _history(meeting_id, "minutes")}
    if snapshot is None:
        return {**base, "approved": False, "can_send": False, "recipients": [],
                "reason": "Only approved minutes can be sent. Approve the minutes first."}
    can = minutes_service.can_send(meeting_id, user, snapshot)
    return {
        **base,
        "approved": True,
        "version": snapshot["version"],
        "approved_at": snapshot["approved_at"],
        "approved_by": snapshot["approved_by"],
        "can_send": can,
        "reason": None if can else "Only the meeting owner or the reviewer who approved these minutes can send them.",
        "recipients": minutes_service.recipients(meeting_id) if can else [],
    }


@router.post("/meetings/{meeting_id}/minutes-email", dependencies=meeting_access)
def send_minutes_email(meeting_id: str, payload: MinutesEmailRequest, request: Request):
    db.init_db()
    user = _signed_in(request)
    snapshot = minutes_service.current_approved_snapshot(meeting_id)
    if snapshot is None:
        raise HTTPException(status_code=409, detail="Only approved minutes can be sent. Approve the minutes first.")
    if not minutes_service.can_send(meeting_id, user, snapshot):
        raise HTTPException(status_code=403,
                            detail="Only the meeting owner or the reviewer who approved these minutes can send them.")
    if payload.version != snapshot["version"]:
        raise HTTPException(status_code=409, detail=f"These minutes are now version {snapshot['version']}, "
                                                    f"not {payload.version}. Review the current version first.")
    if payload.attach_pdf and not payload.confirm_attachment:
        raise HTTPException(status_code=422, detail="Confirm the recipients before attaching the minutes PDF.")
    keys = list(dict.fromkeys(k.strip() for k in payload.recipients if k and k.strip()))
    if not keys:
        raise HTTPException(status_code=422, detail="Choose at least one recipient.")
    entitled = {r["key"]: r for r in minutes_service.recipients(meeting_id)}
    refused = [k for k in keys if k not in entitled or not entitled[k]["eligible"]]
    if refused:
        raise HTTPException(status_code=422, detail="These recipients cannot receive this meeting's minutes: "
                                                    + ", ".join(refused))

    queued, already = [], []
    for key in keys:
        row, created = outbox.enqueue_minutes(meeting_id, snapshot["version"], entitled[key], payload.attach_pdf,
                                              requested_by=user["user_id"])
        (queued if created else already).append(outbox.email_view(row))
    db.record_audit_event(
        meeting_id, "minutes_email_requested", field_name="minutes_version",
        new_value=json.dumps({
            "version": snapshot["version"],
            "recipients": [entitled[k]["email"] for k in keys],
            "attach_pdf": payload.attach_pdf,
            "queued": len(queued),
            "already_sent_or_pending": len(already),
            "provider": providers.provider_status()["provider"],
        }),
        reviewer_id=_identity(user),
    )
    if queued:
        outbox.kick()
    return {"version": snapshot["version"], "queued": queued, "already": already,
            "provider": providers.provider_status(), "history": _history(meeting_id, "minutes")}


@router.get("/meetings/{meeting_id}/emails", dependencies=meeting_access)
def email_history(meeting_id: str, request: Request, kind: Optional[str] = None):
    """Delivery history of a meeting's emails (owner only)."""
    db.init_db()
    _signed_in(request)
    auth.require_owner(request, meeting_id)
    if kind not in (None, "invitation", "minutes"):
        raise HTTPException(status_code=422, detail="kind must be invitation or minutes.")
    rows = []
    for k in ([kind] if kind else ["invitation", "minutes"]):
        rows.extend(_history(meeting_id, k))
    return {"emails": rows, "provider": providers.provider_status()}


@router.post("/meetings/{meeting_id}/emails/{outbox_id}/retry", dependencies=meeting_access)
def retry_email(meeting_id: str, outbox_id: int, request: Request):
    """Retry one failed message. The same record is re-queued, so a retry never duplicates a delivery."""
    db.init_db()
    user = _signed_in(request)
    row = db.get_email(outbox_id)
    if row is None or row["meeting_id"] != meeting_id:
        raise HTTPException(status_code=404, detail="Email not found.")
    if row["kind"] == "minutes":
        snapshot = minutes_service.current_approved_snapshot(meeting_id)
        if snapshot is None or not minutes_service.can_send(meeting_id, user, snapshot):
            raise HTTPException(status_code=403, detail="You cannot send these minutes.")
        if snapshot["version"] != row["minutes_version"]:
            raise HTTPException(status_code=409, detail=f"Version {row['minutes_version']} is no longer the approved "
                                                        f"version (now {snapshot['version']}). Send the current version.")
    else:
        auth.require_owner(request, meeting_id)
    if row["status"] not in outbox.RETRYABLE:
        raise HTTPException(status_code=409, detail="Only failed emails can be retried.")
    outbox.retry(outbox_id)
    if row["kind"] == "minutes":
        db.record_audit_event(meeting_id, "minutes_email_retry", field_name="minutes_version",
                              new_value=json.dumps({"outbox_id": outbox_id, "version": row["minutes_version"],
                                                    "recipient": row["recipient_email"]}),
                              reviewer_id=_identity(user))
    outbox.kick()
    return {"email": outbox.email_view(db.get_email(outbox_id))}


# ---------------------------------------------------------------------------
# Brevo delivery webhook
# ---------------------------------------------------------------------------

def _webhook_authorized(request: Request) -> bool:
    expected = getattr(config, "BREVO_WEBHOOK_TOKEN", None)
    if not expected:
        return False
    supplied = request.headers.get("x-meet2mom-webhook-token") or ""
    authorization = request.headers.get("authorization") or ""
    if not supplied and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    return bool(supplied) and hmac.compare_digest(supplied.encode(), expected.encode())


@router.post("/email/webhooks/brevo")
async def brevo_webhook(request: Request):
    """Delivery events from Brevo. Disabled unless BREVO_WEBHOOK_TOKEN is set; the token is required."""
    if not getattr(config, "BREVO_WEBHOOK_TOKEN", None):
        raise HTTPException(status_code=503, detail="Delivery webhooks are not configured.")
    if not _webhook_authorized(request):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Expected a JSON body.")
    events = body if isinstance(body, list) else [body]
    db.init_db()
    applied = sum(1 for e in events if isinstance(e, dict) and outbox.apply_brevo_event(e))
    return {"received": len(events), "applied": applied}
