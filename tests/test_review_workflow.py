"""
Phase 3 comprehensive review workflow tests:
1. Review draft persistence and isolation from original AI claims.
2. Editing decision / action items and ensuring original AI claims remain untouched.
3. Marking edited claims as 'human_edited' without false AI verification claims.
4. Item addition and exclusion.
5. Explicit approval locking and idempotency.
6. Reopening approved drafts with reason while preserving previous approved snapshot.
7. Audit log verification with timestamps and reviewer identities.
8. Approved JSON and PDF export verification.
9. Audio streaming endpoint.
10. Database migration and restart safety.
"""
from __future__ import annotations

import json
import zlib
import pytest
from fastapi.testclient import TestClient
from reportlab.pdfbase import pdfutils

from app.api.main import app
from app.core import config, db
from app.core.storage import MeetingStorage
from tests.scenario_helpers import (
    FILLER,
    add_action,
    add_decision,
    make_meeting,
    record_extraction,
)
from tests.semantic_fakes import install_semantic_fakes


@pytest.fixture()
def client(monkeypatch):
    install_semantic_fakes(monkeypatch)
    return TestClient(app)


def _setup_verified_meeting():
    mid = make_meeting(FILLER + [
        ("SPEAKER_00", "We decided to migrate the primary database to PostgreSQL."),     # u00004
        ("SPEAKER_01", "Great, the whole team agrees on PostgreSQL."),                    # u00005
        ("SPEAKER_02", "Bob will configure the replica servers by Friday."),              # u00006
    ])
    d_id = add_decision(mid, 0, "Migrate the primary database to PostgreSQL", ["u00004", "u00005"])
    a_id = add_action(mid, 0, "Configure the replica servers", ["u00006"],
                      owner="Bob", owner_type="explicit_third_person",
                      deadline_text="by Friday", deadline_resolved="2026-10-23")
    record_extraction(mid)
    return mid, d_id, a_id


def test_review_draft_initialization_preserves_original_claims(client):
    mid, d_id, a_id = _setup_verified_meeting()
    client.post(f"/api/verify/{mid}")

    # Load review draft
    res = client.get(f"/api/meetings/{mid}/review")
    assert res.status_code == 200
    data = res.json()
    assert data["draft_status"] == "draft"
    assert data["counts"]["total_items"] == 2
    assert data["counts"]["original"] == 2
    assert data["approved_snapshot"] is None

    # Verify original AI claims in DB are untouched
    orig_claims = db.get_claims(mid)
    assert len(orig_claims) == 2
    orig_texts = {c["text"] for c in orig_claims}
    assert "Migrate the primary database to PostgreSQL" in orig_texts


def test_edit_item_preserves_original_ai_claims_and_marks_edited(client):
    mid, d_id, a_id = _setup_verified_meeting()
    client.post(f"/api/verify/{mid}")

    # Edit action item owner and deadline
    update_payload = {
        "text": "Configure the replica servers and primary failover",
        "owner": "Alice",
        "deadline_text": "by Monday",
        "deadline_resolved": "2026-10-26",
        "reviewer": "Madhura",
        "reason": "Reassigned during sprint planning",
    }
    update_res = client.put(f"/api/meetings/{mid}/review/items/{a_id}", json=update_payload)
    assert update_res.status_code == 200
    updated_body = update_res.json()
    assert updated_body["item"]["owner"] == "Alice"
    assert updated_body["item"]["review_status"] == "edited"

    # Reload review draft
    reload_res = client.get(f"/api/meetings/{mid}/review")
    reload_data = reload_res.json()
    action_item = next(it for it in reload_data["items"] if it["item_id"] == a_id)
    assert action_item["owner"] == "Alice"
    assert action_item["display_status"] == "human_edited"
    assert action_item["review_status"] == "edited"

    # CRITICAL INVARIANT: Original claim in claims table remains 100% untouched
    orig_claims = db.get_claims(mid)
    orig_action = next(c for c in orig_claims if c["claim_id"] == a_id)
    assert orig_action["payload"]["owner"] == "Bob"
    assert orig_action["payload"]["deadline_text"] == "by Friday"
    assert orig_action["text"] == "Configure the replica servers"


