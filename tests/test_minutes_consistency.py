"""
Phase 8.1: approved minutes, the review view and exports agree, and the
meeting language is reported truthfully.

Regression for a real approved meeting (eaba38…): an action item held for
review (owner from an uncertain SPEAKER_00 label, low ASR confidence) was
filed as "excluded" on approval, so the PDF said "No reliable action items
detected" with a green tick and "0 held for review", while the LLM discussion
summary described the commitment. Its language showed as "ml" for English.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.api import meeting_routes
from app.api.main import app
from app.core import config, db
from app.core.storage import MeetingStorage
from app.services.pipeline import recorded_pipeline_adapter as adapter
from tests.scenario_helpers import FILLER, add_action, add_decision, make_meeting, record_extraction
from tests.semantic_fakes import install_semantic_fakes


def _meeting_with_held_and_confirmed(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [
        # Held: the owner comes from an uncertain speaker label (as in the PDF).
        ("SPEAKER_00", "I will submit the marketing report by Friday.", {"spk": 0.15}),
        ("SPEAKER_01", "We decided to migrate the primary database to PostgreSQL."),
    ] + FILLER[2:])
    held = add_action(mid, 0, "Submit the marketing report", ["u00002"], owner="SPEAKER_00",
                      owner_type="explicit_first_person", deadline_text="by Friday", deadline_resolved="2026-10-30")
    confirmed = add_decision(mid, 0, "Migrate the primary database to PostgreSQL", ["u00003"])
    record_extraction(mid)
    client = TestClient(app)
    assert client.post(f"/api/verify/{mid}").status_code == 200
    return client, mid, held, confirmed


def _approve(client, mid):
    res = client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "Rita"})
    assert res.status_code == 200, res.text
    return res.json()["mom"]


def test_held_items_stay_held_in_the_approved_minutes(monkeypatch):
    client, mid, held, confirmed = _meeting_with_held_and_confirmed(monkeypatch)
    review = client.get(f"/api/meetings/{mid}/review").json()
    mom = _approve(client, mid)

    assert [a["claim_id"] for a in mom["action_items"]] == []               # never confirmed silently
    assert [d["claim_id"] for d in mom["decisions"]] == [confirmed]
    held_entry = next(x for x in mom["needs_review"] if x["claim_id"] == held)
    assert held_entry["claim_type"] == "action" and held_entry["reasons"]
    assert held_entry["speaker"] == "SPEAKER_00"
    assert mom["excluded_from_mom"] == []                                    # not a reviewer exclusion

    v = mom["verification"]
    assert (v["confirmed_decisions"], v["reliable_action_items"], v["held_for_review"], v["rejected"]) == (1, 0, 1, 0)
    assert v["held_for_review"] == review["counts"]["needs_review"]          # review UI and minutes agree
    assert "1 item was held back" in mom["summary"]

    # JSON export and a later minutes request return the same approved data.
    exported = client.get(f"/api/meetings/{mid}/export/json").json()
    assert exported["needs_review"] == mom["needs_review"] and exported["action_items"] == mom["action_items"]
    again = client.post(f"/api/generate-mom/{mid}").json()
    assert again["status"] == "approved_snapshot" and again["mom"]["needs_review"] == mom["needs_review"]


def test_reviewer_exclusion_and_rejection_are_reported_apart_from_held(monkeypatch):
    client, mid, held, confirmed = _meeting_with_held_and_confirmed(monkeypatch)
    assert client.put(f"/api/meetings/{mid}/review/items/{confirmed}",
                      json={"is_excluded": True, "reason": "Duplicate"}).status_code == 200
    mom = _approve(client, mid)
    assert [x["claim_id"] for x in mom["excluded_from_mom"]] == [confirmed]
    assert [x["claim_id"] for x in mom["needs_review"]] == [held]
    assert mom["verification"]["excluded_items"] == 1 and mom["verification"]["held_for_review"] == 1


def test_a_reviewer_supplied_deadline_clears_only_the_no_deadline_warning(monkeypatch):
    install_semantic_fakes(monkeypatch)
    mid = make_meeting(FILLER[:2] + [("Priya", "I will update the onboarding guide.")] + FILLER[2:])
    cid = add_action(mid, 0, "Update the onboarding guide", ["u00002"], owner="Priya",
                     owner_type="explicit_first_person")
    record_extraction(mid)
    client = TestClient(app)
    client.post(f"/api/verify/{mid}")
    claim = client.get(f"/api/meetings/{mid}/claims").json()["claims"][0]
    assert "no_deadline" in {r["code"] for r in claim["verification"]["final"]["reasons"]}

    assert client.put(f"/api/meetings/{mid}/review/items/{cid}",
                      json={"deadline_text": "Friday", "state": "accepted"}).status_code == 200
    mom = _approve(client, mid)
    item = mom["action_items"][0]
    assert item["dueDate"] == "Friday"
    assert "No deadline was stated." not in item["warnings"]


# ---------------------------------------------------------------------------
# Language: configured vs detected
# ---------------------------------------------------------------------------

def _write_ready_transcript(mid, languages):
    storage = MeetingStorage(mid)
    storage.ensure_dirs()
    segments = [{"start": i * 5.0, "end": i * 5.0 + 4, "speaker": f"SPEAKER_0{i}", "text": f"Sentence {i}.",
                 "language": lang} for i, lang in enumerate(languages)]
    storage.path_for("transcript", "meeting_transcript_ready.json").write_text(json.dumps(segments), encoding="utf-8")


def test_configured_language_is_the_meeting_language(monkeypatch):
    mid = make_meeting([("A", "Hello.")])
    _write_ready_transcript(mid, ["en", "ml"])                 # Whisper's per-segment detection
    monkeypatch.setattr(config, "FORCE_LANGUAGE", "en")
    transcript = adapter.load_frontend_transcript(mid)
    assert (transcript["language"], transcript["language_source"]) == ("en", "configured")


def test_detected_language_is_labelled_as_detected(monkeypatch):
    mid = make_meeting([("A", "Hello.")])
    _write_ready_transcript(mid, ["ml"])
    monkeypatch.setattr(config, "FORCE_LANGUAGE", None)
    transcript = adapter.load_frontend_transcript(mid)
    assert (transcript["language"], transcript["language_source"]) == ("ml", "detected")


def test_minutes_show_the_language_name_source_and_disagreement():
    mid = make_meeting([("A", "Hello."), ("B", "Thank you.")])
    with db.get_conn() as conn:
        conn.execute("UPDATE utterances SET lang_tags = ? WHERE meeting_id = ? AND utt_id = 'u00000'", ('["en"]', mid))
        conn.execute("UPDATE utterances SET lang_tags = ? WHERE meeting_id = ? AND utt_id = 'u00001'", ('["ml"]', mid))
    storage = MeetingStorage(mid)
    storage.ensure_dirs()
    storage.path_for("transcript", "frontend_transcript.json").write_text(
        json.dumps({"language": "ml", "language_source": "detected"}), encoding="utf-8")

    info = meeting_routes._meeting_language_info(storage, db.get_utterances(mid))
    assert info == {"language": "ml", "language_name": "Malayalam", "language_source": "detected",
                    "segment_languages": {"en": 1, "ml": 1}}
    assert meeting_routes.format_language(info) == "Malayalam (ml), detected"
    assert meeting_routes.format_language({"language": "en", "language_source": "configured"}) == "English (en), set"
    assert meeting_routes.format_language({"language": "en"}) == "English (en)"     # source not recorded
    assert meeting_routes.format_language({}) == "Not specified"


def test_asr_settings_change_the_pipeline_cache_key(monkeypatch):
    monkeypatch.setattr(config, "FORCE_LANGUAGE", None)
    auto = adapter.asr_settings_key()
    monkeypatch.setattr(config, "FORCE_LANGUAGE", "en")
    forced = adapter.asr_settings_key()
    monkeypatch.setattr(config, "WHISPER_SPLIT_GAP_SECONDS", 0.0)
    no_split = adapter.asr_settings_key()
    assert len({auto, forced, no_split}) == 3                 # a stale "ml" transcript is never reused
