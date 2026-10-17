"""Builders for synthetic meetings used by the Phase 1 verification tests."""
from __future__ import annotations

import json
from uuid import uuid4

from app.core import db
from app.core.storage import MeetingStorage
from app.schemas.claim import ActionItem, Decision
from app.schemas.utterance import Utterance


def make_meeting(lines, *, asr=0.9, spk=0.9, filename="synthetic-scenario.wav") -> str:
    """
    lines: [(speaker, text)] or [(speaker, text, {"asr":..,"spk":..,"overlap":..})].
    Utterances are 5 s apart and get ids u00000, u00001, ...
    """
    meeting_id = f"scn-{uuid4().hex[:12]}"
    db.insert_meeting(meeting_id, filename=filename)
    utterances = []
    for index, line in enumerate(lines):
        speaker, text = line[0], line[1]
        opts = line[2] if len(line) > 2 else {}
        asr_conf = opts.get("asr", asr)
        utterances.append(
            Utterance(
                meeting_id=meeting_id,
                utt_id=f"u{index:05d}",
                start=index * 5.0,
                end=index * 5.0 + 4.0,
                speaker_id=speaker,
                text=text,
                asr_conf=asr_conf,
                spk_conf=opts.get("spk", spk),
                overlap=opts.get("overlap", False),
                conf_source="numeric" if asr_conf is not None else "missing",
            )
        )
    db.bulk_insert_utterances(meeting_id, utterances)
    return meeting_id


def write_single_topic(meeting_id: str) -> None:
    """One topic spanning the meeting, so the rule extractor sees every sentence."""
    utterances = db.get_utterances(meeting_id)
    storage = MeetingStorage(meeting_id)
    storage.ensure_dirs()
    storage.path_for("intelligence", "topics.json").write_text(
        json.dumps({
            "topics": [{
                "topic_id": "topic_1",
                "topic_label": "Scenario",
                "start": utterances[0].start,
                "end": utterances[-1].end,
                "text": " ".join(u.text for u in utterances),
                "speakers": sorted({u.speaker_id for u in utterances}),
                "segment_count": len(utterances),
            }]
        }),
        encoding="utf-8",
    )
    storage.path_for("transcript", "frontend_transcript.json").write_text(
        json.dumps({
            "language": "en",
            "segments": [
                {"start": u.start, "end": u.end, "speaker": u.speaker_id, "text": u.text,
                 "speaker_attribution_confidence": u.spk_conf}
                for u in utterances
            ],
        }),
        encoding="utf-8",
    )


def add_decision(meeting_id, n, text, evidence, state="accepted", **fields) -> str:
    claim_id = f"{meeting_id}_d{n:05d}"
    db.insert_claim_with_evidence(
        Decision(claim_id=claim_id, meeting_id=meeting_id, text=text, evidence=evidence, state=state, **fields)
    )
    return claim_id


def add_action(meeting_id, n, text, evidence, owner=None, owner_type="unassigned", **fields) -> str:
    claim_id = f"{meeting_id}_a{n:05d}"
    db.insert_claim_with_evidence(
        ActionItem(
            claim_id=claim_id, meeting_id=meeting_id, text=text, evidence=evidence,
            owner=owner, owner_type=owner_type, **fields,
        )
    )
    return claim_id


def record_extraction(meeting_id: str) -> None:
    """Directly inserted claims still need an extraction run for the API/MoM."""
    db.upsert_extraction_run(
        meeting_id, status="completed", mode_requested="rules", mode_used="rules",
        accepted=len(db.get_claims(meeting_id)), dropped=0, extractor_version="test-fixture",
    )


def reason_codes(claim: dict) -> set[str]:
    return {r["code"] for r in claim["verification"]["final"]["reasons"]}


def final_label(claim: dict) -> str:
    return claim["verification"]["final"]["label"]


FILLER = [
    ("SPEAKER_03", "The coffee machine on the third floor is broken again."),
    ("SPEAKER_02", "Traffic was terrible this morning because of the rain."),
    ("SPEAKER_03", "Has anyone seen the blue marker for the whiteboard?"),
    ("SPEAKER_02", "The quarterly newsletter came out yesterday afternoon."),
]
