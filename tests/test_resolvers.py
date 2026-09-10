from __future__ import annotations

"""
tests/test_resolvers.py
=======================
Tests for deterministic deadline and owner resolution.
"""

from datetime import date
import pytest

from app.schemas.claim import ActionItem
from app.schemas.utterance import Utterance
from app.services.intelligence.deadline_resolver import resolve_deadline
from app.services.intelligence.owner_resolver import resolve_owner


def test_deadline_table_multi_language():
    # 2026-09-09 is a Wednesday
    meeting_date = date(2026, 9, 9)

    test_cases = [
        # Absolute formats
        ("2026-09-12", ("2026-09-12", "absolute")),
        ("12 September", ("2026-09-12", "absolute")),
        ("Sept 12", ("2026-09-12", "absolute")),
        ("by 2026-09-20", ("2026-09-20", "absolute")),

        # Relative offsets (English)
        ("tomorrow", ("2026-09-10", "relative")),
        ("in two days", ("2026-09-11", "relative")),
        ("next week", ("2026-09-16", "relative")),
        ("EOD", ("2026-09-09", "relative")),
        ("end of month", ("2026-09-30", "relative")),

        # Weekdays (Wednesday + weekday)
        ("Friday", ("2026-09-11", "relative")),
        ("next Monday", ("2026-09-14", "relative")),
        ("by Tuesday", ("2026-09-15", "relative")),

        # Hindi & Marathi markers
        ("kal", ("2026-09-10", "relative")),
        ("parso", ("2026-09-11", "relative")),
        ("agle hafte", ("2026-09-16", "relative")),
        ("उद्या", ("2026-09-10", "relative")),
        ("पुढच्या आठवड्यात", ("2026-09-16", "relative")),
        ("शुक्रवार", ("2026-09-11", "relative")),
        ("सोमवार", ("2026-09-14", "relative")),

        # Event anchored & recurring
        ("before release", (None, "event_anchored")),
        ("after the client approves", (None, "event_anchored")),
        ("post demo", (None, "event_anchored")),
        ("every Monday", (None, "recurring")),
        ("weekly", (None, "recurring")),

        # Empty / unparseable
        (None, (None, "none")),
        ("xyz gibberish", (None, "none")),
    ]

    assert len(test_cases) >= 15

    for phrase, (expected_iso, expected_type) in test_cases:
        res_iso, res_type, conf = resolve_deadline(phrase, meeting_date)
        assert res_iso == expected_iso, f"Failed ISO for '{phrase}': got {res_iso}, expected {expected_iso}"
        assert res_type == expected_type, f"Failed type for '{phrase}': got {res_type}, expected {expected_type}"
        if expected_type != "none":
            assert conf > 0.0


def test_wednesday_meeting_plus_friday():
    # Wednesday 2026-09-09 + "Friday" -> same week's Friday (2026-09-11), not last week's
    meeting_date = date(2026, 9, 9)
    res_iso, res_type, conf = resolve_deadline("Friday", meeting_date)
    assert res_iso == "2026-09-11"
    assert res_type == "relative"
    assert conf == 0.85


def test_event_anchored_deadline():
    meeting_date = date(2026, 9, 9)
    res_iso, res_type, conf = resolve_deadline("before release", meeting_date)
    assert res_iso is None
    assert res_type == "event_anchored"
    assert conf == 0.50


def test_first_person_owner_uses_diarization_speaker():
    action = ActionItem(
        claim_id="act_01",
        meeting_id="m001",
        text="I will configure the database",
        evidence=["u00000"],
        owner="Hallucinated Name By LLM",
        owner_type="explicit_first_person",
    )

    utts = [
        Utterance(
            meeting_id="m001",
            utt_id="u00000",
            start=0.0,
            end=2.0,
            speaker_id="SPEAKER_01",
            speaker_name="Real Speaker Alice",
            text="I will configure the database server tomorrow.",
            spk_conf=0.91,
        )
    ]

    owner, owner_type, conf = resolve_owner(action, utts)
    # Must use diarization speaker, NOT the LLM's hallucinated name
    assert owner == "Real Speaker Alice"
    assert owner_type == "explicit_first_person"
    assert conf == 0.91


def test_third_person_owner_not_matching_speaker_downgraded():
    action = ActionItem(
        claim_id="act_02",
        meeting_id="m001",
        text="Zack will prepare the slides",
        evidence=["u00000"],
        owner="Zack",
        owner_type="explicit_third_person",
    )

    utts = [
        Utterance(
            meeting_id="m001",
            utt_id="u00000",
            start=0.0,
            end=2.0,
            speaker_id="SPEAKER_00",
            speaker_name="Alice",
            text="Someone needs to prepare the slides.",
            spk_conf=0.90,
        )
    ]

    owner, owner_type, conf = resolve_owner(action, utts)
    # Zack is not in evidence text and not among speakers -> downgraded to contextual
    assert owner == "Zack"
    assert owner_type == "contextual"
    assert conf == pytest.approx(0.425, abs=1e-3)


def test_group_unassigned_owner():
    action = ActionItem(
        claim_id="act_03",
        meeting_id="m001",
        text="We need to review the docs",
        evidence=["u00000"],
        owner="Everyone",
        owner_type="group",
    )

    owner, owner_type, conf = resolve_owner(action, [])
    assert owner is None
    assert owner_type == "group"
    assert conf == 1.0
