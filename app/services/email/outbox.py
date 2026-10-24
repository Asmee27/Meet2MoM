"""
Email outbox and dispatcher (Phase 8.4).

Requests only queue messages (email_outbox rows, unique per idempotency key)
and return; a dispatcher sends them in the background:

* EMAIL_DISPATCH=api (default): a daemon thread in the API process, woken
  right after something is queued and every POLL_SECONDS for retries.
* EMAIL_DISPATCH=worker: scripts/worker.py calls dispatch_pending().
* EMAIL_DISPATCH=off: nothing sends (tests call dispatch_pending()).

Link tokens are never stored in the outbox. An invitation or minutes link is
issued when the message is actually sent (only its hash is stored, as for
every invitation), placed in the email body in memory, and never logged. If
the message is not sent, the token is deleted again.

Status of a message (the UI shows exactly these):
  queued, sending           waiting / in progress (retries wait in 'queued')
  not_sent_dev              development mode: nothing was sent
  provider_accepted         the provider accepted it; not proof of delivery
  deferred, soft_bounced    the provider is still trying
  delivered                 the provider reported delivery to the recipient's server
  hard_bounced, invalid,
  blocked, spam             the provider reported a permanent problem
  failed                    not sent (or unknown after a timeout); see last_error
  cancelled                 not sent because the invitation, meeting or minutes changed
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.core import auth, config, db
from app.services.email import providers, templates
from app.services.email.addresses import mask_email

logger = logging.getLogger(__name__)

POLL_SECONDS = 20
STALE_SENDING = timedelta(minutes=10)
BACKOFF_SECONDS = (30, 120, 600)          # waits before attempts 2, 3, 4…

# Only messages that did not get through for a reason a retry can fix. Bounced,
# invalid or blocked addresses would fail the same way; cancelled messages were
# stopped on purpose (revoked, cancelled meeting, minutes reopened).
RETRYABLE = ("failed",)
# Webhook precedence: a later event never moves a message back to a weaker state.
_RANK = {"provider_accepted": 1, "deferred": 2, "soft_bounced": 3, "delivered": 4,
         "hard_bounced": 5, "invalid": 5, "blocked": 5, "spam": 5, "failed": 5}
_BREVO_EVENTS = {
    "delivered": "delivered",
    "hard_bounce": "hard_bounced",
    "soft_bounce": "soft_bounced",
    "invalid_email": "invalid",
    "blocked": "blocked",
    "spam": "spam",
    "deferred": "deferred",
    "error": "failed",
}
# Recorded in the history but do not change the status (and opens or clicks
# are not stored at all).
_BREVO_RECORD_ONLY = ("request", "unsubscribed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


# ---------------------------------------------------------------------------
# Queueing
# ---------------------------------------------------------------------------

def enqueue_invitation(meeting_id: str, invitee: Dict[str, Any], requested_by: str,
                       attempt: Optional[int] = None) -> tuple[Dict[str, Any], bool]:
    """One invitation email per (invitee, attempt). Re-queuing the same attempt is a no-op."""
    attempt = attempt or (db.count_invitation_emails(invitee["invitee_id"]) or 0) + 1
    return db.enqueue_email(
        idempotency_key=f"invite:{invitee['invitee_id']}:{attempt}", kind="invitation", meeting_id=meeting_id,
        recipient_email=invitee["email"], invitee_id=invitee["invitee_id"], requested_by=requested_by,
    )


def enqueue_minutes(meeting_id: str, version: int, recipient: Dict[str, Any], attach_pdf: bool,
                    requested_by: str) -> tuple[Dict[str, Any], bool]:
    """One minutes email per (version, recipient, attachment choice)."""
    key = f"minutes:{meeting_id}:v{version}:{recipient['key']}{':pdf' if attach_pdf else ''}"
    return db.enqueue_email(
        idempotency_key=key, kind="minutes", meeting_id=meeting_id, recipient_email=recipient["email"],
        recipient_name=recipient.get("name"), recipient_user_id=recipient.get("user_id"),
        invitee_id=recipient.get("invitee_id"), minutes_version=version, attach_pdf=attach_pdf,
        requested_by=requested_by,
    )


def retry(outbox_id: int) -> bool:
    """Queue a message that did not reach the recipient again (same row, so no duplicate record)."""
    return db.update_email(outbox_id, only_if_status=RETRYABLE, status="queued", attempts=0,
                           next_attempt_at=_iso(_now()), last_error=None, provider_message_id=None)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

class _Skip(Exception):
    """The message must not be sent any more (cancelled with this reason)."""


def _invitation_message(row: Dict[str, Any]) -> tuple[providers.OutgoingEmail, Optional[str]]:
    schedule = db.get_scheduled_meeting(row["meeting_id"])
    invitee = db.get_invitee(row["meeting_id"], row["invitee_id"]) if row.get("invitee_id") else None
    if schedule is None or invitee is None:
        raise _Skip("The meeting or invitation no longer exists.")
    if schedule["status"] == "cancelled":
        raise _Skip("The meeting was cancelled before this invitation was sent.")
    if invitee["status"] == "revoked":
        raise _Skip("The invitation was revoked before it was sent.")
    if invitee["status"] == "accepted":
        raise _Skip("The invitee had already accepted, so no email was needed.")

    from app.api.schedule_routes import link_expiry

    expires = link_expiry(schedule)
    token = auth.create_invitation(schedule["meeting_id"], schedule["created_by"], expires_at=expires,
                                   invitee_id=invitee["invitee_id"], channel="email")
    link = f"{config.PUBLIC_APP_URL}/?room={schedule['room_name']}&invite={token}"
    host = db.get_user(schedule["created_by"]) or {}
    content = templates.invitation_email(schedule, host.get("display_name") or "The host", link,
                                         expires.strftime("%d %b %Y %H:%M UTC"))
    message = providers.OutgoingEmail(
        to_email=row["recipient_email"], to_name=None, subject=content["subject"], html=content["html"],
        reference=f"m2m-outbox-{row['outbox_id']}", tags=["meet2mom", "invitation"],
    )
    return message, auth.token_hash(token)


def _minutes_message(row: Dict[str, Any]) -> tuple[providers.OutgoingEmail, Optional[str]]:
    from app.services.email import minutes as minutes_service

    meeting_id = row["meeting_id"]
    current = minutes_service.current_approved_snapshot(meeting_id)
    if current is None:
        raise _Skip("The minutes were reopened before this email was sent; nothing approved to send.")
    if current["version"] != row["minutes_version"]:
        raise _Skip(f"Version {row['minutes_version']} was replaced by version {current['version']} before sending.")
    recipient = minutes_service.recipient_for_row(meeting_id, row)
    if recipient is None or not recipient["eligible"]:
        raise _Skip("The recipient is no longer entitled to this meeting's minutes.")

    link = f"{config.PUBLIC_APP_URL}/?meeting={meeting_id}&step=minutes"
    token_hash = None
    if recipient["kind"] == "invitee":
        # Not a member yet: the link carries a fresh invitation for this
        # meeting, accepted after sign-in (the email must match, as always).
        schedule = db.get_scheduled_meeting(meeting_id)
        from app.api.schedule_routes import link_expiry

        token = auth.create_invitation(meeting_id, schedule["created_by"], expires_at=link_expiry(schedule),
                                       invitee_id=recipient["invitee_id"], channel="minutes")
        link += f"&invite={token}"
        token_hash = auth.token_hash(token)

    attachments = []
    if row.get("attach_pdf"):
        from app.api.meeting_routes import build_pdf

        pdf_path = build_pdf(meeting_id, current["mom_payload"])
        attachments.append((f"minutes-v{current['version']}.pdf", pdf_path.read_bytes()))
    content = templates.minutes_email(current, minutes_service.meeting_date(meeting_id), link,
                                      needs_sign_in_with_invite=recipient["kind"] == "invitee",
                                      attached=bool(attachments))
    message = providers.OutgoingEmail(
        to_email=row["recipient_email"], to_name=row.get("recipient_name"), subject=content["subject"],
        html=content["html"], reference=f"m2m-outbox-{row['outbox_id']}", tags=["meet2mom", "minutes"],
        attachments=attachments,
    )
    return message, token_hash


def _send_one(row: Dict[str, Any], sender) -> str:
    token_hash = None
    try:
        build = _invitation_message if row["kind"] == "invitation" else _minutes_message
        message, token_hash = build(row)
        outcome = sender.send(message)
    except _Skip as skip:
        db.update_email(row["outbox_id"], status="cancelled", last_error=str(skip))
        return "cancelled"
    except Exception as exc:                       # never mark anything sent on an unexpected error
        logger.exception("Email %s could not be prepared", row["outbox_id"])
        if token_hash:
            db.delete_invitation_token(token_hash)
        db.update_email(row["outbox_id"], status="failed", provider=sender.name,
                        last_error=f"Could not prepare the email ({type(exc).__name__}).")
        return "failed"

    attempts = int(row.get("attempts") or 0) + 1
    now = _iso(_now())
    if outcome.kind == "accepted":
        db.update_email(row["outbox_id"], status="provider_accepted", provider=sender.name, attempts=attempts,
                        provider_message_id=outcome.message_id, sent_at=now, last_error=None)
        status = "provider_accepted"
    else:
        # Not accepted: the link inside the email exists nowhere, so drop its token.
        if token_hash and outcome.kind != "unknown":
            db.delete_invitation_token(token_hash)
        if outcome.kind == "not_sent_dev":
            db.update_email(row["outbox_id"], status="not_sent_dev", provider=sender.name, attempts=attempts,
                            last_error="Development mode: this email was not sent.")
            status = "not_sent_dev"
        elif outcome.kind == "retry" and attempts < int(getattr(config, "EMAIL_MAX_ATTEMPTS", 4)):
            wait = outcome.retry_after or BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
            next_at = _now() + timedelta(seconds=wait)
            db.update_email(row["outbox_id"], status="queued", provider=sender.name, attempts=attempts,
                            next_attempt_at=_iso(next_at),
                            last_error=f"{outcome.error} Next attempt at {next_at.strftime('%H:%M:%S')} UTC.")
            status = "queued"
        else:
            reason = outcome.error or "The provider did not accept the message."
            if outcome.kind == "retry":
                reason = f"{reason} Gave up after {attempts} attempts."
            db.update_email(row["outbox_id"], status="failed", provider=sender.name, attempts=attempts,
                            last_error=reason)
            status = "failed"
    if row["kind"] == "minutes":
        db.record_audit_event(row["meeting_id"], "minutes_email_status", field_name="status",
                              new_value=json.dumps({"outbox_id": row["outbox_id"], "version": row["minutes_version"],
                                                    "recipient": row["recipient_email"], "status": status}),
                              reviewer_id="system")
    logger.info("Email %s (%s) to %s: %s", row["outbox_id"], row["kind"], mask_email(row["recipient_email"]), status)
    return status


_dispatch_lock = threading.Lock()


def dispatch_pending(limit: int = 20) -> int:
    """Send every due queued message. Safe to call from several places; one runs at a time per process."""
    if not _dispatch_lock.acquire(blocking=False):
        return 0
    try:
        db.init_db()
        db.fail_stale_sending_emails(_iso(_now() - STALE_SENDING))
        sender = providers.get_sender()
        processed = 0
        while True:
            rows = db.claim_due_emails(_iso(_now()), limit=limit)
            if not rows:
                return processed
            for row in rows:
                _send_one(row, sender)
                processed += 1
    finally:
        _dispatch_lock.release()


def kick() -> None:
    """Start sending now, without making the caller wait for the provider."""
    if getattr(config, "EMAIL_DISPATCH", "api") != "api":
        return
    threading.Thread(target=_safe_dispatch, name="email-dispatch", daemon=True).start()


def _safe_dispatch() -> None:
    try:
        dispatch_pending()
    except Exception:
        logger.exception("Email dispatch failed")


_poller_started = threading.Event()


def start_background_dispatcher(process: str = "api") -> None:
    """
    Periodic sending, retries and recovery after a restart, in the process
    EMAIL_DISPATCH names: "api" (the API server) or "worker" (scripts/worker.py).
    """
    if getattr(config, "EMAIL_DISPATCH", "api") != process or _poller_started.is_set():
        return
    _poller_started.set()

    def loop():
        while True:
            _safe_dispatch()
            threading.Event().wait(POLL_SECONDS)

    threading.Thread(target=loop, name="email-dispatch-poller", daemon=True).start()


# ---------------------------------------------------------------------------
# Delivery events (Brevo webhooks)
# ---------------------------------------------------------------------------

def apply_brevo_event(event: Dict[str, Any]) -> Optional[str]:
    """Update a message from one Brevo event. Returns the new status, or None if nothing changed."""
    message_id = str(event.get("message-id") or event.get("message_id") or "").strip()
    name = str(event.get("event") or "").strip().lower()
    if not message_id or not name:
        return None
    row = db.find_email_by_message_id(message_id)
    if row is None:
        return None
    if name not in _BREVO_EVENTS and name not in _BREVO_RECORD_ONLY:
        return None                                        # opens, clicks…: not stored
    event_ts = str(event.get("ts_event") or event.get("date") or "") or None
    db.record_email_event(row["outbox_id"], name, event.get("reason"), event_ts)
    new_status = _BREVO_EVENTS.get(name)
    if new_status is None or _RANK.get(new_status, 0) < _RANK.get(row["status"], 0):
        return None
    if row["status"] not in _RANK and row["status"] != "sending":
        return None                                        # e.g. cancelled or a dev-mode row
    fields: Dict[str, Any] = {"status": new_status}
    if new_status == "delivered":
        fields["delivered_at"] = _iso(_now())
    if event.get("reason"):
        fields["last_error"] = str(event["reason"])[:300]
    db.update_email(row["outbox_id"], **fields)
    if row["kind"] == "minutes":
        db.record_audit_event(row["meeting_id"], "minutes_email_status", field_name="status",
                              new_value=json.dumps({"outbox_id": row["outbox_id"], "version": row["minutes_version"],
                                                    "recipient": row["recipient_email"], "status": new_status}),
                              reviewer_id="provider")
    return new_status


def email_view(row: Dict[str, Any], events: Optional[list] = None) -> Dict[str, Any]:
    """What the UI and API show for one message (no tokens, no links)."""
    return {
        "outbox_id": row["outbox_id"],
        "kind": row["kind"],
        "recipient_email": row["recipient_email"],
        "recipient_name": row.get("recipient_name"),
        "minutes_version": row.get("minutes_version"),
        "attach_pdf": bool(row.get("attach_pdf")),
        "status": row["status"],
        "provider": row.get("provider"),
        "attempts": row.get("attempts") or 0,
        "last_error": row.get("last_error"),
        "next_attempt_at": row.get("next_attempt_at") if row["status"] == "queued" else None,
        "created_at": row["created_at"],
        "sent_at": row.get("sent_at"),
        "delivered_at": row.get("delivered_at"),
        "can_retry": row["status"] in RETRYABLE,
        "events": [{"event": e["event"], "reason": e["reason"], "at": e["event_ts"] or e["received_at"]}
                   for e in (events or [])],
    }
