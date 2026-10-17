"""
Golden regression fixture: demo_01 (Phase 0).

The fixture files in tests/fixtures/golden/demo_01/ are byte-copies of the
outputs the pipeline produced for storage/demo_01 on 2026-09-10 (provenance
and checksums in tests/fixtures/golden/demo_01/README.md).

These tests pin CURRENT behaviour of the deterministic stages so later phases
can detect changes. They are NOT ground truth: decision 1 cites u00030, which
the roadmap identifies as a likely mis-citation, and the recording contains
action items that the rule extractor misses.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
from pathlib import Path
from uuid import uuid4

from app.core import db
from app.core.storage import MeetingStorage
from app.services.intelligence.action_item_extractor import ActionItemExtractor
from app.services.intelligence.decision_extractor import DecisionExtractor
from app.services.intelligence.extraction_pipeline import run_extraction
from app.services.transcript.transcript_index import build_transcript_index, persist_transcript_index
from app.services.verification.evidence_verifier import EvidenceVerifier

FIXTURE = Path(__file__).parent / "fixtures" / "golden" / "demo_01"


def _load(name: str):
    return json.loads((FIXTURE / name).read_text(encoding="utf-8"))


def _quiet(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args)


def test_transcript_index_matches_golden():
    utterances = build_transcript_index(_load("meeting_transcript_ready.json"), "demo_01")
    expected = _load("transcript_index.json")
    expected = expected.get("utterances", expected) if isinstance(expected, dict) else expected

    def key(u):
        return (u["utt_id"], u["speaker_id"], round(u["start"], 2), round(u["end"], 2), u["text"])

    assert len(utterances) == 39
    assert len({u.speaker_id for u in utterances}) == 5
    assert [key(u.model_dump()) for u in utterances] == [key(u) for u in expected]


def test_rule_decisions_and_actions_match_golden(tmp_path):
    decisions_path = _quiet(
        DecisionExtractor(output_dir=str(tmp_path)).process, str(FIXTURE / "topics.json")
    )
    actions_path = _quiet(
        ActionItemExtractor(output_dir=str(tmp_path)).process,
        str(FIXTURE / "frontend_transcript.json"),
        str(FIXTURE / "topics.json"),
    )
    decisions = json.loads(Path(decisions_path).read_text(encoding="utf-8"))
    actions = json.loads(Path(actions_path).read_text(encoding="utf-8"))

    assert [d["text"] for d in decisions["final_decisions"]] == [
        d["text"] for d in _load("decisions.json")["final_decisions"]
    ]
    assert actions["action_items"] == _load("action_items.json")["action_items"] == []


def test_evidence_verifier_statuses_match_golden(tmp_path):
    output = _quiet(
        EvidenceVerifier(output_dir=str(tmp_path)).process,
        str(FIXTURE / "frontend_transcript.json"),
        str(FIXTURE / "decisions.json"),
        str(FIXTURE / "action_items.json"),
    )
    actual = json.loads(Path(output).read_text(encoding="utf-8"))

    def summary(data):
        return [
            (d["text"], d["verification_status"], d["proposal_speaker"], d["proposal_timestamp"])
            for d in data["verified_decisions"]
        ]

    assert summary(actual) == summary(_load("verified_intelligence.json"))
    assert [s for _, s, _, _ in summary(actual)] == ["verified_with_warning", "review"]


# Phase 1 deliberately changed rules-mode claims (rules-v2): decisions now cite
# the full proposal + confirmation span instead of one utterance picked by an
# interpolated timestamp, and unconfirmed proposals are kept as "proposed"
# instead of being dropped. The frozen claims.json (rules-v1) is kept unchanged
# as the input for the u00030 mis-citation check (Phase 1 Gate C).
RULES_V2_CLAIMS = [
    ("decision", "accepted", "Start with a review of the current training approach and look at what we "
     "need to do to address the problems brought up in the survey.", ["u00030", "u00031", "u00032"]),
    ("decision", "proposed", "We could do that.", ["u00032"]),
    ("decision", "accepted", "Look at the report together.", ["u00034", "u00035"]),
    ("decision", "proposed", "Perhaps we could start with the highlights first.", ["u00037", "u00038"]),
]


def test_rules_v1_snapshot_is_unchanged_reference_for_gate_c():
    v1 = [(c["claim_type"], c["text"], c["evidence"]) for c in _load("claims.json")["claims"]]
    assert [evidence for _, _, evidence in v1] == [["u00030"], ["u00033"]]


def test_rules_mode_claims_match_golden_with_grounded_evidence():
    meeting_id = f"golden-{uuid4().hex}"
    db.insert_meeting(meeting_id, filename="demo_01.wav")
    _quiet(
        persist_transcript_index,
        build_transcript_index(_load("meeting_transcript_ready.json"), meeting_id),
        meeting_id,
    )
    storage = MeetingStorage(meeting_id)
    storage.ensure_dirs()
    shutil.copy(FIXTURE / "topics.json", storage.path_for("intelligence", "topics.json"))
    shutil.copy(FIXTURE / "frontend_transcript.json", storage.path_for("transcript", "frontend_transcript.json"))

    stats = _quiet(run_extraction, meeting_id, "rules")

    actual = [
        (c["claim_type"], c["payload"]["state"], c["text"], c["evidence"])
        for c in db.get_claims(meeting_id)
    ]
    assert actual == RULES_V2_CLAIMS
    assert stats["accepted"] == 4 and stats["dropped"] == 0
    # Same decision texts as the v1 snapshot; only citations and kept proposals differ.
    v1_texts = [c["text"] for c in _load("claims.json")["claims"]]
    assert [text for _, state, text, _ in actual if state == "accepted"] == v1_texts