def test_exclude_and_add_review_items(client):
    mid, d_id, a_id = _setup_verified_meeting()
    client.post(f"/api/verify/{mid}")

    # Exclude decision
    exclude_res = client.put(f"/api/meetings/{mid}/review/items/{d_id}", json={
        "is_excluded": True,
        "reviewer": "Samarth",
        "reason": "Postponed until next quarter",
    })
    assert exclude_res.status_code == 200

    # Add new manual action item
    new_item_payload = {
        "item_type": "action",
        "text": "Schedule follow-up architecture review",
        "owner": "Samarth",
        "deadline_text": "next Wednesday",
        "reviewer": "Madhura",
        "reason": "Agreed verbally at end of call",
    }
    add_res = client.post(f"/api/meetings/{mid}/review/items", json=new_item_payload)
    assert add_res.status_code == 200
    new_item = add_res.json()["item"]
    assert new_item["review_status"] == "added"
    assert new_item["text"] == "Schedule follow-up architecture review"

    # Check review summary counts
    review = client.get(f"/api/meetings/{mid}/review").json()
    assert review["counts"]["excluded"] == 1
    assert review["counts"]["added"] == 1


def test_approval_locks_draft_and_creates_immutable_snapshot(client):
    mid, d_id, a_id = _setup_verified_meeting()
    client.post(f"/api/verify/{mid}")

    # Edit an item before approval
    client.put(f"/api/meetings/{mid}/review/items/{a_id}", json={
        "owner": "Alice",
        "reviewer": "Madhura",
    })

    # Approve meeting review
    approve_res = client.post(f"/api/meetings/{mid}/approve", json={
        "reviewer": "Madhura",
        "notes": "Reviewed and verified against discussion.",
    })
    assert approve_res.status_code == 200
    body = approve_res.json()
    assert body["status"] == "approved"
    assert body["version"] == 1
    assert body["approved_by"] == "Madhura"
    assert (MeetingStorage(mid).path_for("mom", "Meet2MoM-Meeting-Minutes.pdf")).exists()

    # LOCK TEST: Attempting to edit an approved item must return 409 Conflict
    edit_attempt = client.put(f"/api/meetings/{mid}/review/items/{a_id}", json={
        "owner": "Charlie",
        "reviewer": "Samarth",
    })
    assert edit_attempt.status_code == 409
    assert "reopen" in edit_attempt.json()["detail"].lower()

    # Attempting to add an item to approved draft must return 409 Conflict
    add_attempt = client.post(f"/api/meetings/{mid}/review/items", json={
        "item_type": "decision",
        "text": "Unapproved decision",
    })
    assert add_attempt.status_code == 409

    # IDEMPOTENCY: Repeated approval returns 200 with existing snapshot
    repeat_res = client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Madhura"})
    assert repeat_res.status_code == 200
    assert repeat_res.json()["snapshot"]["version"] == 1


def test_reopening_preserves_previous_snapshot_and_allows_version_2(client):
    mid, d_id, a_id = _setup_verified_meeting()
    client.post(f"/api/verify/{mid}")

    # Approve version 1
    client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Madhura"})
    snap1 = db.get_latest_approved_snapshot(mid)
    assert snap1["version"] == 1

    # Reopen requires explicit reason
    bad_reopen = client.post(f"/api/meetings/{mid}/reopen", json={"reviewer": "Samarth", "reason": ""})
    assert bad_reopen.status_code == 422

    # Valid reopen
    reopen_res = client.post(f"/api/meetings/{mid}/reopen", json={
        "reviewer": "Samarth",
        "reason": "Need to update replica configuration timeline.",
    })
    assert reopen_res.status_code == 200
    assert reopen_res.json()["draft_status"] == "reopened"

    # Prior snapshot must still exist and be marked reopened
    all_snaps = db.get_all_approved_snapshots(mid)
    assert len(all_snaps) == 1
    assert all_snaps[0]["reopened_by"] == "Samarth"
    assert all_snaps[0]["reopen_reason"] == "Need to update replica configuration timeline."

    # Now editing is allowed again
    edit_res = client.put(f"/api/meetings/{mid}/review/items/{a_id}", json={
        "deadline_text": "by next month",
        "reviewer": "Samarth",
    })
    assert edit_res.status_code == 200

    # Approve version 2
    approve2_res = client.post(f"/api/meetings/{mid}/approve", json={
        "reviewer": "Samarth",
        "notes": "Approved updated timeline.",
    })
    assert approve2_res.status_code == 200
    assert approve2_res.json()["version"] == 2

    # Check snapshots history
    history = db.get_all_approved_snapshots(mid)
    assert len(history) == 2
    assert [s["version"] for s in history] == [1, 2]


