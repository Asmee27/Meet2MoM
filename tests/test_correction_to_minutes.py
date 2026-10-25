"""
Phase 9.1: transcript correction carried through to the minutes, and the
meeting date.

Reported: an uploaded meeting's PDF said "Deliver the front end vibration"
(low ASR confidence). A reviewer can correct the utterance after listening;
the claim extracted from the misheard words is then held, naming the words,
until the reviewer edits the item. Nothing is replaced automatically.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.api.main import app
from app.core import db
from app.services.verification.claims_view import WORDING_REASON_CODE, build_claims_view, outdated_wording
from tests.scenario_helpers import FILLER, add_action, make_meeting, reason_codes, record_extraction
from tests.semantic_fakes import install_semantic_fakes

ASR_LINE = "I will deliver the front end vibration test by Friday."
FIXED_LINE = "I will deliver the front end validation test by Friday."


def _meeting(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [("SPEAKER_01", ASR_LINE), ("SPEAKER_00", "Thanks, sounds good.")])
    cid = add_action(mid, 0, "Deliver the front end vibration test", ["u00002"], owner="SPEAKER_01",
                     owner_type="explicit_first_person", deadline_text="by Friday", deadline_resolved="2026-10-30")
    record_extraction(mid)
    client = TestClient(app)
    assert client.post(f"/api/verify/{mid}").status_code == 200
    return client, mid, cid


def _claim(mid, cid):
    return next(c for c in build_claims_view(mid)["claims"] if c["claim_id"] == cid)


def test_outdated_wording_names_only_words_the_reviewer_removed():
    corrections = {"u1": {"asr_text": ASR_LINE, "corrected_text": FIXED_LINE}}
    assert outdated_wording("Deliver the front end vibration test", ["u1"], corrections) == ["vibration"]
    assert outdated_wording("Deliver the front end validation test", ["u1"], corrections) == []
    assert outdated_wording("Deliver the front end vibration test", ["u2"], corrections) == []   # not cited


def test_a_claim_worded_from_misheard_text_is_held_until_the_reviewer_edits_it(monkeypatch):
    client, mid, cid = _meeting(monkeypatch)
    assert _claim(mid, cid)["exportable"] is True and _claim(mid, cid)["outdated_wording"] == []

    res = client.put(f"/api/meetings/{mid}/transcript/u00002",
                     json={"text": FIXED_LINE, "reason": "Checked against the recording"})
    assert res.status_code == 200
    # Approval is refused until verification runs again on the corrected words.
    assert client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Rita"}).status_code == 409
    assert client.post(f"/api/verify/{mid}").status_code == 200

    claim = _claim(mid, cid)
    assert claim["verification_stale"] is False
    assert claim["evidence"][0]["text"] == FIXED_LINE                      # verified against the correction
    assert claim["outdated_wording"] == ["vibration"]
    assert WORDING_REASON_CODE in reason_codes(claim) and claim["exportable"] is False
    message = next(r["message"] for r in claim["verification"]["final"]["reasons"] if r["code"] == WORDING_REASON_CODE)
    assert '"vibration"' in message and "check the recording" in message

    draft = client.post(f"/api/generate-mom/{mid}").json()["mom"]
    assert draft["meeting_date"] is None                                    # uploaded: date unknown
    assert not any("vibration" in a["task"] for a in draft["action_items"])   # never shown as confirmed
    assert any(h["claim_id"] == cid for h in draft["needs_review"])

    # The reviewer fixes the item text after listening, and accepts it.
    assert client.put(f"/api/meetings/{mid}/review/items/{cid}",
                      json={"text": "Deliver the front end validation test", "state": "accepted",
                            "reason": "Corrected after listening"}).status_code == 200
    assert _claim(mid, cid)["outdated_wording"] == []

    approved = client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Rita"})
    assert approved.status_code == 200
    mom = approved.json()["mom"]
    assert [a["task"] for a in mom["action_items"]] == ["Deliver the front end validation test"]
    assert mom["action_items"][0]["dueDate"] == "2026-10-30"
    assert mom["needs_review"] == []
    exported = client.get(f"/api/meetings/{mid}/export/json").json()
    assert exported["action_items"] == mom["action_items"]
    assert exported["evidence_appendix"] == mom["evidence_appendix"]
    review = client.get(f"/api/meetings/{mid}/review").json()
    assert review["counts"]["needs_review"] == 0 and review["counts"]["confirmed"] == 1

    # The ASR text and the audit history are kept.
    rows = {u["utt_id"]: u for u in client.get(f"/api/meetings/{mid}/transcript").json()}
    assert rows["u00002"]["asr_text"] == ASR_LINE and rows["u00002"]["text"] == FIXED_LINE
    audit = client.get(f"/api/meetings/{mid}/audit-log").json()["audit_log"]
    assert any(e["action"] == "transcript_corrected" for e in audit)
    assert any(e.get("field_name") == "text" and e.get("new_value") == "Deliver the front end validation test"
               for e in audit)


def test_meeting_date_comes_from_known_facts_only(monkeypatch):
    install_semantic_fakes(monkeypatch)
    client = TestClient(app)

    from app.services.meeting_dates import meeting_date_info

    upload = make_meeting([("SPEAKER_00", "We decided to ship the release.")])
    assert meeting_date_info(upload) == {"date": None, "source": None}          # upload time is not the date

    room = "date-room"
    live = db.create_live_meeting(room, title="Sync")
    started = datetime(2026, 10, 24, 11, 46, 40, tzinfo=timezone.utc)
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO recording_segments (segment_id, meeting_id, seq, state, started_by, started_at, "
            "capture_started_at, updated_at) VALUES ('seg-date', ?, 1, 'finalized', 'host', ?, ?, ?)",
            (live["meeting_id"], started.isoformat(), started.isoformat(), started.isoformat()),
        )
    assert meeting_date_info(live["meeting_id"]) == {"date": "24 Oct 2026, 11:46 UTC", "source": "live recording start"}

    owner = client.post("/api/auth/register", json={"email": "date-owner@example.com", "password": "correct horse 1",
                                                      "display_name": "Owner"}).json()["user"]
    start = datetime.now(timezone.utc) + timedelta(days=3)
    sched_id = "sched-date-1"
    db.create_scheduled_meeting(sched_id, title="Planning", agenda=None, scheduled_start_utc=start.isoformat(),
                                timezone_name="Asia/Kolkata", duration_minutes=30, room_name="m2m-date-test",
                                owner_id=owner["user_id"], request_id=None)
    info = meeting_date_info(sched_id)
    assert info["source"] == "scheduled start" and info["date"].endswith("(Asia/Kolkata)")
