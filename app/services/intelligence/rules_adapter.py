from __future__ import annotations

"""
rules_adapter.py
================
Adapter mapping existing rule-based DecisionExtractor and ActionItemExtractor
outputs into the validated candidate claim shape used by extraction_pipeline.
Establishes the rule-based baseline without altering existing extractor code.
"""

import json
import logging
import string
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core import db as _db
from app.core.storage import MeetingStorage
from app.services.intelligence.action_item_extractor import ActionItemExtractor
from app.services.intelligence.context_builder import ExtractionWindow
from app.services.intelligence.decision_extractor import DecisionExtractor

logger = logging.getLogger(__name__)


def _normalise_text(t: str) -> str:
    """Normalize text for fuzzy containment matching."""
    s = t.lower()
    s = s.translate(str.maketrans("", "", string.punctuation))
    return " ".join(s.split())


def _find_utt_id(
    record: Dict[str, Any],
    utts: List[Any],
    windows: List[ExtractionWindow],
) -> tuple[List[str], str]:
    """
    Derive utt_ids by matching the extractor's source segment/timestamp back
    to the transcript index.
    Prefer exact timestamp match; fallback to normalized text containment.
    Returns (evidence_list, window_id).
    """
    ts = record.get("timestamp")
    evidence_text = str(
        record.get("evidence_text")
        or record.get("original_proposal")
        or record.get("text")
        or record.get("task")
        or ""
    )
    norm_ev = _normalise_text(evidence_text)

    matched_uid: Optional[str] = None

    candidate_utts = [
        u
        for window in windows
        for u in utts
        if u.utt_id in window.utt_ids
    ]
    # Preserve transcript order while removing overlap duplicates.
    candidate_utts = list({u.utt_id: u for u in candidate_utts}.values())

    # 1. Exact / close timestamp match
    if ts is not None:
        try:
            target_ts = float(ts)
            for u in candidate_utts:
                if abs(u.start - target_ts) <= 0.01:
                    matched_uid = u.utt_id
                    break
            if not matched_uid:
                for u in candidate_utts:
                    if u.start <= target_ts <= u.end:
                        matched_uid = u.utt_id
                        break
        except (ValueError, TypeError):
            pass

    # 2. Text match within utterances if timestamp didn't match
    if not matched_uid and norm_ev:
        for u in candidate_utts:
            norm_u = _normalise_text(u.text)
            if norm_ev in norm_u or (len(norm_ev) >= 8 and norm_u in norm_ev):
                matched_uid = u.utt_id
                break

    if not matched_uid:
        # NO utt_id can be derived -> evidence: [] (validator drops as EMPTY_EVIDENCE)
        fallback_wid = windows[0].window_id if windows else "w000"
        return [], fallback_wid

    # Find the extraction window that contains this utt_id
    assigned_wid = windows[0].window_id if windows else "w000"
    for w in windows:
        if matched_uid in w.utt_ids:
            assigned_wid = w.window_id
            break

    return [matched_uid], assigned_wid


def extract_with_rules(
    meeting_id: str,
    windows: List[ExtractionWindow],
) -> List[Dict[str, Any]]:
    """
    Execute rule-based baseline extractors and map outputs to candidate claim dicts.
    """
    storage = MeetingStorage(meeting_id)
    all_utts = _db.get_utterances(meeting_id)

    # 1. Ensure topics.json is available for DecisionExtractor
    topics_path = storage.path_for("intelligence", "topics.json")
    if not topics_path.exists():
        topics_data = [
            {
                "topic_id": w.topic_id or w.window_id,
                "topic_label": f"Topic {w.window_id}",
                "start": w.start_time,
                "end": w.end_time,
                "text": w.text_block,
                "speakers": [],
            }
            for w in windows
        ]
        topics_path.parent.mkdir(parents=True, exist_ok=True)
        topics_path.write_text(json.dumps(topics_data, indent=2), encoding="utf-8")

    # 2. Ensure frontend_transcript.json is available for ActionItemExtractor
    transcript_path = storage.path_for("transcript", "frontend_transcript.json")
    if not transcript_path.exists():
        transcript_path = storage.root / "frontend_transcript.json"
    if not transcript_path.exists():
        transcript_path = storage.path_for("transcript", "frontend_transcript.json")
        t_data = {
            "segments": [
                {
                    "start": u.start,
                    "end": u.end,
                    "speaker": u.speaker_id,
                    "text": u.text,
                    "speaker_attribution_confidence": u.spk_conf,
                    "confidence_flag": "high" if (u.asr_conf or 0) >= 0.8 else "normal",
                }
                for u in all_utts
            ]
        }
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(json.dumps(t_data, indent=2), encoding="utf-8")

    # 3. Call existing DecisionExtractor unchanged
    dec_extractor = DecisionExtractor(output_dir=str(storage.intelligence))
    dec_file = dec_extractor.process(str(topics_path))
    dec_data = json.loads(dec_file.read_text(encoding="utf-8"))
    raw_decisions = dec_data.get("final_decisions", [])

    # 4. Call existing ActionItemExtractor unchanged
    act_extractor = ActionItemExtractor(output_dir=str(storage.intelligence))
    act_file = act_extractor.process(str(transcript_path), str(topics_path))
    act_data = json.loads(act_file.read_text(encoding="utf-8"))
    raw_actions = act_data.get("action_items", [])

    candidates: List[Dict[str, Any]] = []

    # Map decisions
    for d in raw_decisions:
        ev, wid = _find_utt_id(d, all_utts, windows)
        spk = d.get("speaker")
        proposer = spk if spk and spk != "UNKNOWN" else None
        conf = float(d.get("confidence", 0.9))
        uncertainty = "low" if conf >= 0.8 else ("medium" if conf >= 0.6 else "high")
        is_conditional = bool(d.get("conditional", False))

        cand = {
            "claim_type": "decision",
            "meeting_id": meeting_id,
            "window_id": wid,
            "text": str(d.get("text", "")).strip(),
            "state": "accepted",
            "proposer": proposer,
            "conditional": is_conditional,
            "condition_text": None,
            "uncertainty": uncertainty,
            "evidence": ev,
        }
        candidates.append(cand)

    # Map action items
    for a in raw_actions:
        ev, wid = _find_utt_id(a, all_utts, windows)
        raw_owner_type = str(a.get("owner_type", "")).lower()

        if raw_owner_type in ("speaker_commitment", "explicit_first_person"):
            owner_type = "explicit_first_person"
        elif raw_owner_type in ("explicit_name", "direct_assignment", "explicit_third_person"):
            owner_type = "explicit_third_person"
        elif raw_owner_type in ("group_commitment", "group"):
            owner_type = "group"
        elif raw_owner_type in ("unassigned", "none") or not a.get("owner"):
            owner_type = "unassigned"
        else:
            owner_type = "contextual"

        owner_val = a.get("owner")
        if owner_val in ("UNKNOWN", "GROUP", None):
            owner_val = None

        cand = {
            "claim_type": "action",
            "meeting_id": meeting_id,
            "window_id": wid,
            "text": str(a.get("task", "")).strip(),
            "owner": owner_val,
            "owner_type": owner_type,
            "deadline_text": a.get("deadline_text"),
            "commitment_type": "firm",
            "evidence": ev,
        }
        candidates.append(cand)

    return candidates
