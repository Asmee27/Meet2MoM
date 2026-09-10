"""
tests/test_context_builder.py
=============================
Tests for app/services/intelligence/context_builder.py:
- Windows cover every utterance at least once (union of utt_ids == all utt_ids)
- Consecutive windows share exactly overlap_utterances ids
- text_block lines match regex ^\\[u\\d{5}\\] \\S+ \\(\\d+:\\d{2}\\)
- Deterministic: two calls return identical window_ids and utt_ids
"""
from __future__ import annotations

import re
from uuid import uuid4
import pytest

from app.core import db as _db
from app.schemas.utterance import Utterance
from app.services.intelligence.context_builder import (
    ExtractionWindow,
    build_windows,
    window_utt_id_set,
)


@pytest.fixture(autouse=True)
def setup_database():
    _db.init_db()


def _populate_utterances(meeting_id: str, count: int = 125) -> list[Utterance]:
    _db.insert_meeting(meeting_id, filename="test.mp4")
    utts = []
    for i in range(count):
        uid = f"u{i:05d}"
        spk = f"SPK_{i % 3:02d}"
        name = "Priya" if i % 2 == 0 else None
        start = float(i * 10)  # every 10 seconds
        end = start + 8.0
        text = f"Utterance number {i} discussion content."
        utts.append(
            Utterance(
                meeting_id=meeting_id,
                utt_id=uid,
                start=start,
                end=end,
                speaker_id=spk,
                speaker_name=name,
                text=text,
                asr_conf=0.92,
                spk_conf=0.90,
                conf_source="numeric",
            )
        )
    _db.bulk_insert_utterances(meeting_id, utts)
    return utts


def test_windows_cover_every_utterance():
    mid = uuid4().hex
    utts = _populate_utterances(mid, count=125)
    all_expected_ids = {u.utt_id for u in utts}

    windows = build_windows(mid, max_utterances=50, overlap_utterances=10)
    assert len(windows) >= 3

    union_ids = set()
    for w in windows:
        assert isinstance(w, ExtractionWindow)
        union_ids.update(w.utt_ids)
        assert union_ids.issubset(all_expected_ids)
        assert window_utt_id_set(w) == set(w.utt_ids)

    assert union_ids == all_expected_ids, "Windows must cover every utterance at least once"


def test_consecutive_windows_share_exact_overlap():
    mid = uuid4().hex
    _populate_utterances(mid, count=130)

    overlap = 15
    windows = build_windows(mid, max_utterances=60, overlap_utterances=overlap)
    assert len(windows) >= 3

    for i in range(len(windows) - 1):
        w1_ids = set(windows[i].utt_ids)
        w2_ids = set(windows[i + 1].utt_ids)
        shared = w1_ids.intersection(w2_ids)
        assert len(shared) == overlap, f"Expected {overlap} shared ids between window {i} and {i+1}, got {len(shared)}"


def test_text_block_lines_match_regex():
    mid = uuid4().hex
    _populate_utterances(mid, count=10)

    windows = build_windows(mid, max_utterances=10, overlap_utterances=2)
    assert len(windows) == 1

    pattern = re.compile(r"^\[u\d{5}\] \S+ \(\d+:\d{2}\)")
    lines = windows[0].text_block.strip().split("\n")
    assert len(lines) == 10

    for line in lines:
        assert pattern.match(line), f"Line did not match pattern: '{line}'"


def test_deterministic_calls():
    mid = uuid4().hex
    _populate_utterances(mid, count=80)

    windows_1 = build_windows(mid, max_utterances=40, overlap_utterances=10)
    windows_2 = build_windows(mid, max_utterances=40, overlap_utterances=10)

    assert len(windows_1) == len(windows_2)
    for w1, w2 in zip(windows_1, windows_2):
        assert w1.window_id == w2.window_id
        assert w1.utt_ids == w2.utt_ids
        assert w1.text_block == w2.text_block
        assert w1.start_time == w2.start_time
        assert w1.end_time == w2.end_time
