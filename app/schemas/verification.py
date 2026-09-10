from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


class VerificationResult(BaseModel):
    claim_id: str
    status: Literal["verified", "verified_with_warning", "review", "rejected"]
    entail_score: float
    contradict_score: float
    fused_score: float
    best_span: Optional[str] = None
    signals: dict[str, float] = Field(default_factory=dict)
