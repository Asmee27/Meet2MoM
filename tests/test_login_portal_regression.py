"""
Phase 8.2 regression: the "login portal" demo meeting (a2095015…).

Real transcript (LiveKit tracks, attribution confidence 1.0):
  u00000 Samarth: We have decided to launch the new login portal next Monday.
  u00001 Asmee:   I agree with the decision. I will complete the front-end
                  validation and submit the bug report by Friday.
  u00002 Samarth: Perfect.
  u00003 Samarth: I will finalize the backend API, deployment and email the
                  access links back on to you.      <- ASR; the audio is unclear
                                                       there ("by title" in a
                                                       re-decode), not "Thursday"

Two defects were reported:
- Asmee's action was held with "contradiction 0.97". The real NLI model scored
  Samarth's unrelated task (u00002-u00003) as contradicting Asmee's task. The
  contradiction came only from a retrieved, off-topic utterance.
- Samarth's action had no deadline. The transcript does not contain one, so
  none may be invented; a reviewer corrects the transcript against the audio.

The fake NLI used here cannot produce an off-topic contradiction, so the one
real-model score that caused the defect is replayed (recorded from
mDeBERTa-v3-base-mnli-xnli on this meeting: contradiction 0.968).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.core import db
from app.core.llm_client import LLMClient
from app.schemas.utterance import Utterance
from app.services.intelligence.deadline_resolver import resolve_deadline
from app.services.intelligence.extraction_pipeline import run_extraction
from app.services.verification import nli_scorer
from app.services.verification.claim_verification import verify_meeting
from app.services.verification.claims_view import build_claims_view
from tests.scenario_helpers import write_single_topic
from tests.semantic_fakes import install_semantic_fakes

HOST, GUEST = ("u_host", "Samarth"), ("u_guest", "Asmee")
LINES = [
    (HOST, 2.6, 7.0, "We have decided to launch the new login portal next Monday."),
    (GUEST, 17.5, 24.3, "I agree with the decision. I will complete the front-end validation and submit the bug report by Friday."),
    (HOST, 30.5, 30.9, "Perfect."),
    (HOST, 31.2, 36.6, "I will finalize the backend API, deployment and email the access links back on to you."),
]
# What Gemini returned for this meeting (claims table of a2095015…).
GEMINI = {
    "decisions": [{"text": "Launch the new login portal next Monday", "state": "accepted",
                   "proposer": "Samarth", "evidence": ["u00000", "u00001"]}],
    "actions": [
        {"text": "Complete the front-end validation and submit the bug report", "owner": "Asmee",
         "owner_type": "explicit_first_person", "deadline_text": "by Friday", "evidence": ["u00001"]},
        {"text": "Finalize the backend API, deployment and email the access links back", "owner": "Samarth",
         "owner_type": "explicit_first_person", "deadline_text": None, "evidence": ["u00003"]},
    ],
}


def _meeting(lines=LINES):
    mid = f"portal-{uuid4().hex[:10]}"
    db.insert_meeting(mid, filename="live-portal.wav")
    db.bulk_insert_utterances(mid, [
        Utterance(meeting_id=mid, utt_id=f"u{i:05d}", start=start, end=end, speaker_id=spk[0],
                  speaker_name=spk[1], text=text, asr_conf=0.9, spk_conf=1.0, overlap=False,
                  conf_source="numeric")
        for i, (spk, start, end, text) in enumerate(lines)
    ])
    write_single_topic(mid)
    return mid


def _extract(mid, response):
    client = LLMClient(provider="stub", model="gemini-test", backoff_delays=[0.01])
    client.set_stub_response(response)
    stats = run_extraction(mid, mode="llm", client=client)
    assert stats["mode_used"] == "llm", stats
    return {c["claim_id"][-6:]: c for c in build_claims_view(mid)["claims"]}


def _replay_real_off_topic_contradiction(pipe):
    """The real model's score for Samarth's task vs Asmee's task (0.968)."""
    fake_score = pipe._score

    def score(premise, hypothesis):
        if "finalize the backend" in premise.lower() and "front-end validation" in hypothesis.lower() \
                and "front-end validation" not in premise.lower():
            return [{"label": "entailment", "score": 0.01}, {"label": "neutral", "score": 0.022},
                    {"label": "contradiction", "score": 0.968}]
        return fake_score(premise, hypothesis)

    pipe._score = score


def _by_text(claims, fragment):
    return next(c for c in claims.values() if fragment in c["text"])


# ---------------------------------------------------------------------------
# 1-8: extraction, owners, deadlines, verification
# ---------------------------------------------------------------------------

