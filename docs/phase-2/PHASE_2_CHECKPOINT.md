# Phase 2 checkpoint — 2026-10-16

Status: **in progress, paused for unattended runs.** Nothing has been committed. Production `storage/` and `storage/meet2mom.db` were not touched by any evaluation (everything runs in a temporary root).

## Done

- AMI loader and series split: train ES/IB, dev IS, test TS (held out). Embeddings are cached for all 107,009 utterances.
- Evaluation harness that runs the production extraction pipeline in an isolated root, with evidence-overlap scoring (strict and ±2 utterances), false-confirmation counts and attribute accuracy.
- Ground truth: AMI gold; `demo_01` and Q4 (real, single annotator, seen during development); 8 synthetic meetings (held out).
- Dialogue model `dialogue-v1` (`app/services/intelligence/models/dialogue_model_v1.npz`). Thresholds were selected on AMI dev only. The SHA is frozen in `evaluation/results/model_used_for_test.sha256`.
- Hybrid extractor (rules-v2 + dialogue) is now the default fallback when no LLM is available. Learned-only claims are never final, and the learned model can only make states more cautious. It falls back to rules if the model file is missing or embedding fails.
- Tests: `tests/test_dialogue_model.py` (10) and `tests/test_evaluation_harness.py` (6) pass, along with the 37 tests affected by the default change. The full suite has not been re-run since the default change.
- Held-out AMI test, rules-v2 baseline: **complete** (`evaluation/results/test_rules.json`).

## Interim metrics

**AMI dev, ±2 utterances**

| extractor | decision P / R / F1 | action P / R / F1 | false confirmations |
|---|---|---|---|
| rules-v2 | 0.097 / 0.371 / 0.153 | 0.063 / 0.062 / 0.062 | 169 |
| hybrid (final) | 0.108 / 0.541 / 0.180 | 0.333 / 0.831 / 0.476 | 168 |
| dialogue only | 0.147 / 0.315 / 0.200 | 0.519 / 0.800 / 0.629 | 142 |

**AMI test (held out), rules-v2, ±2 utterances**

| decision P / R / F1 | action P / R / F1 | false confirmations |
|---|---|---|
| 0.060 / 0.238 / 0.096 | 0.084 / 0.113 / 0.096 | 274 of 295 final claims |

Hybrid on the test split is still running.

**Real recordings (`demo_01` + Q4; contaminated because they were seen during development)**
- Rules-v2: decision F1 1.0, action recall 0/6.
- Hybrid: decision F1 0.92, action recall 0.17.
- Hybrid with the offer path on: action recall 0.67.
- Known risk: `demo_01` decision 1 is still labelled "accepted", but the gold label is "conditional".

**Synthetic (held out)**

| extractor | decision F1 | action F1 |
|---|---|---|
| rules-v2 | 0.757 | 0.778 |
| hybrid | 0.818 | 0.778 |
| hybrid with offer path | — | 0.842 |

The Hinglish synthetic meeting scores poorly.

## Stopped for the night (2026-10-16)

Both evaluation jobs were stopped on purpose; nothing is running now.
- Hybrid test: 8 of 39 meetings saved in `test_hybrid.json.partial.jsonl`.
- Dev verification: 2 claim groups (6 claims) saved in `verification_dev.json`.

To resume, run `evaluation/run_remaining_phase2.ps1`. It skips the saved work. The table below is the state before the stop.

## Still running at checkpoint time

| PID | job | resumable if killed? |
|---|---|---|
| 352 / 7932 | hybrid extraction on the AMI test split → `test_hybrid.json` | yes (`test_hybrid.json.partial.jsonl`) |
| 24504 / 5956 | verification eval on dev → `verification_dev.json` | yes (finished groups are saved in the file) |
| 24112 / 6704 | user's uvicorn on port 8000 | not part of the evaluation; optional |

## Remaining (automated by `evaluation/run_remaining_phase2.ps1`)

1. `test_hybrid` (finish)
2. `verification_dev` (finish)
3. `verification_test`
4. Fusion calibration: thresholds chosen on dev and reported once on test → `fusion_calibration.json`
5. Full backend pytest → `pytest_phase2.xml` (also checks that the production DB hash is unchanged)
6. Frontend vitest and build check

## For Claude to do next session (not automated)

- Review the result files and `logs/run_remaining_summary.log`.
- Decide on the offer path. It is currently **off**: AMI dev F1 gets worse with it on, while short real and synthetic meetings improve.
- Apply calibrated fusion thresholds only if the dev-selected values also hold on test.
- Write `docs/phase-2/PHASE_2_EVALUATION_REPORT.md` and update the feature status.
  - BLOCKED: LLM extraction (no API key and no Ollama).
  - UNVERIFIED: real Hinglish/Marathi recordings.
- No commits without approval.
