from __future__ import annotations

"""
owner_resolver.py
=================
Deterministic owner resolution for action items using diarization-first rules
and fuzzy matching.
"""

import re
from typing import List, Optional, Tuple
from rapidfuzz import fuzz

from app.schemas.claim import ActionItem
from app.schemas.utterance import Utterance


def resolve_owner(
    action: ActionItem,
    utterances: List[Utterance],
) -> Tuple[Optional[str], str, float]:
    """
    Resolve action item owner following diarization-first principles.

    Returns:
      (owner_name_or_None, owner_type, confidence)
    """
    owner_type = action.owner_type
    evidence_ids = set(action.evidence or [])

    # Map evidence utterances
    evidence_utts = [u for u in utterances if u.utt_id in evidence_ids]

    # -----------------------------------------------------------------------
    # 1. GROUP / UNASSIGNED
    # -----------------------------------------------------------------------
    if owner_type in ("group", "unassigned"):
        # Confidently unassigned is a valid, useful answer
        return None, owner_type, 1.0

    # -----------------------------------------------------------------------
    # 2. EXPLICIT FIRST PERSON ("I'll...", "मैं करूँगा")
    # -----------------------------------------------------------------------
    if owner_type == "explicit_first_person":
        # Owner is the SPEAKER of the evidence utterance from diarization,
        # NOT the name the LLM guessed.
        if evidence_utts:
            primary_utt = evidence_utts[0]
            owner = primary_utt.speaker_name or primary_utt.speaker_id
            conf = primary_utt.spk_conf if primary_utt.spk_conf is not None else 0.5
            return owner, "explicit_first_person", conf

        # Fallback if evidence not found in utterances
        return action.owner, "explicit_first_person", 0.5

    # -----------------------------------------------------------------------
    # 3. EXPLICIT THIRD PERSON ("Anna will...")
    # -----------------------------------------------------------------------
    if owner_type == "explicit_third_person":
        llm_name = (action.owner or "").strip()
        if not llm_name:
            return None, "contextual", 0.425

        base_conf = 0.85

        # Check if name appears verbatim in the evidence text
        evidence_text = " ".join(u.text for u in evidence_utts)
        # Use word boundary or simple substring if punctuated
        verbatim_match = bool(
            re.search(rf"\b{re.escape(llm_name)}\b", evidence_text, re.IGNORECASE)
        )

        # Check if fuzzy-matches a known speaker_name in the meeting (rapidfuzz ratio >= 85)
        known_speakers = [
            u.speaker_name for u in utterances
            if u.speaker_name and u.speaker_name.strip()
        ]

        fuzzy_matched_speaker = None
        for spk in known_speakers:
            if fuzz.ratio(llm_name.lower(), spk.lower()) >= 85:
                fuzzy_matched_speaker = spk
                break

        if verbatim_match or fuzzy_matched_speaker:
            final_owner = fuzzy_matched_speaker or llm_name
            return final_owner, "explicit_third_person", base_conf
        else:
            # Downgrade owner_type to "contextual" and halve confidence
            return llm_name, "contextual", round(base_conf / 2.0, 3)

    # -----------------------------------------------------------------------
    # 4. CONTEXTUAL / DEFAULT
    # -----------------------------------------------------------------------
    return action.owner, "contextual", 0.50
