from __future__ import annotations

import re
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


UTT_ID_PATTERN = re.compile(r"^u\d{5}$")


class Utterance(BaseModel):
    meeting_id: str
    utt_id: str
    start: float
    end: float
    speaker_id: str
    speaker_name: Optional[str] = None
    text: str
    lang_tags: list[str] = Field(default_factory=list)
    script: Literal["latin", "devanagari", "mixed"] = "latin"
    asr_conf: Optional[float] = Field(default=None, ge=0, le=1)
    spk_conf: Optional[float] = Field(default=None, ge=0, le=1)
    no_speech_prob: Optional[float] = Field(default=None, ge=0, le=1)
    conf_source: Literal["numeric", "bucketed", "missing"] = "missing"
    overlap: bool = False
    snr: Optional[float] = None
    topic_id: Optional[str] = None

    @field_validator("utt_id")
    @classmethod
    def validate_utt_id(cls, v: str) -> str:
        if not UTT_ID_PATTERN.match(v):
            raise ValueError(f"utt_id '{v}' must match pattern ^u\\d{{5}}$")
        return v

    @model_validator(mode="after")
    def validate_time_range(self) -> Utterance:
        if self.end <= self.start:
            raise ValueError(f"end ({self.end}) must be strictly greater than start ({self.start})")
        return self