def test_the_reported_meeting_extracts_one_decision_and_two_owned_actions(monkeypatch):
    pipe = install_semantic_fakes(monkeypatch)
    _replay_real_off_topic_contradiction(pipe)
    mid = _meeting()
    claims = _extract(mid, GEMINI)
    verify_meeting(mid)
    claims = {c["claim_id"][-6:]: c for c in build_claims_view(mid)["claims"]}

    decision = _by_text(claims, "login portal")
    assert decision["claim_type"] == "decision" and decision["verification"]["final"]["label"] == "verified"
    assert "u00000" in decision["evidence_ids"]

    guest = _by_text(claims, "front-end validation")
    assert (guest["owner"], guest["owner_type"]) == ("Asmee", "explicit_first_person")
    assert guest["evidence"][0]["speaker"] == "Asmee"
    meeting_day = datetime.fromisoformat(db.get_meeting(mid)["created_at"]).date()
    assert guest["deadline_resolved"] == resolve_deadline("by Friday", meeting_day)[0]
    assert date.fromisoformat(guest["deadline_resolved"]).weekday() == 4          # a Friday

    host = _by_text(claims, "backend API")
    assert (host["owner"], host["evidence"][0]["speaker"]) == ("Samarth", "Samarth")
    assert host["deadline_text"] is None and host["deadline_resolved"] is None     # not in the transcript


def test_an_off_topic_retrieved_utterance_no_longer_holds_the_action(monkeypatch):
    pipe = install_semantic_fakes(monkeypatch)
    _replay_real_off_topic_contradiction(pipe)
    mid = _meeting()
    _extract(mid, GEMINI)
    verify_meeting(mid)
    guest = _by_text({c["claim_id"]: c for c in build_claims_view(mid)["claims"]}, "front-end validation")

    assert guest["verification"]["final"]["label"] in ("verified", "verified_with_warning")
    assert guest["exportable"] is True
    codes = {r["code"] for r in guest["verification"]["final"]["reasons"]}
    assert "contradicting_evidence_elsewhere" not in codes
    details = guest["verification"]["semantic"]["details"]
    assert details["x_off_topic"] == pytest.approx(0.968)                          # still recorded
    assert "u00003" in details["x_off_topic_utt_ids"]


def test_the_original_behaviour_is_reproduced_without_the_topic_gate(monkeypatch):
    pipe = install_semantic_fakes(monkeypatch)
    _replay_real_off_topic_contradiction(pipe)
    monkeypatch.setattr(nli_scorer, "CONTRADICTION_TOPIC_MIN", 0.0)
    mid = _meeting()
    _extract(mid, GEMINI)
    verify_meeting(mid)
    guest = _by_text({c["claim_id"]: c for c in build_claims_view(mid)["claims"]}, "front-end validation")
    assert guest["verification"]["final"]["label"] == "review"
    assert "contradicting_evidence_elsewhere" in {r["code"] for r in guest["verification"]["final"]["reasons"]}


