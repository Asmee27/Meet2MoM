import pytest
from pathlib import Path

from app.core.config import STORAGE_ROOT
from app.core.storage import MeetingStorage


def test_storage_properties():
    meeting_id = "test_meeting_001"
    storage = MeetingStorage(meeting_id)

    assert storage.root == STORAGE_ROOT / meeting_id
    assert storage.recording == STORAGE_ROOT / meeting_id / "recording"
    assert storage.audio == STORAGE_ROOT / meeting_id / "audio"
    assert storage.chunks == STORAGE_ROOT / meeting_id / "chunks"
    assert storage.transcript == STORAGE_ROOT / meeting_id / "transcript"
    assert storage.intelligence == STORAGE_ROOT / meeting_id / "intelligence"
    assert storage.verification == STORAGE_ROOT / meeting_id / "verification"
    assert storage.mom == STORAGE_ROOT / meeting_id / "mom"


import shutil
from uuid import uuid4

def test_storage_ensure_dirs_and_path_for():
    meeting_id = f"test_{uuid4().hex}"
    storage = MeetingStorage(meeting_id)
    try:
        storage.ensure_dirs()

        assert storage.root.exists()
        assert storage.recording.exists()
        assert storage.audio.exists()
        assert storage.chunks.exists()
        assert storage.transcript.exists()
        assert storage.intelligence.exists()
        assert storage.verification.exists()
        assert storage.mom.exists()

        # Test path_for
        p = storage.path_for("transcript", "meeting_transcript_ready.json")
        assert p == storage.transcript / "meeting_transcript_ready.json"
        assert not storage.exists("transcript", "meeting_transcript_ready.json")

        # Write file and test exists
        p.write_text("{}", encoding="utf-8")
        assert storage.exists("transcript", "meeting_transcript_ready.json")
    finally:
        shutil.rmtree(storage.root, ignore_errors=True)


def test_storage_path_traversal_guard():
    storage = MeetingStorage("m_safe")
    
    # Path traversal outside meeting root must raise ValueError
    with pytest.raises(ValueError, match="Path traversal detected"):
        storage.path_for("root", "../evil.json")

    with pytest.raises(ValueError, match="Path traversal detected"):
        storage.path_for("transcript", "../../escape.json")


def test_invalid_meeting_id():
    with pytest.raises(ValueError, match="Invalid meeting_id"):
        MeetingStorage("")

    with pytest.raises(ValueError, match="Invalid meeting_id"):
        MeetingStorage("../sneaky")

    with pytest.raises(ValueError, match="Invalid meeting_id"):
        MeetingStorage("a/b")

    with pytest.raises(ValueError, match="Invalid meeting_id"):
        MeetingStorage("a\\b")
