"""
Phase 8: reviewer correction of transcript text.

The real case: "by Friday" was transcribed as "by FFmpeg". Nothing is replaced
automatically; a reviewer corrects the utterance, the ASR text is kept, the
change is audited, and claims verified against the old words are held until
verification runs again.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.main import app
from app.core import db
from app.core.storage import MeetingStorage
from app.schemas.job import JobStatus
from app.services.verification.claims_view import STALE_REASON_CODE, build_claims_view
from tests.scenario_helpers import (
    FILLER,
    add_action,
    make_meeting,
    reason_codes,
    record_extraction,
)
from tests.semantic_fakes import install_semantic_fakes

ASR_LINE = "I will submit the changes by FFmpeg."
FIXED_LINE = "I will submit the changes by Friday."


def _meeting_with_verified_action(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [("SPEAKER_01", ASR_LINE), ("SPEAKER_00", "Thanks, sounds good.")])
    cid = add_action(mid, 0, "Submit the changes", ["u00002"], owner="SPEAKER_01", owner_type="explicit_first_person")
    record_extraction(mid)
    client = TestClient(app)
    assert client.post(f"/api/verify/{mid}").status_code == 200
    return client, mid, cid


def _correct(client, mid, utt_id="u00002", text=FIXED_LINE, reason="Checked against the audio"):
    return client.put(f"/api/meetings/{mid}/transcript/{utt_id}", json={"text": text, "reason": reason})


def test_correction_is_persisted_with_the_asr_text_and_audited(monkeypatch):
    client, mid, _ = _meeting_with_verified_action(monkeypatch)

    res = _correct(client, mid)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["correction"]["asr_text"] == ASR_LINE
    assert body["correction"]["corrected_text"] == FIXED_LINE
    assert body["utterance"]["text"] == FIXED_LINE and body["utterance"]["corrected"] is True

    # Survives a reload: the transcript endpoint shows both versions.
    rows = {u["utt_id"]: u for u in client.get(f"/api/meetings/{mid}/transcript").json()}
    assert rows["u00002"]["text"] == FIXED_LINE
    assert rows["u00002"]["asr_text"] == ASR_LINE
    assert rows["u00002"]["correction"]["reason"] == "Checked against the audio"
    assert rows["u00001"]["corrected"] is False and rows["u00001"]["correction"] is None

    # The ASR row itself is unchanged; the correction is an overlay.
    with db.get_conn() as conn:
        stored = conn.execute(
            "SELECT text FROM utterances WHERE meeting_id = ? AND utt_id = 'u00002'", (mid,)
        ).fetchone()[0]
    assert stored == ASR_LINE

    audit = client.get(f"/api/meetings/{mid}/audit-log").json()["audit_log"]
    event = next(e for e in audit if e["action"] == "transcript_corrected")
    assert (event["item_id"], event["old_value"], event["new_value"]) == ("u00002", ASR_LINE, FIXED_LINE)

    history = client.get(f"/api/meetings/{mid}/transcript-corrections").json()["corrections"]
    assert [h["corrected_text"] for h in history] == [FIXED_LINE]


def test_claims_verified_before_a_correction_are_held_and_approval_is_refused(monkeypatch):
    client, mid, cid = _meeting_with_verified_action(monkeypatch)
    before = next(c for c in build_claims_view(mid)["claims"] if c["claim_id"] == cid)
    assert before["verification_stale"] is False

    assert _correct(client, mid).status_code == 200

    view = build_claims_view(mid)
    claim = next(c for c in view["claims"] if c["claim_id"] == cid)
    assert claim["verification"]["final"]["label"] == "review"
    assert claim["exportable"] is False and claim["verification_stale"] is True
    assert STALE_REASON_CODE in reason_codes(claim)
    assert "u00002" in claim["verification"]["final"]["reasons"][0]["utt_ids"]
    assert view["transcript"]["stale_claims"] == 1
    assert view["transcript"]["corrected_utterances"] == ["u00002"]

    res = client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Reviewer"})
    assert res.status_code == 409 and "verification again" in res.json()["detail"]

    # Verification runs again against the corrected words (no force needed),
    # after which the claim is judged afresh and approval is possible.
    assert client.post(f"/api/verify/{mid}").json()["reused_existing"] is False
    refreshed = next(c for c in build_claims_view(mid)["claims"] if c["claim_id"] == cid)
    assert refreshed["verification_stale"] is False
    assert STALE_REASON_CODE not in reason_codes(refreshed)
    assert refreshed["evidence"][0]["text"] == FIXED_LINE
    assert client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Reviewer"}).status_code == 200


def test_correction_invalidates_the_retrieval_index_and_stale_minutes(monkeypatch):
    client, mid, _ = _meeting_with_verified_action(monkeypatch)
    storage = MeetingStorage(mid)
    spans = storage.path_for("verification", "retrieval/spans.json")
    mom = storage.path_for("mom", "mom.json")
    mom.parent.mkdir(parents=True, exist_ok=True)
    mom.write_text("{}", encoding="utf-8")
    assert spans.exists()

    assert _correct(client, mid).status_code == 200
    assert not spans.exists() and not mom.exists()


def test_invalid_corrections_are_refused(monkeypatch):
    client, mid, _ = _meeting_with_verified_action(monkeypatch)
    assert _correct(client, mid, text="   ").status_code == 422
    assert _correct(client, mid, text=ASR_LINE).status_code == 422          # unchanged
    assert _correct(client, mid, utt_id="u09999").status_code == 404
    assert _correct(client, mid, text="x" * 4001).status_code == 422
    assert db.get_transcript_corrections(mid) == []


def test_approved_minutes_and_running_jobs_block_corrections(monkeypatch):
    client, mid, _ = _meeting_with_verified_action(monkeypatch)
    assert client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Reviewer"}).status_code == 200
    res = _correct(client, mid)
    assert res.status_code == 409 and "reopen" in res.json()["detail"]

    other = make_meeting([("A", ASR_LINE)])
    db.upsert_job_status(JobStatus(meeting_id=other, stage="verification", progress=0.8, state="running",
                                   error=None, updated_at=datetime.now(timezone.utc)))
    assert _correct(client, other, utt_id="u00000").status_code == 409


def test_reverting_keeps_history_and_shows_the_asr_text(monkeypatch):
    client, mid, _ = _meeting_with_verified_action(monkeypatch)
    assert _correct(client, mid).status_code == 200
    assert _correct(client, mid, text=ASR_LINE, reason="Undo").status_code == 200

    row = next(u for u in client.get(f"/api/meetings/{mid}/transcript").json() if u["utt_id"] == "u00002")
    assert row["text"] == ASR_LINE and row["corrected"] is False
    history = db.get_transcript_corrections(mid)
    assert [h["previous_text"] for h in history] == [ASR_LINE, FIXED_LINE]
    # The verifier saw FIXED_LINE last time it could have, so a re-run is still needed.
    assert build_claims_view(mid)["transcript"]["stale_claims"] == 1


def test_correction_is_not_applied_to_a_reprocessed_utterance():
    mid = make_meeting([("A", ASR_LINE)])
    db.record_transcript_correction(mid, "u00000", FIXED_LINE, reviewer="R")
    with db.get_conn() as conn:  # reprocessing produced different words for the same id
        conn.execute("UPDATE utterances SET text = 'A different sentence.' WHERE meeting_id = ?", (mid,))
    assert db.get_utterances(mid)[0].text == "A different sentence."
    assert db.get_active_transcript_corrections(mid) == {}


def test_deleting_a_meeting_removes_its_corrections():
    mid = make_meeting([("A", ASR_LINE)])
    db.record_transcript_correction(mid, "u00000", FIXED_LINE, reviewer="R")
    assert TestClient(app).delete(f"/api/meetings/{mid}").status_code == 200
    assert db.get_transcript_corrections(mid) == []
