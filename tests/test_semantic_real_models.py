"""
Phase 1 Gate C with the REAL embedding and NLI models (slow, CPU heavy).

The frozen rules-v1 demo_01 claims cite u00030 (only the start of the
proposal) and u00033 (does not contain decision 2). The semantic verifier must
hold both back from the minutes and suggest better evidence; the corrected
rules-v2 citations must not be flagged as mis-cited.

Runs tests/real_model_checks.py in a subprocess so the real
sentence-transformers package is used (other test modules stub it).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def real_results(tmp_path_factory):
    root = tmp_path_factory.mktemp("real_models")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "tests.real_model_checks", str(root)],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=3600,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    line = next(l for l in proc.stdout.splitlines() if l.startswith("RESULT_JSON="))
    return json.loads(line[len("RESULT_JSON="):])


def _by_citation(result, first_utt):
    return next(c for c in result["claims"] if c["evidence_ids"][0] == first_utt)


@pytest.mark.slow
def test_gate_c_u00030_miscitation_is_flagged_with_alternative_evidence(real_results):
    v1 = real_results["v1"]
    assert v1["status"] == "completed"
    claim = _by_citation(v1, "u00030")
    assert claim["semantic_label"] in ("supported_elsewhere", "supported_with_context")
    assert claim["final"] in ("review", "rejected")
    assert claim["exportable"] is False
    assert claim["suggestion"] and claim["suggestion"] != ["u00030"]
    assert {"possible_miscitation", "citation_incomplete"} & set(claim["reason_codes"])


@pytest.mark.slow
def test_decision_2_incomplete_citation_u00033_is_flagged(real_results):
    claim = _by_citation(real_results["v1"], "u00033")
    assert claim["exportable"] is False
    assert {"possible_miscitation", "citation_incomplete"} & set(claim["reason_codes"])
    assert "u00034" in (claim["suggestion"] or [])


@pytest.mark.slow
def test_corrected_rules_v2_citations_are_not_flagged_and_proposals_are_held(real_results):
    v2 = real_results["v2"]
    assert v2["status"] == "completed"
    for claim in v2["claims"]:
        assert claim["semantic_label"] not in ("unavailable", "error")
        assert not {"possible_miscitation", "citation_incomplete"} & set(claim["reason_codes"])
        if claim["state"] != "accepted":
            assert claim["exportable"] is False
    assert v2["timings"]["semantic_ms_per_claim_mean"] > 0
