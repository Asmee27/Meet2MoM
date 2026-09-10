from __future__ import annotations

"""
context_builder.py
==================
Builds windowed, utt_id-tagged extraction contexts from the database-backed
transcript index. Ensures every piece of text seen by downstream extraction
is strictly tagged with utterance identifiers for citation and verification.
"""

from dataclasses import dataclass
from typing import List, Optional, Set
import re

from app.core import db as _db
from app.schemas.utterance import Utterance


@dataclass
class ExtractionWindow:
    window_id: str          # e.g. "w000"
    meeting_id: str
    utt_ids: list[str]      # ordered
    start_time: float
    end_time: float
    text_block: str
    topic_id: str | None


def window_utt_id_set(window: ExtractionWindow) -> Set[str]:
    """Return the set of utterance IDs contained in this window."""
    return set(window.utt_ids)


def _format_time(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"({m}:{s:02d})"


def _format_utterance_line(u: Utterance) -> str:
    """
    Format an utterance as:
      [u00042] SPK_01 (12:03) Priya: we should move deployment to Friday
    If speaker_name is absent:
      [u00042] SPK_01 (12:03) we should move deployment to Friday
    """
    t_str = _format_time(u.start)
    if u.speaker_name and u.speaker_name.strip():
        return f"[{u.utt_id}] {u.speaker_id} {t_str} {u.speaker_name.strip()}: {u.text.strip()}"
    return f"[{u.utt_id}] {u.speaker_id} {t_str} {u.text.strip()}"


def _create_window(
    window_index: int,
    meeting_id: str,
    utts: List[Utterance],
    topic_id: Optional[str] = None,
) -> ExtractionWindow:
    wid = f"w{window_index:03d}"
    utt_ids = [u.utt_id for u in utts]
    start_time = utts[0].start
    end_time = utts[-1].end
    lines = [_format_utterance_line(u) for u in utts]
    text_block = "\n".join(lines)
    return ExtractionWindow(
        window_id=wid,
        meeting_id=meeting_id,
        utt_ids=utt_ids,
        start_time=start_time,
        end_time=end_time,
        text_block=text_block,
        topic_id=topic_id,
    )


def _slice_with_overlap(
    utts: List[Utterance],
    max_utterances: int,
    overlap_utterances: int,
) -> List[List[Utterance]]:
    if not utts:
        return []
    if len(utts) <= max_utterances:
        return [utts]

    step = max(1, max_utterances - overlap_utterances)
    slices = []
    i = 0
    while i < len(utts):
        chunk = utts[i : i + max_utterances]
        slices.append(chunk)
        if i + max_utterances >= len(utts):
            break
        i += step
    return slices


def build_windows(
    meeting_id: str,
    max_utterances: int = 60,
    overlap_utterances: int = 10,
    topics: Optional[List[dict]] = None,
) -> List[ExtractionWindow]:
    """
    Build windowed extraction contexts from utterances in the database.
    """
    all_utts = _db.get_utterances(meeting_id)
    if not all_utts:
        return []

    windows: List[ExtractionWindow] = []
    w_idx = 0

    if not topics:
        # Fixed-size sliding windows with overlap
        chunks = _slice_with_overlap(all_utts, max_utterances, overlap_utterances)
        for chunk in chunks:
            windows.append(_create_window(w_idx, meeting_id, chunk, topic_id=None))
            w_idx += 1
        return windows

    # If topics are supplied, prefer topic boundaries
    for topic in topics:
        tid = str(topic.get("topic_id")) if topic.get("topic_id") is not None else None
        t_start = float(topic.get("start", 0.0))
        t_end = float(topic.get("end", float("inf")))

        # Filter utterances belonging to this topic
        topic_utts = [
            u for u in all_utts
            if (u.topic_id is not None and str(u.topic_id) == tid)
            or (u.topic_id is None and t_start <= u.start <= t_end)
        ]

        if not topic_utts:
            continue

        chunks = _slice_with_overlap(topic_utts, max_utterances, overlap_utterances)
        for chunk in chunks:
            windows.append(_create_window(w_idx, meeting_id, chunk, topic_id=tid))
            w_idx += 1

    if not windows:
        # Fallback if topics matched zero utterances
        chunks = _slice_with_overlap(all_utts, max_utterances, overlap_utterances)
        for chunk in chunks:
            windows.append(_create_window(w_idx, meeting_id, chunk, topic_id=None))
            w_idx += 1

    return windows