def test_audit_history_records_all_actions(client):
    mid, d_id, a_id = _setup_verified_meeting()
    client.post(f"/api/verify/{mid}")

    client.put(f"/api/meetings/{mid}/review/items/{a_id}", json={
        "owner": "Alice",
        "reviewer": "Madhura",
        "reason": "Reassigned owner",
    })
    client.post(f"/api/meetings/{mid}/approve", json={
        "reviewer": "Madhura",
        "notes": "First approval",
    })
    client.post(f"/api/meetings/{mid}/reopen", json={
        "reviewer": "Samarth",
        "reason": "Fix deadline",
    })

    audit_res = client.get(f"/api/meetings/{mid}/audit-log")
    assert audit_res.status_code == 200
    log = audit_res.json()["audit_log"]
    actions = [entry["action"] for entry in log]
    assert "edit_owner" in actions
    assert "approve_mom" in actions
    assert "reopen_review" in actions

    owner_entry = next(e for e in log if e["action"] == "edit_owner")
    assert owner_entry["old_value"] == "Bob"
    assert owner_entry["new_value"] == "Alice"
    assert owner_entry["reviewer_id"] == "Madhura"
    assert owner_entry["reason"] == "Reassigned owner"


def test_export_endpoints_respect_approval_lifecycle(client):
    mid, d_id, a_id = _setup_verified_meeting()
    client.post(f"/api/verify/{mid}")

    # Unapproved JSON export returns 409
    unapproved_export = client.get(f"/api/meetings/{mid}/export/json")
    assert unapproved_export.status_code == 409

    # Unapproved with ?preview=true succeeds
    preview_export = client.get(f"/api/meetings/{mid}/export/json?preview=true")
    assert preview_export.status_code == 200
    assert "decisions" in preview_export.json()

    # Approve meeting
    client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Madhura"})

    # Approved JSON export returns approved snapshot
    approved_export = client.get(f"/api/meetings/{mid}/export/json")
    assert approved_export.status_code == 200
    body = approved_export.json()
    assert body["status"] == "approved"
    assert body["approved_by"] == "Madhura"

    # PDF download returns approved PDF
    pdf_res = client.get(f"/api/download-mom/{mid}")
    assert pdf_res.status_code == 200
    assert pdf_res.content[:4] == b"%PDF"


def test_audio_endpoint_handles_missing_and_available(client):
    mid, _, _ = _setup_verified_meeting()
    # Meeting with no audio file returns 404
    no_audio = client.get(f"/api/meetings/{mid}/audio")
    assert no_audio.status_code == 404

    # Create dummy audio file in recording folder
    storage = MeetingStorage(mid)
    storage.ensure_dirs()
    dummy_audio = storage.recording / "recording.wav"
    dummy_audio.write_bytes(b"RIFFdummywavecontent1234")

    audio_res = client.get(f"/api/meetings/{mid}/audio")
    assert audio_res.status_code == 200
    assert audio_res.content == b"RIFFdummywavecontent1234"