def test_a_genuine_reversal_of_the_action_is_still_held(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = _meeting(LINES + [
        (GUEST, 40.0, 44.0, "Actually I will not complete the front-end validation, drop the bug report for now."),
    ])
    _extract(mid, GEMINI)
    verify_meeting(mid)
    guest = _by_text({c["claim_id"]: c for c in build_claims_view(mid)["claims"]}, "front-end validation")
    assert guest["verification"]["final"]["label"] == "review"
    assert "contradicting_evidence_elsewhere" in {r["code"] for r in guest["verification"]["final"]["reasons"]}
    assert "u00004" in guest["verification"]["semantic"]["contradiction_utt_ids"]


def test_agreeing_with_the_decision_does_not_contradict_it(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = _meeting()
    _extract(mid, GEMINI)
    verify_meeting(mid)
    decision = _by_text({c["claim_id"]: c for c in build_claims_view(mid)["claims"]}, "login portal")
    assert decision["verification"]["final"]["label"] == "verified"
    assert not {r["code"] for r in decision["verification"]["final"]["reasons"]} & {
        "contradicting_evidence_elsewhere", "cited_evidence_partially_contradicts"}


def test_a_missing_deadline_stays_explicit_without_rejecting_the_task(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = _meeting()
    _extract(mid, GEMINI)
    verify_meeting(mid)
    host = _by_text({c["claim_id"]: c for c in build_claims_view(mid)["claims"]}, "backend API")
    assert host["verification"]["final"]["label"] == "verified_with_warning" and host["exportable"]
    assert "no_deadline" in {r["code"] for r in host["verification"]["final"]["reasons"]}


def test_a_deadline_the_llm_adds_without_transcript_support_is_removed(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = _meeting()
    invented = {**GEMINI, "actions": [
        GEMINI["actions"][0],
        # "by Thursday" is not in u00003, and "next Monday" belongs to the decision.
        {**GEMINI["actions"][1], "deadline_text": "by Thursday"},
    ]}
    host = _by_text(_extract(mid, invented), "backend API")
    assert host["deadline_text"] is None and host["deadline_resolved"] is None

    mid2 = _meeting()
    unrelated = {**GEMINI, "actions": [GEMINI["actions"][0], {**GEMINI["actions"][1], "deadline_text": "next Monday"}]}
    assert _by_text(_extract(mid2, unrelated), "backend API")["deadline_text"] is None


def test_a_spoken_weekday_deadline_is_kept_and_resolved(monkeypatch):
    install_semantic_fakes(monkeypatch)
    lines = LINES[:3] + [(HOST, 31.2, 36.6,
                          "I will finalize the backend API deployment and email the access links by Thursday.")]
    mid = _meeting(lines)
    said = {**GEMINI, "actions": [GEMINI["actions"][0], {**GEMINI["actions"][1], "deadline_text": "by Thursday"}]}
    host = _by_text(_extract(mid, said), "backend API")
    meeting_day = datetime.fromisoformat(db.get_meeting(mid)["created_at"]).date()
    assert host["deadline_text"] == "by Thursday"
    assert host["deadline_resolved"] == resolve_deadline("by Thursday", meeting_day)[0]
    assert date.fromisoformat(host["deadline_resolved"]).weekday() == 3


@pytest.mark.parametrize("text,expected_weekday", [("by Friday", 4), ("by Thursday", 3), ("next Monday", 0)])
def test_weekday_deadlines_resolve_to_the_next_such_day(text, expected_weekday):
    saturday = date(2026, 10, 24)
    resolved, kind, _ = resolve_deadline(text, saturday)
    resolved = date.fromisoformat(resolved)
    assert kind == "relative" and resolved.weekday() == expected_weekday
    assert saturday < resolved <= saturday + timedelta(days=7)


def test_no_deadline_resolves_to_nothing():
    assert resolve_deadline(None, date(2026, 10, 24)) == (None, "none", 0.0)
    assert resolve_deadline("back on to you", date(2026, 10, 24))[0] is None


# ---------------------------------------------------------------------------
# 9-10: consistency across review, minutes and exports; correction safeguards
# ---------------------------------------------------------------------------

def test_review_minutes_and_exports_agree(monkeypatch):
    pipe = install_semantic_fakes(monkeypatch)
    _replay_real_off_topic_contradiction(pipe)
    mid = _meeting()
    _extract(mid, GEMINI)
    client = TestClient(app)
    assert client.post(f"/api/verify/{mid}").status_code == 200
    review = client.get(f"/api/meetings/{mid}/review").json()
    mom = client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Rita"}).json()["mom"]

    assert len(mom["decisions"]) == 1 and len(mom["action_items"]) == 2 and mom["needs_review"] == []
    assert mom["verification"]["held_for_review"] == review["counts"]["needs_review"] == 0
    assert "2 action items were confirmed" in mom["summary"]
    owners = {a["task"]: (a["owner"], a["dueDate"]) for a in mom["action_items"]}
    assert owners["Finalize the backend API, deployment and email the access links back"] == ("Samarth", "Not specified")
    exported = client.get(f"/api/meetings/{mid}/export/json").json()
    assert exported["action_items"] == mom["action_items"] and exported["decisions"] == mom["decisions"]


def test_correcting_the_deadline_against_the_audio_goes_through_review(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = _meeting()
    _extract(mid, GEMINI)
    client = TestClient(app)
    client.post(f"/api/verify/{mid}")

    # A reviewer listens at 0:35 and corrects the words; nothing is replaced automatically.
    fixed = "I will finalize the backend API, deployment and email the access links by Thursday."
    assert client.put(f"/api/meetings/{mid}/transcript/u00003",
                      json={"text": fixed, "reason": "Checked against the audio at 0:35"}).status_code == 200
    assert client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Rita"}).status_code == 409   # stale
    assert client.post(f"/api/verify/{mid}").status_code == 200

    host_item = next(i for i in client.get(f"/api/meetings/{mid}/review").json()["items"] if "backend API" in i["text"])
    assert host_item["deadline_text"] is None                                  # claims are not re-extracted
    assert client.put(f"/api/meetings/{mid}/review/items/{host_item['item_id']}",
                      json={"deadline_text": "by Thursday", "state": "accepted"}).status_code == 200
    mom = client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Rita"}).json()["mom"]
    action = next(a for a in mom["action_items"] if "backend API" in a["task"])
    assert action["dueDate"] == "by Thursday" and "No deadline was stated." not in action["warnings"]
