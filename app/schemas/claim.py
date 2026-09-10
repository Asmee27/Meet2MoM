from __future__ import annotations

import re
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator


UTT_ID_PATTERN = re.compile(r"^u\d{5}$")


class ClaimBase(BaseModel):
    claim_id: str
    meeting_id: str
    text: str
    evidence: list[str] = Field(min_length=1)

    @field_validator("evidence")
    @classmethod
    def validate_evidence(cls, v: list[str]) -> list[str]:
        for utt_id in v:
            if not UTT_ID_PATTERN.match(utt_id):
                raise ValueError(f"evidence entry '{utt_id}' must match pattern ^u\\d{{5}}$")
        return v


class Decision(ClaimBase):
    claim_type: Literal["decision"] = "decision"
    state: Literal["proposed", "accepted", "rejected", "deferred", "conditional"]
    proposer: Optional[str] = None
    conditional: bool = False
    condition_text: Optional[str] = None
    uncertainty: Literal["low", "medium", "high"] = "low"


class ActionItem(ClaimBase):
    claim_type: Literal["action"] = "action"
    owner: Optional[str] = None
    owner_type: Literal[
        "explicit_first_person",
        "explicit_third_person",
        "contextual",
        "group",
        "unassigned",
    ]
    deadline_text: Optional[str] = None
    deadline_type: Literal[
        "absolute",
        "relative",
        "event_anchored",
        "recurring",
        "none",
    ] = "none"
    deadline_resolved: Optional[str] = None
    commitment_type: Literal[
        "firm",
        "tentative",
        "hypothetical",
        "assigned_to_absent",
        "past_action",
    ] = "firm"
    resolution_conf: Optional[dict[str, float]] = None
