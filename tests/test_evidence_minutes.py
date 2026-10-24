"""
Phase 8.3: unsupported deadlines, extraction fallback and the evidence appendix.

Regression for meeting fad3ce32… (upload of the login-portal recording): the
mixed audio was transcribed as "... email the access links by Twitter", Gemini
timed out, the hybrid extractor kept "by Twitter" in the task, and the PDF
showed that task beside "No deadline was stated." Both voices were labelled
SPEAKER_00.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api import meeting_routes
from app.api.main import app
from app.core import db
from app.schemas.utterance import Utterance
from app.services.verification.fusion import unrecognized_deadline_phrase
from tests.scenario_helpers import add_action, add_decision, record_extraction
from tests.semantic_fakes import install_semantic_fakes

TWITTER = "I will finalize the backend API, deployment, and email the access links by Twitter."


def _meeting(lines):
    mid = f"ev83-{uuid4().hex[:10]}"
    db.insert_meeting(mid, filename="recording.wav")
    db.bulk_insert_utterances(mid, [
        Utterance(meeting_id=mid, utt_id=f"u{i:05d}", start=i * 5.0, end=i * 5.0 + 4, speaker_id=spk,
                  speaker_name=name, text=text, asr_conf=0.9, spk_conf=conf, overlap=False, conf_source="numeric")
        for i, (spk, name, text, conf) in enumerate(lines)
    ])
    return mid


def _fallback_run(mid):
    db.upsert_extraction_run(
        mid, status="completed", mode_requested="llm", mode_used="hybrid",
        fallback_reason="LLM extraction failed: LLM request timed out after 180s (2 attempts).",
        accepted=len(db.get_claims(mid)), dropped=0, extractor_version="rules-v2+dialogue-v1",
    )


def _claims(client, mid):
    return {c["claim_id"]: c for c in client.get(f"/api/meetings/{mid}/claims").json()["claims"]}


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("finalize the backend API and email the access links by Twitter", "by Twitter"),
    ("submit the bug report by Friday", None),
    ("finish it before the demo", None),
    ("review it by next week", None),
    ("deploy by 12 November", None),
    ("send the report by email", None),
    ("check the mobile layout", None),
])
def test_unrecognized_trailing_deadline_phrases(text, expected):
    assert unrecognized_deadline_phrase(text) == expected


def test_a_known_person_is_not_mistaken_for_a_deadline():
    assert unrecognized_deadline_phrase("get the budget approved by Priya", ["Priya"]) is None


def test_by_twitter_is_flagged_and_never_shown_as_a_deadline(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = _meeting([("SPEAKER_00", None, TWITTER, 1.0)])
    cid = add_action(mid, 0, "finalize the backend API, deployment, and email the access links by Twitter",
                     ["u00000"], owner="SPEAKER_00", owner_type="explicit_first_person")
    _fallback_run(mid)
    client = TestClient(app)
    client.post(f"/api/verify/{mid}")

    claim = _claims(client, mid)[cid]
    codes = {r["code"] for r in claim["verification"]["final"]["reasons"]}
    assert "deadline_unrecognized" in codes and "no_deadline" not in codes      # not "no deadline stated"
    assert claim["exportable"] is True                                          # the task itself stays valid
    assert claim["deadline_resolved"] is None

    mom = client.post(f"/api/generate-mom/{mid}").json()["mom"]
    item = mom["action_items"][0]
    assert item["dueDate"] is None
    assert any("by Twitter" in w and "not a recognisable date" in w for w in item["warnings"])


def test_an_unresolvable_llm_deadline_is_labelled_unresolved(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = _meeting([("u_host", "Samarth", TWITTER, 1.0)])
    cid = add_action(mid, 0, "Finalize the backend API, deployment and email the access links", ["u00000"],
                     owner="Samarth", owner_type="explicit_first_person", deadline_text="by Twitter")
    record_extraction(mid)
    client = TestClient(app)
    client.post(f"/api/verify/{mid}")
    assert _claims(client, mid)[cid]["deadline_type"] in (None, "none")

    mom = client.post(f"/api/generate-mom/{mid}").json()["mom"]
    assert mom["action_items"][0]["dueDate"] == 'Unresolved: "by Twitter"'
    assert any(f.startswith("Deadline unclear for") for f in mom["follow_ups"])

    approved = client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Rita"}).json()["mom"]
    assert approved["action_items"][0]["dueDate"] == 'Unresolved: "by Twitter"'


def test_a_reviewer_typed_deadline_is_shown_as_typed_and_audited(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = _meeting([("u_host", "Samarth", TWITTER, 1.0)])
    cid = add_action(mid, 0, "Finalize the backend API, deployment and email the access links", ["u00000"],
                     owner="Samarth", owner_type="explicit_first_person")
    record_extraction(mid)
    client = TestClient(app)
    client.post(f"/api/verify/{mid}")
    assert client.put(f"/api/meetings/{mid}/review/items/{cid}",
                      json={"deadline_text": "Thursday", "state": "accepted",
                            "reason": "Checked the audio"}).status_code == 200
    mom = client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Rita"}).json()["mom"]
    assert mom["action_items"][0]["dueDate"] == "Thursday"
    audit = client.get(f"/api/meetings/{mid}/audit-log").json()["audit_log"]
    assert any(e["field_name"] == "deadline_text" and e["new_value"] == "Thursday" for e in audit)


# ---------------------------------------------------------------------------
# Fallback and appendix
# ---------------------------------------------------------------------------

def _mixed_meeting(monkeypatch):
    """Two items confirmed, one held (uncertain speaker), on unnamed labels."""
    install_semantic_fakes(monkeypatch)
    mid = _meeting([
        ("SPEAKER_00", None, "We decided to migrate the primary database to PostgreSQL.", 0.95),
        ("SPEAKER_00", None, "I will complete the front-end validation and submit the bug report by Friday.", 0.95),
        ("SPEAKER_00", None, "I will send the signed contract to the client tomorrow.", 0.2),
    ])
    decision = add_decision(mid, 0, "Migrate the primary database to PostgreSQL", ["u00000"])
    confirmed = add_action(mid, 0, "Complete the front-end validation and submit the bug report", ["u00001"],
                           owner="SPEAKER_00", owner_type="explicit_first_person", deadline_text="by Friday",
                           deadline_resolved="2026-10-30")
    held = add_action(mid, 1, "Send the signed contract to the client", ["u00002"], owner="SPEAKER_00",
                      owner_type="explicit_first_person", deadline_text="tomorrow", deadline_resolved="2026-10-25")
    _fallback_run(mid)
    client = TestClient(app)
    client.post(f"/api/verify/{mid}")
    return client, mid, decision, confirmed, held


def test_fallback_extraction_is_named_with_its_reason(monkeypatch):
    client, mid, *_ = _mixed_meeting(monkeypatch)
    extraction = client.post(f"/api/generate-mom/{mid}").json()["mom"]["extraction"]
    assert extraction["llm_used"] is False and extraction["mode_used"] == "hybrid"
    note = extraction["fallback_note"]
    assert "did not run" in note and "hybrid extractor" in note and "timed out" in note


def test_appendix_shows_every_outcome_with_evidence_and_speaker_link(monkeypatch):
    client, mid, decision, confirmed, held = _mixed_meeting(monkeypatch)
    mom = client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Rita"}).json()["mom"]
    rows = mom["evidence_appendix"]
    by_item = {}
    for row in rows:
        by_item.setdefault(row["item_id"], []).append(row)

    assert {r["outcome"] for r in by_item[decision]} == {"confirmed"}
    assert {r["outcome"] for r in by_item[confirmed]} == {"confirmed"}
    held_rows = by_item[held]
    assert {r["outcome"] for r in held_rows} == {"held_for_review"}
    assert held_rows[0]["reasons"] and held_rows[0]["quote"].startswith("I will send the signed contract")
    assert held_rows[0]["item_text"] == "Send the signed contract to the client"
    assert all(r["speaker_link"] == "unnamed" for r in rows)                  # never linked to an account
    assert rows[0]["verifier"].startswith("Text match + NLI")
    assert "semantic" in rows[0]["verifier_versions"]                          # full ids stay in JSON

    # Counts agree between minutes, appendix and review screen.
    confirmed_ids = {d["claim_id"] for d in mom["decisions"]} | {a["claim_id"] for a in mom["action_items"]}
    assert confirmed_ids == {r["item_id"] for r in rows if r["outcome"] == "confirmed"}
    assert {x["claim_id"] for x in mom["needs_review"]} == {r["item_id"] for r in rows if r["outcome"] == "held_for_review"}
    review = client.get(f"/api/meetings/{mid}/review").json()
    assert review["counts"]["needs_review"] == mom["verification"]["held_for_review"] == 1
    assert client.get(f"/api/meetings/{mid}/export/json").json()["evidence_appendix"] == rows

    pdf = client.get(f"/api/download-mom/{mid}")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_participant_tracks_are_marked_as_participants(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = _meeting([("u_guest", "Asmee", "I will complete the front-end validation by Friday.", 1.0)])
    add_action(mid, 0, "Complete the front-end validation", ["u00000"], owner="Asmee",
               owner_type="explicit_first_person", deadline_text="by Friday", deadline_resolved="2026-10-30")
    record_extraction(mid)
    client = TestClient(app)
    client.post(f"/api/verify/{mid}")
    rows = client.post(f"/api/generate-mom/{mid}").json()["mom"]["evidence_appendix"]
    assert rows[0]["speaker"] == "Asmee" and rows[0]["speaker_link"] == "participant"


def test_reviewer_exclusions_are_listed_but_not_used_as_evidence(monkeypatch):
    client, mid, decision, *_ = _mixed_meeting(monkeypatch)
    client.put(f"/api/meetings/{mid}/review/items/{decision}", json={"is_excluded": True, "reason": "Duplicate"})
    mom = client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Rita"}).json()["mom"]
    assert decision not in {r["item_id"] for r in mom["evidence_appendix"]}
    assert [x["claim_id"] for x in mom["excluded_from_mom"]] == [decision]
    assert mom["excluded_from_mom"][0]["claim_type"] == "decision"
    pdf = meeting_routes.build_pdf(mid, mom)
    assert pdf.exists()
