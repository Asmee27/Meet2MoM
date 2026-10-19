from __future__ import annotations

"""
transcript_index.py
===================
Builds a deterministic, sorted, utt_id-keyed index over the merged transcript
produced by speaker_merger.py, persists it to the DB and to JSON, and provides
window/span helpers for downstream intelligence services.

Field mapping from meeting_transcript_ready.json
-------------------------------------------------
  asr_conf  <- asr_confidence (str: "high"|"medium"|"low")  -- mapped to float
  spk_conf  <- speaker_attribution_confidence (float 0-1)
  overlap   <- possible_overlap (bool)
  speaker_id<- speaker (e.g. "SPEAKER_00")
  lang_tags <- [language] (ISO code from the segment)
  script    <- classify_script_usage(text)
"""

import json
import logging
from pathlib import Path
from typing import List, Tuple

from app.core import db as _db
from app.core.config import STORAGE_ROOT
from app.schemas.utterance import Utterance
from app.services.transcription.language_utils import classify_script_usage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ASR confidence string → float mapping
# ---------------------------------------------------------------------------

_ASR_CONF_MAP: dict[str, float] = {
    "high":   0.90,
    "medium": 0.70,
    "low":    0.40,
    "normal": 0.85,  # transcriber uses "confidence_flag": "normal"
}


def _asr_conf_from_logprob(avg_logprob: float | None) -> float | None:
    if avg_logprob is None:
        return None
    # map [-1.0, 0.0] -> [0.0, 1.0], clamped
    return max(0.0, min(1.0, 1.0 + float(avg_logprob)))


def _resolve_asr_conf(seg: dict, utt_id: str) -> tuple[float | None, str]:
    """
    Resolve asr_conf and conf_source for a segment.
    Priority order: numeric avg_logprob -> string flag fallback -> None.
    Never invent a value.
    """
    avg_logprob = seg.get("avg_logprob")
    if avg_logprob is not None:
        try:
            val = _asr_conf_from_logprob(float(avg_logprob))
            if val is not None:
                return val, "numeric"
        except (ValueError, TypeError):
            pass

    # String flag fallback
    raw_flag = seg.get("asr_confidence") or seg.get("confidence_flag")
    if isinstance(raw_flag, str):
        mapped = _ASR_CONF_MAP.get(raw_flag.lower())
        if mapped is not None:
            return mapped, "bucketed"

    # Missing
    logger.warning("Utterance %s: field 'asr_conf' is missing; storing None", utt_id)
    return None, "missing"


def _resolve_spk_conf(seg: dict, utt_id: str) -> float | None:
    """
    Validate and return speaker_attribution_confidence, or None if missing.
    Never invent a value.
    """
    raw_spk = (
        seg.get("speaker_attribution_confidence")
        if seg.get("speaker_attribution_confidence") is not None
        else seg.get("spk_conf")
    )
    if raw_spk is not None:
        try:
            val = float(raw_spk)
            if 0.0 <= val <= 1.0:
                return val
        except (ValueError, TypeError):
            pass

    logger.warning("Utterance %s: field 'spk_conf' is missing; storing None", utt_id)
    return None


def _resolve_no_speech_prob(seg: dict, utt_id: str) -> float | None:
    raw_value = seg.get("no_speech_prob")
    if raw_value is None:
        raw_value = seg.get("no_speech_probability")
    if raw_value is not None:
        try:
            value = float(raw_value)
            if 0.0 <= value <= 1.0:
                return value
        except (ValueError, TypeError):
            pass
    return None


def _map_script(text: str) -> str:
    """Map language_utils script classification to Utterance Literal."""
    raw = classify_script_usage(text)
    if raw == "mixed_devanagari_latin":
        return "mixed"
    if raw == "devanagari":
        return "devanagari"
    return "latin"


# ---------------------------------------------------------------------------
# PUBLIC: build_transcript_index
# ---------------------------------------------------------------------------

