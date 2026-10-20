"""
Select dialogue-model decoding thresholds for HYBRID extraction on AMI dev.

Reuses the rules-v2 claims saved by the dev baseline run, merges them with the
learned candidates exactly as production does (dialogue_model.merge_candidates)
and scores against AMI dev gold. Objective: mean of decision and action F1
(+/-2 utterance evidence match), subject to false confirmations not exceeding
the rules-v2 dev baseline. Test data is never read.

    python -m evaluation.select_thresholds
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import numpy as np

from app.services.intelligence import dialogue_model as dm
from evaluation.harness import aggregate, score_case
from evaluation.train_dialogue_model import U, case_of, load

RESULTS = Path(__file__).parent / "results"


def main() -> int:
    baseline = json.loads((RESULTS / "baseline_rules_v2_ami_dev.json").read_text(encoding="utf-8"))
    rules_by_case = {c["case"]: c["claims"] for c in baseline["cases"]}
    max_false = baseline["per_set"]["ami-dev"]["confirmations"]["false_confirmations"]

    model = dm.load_model()
    cached = []
    for data, emb in load("dev"):
        utts = [U(u["utt_id"], u["text"], u["speaker"]) for u in data["utterances"]]
        sc = dm.score_utterances(model, emb, [u.text for u in utts], [u.speaker_id for u in utts])
        rules = []
        for c in rules_by_case[data["meeting_id"]]:
            cand = {"claim_type": c["type"], "evidence": c["evidence"], "text": c["text"]}
            if c["type"] == "decision":
                cand["state"] = c["state"]
            else:
                cand.update({"commitment_type": c["commitment"], "owner": c["owner"], "deadline_text": c["deadline"]})
            rules.append(cand)
        cached.append((data, emb, utts, sc, {}, rules))

    grid = []
    for td, ta, offer in itertools.product((0.6, 0.7, 0.8, 0.9, 0.95), (0.6, 0.7, 0.8, 0.9), (None, 0.6, 0.7, 0.8, 0.9)):
        th = dict(model.thresholds, decision=td, action=ta, offer=offer)
        model.thresholds = th
        scores = []
        for data, emb, utts, sc, pc, rules in cached:
            merged = dm.merge_candidates(rules, dm.decode(model, utts, emb, scores=sc, pair_cache=pc))
            claims = [{"claim_id": f"{data['meeting_id']}_{k}", "claim_type": c["claim_type"], "evidence": c["evidence"],
                       "payload": {key: c.get(key) for key in ("state", "owner", "owner_type", "deadline_text", "commitment_type")},
                       "text": c["text"]} for k, c in enumerate(merged)]
            scores.append(score_case(case_of(data), claims))
        agg = aggregate(scores)
        f1 = (agg["decision_near"]["f1"] + agg["action_near"]["f1"]) / 2
        fc = agg["confirmations"]["false_confirmations"]
        grid.append((fc <= max_false, round(f1, 4), -agg["claims_per_meeting"], th, agg))
        print(f"td={td} ta={ta} offer={offer}: dec F1={agg['decision_near']['f1']} act F1={agg['action_near']['f1']} "
              f"claims/mtg={agg['claims_per_meeting']} false_conf={fc}", flush=True)
    grid.sort(key=lambda g: (g[0], g[1], g[2]), reverse=True)
    feasible, f1, _, th, agg = grid[0]

    data = dict(np.load(dm.MODEL_PATH, allow_pickle=False))
    meta = json.loads(str(data.pop("meta")))
    meta["thresholds"] = th
    meta["threshold_selection"] = {"objective": "mean near-F1 of HYBRID (rules-v2 + dialogue) on AMI dev",
                                   "constraint": f"false_confirmations <= {max_false} (rules-v2 dev)",
                                   "constraint_satisfied": bool(feasible)}
    np.savez(dm.MODEL_PATH, meta=json.dumps(meta), **data)
    report_path = dm.MODEL_PATH.with_suffix(".json")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["hybrid_threshold_selection"] = {**meta["threshold_selection"], "thresholds": th, "hybrid_dev": agg}
    report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps({"thresholds": th, "feasible": feasible, "dev": agg}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