def test_database_migration_v1_to_v2_and_restart(tmp_path):
    """Verify additive migration from user_version 1 to 2 preserves data across DB re-opens."""
    test_db = tmp_path / "migration_test.db"

    # 1. Initialize schema v1 directly
    with db.get_conn(test_db) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS meetings (
                meeting_id TEXT PRIMARY KEY, filename TEXT, duration REAL, created_at TEXT, status TEXT
            );
            CREATE TABLE IF NOT EXISTS claims (
                claim_id TEXT PRIMARY KEY, meeting_id TEXT, claim_type TEXT, text TEXT, payload TEXT, created_at TEXT,
                FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
            );
            """
        )
        conn.executescript(db._MIGRATION_V1)
        conn.execute("PRAGMA user_version = 1")
        conn.execute("INSERT INTO meetings (meeting_id, filename, status) VALUES ('m_legacy', 'legacy.wav', 'completed')")
        conn.execute("INSERT INTO claims (claim_id, meeting_id, claim_type, text, payload, created_at) VALUES ('c1', 'm_legacy', 'decision', 'Legacy decision', '{}', '2026-10-01')")

    # Verify version 1
    assert db.get_schema_version(test_db) == 1

    # 2. Run init_db which triggers _migrate
    db.init_db(test_db)
    assert db.get_schema_version(test_db) == db.SCHEMA_VERSION

    # 3. Verify legacy data is preserved intact
    with db.get_conn(test_db) as conn:
        m = conn.execute("SELECT * FROM meetings WHERE meeting_id = 'm_legacy'").fetchone()
        assert m["filename"] == "legacy.wav"
        c = conn.execute("SELECT * FROM claims WHERE claim_id = 'c1'").fetchone()
        assert c["text"] == "Legacy decision"

        # Verify new v2 tables exist and function
        conn.execute("INSERT INTO review_drafts (meeting_id, draft_status, created_at, updated_at) VALUES ('m_legacy', 'draft', '2026-10-17', '2026-10-17')")
        draft = conn.execute("SELECT * FROM review_drafts WHERE meeting_id = 'm_legacy'").fetchone()
        assert draft["draft_status"] == "draft"


def test_evidence_appendix_in_json_and_pdf(client):
    mid, d_id, a_id = _setup_verified_meeting()
    client.post(f"/api/verify/{mid}")

    # 1. Add a reviewer-curated item with no cited utterances
    add_res = client.post(f"/api/meetings/{mid}/review/items", json={
        "item_type": "decision",
        "text": "Reviewer manual decision",
        "reviewer": "Madhura",
        "evidence_ids": [],
    })
    assert add_res.status_code == 200
    added_item_id = add_res.json()["item"]["item_id"]

    # 2. Exclude the decision item d_id
    ex_res = client.put(f"/api/meetings/{mid}/review/items/{d_id}", json={
        "is_excluded": True,
        "reviewer": "Madhura",
        "reason": "Out of scope decision",
    })
    assert ex_res.status_code == 200

    # 3. Unapproved JSON export without preview returns 409
    unapproved_res = client.get(f"/api/meetings/{mid}/export/json")
    assert unapproved_res.status_code == 409

    # 4. Unapproved export with preview=true succeeds and includes evidence_appendix
    preview_res = client.get(f"/api/meetings/{mid}/export/json?preview=true")
    assert preview_res.status_code == 200
    preview_mom = preview_res.json()
    assert "evidence_appendix" in preview_mom

    # 5. Approve the meeting review
    app_res = client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Madhura"})
    assert app_res.status_code == 200

    # 6. Approved JSON export contains authoritative evidence appendix
    export_res = client.get(f"/api/meetings/{mid}/export/json")
    assert export_res.status_code == 200
    mom = export_res.json()
    assert "evidence_appendix" in mom
    appendix = mom["evidence_appendix"]

    # Utterances from database
    utts = {u.utt_id: u for u in db.get_utterances(mid)}

    # Excluded item d_id must NOT appear anywhere in the appendix
    item_ids_in_appendix = [row["item_id"] for row in appendix]
    assert d_id not in item_ids_in_appendix

    # Reviewer-added item must appear with evidence_role 'none' and explicit note
    reviewer_rows = [row for row in appendix if row["item_id"] == added_item_id]
    assert len(reviewer_rows) == 1
    r_row = reviewer_rows[0]
    assert r_row["evidence_role"] == "none"
    assert r_row["quote"] == "Reviewer-added; no transcript evidence"
    assert r_row["utt_id"] is None
    assert r_row["speaker"] is None
    assert r_row["start_s"] is None
    assert r_row["end_s"] is None
    assert r_row["start_label"] is None
    assert r_row["end_label"] is None
    assert "deterministic" in r_row["verifier_versions"]
    assert "semantic" in r_row["verifier_versions"]
    assert "thresholds" in r_row["verifier_versions"]

    # Action item a_id cited 'u00006'
    action_rows = [row for row in appendix if row["item_id"] == a_id]
    assert len(action_rows) == 1
    a_row = action_rows[0]
    assert a_row["utt_id"] == "u00006"
    assert a_row["quote"] == utts["u00006"].text
    assert a_row["speaker"] == (utts["u00006"].speaker_name or utts["u00006"].speaker_id)
    assert a_row["start_label"] == "00:30"
    assert a_row["end_label"] == "00:34"
    assert a_row["evidence_role"] in ("evidence", "confirmation")
    assert a_row["start_s"] == round(utts["u00006"].start, 2)
    assert a_row["end_s"] == round(utts["u00006"].end, 2)

    # 7. PDF download builds without error and contains the heading 'Evidence Appendix'
    pdf_res = client.get(f"/api/download-mom/{mid}")
    assert pdf_res.status_code == 200
    assert pdf_res.content[:4] == b"%PDF"

    # Verify decompressed PDF streams contain 'Evidence Appendix'
    raw_pdf = pdf_res.content
    idx = 0
    found_heading = False
    while True:
        s_idx = raw_pdf.find(b"stream\n", idx)
        if s_idx == -1:
            break
        start = s_idx + len(b"stream\n")
        term = raw_pdf.find(b"~>", start)
        if term == -1:
            break
        stream_data = raw_pdf[start:term + 2]
        try:
            decoded = pdfutils.asciiBase85Decode(stream_data)
            decomp = zlib.decompress(decoded)
            if b"Evidence Appendix" in decomp:
                found_heading = True
                break
        except Exception:
            pass
        idx = term + 2

    assert found_heading is True


