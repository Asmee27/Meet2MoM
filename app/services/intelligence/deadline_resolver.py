from __future__ import annotations

"""
deadline_resolver.py
====================
Deterministic deadline resolution for action items using regex and python-dateutil.
Maps unstructured deadline text into resolved ISO-8601 dates, deadline types,
and calibrated resolution confidences.
"""

from datetime import date, datetime, timedelta
import re
from typing import Optional, Tuple
from dateutil import parser as date_parser

# Module-level Hindi / Marathi marker mapping
# Key: lowercase phrase/word
# Value: (target_type, semantic_key, default_confidence)
HINDI_MARATHI_MARKERS = {
    # Hindi / Marathi relative offsets
    "kal": ("relative_offset", "tomorrow", 0.80),
    "उद्या": ("relative_offset", "tomorrow", 0.80),
    "parso": ("relative_offset", "day_after_tomorrow", 0.80),
    "परवा": ("relative_offset", "day_after_tomorrow", 0.80),
    "agle hafte": ("relative_offset", "next_week", 0.80),
    "पुढच्या आठवड्यात": ("relative_offset", "next_week", 0.80),

    # Weekdays in Devanagari
    "सोमवार": ("relative_weekday", 0, 0.85),
    "मंगळवार": ("relative_weekday", 1, 0.85),
    "मंगलवार": ("relative_weekday", 1, 0.85),
    "बुधवार": ("relative_weekday", 2, 0.85),
    "गुरुवार": ("relative_weekday", 3, 0.85),
    "शुक्रवार": ("relative_weekday", 4, 0.85),
    "शनिवार": ("relative_weekday", 5, 0.85),
    "रविवार": ("relative_weekday", 6, 0.85),
}

# English weekdays (Monday=0 ... Sunday=6)
ENGLISH_WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _last_day_of_month(d: date) -> date:
    """Return the last calendar day of the given date's month."""
    # Step into next month, then subtract day count
    next_month = d.replace(day=28) + timedelta(days=4)
    return next_month - timedelta(days=next_month.day)


