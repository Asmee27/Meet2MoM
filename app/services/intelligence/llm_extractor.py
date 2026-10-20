from __future__ import annotations

"""
llm_extractor.py
================
Structured meeting intelligence extractor powered by LLMClient.
Extracts decisions and action items from windowed transcript blocks with mandatory
evidence citations pointing to exact utterance IDs.
"""

import hashlib
import json
import logging
import re
import string
from pathlib import Path
from typing import Any, Dict, List, Optional

from rapidfuzz import fuzz

from app.core.llm_client import LLMClient, LLMOutputError
from app.core.storage import MeetingStorage
from app.services.intelligence.context_builder import ExtractionWindow

logger = logging.getLogger(__name__)

# v2: adds cited discussion_points; responses are schema-checked before caching.
PROMPT_VERSION = "v2"
_LINE_PATTERN = re.compile(r"^\[(u\d{5})\]\s*(.*)$")
# How closely an LLM deadline must match the cited words to be kept.
DEADLINE_MATCH_MIN = 90

SYSTEM_PROMPT = """You extract decisions and action items from a meeting transcript.
Every line is prefixed with an utterance id like [u00042].
EVERY item you output MUST include an "evidence" array of utterance ids copied EXACTLY from the transcript above. Never invent an id. Never guess an id.
If you cannot cite a real id for an item, DO NOT output that item.
If the window contains no decisions and no action items, return {"decisions": [], "actions": [], "discussion_points": []}.
Only set "owner", "proposer" and "deadline_text" when the cited utterances state them. Copy deadline_text from the transcript words; otherwise use null. Never infer a date, owner or proposer that is not said.
"discussion_points" are short neutral summaries of substantive topics discussed (skip small talk), each citing the utterances it summarises. Do not add facts that are not in the cited utterances.
Do not output deadline_type, deadline_resolved, or claim ids.
Output ONLY JSON. No prose, no markdown fences.

Schema:
{
  "decisions": [
    {
      "text": "string description of decision",
      "state": "proposed" | "accepted" | "rejected" | "deferred" | "conditional",
      "proposer": "string name or null",
      "conditional": boolean,
      "condition_text": "string condition or null",
      "uncertainty": "low" | "medium" | "high",
      "evidence": ["u00001", ...]
    }
  ],
  "actions": [
    {
      "text": "string task description",
      "owner": "string owner or null",
      "owner_type": "explicit_first_person" | "explicit_third_person" | "contextual" | "group" | "unassigned",
      "deadline_text": "string deadline or null",
      "commitment_type": "firm" | "tentative" | "hypothetical" | "assigned_to_absent" | "past_action",
      "evidence": ["u00002", ...]
    }
  ],
  "discussion_points": [
    {
      "text": "string summary of what was discussed",
      "evidence": ["u00001", ...]
    }
  ]
}

Few-shot Example 1:
Transcript:
[u00010] SPK_01 (01:15) Alice: We should migrate our primary database to PostgreSQL.
[u00011] SPK_02 (01:25) Bob: Agreed, let's do PostgreSQL. I will set up the cluster by Friday.
Output:
{
  "decisions": [
    {
      "text": "Migrate primary database to PostgreSQL",
      "state": "accepted",
      "proposer": "Alice",
      "conditional": false,
      "condition_text": null,
      "uncertainty": "low",
      "evidence": ["u00010", "u00011"]
    }
  ],
  "actions": [
    {
      "text": "Set up PostgreSQL cluster",
      "owner": "Bob",
      "owner_type": "explicit_first_person",
      "deadline_text": "by Friday",
      "commitment_type": "firm",
      "evidence": ["u00011"]
    }
  ],
  "discussion_points": [
    {
      "text": "Alice proposed moving the primary database to PostgreSQL and Bob agreed",
      "evidence": ["u00010", "u00011"]
    }
  ]
}

Few-shot Example 2:
Transcript:
[u00020] SPK_01 (03:00) Alice: How is the weather over there today?
[u00021] SPK_02 (03:05) Bob: It has been raining all morning, pretty cold.
Output:
{
  "decisions": [],
  "actions": [],
  "discussion_points": []
}
"""


def _compute_cache_key(window: ExtractionWindow, model: str) -> str:
    combined = f"{window.text_block}|{model}|{PROMPT_VERSION}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def validate_response(data: Any) -> Dict[str, Any]:
    """
    Check the response shape before anything is cached or trusted. A reply
    that is valid JSON but not this schema (prose wrapped as {"result": ...},
    a bare list, wrong field types) is invalid output, not "no claims".
    """
    if not isinstance(data, dict) or not ({"decisions", "actions"} & set(data)):
        raise LLMOutputError("LLM response does not match the extraction schema")
    for key in ("decisions", "actions", "discussion_points"):
        value = data.get(key, [])
        if value is None:
            data[key] = []
        elif not isinstance(value, list):
            raise LLMOutputError(f"LLM response field '{key}' is not a list")
    return data


