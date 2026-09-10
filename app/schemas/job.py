from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel


class JobStatus(BaseModel):
    meeting_id: str
    stage: str
    progress: float
    state: Literal["queued", "running", "done", "failed"]
    error: Optional[str] = None
    updated_at: datetime
