"""
Email content (Phase 8.4). Every value from a meeting is HTML-escaped.

Minutes emails are built only from an approved snapshot: its confirmed
decisions and action items. Held items are counted and named as not
confirmed; they are never listed as decisions or commitments.
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

SUMMARY_LIMIT = 900
LIST_LIMIT = 15


def format_start(schedule: Dict[str, Any]) -> str:
    start = datetime.fromisoformat(schedule["scheduled_start_utc"])
    zone = ZoneInfo(schedule["timezone"])
    local = start.astimezone(zone)
    offset = local.strftime("%z")
    offset = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"
    return f"{local.strftime('%A, %d %B %Y, %H:%M')} ({schedule['timezone']}, {offset})"


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html><body style=\"margin:0;background:#f5f6fa;font-family:Segoe UI,Arial,sans-serif;"
        "color:#1d2033\"><div style=\"max-width:600px;margin:0 auto;padding:24px\">"
        "<div style=\"background:#ffffff;border:1px solid #e3e6ef;border-radius:12px;padding:24px\">"
        f"<p style=\"margin:0 0 4px;font-size:12px;color:#5b6178;letter-spacing:.04em\">MEET2MOM</p>"
        f"<h1 style=\"margin:0 0 16px;font-size:20px\">{escape(title)}</h1>{body}</div>"
        "<p style=\"font-size:12px;color:#7a8099;margin:16px 4px\">You received this because a Meet2MoM meeting "
        "host added your address. If you did not expect it, you can ignore this email.</p></div></body></html>"
    )


def _button(url: str, label: str) -> str:
    return (f"<p style=\"margin:20px 0\"><a href=\"{escape(url, quote=True)}\" style=\"background:#4f46e5;"
            f"color:#ffffff;padding:10px 18px;border-radius:8px;text-decoration:none;display:inline-block\">"
            f"{escape(label)}</a></p>")


def invitation_email(schedule: Dict[str, Any], host_name: str, link: str, expires_text: str) -> Dict[str, str]:
    title = schedule["title"]
    rows = [
        ("When", format_start(schedule)),
        ("Duration", f"{schedule['duration_minutes']} minutes" if schedule.get("duration_minutes") else None),
        ("Host", host_name),
    ]
    details = "".join(
        f"<tr><td style=\"padding:4px 12px 4px 0;color:#5b6178\">{escape(k)}</td><td>{escape(v)}</td></tr>"
        for k, v in rows if v
    )
    agenda = (f"<p style=\"margin:16px 0 4px;font-weight:600\">Agenda</p>"
              f"<p style=\"margin:0;white-space:pre-wrap\">{escape(schedule['agenda'])}</p>") if schedule.get("agenda") else ""
    body = (
        f"<p>{escape(host_name)} invited you to a meeting.</p>"
        f"<table style=\"border-collapse:collapse;font-size:14px\">{details}</table>{agenda}"
        + _button(link, "Open invitation")
        + "<p style=\"font-size:13px;color:#5b6178\">Sign in (or create an account) with this email address, then "
          f"open the link. It works only for this meeting and expires {escape(expires_text)}. "
          "The meeting is recorded only if participants consent when they join.</p>"
    )
    return {"subject": f"Invitation: {title}", "html": _page(title, body)}


def _items(title: str, items: List[str]) -> str:
    if not items:
        return ""
    shown = "".join(f"<li style=\"margin:2px 0\">{escape(i)}</li>" for i in items[:LIST_LIMIT])
    more = f"<li>…and {len(items) - LIST_LIMIT} more in the full minutes</li>" if len(items) > LIST_LIMIT else ""
    return f"<p style=\"margin:16px 0 4px;font-weight:600\">{escape(title)}</p><ul style=\"margin:0;padding-left:20px\">{shown}{more}</ul>"


def minutes_email(snapshot: Dict[str, Any], meeting_date: Optional[str], link: str,
                  needs_sign_in_with_invite: bool, attached: bool) -> Dict[str, str]:
    mom = snapshot["mom_payload"] or {}
    title = mom.get("meeting_title") or "Meeting minutes"
    version = snapshot["version"]
    approved_at = (snapshot.get("approved_at") or "")[:16].replace("T", " ")
    approved_by = (snapshot.get("approved_by") or "").split(" <")[0]
    summary = str(mom.get("summary") or "").strip()
    if len(summary) > SUMMARY_LIMIT:
        summary = summary[:SUMMARY_LIMIT].rsplit(" ", 1)[0] + "…"

    decisions = [str(d.get("text") or "") for d in mom.get("decisions") or []]
    actions = []
    for a in mom.get("action_items") or []:
        parts = [str(a.get("task") or "")]
        if a.get("owner"):
            parts.append(f"owner: {a['owner']}")
        if a.get("dueDate"):
            parts.append(f"due: {a['dueDate']}")
        actions.append(" · ".join(parts))
    held = len(mom.get("needs_review") or [])

    held_note = ""
    if held:
        held_text = (f"{held} items were held for review and are not part of the confirmed minutes." if held != 1
                     else "1 item was held for review and is not part of the confirmed minutes.")
        held_note = f"<p style=\"font-size:13px;color:#8a5a00\">{held_text}</p>"
    access = ("Sign in with this email address; the link adds you to this meeting first." if needs_sign_in_with_invite
              else "Sign in to Meet2MoM to open them.")
    body = (
        f"<p style=\"margin:0 0 12px\"><strong>Approved minutes, version {version}</strong>"
        f"{' · approved ' + escape(approved_at) + ' UTC' if approved_at else ''}"
        f"{' by ' + escape(approved_by) if approved_by else ''}</p>"
        + (f"<p style=\"margin:0 0 12px;color:#5b6178\">Meeting date: {escape(meeting_date)}</p>" if meeting_date else "")
        + (f"<p style=\"white-space:pre-wrap\">{escape(summary)}</p>" if summary else "")
        + _items("Confirmed decisions", decisions)
        + _items("Confirmed action items", actions)
        + held_note
        + _button(link, f"Open approved minutes (v{version})")
        + f"<p style=\"font-size:13px;color:#5b6178\">{escape(access)}"
        + (" The approved PDF is attached." if attached else "") + "</p>"
    )
    return {"subject": f"Approved minutes: {title} (v{version})", "html": _page(title, body)}
