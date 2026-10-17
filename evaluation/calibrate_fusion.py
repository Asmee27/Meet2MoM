"""
Offline calibration of the semantic-verification thresholds (Phase 2).

Reads run_verification_eval.py output (raw per-claim NLI scores), recomputes
the semantic label and "would be exported" for candidate thresholds, and picks
thresholds on DEV only: maximise the corruption catch rate subject to the
false-hold rate on true claims not exceeding the current (Phase 1) settings.

    python -m evaluation.calibrate_fusion --dev dev.json [--test test.json]
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

PHASE1 = {"min_support_margin": 0.25, "mismatch_delta": 0.30, "content_min": 0.5,
          "cited_strict_min": 0.5, "x_reject": 0.80, "x_review": 0.50, "owner_min": 0.5}


def semantic_label(d: dict, th: dict) -> str:
    e_cited, x_cited = d["e_cited"], d["x_cited"]
    if x_cited >= th["x_reject"] and x_cited > e_cited:
        return "contradicted"
    e_bg = d.get("e_background") or 0.0
    cited_margin = e_cited - e_bg
    union_margin = (d.get("best_support") or 0.0) - e_bg
    mismatch = ((union_margin - cited_margin) > th["mismatch_delta"] or cited_margin < th["min_support_margin"]
                or (d.get("content_coverage") or 0.0) < th["content_min"])
    if not mismatch:
        strict = d.get("e_cited_strict")
        if strict is not None and strict < th["cited_strict_min"]:
            return "supported_with_context"
        return "supported"
    return "unsupported_or_elsewhere"


def exported(r: dict, th: dict) -> bool:
    d = r.get("semantic_details") or {}
    if not d or "e_cited" not in d:
        return False
    if semantic_label(d, th) != "supported":
        return False
    x_ids = set(d.get("x_core_utt_ids") or [])
    if (d.get("x_core") or 0.0) >= th["x_review"] and x_ids and not x_ids <= set(r["evidence"]):
        return False
    owner_score = (d.get("per_hypothesis") or {}).get("owner")
    if r.get("owner") and owner_score is not None and owner_score < th["owner_min"]:
        return False
    return True


def evaluate(results: list[dict], th: dict) -> dict:
    out = {}
    for variant in sorted({r["variant"] for r in results}):
        rows = [r for r in results if r["variant"] == variant]
        exp = sum(exported(r, th) for r in rows)
        out[variant] = {"n": len(rows), "exported": exp,
                        ("false_hold_rate" if variant == "true" else "catch_rate"):
                            round((len(rows) - exp) / len(rows), 4) if variant == "true" else round(1 - exp / len(rows), 4)}
    corrupt = [r for r in results if r["variant"] != "true"]
    out["all_corruptions_catch_rate"] = round(1 - sum(exported(r, th) for r in corrupt) / max(1, len(corrupt)), 4)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", required=True)
    ap.add_argument("--test")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    dev = json.loads(Path(args.dev).read_text(encoding="utf-8"))["results"]
    base = evaluate(dev, PHASE1)
    cap = base.get("true", {}).get("false_hold_rate", 1.0)
    grid = []
    for msm, md, xr, xv, cs in itertools.product((0.15, 0.25, 0.35, 0.45), (0.2, 0.3, 0.4), (0.7, 0.8, 0.9),
                                                 (0.4, 0.5, 0.6, 0.7), (0.3, 0.5, 0.7)):
        th = dict(PHASE1, min_support_margin=msm, mismatch_delta=md, x_reject=xr, x_review=xv, cited_strict_min=cs)
        e = evaluate(dev, th)
        fh = e.get("true", {}).get("false_hold_rate", 1.0)
        grid.append((fh <= cap, e["all_corruptions_catch_rate"], -fh, th, e))
    grid.sort(key=lambda g: (g[0], g[1], g[2]), reverse=True)
    best = grid[0]
    report = {"phase1_on_dev": base, "false_hold_cap": cap, "chosen_thresholds": best[3], "chosen_on_dev": best[4]}
    if args.test:
        test = json.loads(Path(args.test).read_text(encoding="utf-8"))["results"]
        report["phase1_on_test"] = evaluate(test, PHASE1)
        report["chosen_on_test"] = evaluate(test, best[3])
    print(json.dumps(report, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


if __name__ == "__main__":
    main(sys.argv[1:])
