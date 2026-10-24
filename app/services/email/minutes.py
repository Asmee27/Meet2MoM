"""
Who may send approved minutes, and to whom (Phase 8.4).

* Only approved minutes are sent, and only the current approved version.
* Senders: the meeting owner, or the signed-in member who approved this version.
* Recipients come only from the meeting itself, never a typed address:
  members (owner and participants), and scheduled-meeting invitees who have
  not joined yet. An invitee's email carries a fresh invitation link that
  adds them to this meeting after they sign in with the invited address.
  Revoked invitees, and accounts still waiting for the host's approval, are
  not eligible.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core import db
from app.services.email import templates


def current_approved_snapshot(meeting_id: str) -> Optional[Dict[str, Any]]:
    draft = db.get_review_draft(meeting_id)
    if not draft or draft.get("draft_status") != "approved":
        return None
    return db.get_latest_approved_snapshot(meeting_id)


def can_send(meeting_id: str, user: Dict[str, Any], snapshot: Dict[str, Any]) -> bool:
    role = db.get_member_role(meeting_id, user["user_id"])
    if role == "owner":
        return True
    approver = f"{user['display_name']} <{user['email']}>"
    return role is not None and (snapshot.get("approved_by") or "") == approver


def recipients(meeting_id: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for member in db.list_meeting_members(meeting_id):
        email = (member["email"] or "").lower()
        seen.add(email)
        out.append({
            "key": f"user:{member['user_id']}", "kind": "owner" if member["role"] == "owner" else "member",
            "name": member["display_name"], "email": email, "user_id": member["user_id"], "invitee_id": None,
            "eligible": True, "reason": None,
        })
    for invitee in db.list_invitees(meeting_id):
        email = invitee["email"].lower()
        if email in seen or invitee["status"] == "accepted":
            continue                                   # already listed as a member
        seen.add(email)
        entry = {"key": f"invitee:{invitee['invitee_id']}", "kind": "invitee", "name": None, "email": email,
                 "user_id": None, "invitee_id": invitee["invitee_id"], "eligible": True,
                 "reason": "Invited, not joined yet. The email includes a link that adds them to this meeting "
                           "after they sign in with this address."}
        if invitee["status"] == "revoked":
            entry.update(eligible=False, reason="Invitation revoked.")
        elif invitee.get("approval_user_id"):
            entry.update(eligible=False, reason="A different account is waiting for your approval for this invite.")
        out.append(entry)
    return out


def recipient_for_row(meeting_id: str, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The recipient a queued message was for, as entitled now (None if gone)."""
    by_key = {r["key"]: r for r in recipients(meeting_id)}
    if row.get("recipient_user_id"):
        return by_key.get(f"user:{row['recipient_user_id']}")
    if row.get("invitee_id"):
        invitee = db.get_invitee(meeting_id, row["invitee_id"])
        if invitee and invitee["status"] == "accepted" and invitee.get("accepted_user_id"):
            return by_key.get(f"user:{invitee['accepted_user_id']}")
        return by_key.get(f"invitee:{row['invitee_id']}")
    return None


def meeting_date(meeting_id: str) -> Optional[str]:
    schedule = db.get_scheduled_meeting(meeting_id)
    if schedule:
        return templates.format_start(schedule)
    meeting = db.get_meeting(meeting_id) or {}
    created = meeting.get("created_at")
    if not created:
        return None
    try:
        return datetime.fromisoformat(created).strftime("%d %B %Y") + " (UTC)"
    except ValueError:
        return None
