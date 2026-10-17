"""
Phase 2 evaluation harness.

Runs the PRODUCTION extraction pipeline (transcript index -> topics ->
run_extraction -> span validation -> DB claims) on evaluation meetings inside a
throwaway storage root, then scores claims against evidence-linked gold.

Matching is by evidence: a predicted claim matches a gold item when their
cited utterance ids overlap (strict), or fall within +/-2 utterances of each
other ("near", reported separately). Text similarity is not used for matching,
so paraphrased gold summaries cannot inflate scores.

Extraction metrics and verification metrics are computed separately.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANNOTATIONS = PROJECT_ROOT / "evaluation" / "annotations"
AMI_DERIVED = PROJECT_ROOT / "eval_data" / "ami" / "derived"
NEAR = 2


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

@dataclass
class GoldItem:
    item_id: str
    item_type: str
    text: str
    evidence: list[str]
    confirmed: bool = True
    state: Optional[str] = None
    owner: Optional[str] = None
    owner_aliases: list[str] = field(default_factory=list)
    deadline: Optional[str] = None


@dataclass
class Case:
    case_id: str
    source: str                  # "ami" | "real" | "synthetic"
    split: str
    segments: list[dict]
    gold: list[GoldItem]
    negatives: list[dict] = field(default_factory=list)
    seen_during_development: bool = False


def load_ami_case(meeting_id: str) -> Case:
    data = json.loads((AMI_DERIVED / f"{meeting_id}.json").read_text(encoding="utf-8"))
    segments = [
        {"start": u["start"], "end": u["end"], "speaker": f"SPEAKER_{u['speaker']}", "text": u["text"],
         "expected_utt_id": u["utt_id"]}
        for u in data["utterances"]
    ]
    gold = [GoldItem(g["item_id"], g["item_type"], g["text"], g["evidence"]) for g in data["gold"] if g["evidence"]]
    return Case(meeting_id, "ami", data["split"], segments, gold)


def ami_cases(split: str, limit: Optional[int] = None) -> list[Case]:
    ids = sorted(p.stem for p in AMI_DERIVED.glob("*.json"))
    cases = [load_ami_case(i) for i in ids]
    cases = [c for c in cases if c.split == split]
    return cases[:limit] if limit else cases


def load_annotated_case(path: Path) -> Case:
    ann = json.loads(path.read_text(encoding="utf-8"))
    transcript = json.loads((PROJECT_ROOT / ann["transcript"]).read_text(encoding="utf-8"))
    segments = transcript if isinstance(transcript, list) else transcript.get("segments", [])
    gold = [
        GoldItem(
            it["id"], it["type"], it["text"], it["evidence"], it.get("confirmed", True),
            it.get("state") or it.get("commitment"), it.get("owner"), it.get("owner_aliases", []),
            it.get("deadline"),
        )
        for it in ann["items"]
    ]
    source = "synthetic" if ann.get("synthetic") else "real"
    return Case(ann["meeting_id"], source, ann.get("split", "real-dev"), segments, gold,
                ann.get("negatives", []), ann.get("seen_during_development", False))


def annotated_cases(pattern: str = "*.json") -> list[Case]:
    return [load_annotated_case(p) for p in sorted(ANNOTATIONS.glob(pattern))]


# ---------------------------------------------------------------------------
# Running the production pipeline in an isolated root
# ---------------------------------------------------------------------------

class IsolatedRoot:
    """Point config.STORAGE_ROOT / DB_PATH at a temp dir for this process."""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="meet2mom_eval_")).resolve()

    def __enter__(self):
        from app.core import config, db, storage
        import app.services.intelligence.claim_writer as claim_writer
        import app.services.transcript.transcript_index as transcript_index

        self._saved = (config.STORAGE_ROOT, config.DB_PATH, db.DB_PATH, storage.STORAGE_ROOT,
                       claim_writer.STORAGE_ROOT, transcript_index.STORAGE_ROOT)
        root = self.root / "storage"
        config.STORAGE_ROOT = storage.STORAGE_ROOT = claim_writer.STORAGE_ROOT = transcript_index.STORAGE_ROOT = root
        config.DB_PATH = db.DB_PATH = self.root / "eval.db"
        db.init_db()
        return self

    def __exit__(self, *exc):
        from app.core import config, db, storage
        import app.services.intelligence.claim_writer as claim_writer
        import app.services.transcript.transcript_index as transcript_index

        (config.STORAGE_ROOT, config.DB_PATH, db.DB_PATH, storage.STORAGE_ROOT,
         claim_writer.STORAGE_ROOT, transcript_index.STORAGE_ROOT) = self._saved
        shutil.rmtree(self.root, ignore_errors=True)


def run_extraction_on_case(case: Case, mode: str, topics: str = "production") -> dict[str, Any]:
    """Index the case transcript and run the production extraction. Returns claims and timings."""
    from app.core import db
    from app.core.storage import MeetingStorage
    from app.services.intelligence.extraction_pipeline import run_extraction
    from app.services.pipeline.intelligence_service import ensure_topics
    from app.services.transcript.transcript_index import build_transcript_index, persist_transcript_index

    meeting_id = f"eval_{case.case_id}".replace(".", "_")[:60]
    db.insert_meeting(meeting_id, filename=f"{case.case_id}.eval")
    timings = {}
    t0 = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        utterances = build_transcript_index({"segments": case.segments}, meeting_id)
        persist_transcript_index(utterances, meeting_id)
    expected = [s.get("expected_utt_id") or s.get("utt_id") for s in case.segments]
    if all(expected) and [u.utt_id for u in utterances] != expected[: len(utterances)]:
        raise RuntimeError(f"{case.case_id}: indexed utterance ids do not match the gold transcript ids")
    timings["index_s"] = round(time.perf_counter() - t0, 3)

    t0 = time.perf_counter()
    storage = MeetingStorage(meeting_id)
    storage.ensure_dirs()
    if topics == "production":
        ensure_topics(meeting_id)
    timings["topics_s"] = round(time.perf_counter() - t0, 3)

    t0 = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()):
        stats = run_extraction(meeting_id, mode=mode)
    timings["extraction_s"] = round(time.perf_counter() - t0, 3)
    claims = db.get_claims(meeting_id)
    return {"meeting_id": meeting_id, "stats": stats, "claims": claims, "timings": timings,
            "utterance_count": len(utterances)}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _num(utt_id: str) -> int:
    return int(utt_id[1:])


def _overlap(a: list[str], b: list[str], tolerance: int = 0) -> bool:
    if tolerance == 0:
        return bool(set(a) & set(b))
    bn = [_num(x) for x in b]
    return any(abs(_num(x) - y) <= tolerance for x in a for y in bn)


def claim_is_confirmation_candidate(claim: dict) -> bool:
    """What the extractor itself asserts as final (before verification)."""
    payload = claim.get("payload") or {}
    if claim["claim_type"] == "decision":
        return payload.get("state") == "accepted"
    return payload.get("commitment_type", "firm") == "firm"


def score_case(case: Case, claims: list[dict], exportable: Optional[set[str]] = None) -> dict[str, Any]:
    """
    exportable: claim ids the full pipeline would put in the confirmed minutes
    (from verification). When None, the extractor's own confirmation claims are used.
    """
    out: dict[str, Any] = {"case": case.case_id, "source": case.source, "split": case.split,
                           "n_claims": len(claims), "n_gold": len(case.gold)}
    for kind in ("decision", "action"):
        gold = [g for g in case.gold if g.item_type == kind]
        pred = [c for c in claims if c["claim_type"] == kind]
        for tol, tag in ((0, "strict"), (NEAR, "near")):
            recalled = [g for g in gold if any(_overlap(c["evidence"], g.evidence, tol) for c in pred)]
            correct = [c for c in pred if any(_overlap(c["evidence"], g.evidence, tol) for g in gold)]
            # any-type: an action found as a decision claim still points the reviewer at it
            recalled_any = [g for g in gold if any(_overlap(c["evidence"], g.evidence, tol) for c in claims)]
            out[f"{kind}_{tag}"] = {
                "gold": len(gold), "pred": len(pred), "tp_gold": len(recalled), "tp_pred": len(correct),
                "recall_any_type": len(recalled_any),
            }
    # confirmation errors: final-looking claims that match no CONFIRMED gold item
    confirmed_gold = [g for g in case.gold if g.confirmed]
    unconfirmed_gold = [g for g in case.gold if not g.confirmed]
    finals = [c for c in claims if (c["claim_id"] in exportable if exportable is not None
                                     else claim_is_confirmation_candidate(c))]
    false_conf = [c for c in finals if not any(
        g.item_type == c["claim_type"] and _overlap(c["evidence"], g.evidence, NEAR) for g in confirmed_gold)]
    conf_of_unconfirmed = [c for c in finals if any(_overlap(c["evidence"], g.evidence, 0) for g in unconfirmed_gold)]
    # A negative only counts when the final claim is not also a correct match.
    negatives_hit = [c for c in false_conf if any(_overlap(c["evidence"], n["evidence"], 0) for n in case.negatives)]
    out["confirmations"] = {
        "final_claims": len(finals),
        "false_confirmations": len(false_conf),
        "confirmed_an_unconfirmed_gold_item": len(conf_of_unconfirmed),
        "confirmed_a_negative": len(negatives_hit),
        "confirmed_gold_found_as_final": sum(
            1 for g in confirmed_gold if any(g.item_type == c["claim_type"] and _overlap(c["evidence"], g.evidence, NEAR) for c in finals)),
        "confirmed_gold": len(confirmed_gold),
    }
    # state / owner / deadline / citation on matched pairs (annotated cases carry these fields)
    pairs = []
    for g in case.gold:
        cands = [c for c in claims if c["claim_type"] == g.item_type and _overlap(c["evidence"], g.evidence, NEAR)]
        if cands:
            best = max(cands, key=lambda c: len(set(c["evidence"]) & set(g.evidence)))
            pairs.append((g, best))
    attr = {"matched": len(pairs), "state_checked": 0, "state_correct": 0, "owner_checked": 0,
            "owner_correct": 0, "deadline_checked": 0, "deadline_correct": 0,
            "citation_precision_sum": 0.0, "citation_recall_sum": 0.0}
    for g, c in pairs:
        p = c.get("payload") or {}
        inter = len(set(c["evidence"]) & set(g.evidence))
        attr["citation_precision_sum"] += inter / max(1, len(c["evidence"]))
        attr["citation_recall_sum"] += inter / max(1, len(g.evidence))
        if g.state is not None:
            attr["state_checked"] += 1
            predicted = p.get("state") if g.item_type == "decision" else p.get("commitment_type", "firm")
            gold_state = g.state
            if g.item_type == "action" and gold_state == "obligation":
                gold_state = "firm"  # an unassigned 'must' is stated as firm; owner checks cover the rest
            attr["state_correct"] += int(predicted == gold_state)
        if g.item_type == "action" and case.source != "ami":
            attr["owner_checked"] += 1
            owner = p.get("owner")
            ok = bool((owner is None and g.owner is None) or (
                owner is not None and (owner in g.owner_aliases or (bool(g.owner) and owner.lower() == g.owner.lower()))))
            if p.get("owner_type") == "group" and "group" in g.owner_aliases:
                ok = True
            attr["owner_correct"] += int(ok)
            attr["deadline_checked"] += 1
            attr["deadline_correct"] += int(bool(p.get("deadline_text")) == bool(g.deadline))
    out["attributes"] = attr
    return out


def aggregate(scores: list[dict]) -> dict[str, Any]:
    agg: dict[str, Any] = {"cases": len(scores)}
    for kind in ("decision", "action"):
        for tag in ("strict", "near"):
            k = f"{kind}_{tag}"
            g = sum(s[k]["gold"] for s in scores)
            p = sum(s[k]["pred"] for s in scores)
            tg = sum(s[k]["tp_gold"] for s in scores)
            tp = sum(s[k]["tp_pred"] for s in scores)
            ra = sum(s[k]["recall_any_type"] for s in scores)
            recall = tg / g if g else None
            precision = tp / p if p else None
            f1 = (2 * precision * recall / (precision + recall)) if precision and recall else 0.0
            agg[k] = {"gold": g, "pred": p, "recall": _r(recall), "precision": _r(precision),
                      "f1": _r(f1), "recall_any_type": _r(ra / g if g else None)}
    c = {key: sum(s["confirmations"][key] for s in scores) for key in scores[0]["confirmations"]} if scores else {}
    agg["confirmations"] = c
    a = {key: sum(s["attributes"][key] for s in scores) for key in scores[0]["attributes"]} if scores else {}
    agg["attributes"] = {
        "matched": a.get("matched"),
        "state_accuracy": _r(a["state_correct"] / a["state_checked"]) if a.get("state_checked") else None,
        "state_checked": a.get("state_checked"),
        "owner_accuracy": _r(a["owner_correct"] / a["owner_checked"]) if a.get("owner_checked") else None,
        "owner_checked": a.get("owner_checked"),
        "deadline_accuracy": _r(a["deadline_correct"] / a["deadline_checked"]) if a.get("deadline_checked") else None,
        "citation_precision": _r(a["citation_precision_sum"] / a["matched"]) if a.get("matched") else None,
        "citation_recall": _r(a["citation_recall_sum"] / a["matched"]) if a.get("matched") else None,
    }
    agg["claims_per_meeting"] = _r(sum(s["n_claims"] for s in scores) / len(scores)) if scores else None
    return agg


def _r(x):
    return None if x is None else round(float(x), 4)
