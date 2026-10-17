"""
Verification evaluation (separate from extraction).

Builds claims with KNOWN correctness from AMI gold items and runs the real
Phase 1 verifier (deterministic + retrieval/NLI + fusion) on them:

  true            gold summary sentence, cited with its gold evidence DAs
  wrong_citation  same text, evidence moved >= 8 utterances away
  negated         outcome negated ("It was decided NOT to ...")
  foreign         a gold sentence from a different meeting, cited at a random spot
  owner_swap      action with its role replaced by another team role

The raw semantic scores are saved so thresholds can be calibrated offline
(calibrate_fusion.py) on dev and then applied unchanged to test.

    python -m evaluation.run_verification_eval --split dev --meetings 6 --per-meeting 3 --out ...
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import random
import re
import sys
import time
from pathlib import Path

from evaluation.harness import AMI_DERIVED, IsolatedRoot

ROLES = ["project manager", "industrial designer", "user interface designer", "marketing expert"]
ROLE_RE = re.compile(r"\b(the )?(project manager|industrial designer|user interface designer|marketing (expert|executive)|interface designer)\b", re.I)


def negate(text: str) -> str:
    t = text.rstrip(".")
    for pat, rep in ((r"\bwill be\b", "will not be"), (r"\bwill\b", "will not"), (r"\bis\b", "is not"),
                     (r"\bare\b", "are not"), (r"\bcan\b", "cannot"), (r"\bshould\b", "should not")):
        if re.search(pat, t):
            return re.sub(pat, rep, t, count=1) + "."
    return f"It was decided not to: {t}."


def swap_owner(text: str, rng: random.Random):
    m = ROLE_RE.search(text)
    if not m:
        return None, None
    current = m.group(2).lower()
    current = "marketing expert" if current.startswith("marketing") else "user interface designer" if "interface" in current else current
    other = rng.choice([r for r in ROLES if r != current])
    return ROLE_RE.sub(f"the {other}", text, count=1), other


def build_claims(split: str, n_meetings: int, per_meeting: int, seed: int):
    rng = random.Random(seed)
    datas = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(AMI_DERIVED.glob("*.json"))]
    datas = [d for d in datas if d["split"] == split and any(g["evidence"] for g in d["gold"])]
    rng.shuffle(datas)
    chosen = datas[:n_meetings]
    pool = [g["text"] for d in datas[n_meetings:] for g in d["gold"]]
    plan = []
    for d in chosen:
        n = len(d["utterances"])
        golds = [g for g in d["gold"] if g["evidence"]]
        rng.shuffle(golds)
        for g in golds[:per_meeting]:
            ev = g["evidence"]
            far = min(n - 1, max(int(e[1:]) for e in ev) + rng.randint(8, 25))
            if far - max(int(e[1:]) for e in ev) < 8:
                far = max(0, min(int(e[1:]) for e in ev) - rng.randint(8, 25))
            base = {"meeting": d["meeting_id"], "gold_id": g["item_id"], "type": g["item_type"]}
            plan.append({**base, "variant": "true", "text": g["text"], "evidence": ev, "owner": None})
            plan.append({**base, "variant": "wrong_citation", "text": g["text"], "evidence": [f"u{far:05d}"], "owner": None})
            plan.append({**base, "variant": "negated", "text": negate(g["text"]), "evidence": ev, "owner": None})
            spot = rng.randrange(n)
            plan.append({**base, "variant": "foreign", "text": rng.choice(pool), "evidence": [f"u{spot:05d}"], "owner": None})
            if g["item_type"] == "action":
                swapped, other = swap_owner(g["text"], rng)
                if swapped:
                    plan.append({**base, "variant": "owner_swap", "text": swapped, "evidence": ev, "owner": other})
    return chosen, plan


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", required=True)
    ap.add_argument("--meetings", type=int, default=6)
    ap.add_argument("--per-meeting", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    from app.core.console import make_stdio_unicode_safe
    make_stdio_unicode_safe()

    chosen, plan = build_claims(args.split, args.meetings, args.per_meeting, args.seed)
    results = []
    started = time.perf_counter()
    # Resumable: claim groups already saved in --out (same seed => same plan) are skipped.
    out_path = Path(args.out)
    done_groups: set[tuple[str, str]] = set()
    if out_path.exists():
        previous = json.loads(out_path.read_text(encoding="utf-8"))
        if previous.get("complete"):
            print(f"{out_path} is already complete; nothing to do")
            return
        results = previous.get("results", [])
        done_groups = {(r["meeting"], r["variant"]) for r in results}
        print(f"resuming: {len(done_groups)} claim groups already verified", flush=True)
    with IsolatedRoot():
        from app.core import db
        from app.schemas.claim import ActionItem, Decision
        from app.services.transcript.transcript_index import build_transcript_index, persist_transcript_index
        from app.services.verification.claim_verification import verify_meeting
        from app.services.verification.claims_view import build_claims_view

        for d in chosen:
            items = [p for p in plan if p["meeting"] == d["meeting_id"]]
            # one DB meeting per variant group keeps claims independent of each other
            for variant in sorted({p["variant"] for p in items}):
                if (d["meeting_id"], variant) in done_groups:
                    continue
                mid = f"v_{d['meeting_id']}_{variant}"
                db.insert_meeting(mid, filename="ami-verification-eval")
                segs = [{"start": u["start"], "end": u["end"], "speaker": f"SPEAKER_{u['speaker']}", "text": u["text"]}
                        for u in d["utterances"]]
                with contextlib.redirect_stdout(io.StringIO()):
                    persist_transcript_index(build_transcript_index({"segments": segs}, mid), mid)
                for k, p in enumerate(x for x in items if x["variant"] == variant):
                    cid = f"{mid}_{k}"
                    p["claim_id"] = cid
                    if p["type"] == "decision":
                        claim = Decision(claim_id=cid, meeting_id=mid, text=p["text"], evidence=p["evidence"], state="accepted")
                    else:
                        claim = ActionItem(
                            claim_id=cid, meeting_id=mid, text=p["text"], evidence=p["evidence"],
                            owner=p["owner"], owner_type="explicit_third_person" if p["owner"] else "unassigned",
                        )
                    db.insert_claim_with_evidence(claim)
                t0 = time.perf_counter()
                summary = verify_meeting(mid)
                view = {c["claim_id"]: c for c in build_claims_view(mid)["claims"]}
                for p in (x for x in items if x["variant"] == variant):
                    c = view[p["claim_id"]]
                    sem = c["verification"]["semantic"] or {}
                    results.append({
                        **p, "final": c["verification"]["final"]["label"], "exportable": c["exportable"],
                        "semantic_label": sem.get("label"), "semantic_details": sem.get("details"),
                        "deterministic": (c["verification"]["deterministic"] or {}).get("label"),
                        "reason_codes": [r["code"] for r in c["verification"]["final"]["reasons"]],
                        "latency_ms": sem.get("latency_ms"),
                    })
                print(f"{mid}: {len(view)} claims verified in {time.perf_counter() - t0:.1f}s "
                      f"({summary.get('final_counts')})", flush=True)
                Path(args.out).write_text(json.dumps({"args": vars(args), "results": results}, indent=1), encoding="utf-8")
    out = {"args": vars(args), "seconds_this_run": round(time.perf_counter() - started, 1),
           "results": results, "complete": True}
    Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"done: {len(results)} claims ({out['seconds_this_run']}s this run)")


if __name__ == "__main__":
    main(sys.argv[1:])
