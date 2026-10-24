"""
Evidence-linked commitment ledger (Phase 9).

1. Approving a meeting turns each approved action item into a commitment that
   keeps its source meeting, item, evidence utterances, assignee (as written in
   the minutes; never linked to an account by name) and deadline.
2. For a later meeting of the same ledger, each open commitment is checked
   against that meeting's transcript: hybrid retrieval finds the most relevant
   spans and the NLI model scores "<task> is done" and "<task> is delayed".
   The result is a *proposal* (done / delayed / no_update) with cited
   utterances and scores.
3. A reviewer confirms or corrects the proposal. Only that changes the
   commitment's status; every proposal and decision stays in the history.

Thresholds are provisional (not calibrated), like fusion-v1.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core import db

logger = logging.getLogger(__name__)

PROPOSAL_MIN = 0.60          # entailment needed to propose done / delayed
TOP_K = 5
METHOD = "retrieval+topic-gate+nli-v3"
# An utterance is only considered when it shares this share of the
# commitment's content words (and at least two of them). Without it, "the
# mobile check is not done" contradicts "the login page is done" and was
# proposed as evidence that the login page work is delayed.
TOPIC_MIN_OVERLAP = 0.4
_STOPWORDS = frozenset(
    "the a an and or of to for by with on in at from before after into about this that these those "
    "will would should could can must is are was were be been being it its we you they he she i our "
    "your their my his her them us me all any some next up out not now yet just also please".split()
)
FINAL_STATUSES = ("done", "delayed", "no_update")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row) -> Dict[str, Any]:
    item = dict(row)
    for key in ("evidence_ids", "scores"):
        if key in item and isinstance(item[key], str):
            try:
                item[key] = json.loads(item[key])
            except ValueError:
                pass
    return item


# ---------------------------------------------------------------------------
# 1. Commitments from approved minutes
# ---------------------------------------------------------------------------

def record_commitments(meeting_id: str, mom: Dict[str, Any], ledger_owner_id: Optional[str]) -> int:
    """
    Create (or refresh, while still open) one commitment per approved action
    item. Returns the number of approved action items recorded.
    """
    count = 0
    now = _now()
    with db.get_conn() as conn:
        for item in mom.get("action_items") or []:
            item_id = item.get("claim_id")
            text = (item.get("task") or "").strip()
            if not item_id or not text:
                continue
            owner = item.get("owner")
            deadline = item.get("dueDate")
            conn.execute(
                """
                INSERT INTO commitments (commitment_id, ledger_owner_id, source_meeting_id, source_item_id,
                                         text, assignee, assignee_user_id, deadline, evidence_ids, status,
                                         created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, 'open', ?, ?)
                ON CONFLICT(source_meeting_id, source_item_id) DO UPDATE SET
                    text = excluded.text, assignee = excluded.assignee, deadline = excluded.deadline,
                    evidence_ids = excluded.evidence_ids, updated_at = excluded.updated_at
                WHERE commitments.status = 'open'
                """,
                (
                    "c_" + uuid.uuid4().hex[:16], ledger_owner_id, meeting_id, item_id, text,
                    None if owner in (None, "", "Not assigned") else owner,
                    None if deadline in (None, "", "Not specified") else deadline,
                    json.dumps(item.get("evidence_ids") or []), now, now,
                ),
            )
            count += 1
    return count


def ledger_owner(meeting_id: str) -> Optional[str]:
    return (db.get_meeting(meeting_id) or {}).get("owner_user_id")


def list_commitments(ledger_owner_id: Optional[str]) -> List[Dict[str, Any]]:
    """The ledger with each commitment's history (newest first)."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM commitments WHERE ledger_owner_id IS ? ORDER BY created_at DESC",
            (ledger_owner_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = _row(row)
            item["history"] = [_row(u) for u in conn.execute(
                "SELECT * FROM commitment_updates WHERE commitment_id = ? ORDER BY update_id",
                (item["commitment_id"],),
            ).fetchall()]
            result.append(item)
    return result


# ---------------------------------------------------------------------------
# 2. Proposals for a follow-up meeting
# ---------------------------------------------------------------------------

# A trailing deadline ("... by Friday") makes the NLI model answer "neutral"
# to "<task> is done." even when the premise says the work is finished.
_TRAILING_DEADLINE = re.compile(
    r"(?:\s+(?:by|before|until|till|no later than)\s+[^,.;]+"
    r"|\s+(?:at|in|during)\s+the next meeting"
    r"|\s+(?:today|tomorrow|tonight|next week|this week|end of (?:the )?(?:day|week)))\s*$",
    re.IGNORECASE,
)


def _task_without_deadline(text: str) -> str:
    task = text.strip().rstrip(".")
    return _TRAILING_DEADLINE.sub("", task).strip() or task


def _hypotheses(text: str) -> Dict[str, str]:
    task = _task_without_deadline(text)
    return {"done": f"{task} is done.", "delayed": f"{task} is delayed."}


def _content_words(text: str) -> set:
    # A five-letter prefix folds simple inflections (update / updated, state / states).
    return {w[:5] for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOPWORDS}


def _on_topic(task: str, utterance: str) -> bool:
    wanted = _content_words(task)
    shared = len(wanted & _content_words(utterance))
    return bool(wanted) and shared >= min(2, len(wanted)) and shared >= TOPIC_MIN_OVERLAP * len(wanted)


def propose_updates(meeting_id: str, *, index=None, scorer=None) -> List[Dict[str, Any]]:
    """
    Propose a status for every outstanding (open or delayed) commitment of
    this meeting's ledger that came from an earlier meeting. Never changes a
    commitment's status.
    """
    from app.services.verification.nli_scorer import NLIScorer
    from app.services.verification.retrieval_index import RetrievalIndex

    owner = ledger_owner(meeting_id)
    with db.get_conn() as conn:
        open_items = [_row(r) for r in conn.execute(
            "SELECT * FROM commitments WHERE ledger_owner_id IS ? AND status IN ('open', 'delayed') AND source_meeting_id != ?",
            (owner, meeting_id),
        ).fetchall()]
    if not open_items:
        return []
    if not db.get_utterances(meeting_id):
        raise ValueError("This meeting has no transcript yet.")
    index = index or RetrievalIndex.load_or_build(meeting_id)
    scorer = scorer or NLIScorer()

    proposals = []
    for item in open_items:
        hits = [h for h in index.retrieve(item["text"], k=TOP_K) if _on_topic(item["text"], h.span.text)]
        hyps = _hypotheses(item["text"])
        pairs, owners = [], []
        for hit in hits:
            for status, hypothesis in hyps.items():
                pairs.append((hit.span.text, hypothesis))
                owners.append((status, hit))
        scores = scorer.score_pairs_cached(meeting_id, pairs) if pairs else []
        best = {"done": (0.0, None), "delayed": (0.0, None)}
        for (status, hit), score in zip(owners, scores):
            # "done" entailed -> done; "done" contradicted counts towards delayed.
            candidates = [(status, score.get("entailment", 0.0))]
            if status == "done":
                candidates.append(("delayed", score.get("contradiction", 0.0)))
            for name, value in candidates:
                if value > best[name][0]:
                    best[name] = (value, hit)
        status, (value, hit) = max(best.items(), key=lambda kv: kv[1][0])
        if value < PROPOSAL_MIN:
            status, hit = "no_update", None
        record = {
            "commitment_id": item["commitment_id"],
            "meeting_id": meeting_id,
            "proposed_status": status,
            "evidence_ids": list(hit.span.utt_ids) if hit else [],
            "scores": {"done": round(best["done"][0], 4), "delayed": round(best["delayed"][0], 4),
                       "threshold": PROPOSAL_MIN},
        }
        with db.get_conn() as conn:
            conn.execute(
                """
                INSERT INTO commitment_updates (commitment_id, meeting_id, proposed_status, evidence_ids,
                                                scores, method, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(commitment_id, meeting_id) DO UPDATE SET
                    proposed_status = excluded.proposed_status, evidence_ids = excluded.evidence_ids,
                    scores = excluded.scores, method = excluded.method
                WHERE commitment_updates.review_status = 'proposed'
                """,
                (record["commitment_id"], meeting_id, status, json.dumps(record["evidence_ids"]),
                 json.dumps(record["scores"]), METHOD, _now()),
            )
        proposals.append(record)
    return proposals


def meeting_follow_ups(meeting_id: str) -> List[Dict[str, Any]]:
    """Proposals for this meeting, with the commitment and the cited utterances."""
    utterances = {u.utt_id: u for u in db.get_utterances(meeting_id)}
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT u.*, c.text, c.assignee, c.assignee_user_id, c.deadline, c.status AS commitment_status,
                   c.source_meeting_id, c.evidence_ids AS source_evidence_ids
            FROM commitment_updates u JOIN commitments c ON c.commitment_id = u.commitment_id
            WHERE u.meeting_id = ? ORDER BY u.update_id
            """,
            (meeting_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = _row(row)
        try:
            item["source_evidence_ids"] = json.loads(item["source_evidence_ids"] or "[]")
        except ValueError:
            pass
        item["evidence"] = [
            {"utt_id": uid, "speaker": utterances[uid].speaker_name or utterances[uid].speaker_id,
             "start": utterances[uid].start, "end": utterances[uid].end, "text": utterances[uid].text}
            for uid in item["evidence_ids"] if uid in utterances
        ]
        result.append(item)
    return result


# ---------------------------------------------------------------------------
# 3. Human review
# ---------------------------------------------------------------------------

def review_update(meeting_id: str, commitment_id: str, final_status: str, reviewer: Optional[str],
                  note: Optional[str] = None) -> Dict[str, Any]:
    """Confirm or correct a proposal. Only here does a commitment's status change."""
    if final_status not in FINAL_STATUSES:
        raise ValueError(f"Status must be one of {', '.join(FINAL_STATUSES)}.")
    if not (reviewer or "").strip():
        raise ValueError("A reviewer is required.")
    with db.get_conn() as conn:
        update = conn.execute(
            "SELECT * FROM commitment_updates WHERE meeting_id = ? AND commitment_id = ?",
            (meeting_id, commitment_id),
        ).fetchone()
        if update is None:
            raise LookupError("No proposal exists for this commitment in this meeting.")
        review_status = "confirmed" if final_status == update["proposed_status"] else "corrected"
        conn.execute(
            """
            UPDATE commitment_updates SET review_status = ?, final_status = ?, reviewer = ?, note = ?, reviewed_at = ?
            WHERE update_id = ?
            """,
            (review_status, final_status, reviewer, (note or None), _now(), update["update_id"]),
        )
        if final_status in ("done", "delayed"):
            conn.execute(
                "UPDATE commitments SET status = ?, updated_at = ? WHERE commitment_id = ?",
                (final_status, _now(), commitment_id),
            )
    return {"commitment_id": commitment_id, "review_status": review_status, "final_status": final_status}
