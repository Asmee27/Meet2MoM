import sys
# Prevent Windows DLL popup for broken torchcodec inside sentence_transformers in this test environment
sys.modules["sentence_transformers"] = None

import json
import shutil
from pathlib import Path
from uuid import uuid4
import pytest

from app.core.config import STORAGE_ROOT, PROJECT_ROOT
from app.core.storage import MeetingStorage
from app.api.meeting_routes import (
    extract_meeting_intelligence,
    verify_meeting_intelligence,
    generate_mom,
)


def test_two_meetings_fully_independent():
    """
    Process two different meetings and confirm storage/<id_a>/ and storage/<id_b>/
    are fully independent with no shared writes, and output/ is never touched.
    """
    id_a = uuid4().hex
    id_b = uuid4().hex

    storage_a = MeetingStorage(id_a)
    storage_b = MeetingStorage(id_b)

    storage_a.ensure_dirs()
    storage_b.ensure_dirs()

    # Create distinct transcripts for meeting A and meeting B
    transcript_a = {
        "file_id": id_a,
        "language": "en",
        "segments": [
            {
                "segment_id": 0,
                "start": 0.0,
                "end": 4.0,
                "speaker": "Alice",
                "text": "We decided to migrate our database to PostgreSQL.",
                "language": "en",
            },
            {
                "segment_id": 1,
                "start": 4.5,
                "end": 8.0,
                "speaker": "Bob",
                "text": "Bob will deploy the PostgreSQL cluster by Friday.",
                "language": "en",
            }
        ],
        "full_text": "We decided to migrate our database to PostgreSQL. Bob will deploy the PostgreSQL cluster by Friday."
    }

    transcript_b = {
        "file_id": id_b,
        "language": "en",
        "segments": [
            {
                "segment_id": 0,
                "start": 0.0,
                "end": 4.0,
                "speaker": "Charlie",
                "text": "We decided to adopt Redis for session caching.",
                "language": "en",
            },
            {
                "segment_id": 1,
                "start": 4.5,
                "end": 8.0,
                "speaker": "Dave",
                "text": "Dave will configure Redis sentinel by Monday.",
                "language": "en",
            }
        ],
        "full_text": "We decided to adopt Redis for session caching. Dave will configure Redis sentinel by Monday."
    }

    # Write transcripts to their respective meeting storages
    storage_a.path_for("transcript", "frontend_transcript.json").write_text(
        json.dumps(transcript_a), encoding="utf-8"
    )
    (storage_a.root / "frontend_transcript.json").write_text(
        json.dumps(transcript_a), encoding="utf-8"
    )

    storage_b.path_for("transcript", "frontend_transcript.json").write_text(
        json.dumps(transcript_b), encoding="utf-8"
    )
    (storage_b.root / "frontend_transcript.json").write_text(
        json.dumps(transcript_b), encoding="utf-8"
    )

    # 1. Run extraction for meeting A
    res_extract_a = extract_meeting_intelligence(id_a)
    assert res_extract_a["status"] == "extracted"

    # 2. Run extraction for meeting B
    res_extract_b = extract_meeting_intelligence(id_b)
    assert res_extract_b["status"] == "extracted"

    # Verify extraction outputs are in their isolated folders
    assert storage_a.exists("intelligence", "topics.json")
    assert storage_a.exists("intelligence", "decisions.json")
    assert storage_a.exists("intelligence", "action_items.json")
    assert storage_a.exists("intelligence", "extraction.json")

    assert storage_b.exists("intelligence", "topics.json")
    assert storage_b.exists("intelligence", "decisions.json")
    assert storage_b.exists("intelligence", "action_items.json")
    assert storage_b.exists("intelligence", "extraction.json")

    # Verify content independence
    data_extract_a = json.loads(storage_a.path_for("intelligence", "extraction.json").read_text(encoding="utf-8"))
    data_extract_b = json.loads(storage_b.path_for("intelligence", "extraction.json").read_text(encoding="utf-8"))

    assert data_extract_a["file_id"] == id_a
    assert data_extract_b["file_id"] == id_b
    assert "PostgreSQL" in json.dumps(data_extract_a)
    assert "PostgreSQL" not in json.dumps(data_extract_b)
    assert "Redis" in json.dumps(data_extract_b)
    assert "Redis" not in json.dumps(data_extract_a)

    # 3. Run verification for both
    res_verify_a = verify_meeting_intelligence(id_a)
    assert "status" in res_verify_a

    res_verify_b = verify_meeting_intelligence(id_b)
    assert "status" in res_verify_b

    assert storage_a.exists("verification", "verified_intelligence.json")
    assert storage_a.exists("verification", "verification_summary.json")

    assert storage_b.exists("verification", "verified_intelligence.json")
    assert storage_b.exists("verification", "verification_summary.json")

    # 4. Generate MoM and PDF for both
    res_mom_a = generate_mom(id_a)
    assert res_mom_a["status"] == "mom_generated"

    res_mom_b = generate_mom(id_b)
    assert res_mom_b["status"] == "mom_generated"

    assert storage_a.exists("mom", "mom.json")
    assert storage_a.exists("mom", "Meet2MoM-Meeting-Minutes.pdf")

    assert storage_b.exists("mom", "mom.json")
    assert storage_b.exists("mom", "Meet2MoM-Meeting-Minutes.pdf")

    # Confirm NO shared output directory writes
    global_output = PROJECT_ROOT / "output"
    if global_output.exists():
        assert not (global_output / "transcripts" / "meeting_transcript_ready.json").exists()
        assert not (global_output / "intelligence").exists()
        assert not (global_output / "mom").exists()

    # Clean up test directories
    shutil.rmtree(storage_a.root, ignore_errors=True)
    shutil.rmtree(storage_b.root, ignore_errors=True)