def resolve_deadline(
    deadline_text: Optional[str],
    meeting_date: date,
) -> Tuple[Optional[str], str, float]:
    """
    Deterministically resolve deadline_text relative to meeting_date.

    Returns:
      (iso_date_or_None, deadline_type, confidence)
    """
    if not deadline_text or not str(deadline_text).strip():
        return None, "none", 0.0

    raw = str(deadline_text).strip()
    low = raw.lower()

    # -------------------------------------------------------------
    # 1. RECURRING
    # -------------------------------------------------------------
    recurring_patterns = [
        r"\b(?:every|each)\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|month|day|sprint|cycle|standup)\b",
        r"\b(?:weekly|daily|monthly|biweekly|bi-weekly|fortnightly|recurring)\b",
    ]
    if any(re.search(pat, low) for pat in recurring_patterns):
        return None, "recurring", 0.60

    # -------------------------------------------------------------
    # 2. EVENT ANCHORED
    # -------------------------------------------------------------
    event_patterns = [
        r"\b(?:before|prior\s+to|after|post)\s+(?:the\s+)?(?:release|launch|demo|client\s+(?:approves?|approval)|approval|deployment|meeting|audit|milestone|presentation|review|signoff)\b",
        r"\b(?:once|when)\s+(?:the\s+)?(?:client\s+approves?|release\s+is\s+done|testing\s+completes?)\b",
    ]
    if any(re.search(pat, low) for pat in event_patterns):
        return None, "event_anchored", 0.50

    # -------------------------------------------------------------
    # 3. HINDI / MARATHI MARKERS
    # -------------------------------------------------------------
    for marker, (m_type, val, conf) in HINDI_MARATHI_MARKERS.items():
        if marker in low:
            if m_type == "relative_offset":
                if val == "tomorrow":
                    target_date = meeting_date + timedelta(days=1)
                    return target_date.isoformat(), "relative", conf
                elif val == "day_after_tomorrow":
                    target_date = meeting_date + timedelta(days=2)
                    return target_date.isoformat(), "relative", conf
                elif val == "next_week":
                    target_date = meeting_date + timedelta(days=7)
                    return target_date.isoformat(), "relative", conf
            elif m_type == "relative_weekday":
                target_wd = val
                days_ahead = (target_wd - meeting_date.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7
                target_date = meeting_date + timedelta(days=days_ahead)
                return target_date.isoformat(), "relative", conf

    # -------------------------------------------------------------
    # 4. RELATIVE OFFSETS (English)
    # -------------------------------------------------------------
    # day after tomorrow
    if re.search(r"\b(?:by\s+)?(?:the\s+)?day\s+after\s+tomorrow\b", low):
        return (meeting_date + timedelta(days=2)).isoformat(), "relative", 0.80

    # tomorrow
    if re.search(r"\b(?:by\s+)?tomorrow\b", low):
        return (meeting_date + timedelta(days=1)).isoformat(), "relative", 0.80

    # EOD / today / end of day / tonight
    if re.search(r"\b(?:by\s+)?(?:eod|end\s+of\s+(?:the\s+)?day|today|tonight)\b", low):
        return meeting_date.isoformat(), "relative", 0.80

    # EOW / end of week
    if re.search(r"\b(?:by\s+)?(?:eow|end\s+of\s+(?:the\s+)?week)\b", low):
        # Friday of current week (or next if already Friday/weekend)
        days_ahead = (4 - meeting_date.weekday()) % 7
        if days_ahead == 0 and meeting_date.weekday() == 4:
            # If said on Friday, end of week is today or next Friday
            days_ahead = 0
        elif days_ahead == 0:
            days_ahead = 7
        return (meeting_date + timedelta(days=days_ahead)).isoformat(), "relative", 0.80

    # end of month
    if re.search(r"\b(?:by\s+)?(?:the\s+)?end\s+of\s+(?:the\s+)?month\b", low):
        return _last_day_of_month(meeting_date).isoformat(), "relative", 0.80

    # next week
    if re.search(r"\b(?:by\s+)?next\s+week\b", low):
        return (meeting_date + timedelta(days=7)).isoformat(), "relative", 0.80

    # in N days / in two days
    m_days = re.search(r"\b(?:within|in)\s+(\w+)\s+days?\b", low)
    if m_days:
        num_str = m_days.group(1)
        num = int(num_str) if num_str.isdigit() else WORD_NUMBERS.get(num_str)
        if num:
            return (meeting_date + timedelta(days=num)).isoformat(), "relative", 0.80

    # in N weeks
    m_weeks = re.search(r"\b(?:within|in)\s+(\w+)\s+weeks?\b", low)
    if m_weeks:
        num_str = m_weeks.group(1)
        num = int(num_str) if num_str.isdigit() else WORD_NUMBERS.get(num_str)
        if num:
            return (meeting_date + timedelta(weeks=num)).isoformat(), "relative", 0.80

    # -------------------------------------------------------------
    # 5. RELATIVE WEEKDAY (e.g. "Friday", "next Monday", "by Tuesday")
    # -------------------------------------------------------------
    for name, wd in ENGLISH_WEEKDAYS.items():
        # Word boundary match for the weekday name
        pattern = rf"\b(?:by|on|this|next)?\s*{name}\b"
        if re.search(pattern, low):
            days_ahead = (wd - meeting_date.weekday()) % 7
            if days_ahead == 0:
                # Strictly after meeting_date
                days_ahead = 7
            target_date = meeting_date + timedelta(days=days_ahead)
            return target_date.isoformat(), "relative", 0.85

    # -------------------------------------------------------------
    # 6. ABSOLUTE DATE (e.g. "12 September", "12/09", "Sept 12", "2026-09-12")
    # -------------------------------------------------------------
    clean_date_str = re.sub(r"^(?:by|on|before|due|until)\s+", "", low).strip()
    try:
        slash_date = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", clean_date_str)
        if slash_date:
            day, month, year = slash_date.groups()
            parsed = date_parser.parse(
                f"{day}/{month}/{year or meeting_date.year}",
                dayfirst=True,
            )
        else:
            parsed = date_parser.parse(
                clean_date_str,
                default=datetime(meeting_date.year, meeting_date.month, meeting_date.day),
                dayfirst=False,
            )
        res_date = parsed.date()

        # If only month/day was given and parsed date is earlier in the year than meeting_date,
        # roll forward to next year
        if "%Y" not in clean_date_str and str(meeting_date.year) not in clean_date_str:
            if res_date < meeting_date and (meeting_date - res_date).days > 180:
                res_date = res_date.replace(year=meeting_date.year + 1)

        return res_date.isoformat(), "absolute", 0.95
    except Exception:
        pass

    # -------------------------------------------------------------
    # 7. UNPARSEABLE
    # -------------------------------------------------------------
    return None, "none", 0.0
