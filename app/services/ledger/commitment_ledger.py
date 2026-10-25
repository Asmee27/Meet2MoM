"""
Evidence-linked commitment ledger (Phase 9).

1. Approving a meeting turns each approved action item into a commitment that
   keeps its source meeting, item, evidence utterances, assignee (as written in
   the minutes; never linked to an account by name) and deadline.
2. For a later meeting of the same ledger, each open commitment is checked
   against that meeting's transcript: hybrid retrieval finds the most relevant
   spans, and each *utterance* in them that is about this commitment is scored
   by the NLI model (Phase 9.1, see propose_updates). The result is a
   *proposal* (done / delayed / no_update, possibly flagged as needing review)
   citing that meeting's utterances, with scores.
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
METHOD = "retrieval+utterance-topic-gate+nli-status-v4"
# Phase 9.1. "Delayed" used to be proposed whenever "<task> is done." was
# contradicted - but a repeated promise ("I will finalize the backend API")
# or "I have not started yet" contradicts "done" without saying anything about
# a delay. With the real NLI model, "<task> is delayed." was also entailed by
# plain future promises ("I will submit the report by Friday": 0.76), and an
# explicit "the deployment is postponed until next week" scored 0.00 because
# the compound task text was not fully restated. So:
#   done     <- "<task> is done." / "<task> has been completed." (task-specific)
#   delayed  <- the utterance itself says the work was postponed, late or
#               blocked, clearly more than "it will be done in the future"
#   not done but no delay stated, or only a repeated promise -> no_update
#   completion and delay/not-finished evidence together     -> needs review
POSTPONED_HYPOTHESES = ("The work has been postponed to a later date.", "The work is late or blocked.")
FUTURE_HYPOTHESIS = "The work will be done in the future."
NOT_FINISHED_HYPOTHESIS = "The work is not finished yet."
# A delay must beat "will be done in the future" by this margin; a promise
# with a date scores about the same on both and is not evidence of a delay.
FUTURE_MARGIN = 0.30
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
    """
    The account whose ledger this meeting's commitments belong to: its primary
    owner. (Phase 11 fix: this used db.get_meeting(), which does not return
    owner_user_id, so every commitment was stored without an owner and a
    signed-in user's ledger was always empty.)
    """
    owners = db.get_meeting_owner_ids(meeting_id)
    return owners[0] if owners else None


def list_commitments(ledger_owner_id: Optional[str]) -> List[Dict[str, Any]]:
    """The ledger with each commitment's history (newest first)."""
    with db.get_conn() as conn:
        if ledger_owner_id is None:
            # Local mode without sign-in: one user, so the whole ledger is theirs.
            rows = conn.execute("SELECT * FROM commitments ORDER BY created_at DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM commitments WHERE ledger_owner_id = ? ORDER BY created_at DESC",
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
    """Task-specific completion hypotheses (the deadline left out)."""
    task = _task_without_deadline(text)
    return {"done": f"{task} is done.", "completed": f"{task} has been completed."}


def _content_words(text: str) -> set:
    # A five-letter prefix folds simple inflections (update / updated, state / states).
    return {w[:5] for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOPWORDS}


def _on_topic(task: str, utterance: str) -> bool:
    wanted = _content_words(task)
    shared = len(wanted & _content_words(utterance))
    return bool(wanted) and shared >= min(2, len(wanted)) and shared >= TOPIC_MIN_OVERLAP * len(wanted)


def _candidate_utterances(meeting_id: str, task: str, index, utterances: Dict[str, Any]) -> List[Any]:
    """
    Utterances of THIS meeting that are about this commitment. Retrieval spans
    group three utterances; each utterance is gated on its own, so a neighbour
    about something else is never cited (it used to be, as part of the span).
    """
    chosen: List[str] = []
    for hit in index.retrieve(task, k=TOP_K):
        if getattr(hit.span, "meeting_id", meeting_id) != meeting_id:
            continue                                   # never another meeting's text
        for uid in hit.span.utt_ids:
            utterance = utterances.get(uid)
            if utterance is not None and uid not in chosen and _on_topic(task, utterance.text or ""):
                chosen.append(uid)
    return [utterances[uid] for uid in chosen]


def classify_status(task: str, candidates: List[Any], scores: List[Dict[str, float]]) -> Dict[str, Any]:
    """
    The proposal from per-utterance NLI scores. `scores[i]` holds the
    entailment of each hypothesis for `candidates[i]`: keys "done",
    "postponed", "future", "not_finished".
    """
    done, delayed, not_finished = [], [], []
    best = {"done": 0.0, "delayed": 0.0, "not_finished": 0.0, "future": 0.0}
    for utterance, s in zip(candidates, scores):
        delay = s["postponed"] if s["postponed"] - s["future"] >= FUTURE_MARGIN else 0.0
        best["done"] = max(best["done"], s["done"])
        best["delayed"] = max(best["delayed"], delay)
        best["not_finished"] = max(best["not_finished"], s["not_finished"])
        best["future"] = max(best["future"], s["future"])
        if s["done"] >= PROPOSAL_MIN and s["not_finished"] < PROPOSAL_MIN and s["postponed"] < PROPOSAL_MIN:
            done.append((s["done"], utterance))
        elif delay >= PROPOSAL_MIN and s["done"] < PROPOSAL_MIN:
            delayed.append((delay, utterance))
        elif s["not_finished"] >= PROPOSAL_MIN:
            not_finished.append((s["not_finished"], utterance))

    def ids(found):
        return [u.utt_id for _, u in sorted(found, key=lambda x: -x[0])[:1]]

    if done and (delayed or not_finished):
        return {"status": "no_update", "needs_review": True, "evidence_ids": ids(done) + ids(delayed or not_finished),
                "reason": "Conflicting evidence: one statement says the work is done, another that it is not "
                          "finished or delayed. Check the recording and decide.", "best": best}
    if done:
        return {"status": "done", "needs_review": False, "evidence_ids": ids(done),
                "reason": "A statement in this meeting says the work is done.", "best": best}
    if delayed:
        return {"status": "delayed", "needs_review": False, "evidence_ids": ids(delayed),
                "reason": "A statement in this meeting says the work is postponed, late or blocked.", "best": best}
    if not_finished:
        return {"status": "no_update", "needs_review": False, "evidence_ids": [], "context_ids": ids(not_finished),
                "reason": "The work is said to be not finished yet, but no delay or new date is stated.",
                "best": best}
    if candidates:
        return {"status": "no_update", "needs_review": False, "evidence_ids": [],
                "context_ids": [u.utt_id for u in candidates[:3]],
                "reason": "The task is mentioned without progress information (for example, the promise is "
                          "repeated).", "best": best}
    return {"status": "no_update", "needs_review": False, "evidence_ids": [],
            "reason": "This commitment is not discussed in this meeting.", "best": best}


def _score_candidates(meeting_id: str, task: str, candidates: List[Any], scorer) -> List[Dict[str, float]]:
    done_hyps = list(_hypotheses(task).values())
    hypotheses = done_hyps + list(POSTPONED_HYPOTHESES) + [FUTURE_HYPOTHESIS, NOT_FINISHED_HYPOTHESIS]
    pairs = [(u.text, h) for u in candidates for h in hypotheses]
    raw = scorer.score_pairs_cached(meeting_id, pairs) if pairs else []
    out = []
    width = len(hypotheses)
    for i in range(len(candidates)):
        entail = [r.get("entailment", 0.0) for r in raw[i * width:(i + 1) * width]]
        n_done, n_post = len(done_hyps), len(POSTPONED_HYPOTHESES)
        out.append({
            "done": max(entail[:n_done]),
            "postponed": max(entail[n_done:n_done + n_post]),
            "future": entail[n_done + n_post],
            "not_finished": entail[n_done + n_post + 1],
        })
    return out


def propose_updates(meeting_id: str, *, index=None, scorer=None) -> List[Dict[str, Any]]:
    """
    Propose a status for every outstanding (open or delayed) commitment of
    this meeting's ledger that came from an earlier meeting. Never changes a
    commitment's status. Evidence is always an utterance of `meeting_id`.
    """
    from app.services.verification.nli_scorer import NLIScorer
    from app.services.verification.retrieval_index import RetrievalIndex

    owner = ledger_owner(meeting_id)
    with db.get_conn() as conn:
        if owner is None:                    # local mode without sign-in
            open_items = [_row(r) for r in conn.execute(
                "SELECT * FROM commitments WHERE status IN ('open', 'delayed') AND source_meeting_id != ?",
                (meeting_id,),
            ).fetchall()]
        else:
            open_items = [_row(r) for r in conn.execute(
                "SELECT * FROM commitments WHERE ledger_owner_id = ? AND status IN ('open', 'delayed') "
                "AND source_meeting_id != ?",
                (owner, meeting_id),
            ).fetchall()]
    if not open_items:
        return []
    utterances = {u.utt_id: u for u in db.get_utterances(meeting_id)}
    if not utterances:
        raise ValueError("This meeting has no transcript yet.")
    index = index or RetrievalIndex.load_or_build(meeting_id)
    scorer = scorer or NLIScorer()

    proposals = []
    for item in open_items:
        candidates = _candidate_utterances(meeting_id, item["text"], index, utterances)
        result = classify_status(item["text"], candidates, _score_candidates(meeting_id, item["text"], candidates, scorer))
        status = result["status"]
        record = {
            "commitment_id": item["commitment_id"],
            "meeting_id": meeting_id,
            "proposed_status": status,
            "evidence_ids": result["evidence_ids"],
            "scores": {
                "done": round(result["best"]["done"], 4),
                "delayed": round(result["best"]["delayed"], 4),
                "not_finished": round(result["best"]["not_finished"], 4),
                "future": round(result["best"]["future"], 4),
                "threshold": PROPOSAL_MIN,
                "future_margin": FUTURE_MARGIN,
                "needs_review": result["needs_review"],
                "reason": result["reason"],
                "context_ids": result.get("context_ids", []),
                "candidate_ids": [u.utt_id for u in candidates],
            },
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
        scores = item.get("scores") if isinstance(item.get("scores"), dict) else {}

        def cite(ids):
            # Utterance IDs restart in every meeting (u00000...), so a citation
            # is always the pair (meeting_id, utt_id) - here the follow-up meeting.
            return [
                {"meeting_id": meeting_id, "utt_id": uid, "ref": f"{meeting_id}:{uid}",
                 "speaker": utterances[uid].speaker_name or utterances[uid].speaker_id,
                 "start": utterances[uid].start, "end": utterances[uid].end, "text": utterances[uid].text}
                for uid in ids if uid in utterances
            ]

        item["evidence"] = cite(item["evidence_ids"])
        item["context"] = cite(scores.get("context_ids") or [])
        item["needs_review"] = bool(scores.get("needs_review"))
        item["reason"] = scores.get("reason")
        # The source evidence belongs to the commitment's own meeting.
        item["source_evidence"] = [
            {"meeting_id": item["source_meeting_id"], "utt_id": uid, "ref": f"{item['source_meeting_id']}:{uid}"}
            for uid in item.get("source_evidence_ids") or []
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
        previous = conn.execute("SELECT status FROM commitments WHERE commitment_id = ?", (commitment_id,)).fetchone()
        conn.execute(
            """
            UPDATE commitment_updates SET review_status = ?, final_status = ?, reviewer = ?, note = ?, reviewed_at = ?,
                                          previous_status = ?
            WHERE update_id = ?
            """,
            (review_status, final_status, reviewer, (note or None), _now(),
             previous["status"] if previous else None, update["update_id"]),
        )
        if final_status in ("done", "delayed"):
            conn.execute(
                "UPDATE commitments SET status = ?, updated_at = ? WHERE commitment_id = ?",
                (final_status, _now(), commitment_id),
            )
    return {"commitment_id": commitment_id, "review_status": review_status, "final_status": final_status,
            "previous_status": previous["status"] if previous else None}