def build_transcript_index(
    merged_transcript: dict | list,
    meeting_id: str,
) -> list[Utterance]:
    """
    Sort merged segments by (start, end, speaker) and assign deterministic
    utt_ids starting at u00000.

    Parameters
    ----------
    merged_transcript:
        The parsed JSON from meeting_transcript_ready.json.  May be a list of
        segments or a dict containing a "segments" / "transcript" key.
    meeting_id:
        The 32-hex meeting identifier.

    Returns
    -------
    A list of validated Utterance models in start-time order.
    """
    # Normalise to list of segment dicts
    if isinstance(merged_transcript, list):
        raw_segments = merged_transcript
    elif isinstance(merged_transcript, dict):
        for key in ("segments", "transcript", "utterances", "speaker_transcript"):
            if isinstance(merged_transcript.get(key), list):
                raw_segments = merged_transcript[key]
                break
        else:
            # The dict itself may be a flat mapping id->segment; try values
            raw_segments = list(merged_transcript.values())
    else:
        raw_segments = []

    # Filter to dicts with a non-empty text field
    valid_segments = [
        seg for seg in raw_segments
        if isinstance(seg, dict) and str(seg.get("text") or "").strip()
    ]

    # Sort strictly: start ASC, end ASC, speaker ASC
    sorted_segments = sorted(
        valid_segments,
        key=lambda s: (
            float(s.get("start", 0)),
            float(s.get("end", 0)),
            str(s.get("speaker") or s.get("speaker_id") or ""),
        ),
    )

    utterances: list[Utterance] = []

    for i, seg in enumerate(sorted_segments):
        utt_id = f"u{i:05d}"

        text = str(seg.get("text", "")).strip()

        # --- Timestamps ---
        start = float(seg.get("start", 0.0))
        end   = float(seg.get("end", start + 0.001))
        if end <= start:
            end = start + 0.001

        # --- Speaker ---
        speaker_id = (
            str(seg.get("speaker") or seg.get("speaker_id") or "SPEAKER_UNKNOWN")
        )
        # Display name, present when identity came from a LiveKit participant track.
        speaker_name = str(seg["speaker_name"]).strip() if seg.get("speaker_name") else None

        # --- ASR confidence & source ---
        asr_conf, conf_source = _resolve_asr_conf(seg, utt_id)

        # --- Speaker confidence ---
        spk_conf = _resolve_spk_conf(seg, utt_id)
        no_speech_prob = _resolve_no_speech_prob(seg, utt_id)

        # --- Overlap ---
        overlap = bool(seg.get("possible_overlap", False))

        # --- Language tags ---
        lang = seg.get("language") or seg.get("detected_language")
        lang_tags: list[str] = [lang] if lang else []

        # --- Script ---
        script = _map_script(text)

        utterances.append(
            Utterance(
                meeting_id=meeting_id,
                utt_id=utt_id,
                start=start,
                end=end,
                speaker_id=speaker_id,
                speaker_name=speaker_name or None,
                text=text,
                lang_tags=lang_tags,
                script=script,
                asr_conf=asr_conf,
                spk_conf=spk_conf,
                no_speech_prob=no_speech_prob,
                conf_source=conf_source,
                overlap=overlap,
                snr=None,
                topic_id=None,
            )
        )

    return utterances


# ---------------------------------------------------------------------------
# PUBLIC: persist_transcript_index
# ---------------------------------------------------------------------------

def persist_transcript_index(
    utterances: list[Utterance],
    meeting_id: str,
) -> Path:
    """
    Persist utterances to:
      1. SQLite DB  (via bulk_insert_utterances)
      2. storage/<meeting_id>/transcript/transcript_index.json
    Returns the path to the JSON file.
    """
    # 1. Replace prior extraction state before replacing utterances.
    _db.init_db()
    _db.ensure_meeting(meeting_id, status="indexed")
    _db.delete_utterances_for_meeting(meeting_id)
    _db.bulk_insert_utterances(meeting_id, utterances)

    # 2. JSON
    index_dir = STORAGE_ROOT / meeting_id / "transcript"
    index_dir.mkdir(parents=True, exist_ok=True)
    index_path = index_dir / "transcript_index.json"

    payload = [u.model_dump() for u in utterances]
    index_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    logger.info(
        "Persisted %d utterances for meeting %s to DB and %s",
        len(utterances),
        meeting_id,
        index_path,
    )
    return index_path


# ---------------------------------------------------------------------------
# PUBLIC: get_utterance_window
# ---------------------------------------------------------------------------

def get_utterance_window(
    meeting_id: str,
    start_utt: str,
    end_utt: str,
) -> list[Utterance]:
    """
    Return utterances whose utt_id falls in the inclusive range
    [start_utt, end_utt] by numeric index.

    E.g. get_utterance_window(mid, "u00003", "u00007")
    """
    all_utts = _db.get_utterances(meeting_id)

    def _idx(utt_id: str) -> int:
        try:
            return int(utt_id[1:])
        except (ValueError, IndexError):
            return -1

    lo = _idx(start_utt)
    hi = _idx(end_utt)

    return [u for u in all_utts if lo <= _idx(u.utt_id) <= hi]


# ---------------------------------------------------------------------------
# PUBLIC: resolve_spans
# ---------------------------------------------------------------------------

def resolve_spans(
    meeting_id: str,
    utt_ids: list[str],
) -> Tuple[List[Utterance], List[str]]:
    """
    Resolve a list of utt_ids to Utterance models.

    Returns
    -------
    (found, missing)
      found   — Utterance models for every utt_id that exists in the DB
      missing — utt_ids that are NOT in the DB
    """
    all_utts = _db.get_utterances(meeting_id)
    by_id = {u.utt_id: u for u in all_utts}

    found: list[Utterance] = []
    missing: list[str] = []

    for uid in utt_ids:
        if uid in by_id:
            found.append(by_id[uid])
        else:
            missing.append(uid)

    return found, missing
