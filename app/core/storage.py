from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from app.core.config import STORAGE_ROOT

MEETING_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def is_valid_meeting_id(meeting_id: str) -> bool:
    return bool(meeting_id) and MEETING_ID_RE.fullmatch(meeting_id) is not None


class MeetingStorage:
    """
    Manages isolated per-meeting directory structure and file paths.

    Guarantees:
    - All paths are derived from STORAGE_ROOT in config.py
    - Paths cannot escape storage/<meeting_id>/
    - Dedicated directories for audio, chunks, transcript, intelligence,
      verification, and mom
    """

    def __init__(self, meeting_id: str):
        cleaned_id = str(meeting_id or "")
        if not is_valid_meeting_id(cleaned_id):
            raise ValueError(f"Invalid meeting_id: '{meeting_id}'")
        self.meeting_id: str = cleaned_id

    @property
    def root(self) -> Path:
        return STORAGE_ROOT / self.meeting_id

    @property
    def recording(self) -> Path:
        return self.root / "recording"

    @property
    def audio(self) -> Path:
        return self.root / "audio"

    @property
    def chunks(self) -> Path:
        return self.root / "chunks"

    @property
    def transcript(self) -> Path:
        return self.root / "transcript"

    @property
    def intelligence(self) -> Path:
        return self.root / "intelligence"

    @property
    def verification(self) -> Path:
        return self.root / "verification"

    @property
    def mom(self) -> Path:
        return self.root / "mom"

    @property
    def metadata(self) -> Path:
        return self.root / "metadata"

    @property
    def diarization(self) -> Path:
        return self.root / "diarization"

    def _guard(self, target_path: Path) -> Path:
        resolved_root = self.root.resolve()
        resolved_target = target_path.resolve()
        try:
            resolved_target.relative_to(resolved_root)
        except ValueError:
            raise ValueError(
                f"Path traversal detected: '{target_path}' escapes meeting root '{self.root}'"
            )
        return resolved_target

    def ensure_dirs(self) -> None:
        """Create all subdirectories for this meeting."""
        self.root.mkdir(parents=True, exist_ok=True)
        self.recording.mkdir(parents=True, exist_ok=True)
        self.audio.mkdir(parents=True, exist_ok=True)
        self.chunks.mkdir(parents=True, exist_ok=True)
        self.transcript.mkdir(parents=True, exist_ok=True)
        self.intelligence.mkdir(parents=True, exist_ok=True)
        self.verification.mkdir(parents=True, exist_ok=True)
        self.mom.mkdir(parents=True, exist_ok=True)
        self.metadata.mkdir(parents=True, exist_ok=True)
        self.diarization.mkdir(parents=True, exist_ok=True)

    def path_for(self, category: str, filename: str) -> Path:
        cat = category.lower().strip()
        cat_map = {
            "root": self.root,
            "recording": self.recording,
            "recordings": self.recording,
            "audio": self.audio,
            "chunks": self.chunks,
            "chunk": self.chunks,
            "transcript": self.transcript,
            "transcripts": self.transcript,
            "intelligence": self.intelligence,
            "verification": self.verification,
            "mom": self.mom,
            "metadata": self.metadata,
            "diarization": self.diarization,
        }

        base_dir = cat_map.get(cat, self.root / cat)
        target = base_dir / filename if filename else base_dir
        return self._guard(target)

    def exists(self, category: str, filename: str) -> bool:
        return self.path_for(category, filename).exists()
