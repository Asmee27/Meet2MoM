"""
When a meeting took place, from the facts the system actually has (Phase 9.1).

* Scheduled meeting: the scheduled start, in the meeting's own timezone.
* Live meeting: when the server started capturing audio (else when the room
  opened), in UTC.
* Uploaded recording: unknown. The upload time is not the meeting date, so
  None is returned and the minutes say "Not specified".
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from app.core import db


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def meeting_date_info(meeting_id: str) -> Dict[str, Optional[str]]:
    schedule = db.get_scheduled_meeting(meeting_id)
    if schedule:
        start = _parse(schedule["scheduled_start_utc"])
        if start:
            local = start.astimezone(ZoneInfo(schedule["timezone"]))
            return {"date": f"{local.strftime('%d %b %Y, %H:%M')} ({schedule['timezone']})",
                    "source": "scheduled start"}
    live = db.get_live_meeting(meeting_id)
    if live:
        segments = db.get_recording_segments(meeting_id)
        started = next((_parse(s.get("capture_started_at")) for s in segments if s.get("capture_started_at")), None)
        started = started or _parse(live.get("created_at"))
        if started:
            return {"date": started.astimezone(timezone.utc).strftime("%d %b %Y, %H:%M UTC"),
                    "source": "live recording start" if segments else "live room opened"}
    return {"date": None, "source": None}
