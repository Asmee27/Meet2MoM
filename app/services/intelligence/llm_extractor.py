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
from pathlib import Path
from typing import Any, Dict, List

from app.core.llm_client import LLMClient
from app.core.storage import MeetingStorage
from app.services.intelligence.context_builder import ExtractionWindow

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """You extract decisions and action items from a meeting transcript.
Every line is prefixed with an utterance id like [u00042].
EVERY item you output MUST include an "evidence" array of utterance ids copied EXACTLY from the transcript above. Never invent an id. Never guess an id.
If you cannot cite a real id for an item, DO NOT output that item.
If the window contains no decisions and no action items, return {"decisions": [], "actions": []}.
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
  ]
}

Few-shot Example 2:
Transcript:
[u00020] SPK_01 (03:00) Alice: How is the weather over there today?
[u00021] SPK_02 (03:05) Bob: It has been raining all morning, pretty cold.
Output:
{
  "decisions": [],
  "actions": []
}
"""


def _compute_cache_key(window: ExtractionWindow, model: str) -> str:
    combined = f"{window.text_block}|{model}|{PROMPT_VERSION}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def extract_from_window(
    window: ExtractionWindow,
    client: LLMClient,
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
            cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
            logger.debug("Cache hit for window %s (%s)", window.window_id, cache_key[:8])
            return _attach_provenance(cached_data, window)
        except Exception:
            pass  # Corrupt cache: fall through to LLM call

    user_prompt = f"Transcript Window [{window.window_id}]:\n{window.text_block}"
    raw_response = client.complete_json(system=SYSTEM_PROMPT, user=user_prompt)

    # Persist raw response to cache
    try:
        cache_file.write_text(json.dumps(raw_response, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to write LLM cache for window %s: %s", window.window_id, e)

    return _attach_provenance(raw_response, window)


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
        "window_id": window.window_id,
        "meeting_id": window.meeting_id,
    }


def extract_meeting(
    meeting_id: str,
    windows: List[ExtractionWindow],
    client: LLMClient,
) -> List[Dict[str, Any]]:
    """
    Extract decisions and action items across all windows of a meeting.
    Returns a unified flat list of raw candidate dictionaries with window_id attached.
    """
    candidates: List[Dict[str, Any]] = []

    for window in windows:
        window_result = extract_from_window(window, client)
        for d in window_result.get("decisions", []):
            if isinstance(d, dict):
                candidates.append(d)
        for a in window_result.get("actions", []):
            if isinstance(a, dict):
                candidates.append(a)

    return candidates