def extract_from_window(
    window: ExtractionWindow,
    client: LLMClient,
    report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Extract decisions and action items from a single ExtractionWindow.
    Checks disk cache under storage/<meeting_id>/intelligence/llm_cache/.
    Attaches window_id and meeting_id to every extracted item.
    """
    storage = MeetingStorage(window.meeting_id)
    cache_dir = storage.root / "intelligence" / "llm_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    cache_key = _compute_cache_key(window, client.model)
    cache_file = cache_dir / f"{cache_key}.json"

    # Cache hit check
    if cache_file.exists():
        try:
            cached_data = validate_response(json.loads(cache_file.read_text(encoding="utf-8")))
            logger.debug("Cache hit for window %s (%s)", window.window_id, cache_key[:8])
            return _ground(_attach_provenance(cached_data, window), window, report)
        except Exception:
            pass  # Corrupt or outdated cache: fall through to LLM call

    user_prompt = f"Transcript Window [{window.window_id}]:\n{window.text_block}"
    raw_response = validate_response(client.complete_json(system=SYSTEM_PROMPT, user=user_prompt))

    # Persist the schema-valid raw response to cache
    try:
        cache_file.write_text(json.dumps(raw_response, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to write LLM cache for window %s: %s", window.window_id, e)

    return _ground(_attach_provenance(raw_response, window), window, report)


def _attach_provenance(data: Dict[str, Any], window: ExtractionWindow) -> Dict[str, Any]:
    """Ensure window_id and meeting_id are attached to all items."""
    decisions = data.get("decisions", [])
    actions = data.get("actions", [])

    for d in decisions:
        if isinstance(d, dict):
            d["window_id"] = window.window_id
            d["meeting_id"] = window.meeting_id
            d["claim_type"] = "decision"

    for a in actions:
        if isinstance(a, dict):
            a["window_id"] = window.window_id
            a["meeting_id"] = window.meeting_id
            a["claim_type"] = "action"

    return {
        "decisions": decisions,
        "actions": actions,
        "discussion_points": data.get("discussion_points", []),
        "window_id": window.window_id,
        "meeting_id": window.meeting_id,
    }


# ---------------------------------------------------------------------------
# Grounding: fields the LLM states must be present in what it cites
# ---------------------------------------------------------------------------

def _norm(text: Any) -> str:
    t = str(text or "").lower().translate(str.maketrans("", "", string.punctuation))
    return " ".join(t.split())


def _window_lines(window: ExtractionWindow) -> Dict[str, str]:
    lines: Dict[str, str] = {}
    for line in window.text_block.splitlines():
        match = _LINE_PATTERN.match(line.strip())
        if match:
            lines[match.group(1)] = match.group(2)
    return lines


def _cited_text(item: Dict[str, Any], lines: Dict[str, str]) -> str:
    evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
    return " ".join(lines.get(str(u), "") for u in evidence)


def _phrase_in(phrase: str, text: str) -> bool:
    p, t = _norm(phrase), _norm(text)
    if not p or not t:
        return False
    return p in t or fuzz.partial_ratio(p, t) >= DEADLINE_MATCH_MIN


def _ground(result: Dict[str, Any], window: ExtractionWindow, report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Remove details the LLM added that its own citations do not contain:
    a deadline must appear in the cited words, a decision proposer must be
    named or be a speaker in the cited lines. The claim itself is kept and
    still goes through span validation and verification. Discussion points
    survive only if every citation is an utterance of this window.
    """
    lines = _window_lines(window)
    removed = []

    for action in result["actions"]:
        if not isinstance(action, dict):
            continue
        deadline = action.get("deadline_text")
        if deadline and not _phrase_in(str(deadline), _cited_text(action, lines)):
            removed.append({"window_id": window.window_id, "claim_type": "action",
                            "field": "deadline_text", "text": str(action.get("text", ""))[:120]})
            action["deadline_text"] = None

    for decision in result["decisions"]:
        if not isinstance(decision, dict):
            continue
        proposer = decision.get("proposer")
        if proposer and not re.search(
            rf"\b{re.escape(str(proposer).strip())}\b", _cited_text(decision, lines), re.IGNORECASE
        ):
            removed.append({"window_id": window.window_id, "claim_type": "decision",
                            "field": "proposer", "text": str(decision.get("text", ""))[:120]})
            decision["proposer"] = None

    points = []
    dropped_points = 0
    window_ids = set(window.utt_ids)
    for point in result.get("discussion_points") or []:
        evidence = point.get("evidence") if isinstance(point, dict) else None
        text = str(point.get("text") or "").strip() if isinstance(point, dict) else ""
        if (
            text
            and isinstance(evidence, list)
            and evidence
            and all(isinstance(u, str) and u in window_ids for u in evidence)
        ):
            points.append({"text": text, "evidence": list(dict.fromkeys(evidence)), "window_id": window.window_id})
        else:
            dropped_points += 1
    result["discussion_points"] = points

    if report is not None:
        report.setdefault("ungrounded_fields_removed", []).extend(removed)
        report.setdefault("discussion_points", []).extend(points)
        report["discussion_points_dropped"] = report.get("discussion_points_dropped", 0) + dropped_points
    return result


def extract_meeting(
    meeting_id: str,
    windows: List[ExtractionWindow],
    client: LLMClient,
    report: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Extract decisions and action items across all windows of a meeting.
    Returns a unified flat list of raw candidate dictionaries with window_id attached.
    If `report` is given it receives the grounding report and the validated
    discussion points.
    """
    candidates: List[Dict[str, Any]] = []

    for window in windows:
        window_result = extract_from_window(window, client, report)
        for d in window_result.get("decisions", []):
            if isinstance(d, dict):
                candidates.append(d)
        for a in window_result.get("actions", []):
            if isinstance(a, dict):
                candidates.append(a)

    return candidates
