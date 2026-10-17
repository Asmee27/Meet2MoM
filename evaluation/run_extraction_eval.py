"""
Extraction evaluation CLI.

    python -m evaluation.run_extraction_eval --mode rules --sets ami-dev real --out evaluation/results/x.json

Sets: ami-train, ami-dev, ami-test (held out), real (annotated recordings),
synthetic (annotated synthetic meetings). Nothing touches the production
storage or database (everything runs in a temp root).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from evaluation.harness import IsolatedRoot, aggregate, ami_cases, annotated_cases, run_extraction_on_case, score_case


def collect(sets: list[str], limit: int | None):
    cases = []
    for name in sets:
        if name.startswith("ami-"):
            cases += ami_cases(name.split("-", 1)[1], limit)
        elif name == "real":
            cases += [c for c in annotated_cases() if c.source == "real"]
        elif name == "synthetic":
            cases += [c for c in annotated_cases() if c.source == "synthetic"]
        else:
            raise SystemExit(f"unknown set {name}")
    return cases


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--sets", nargs="+", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    from app.core.console import make_stdio_unicode_safe
    make_stdio_unicode_safe()

    results = {"mode": args.mode, "sets": args.sets, "limit": args.limit, "per_set": {}, "cases": []}
    started = time.perf_counter()
    # Resumable: every finished meeting is appended to <out>.partial.jsonl and
    # skipped on the next run, so an interrupted evaluation continues where it stopped.
    partial = Path(args.out + ".partial.jsonl") if args.out else None
    done: dict[tuple[str, str], dict] = {}
    if partial and partial.exists():
        for line in partial.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entry = json.loads(line)
                done[(entry["set"], entry["case"])] = entry
        print(f"resuming: {len(done)} meetings already evaluated in {partial.name}", flush=True)
    with IsolatedRoot():
        for set_name in args.sets:
            scores = []
            for case in collect([set_name], args.limit):
                if (set_name, case.case_id) in done:
                    entry = done[(set_name, case.case_id)]
                    scores.append(entry)
                    results["cases"].append(entry)
                    continue
                run = run_extraction_on_case(case, args.mode)
                score = score_case(case, run["claims"])
                score["timings"] = run["timings"]
                score["utterances"] = run["utterance_count"]
                score["extraction_status"] = run["stats"].get("status")
                entry = {
                    **score, "set": set_name,
                    "claims": [
                        {"type": c["claim_type"], "state": (c["payload"] or {}).get("state"),
                         "commitment": (c["payload"] or {}).get("commitment_type"),
                         "owner": (c["payload"] or {}).get("owner"),
                         "deadline": (c["payload"] or {}).get("deadline_text"),
                         "evidence": c["evidence"], "text": c["text"][:200]}
                        for c in run["claims"]
                    ],
                }
                scores.append(entry)
                results["cases"].append(entry)
                if partial:
                    partial.parent.mkdir(parents=True, exist_ok=True)
                    with partial.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                print(f"{set_name} {case.case_id}: claims={len(run['claims'])} "
                      f"dec={score['decision_strict']['tp_gold']}/{score['decision_strict']['gold']} "
                      f"act={score['action_strict']['tp_gold']}/{score['action_strict']['gold']} "
                      f"t={run['timings']}", flush=True)
            results["per_set"][set_name] = aggregate(scores)
            utts = sum(s["utterances"] for s in scores)
            ext = sum(s["timings"]["extraction_s"] for s in scores)
            results["per_set"][set_name]["utterances"] = utts
            results["per_set"][set_name]["extraction_seconds"] = round(ext, 2)
    results["complete"] = True
    results["seconds_total"] = round(time.perf_counter() - started, 1)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in results["per_set"].items()}, indent=1))
    return results


if __name__ == "__main__":
    main(sys.argv[1:])
