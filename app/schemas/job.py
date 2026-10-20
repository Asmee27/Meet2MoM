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
    heartbeat_at: Optional[datetime] = None
    retry_count: int = 0
    file_path: Optional[str] = None
    claim_token: Optional[str] = None

