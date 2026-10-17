# Phase 2 Evaluation Report: Extraction, Verification & Calibration

---

## 1. Executive Summary & Status

Phase 2 evaluated Meet2MoM's core intelligence capabilities on held-out meeting benchmarks prior to reviewer workflow integration (Phase 3). 

* **Held-Out Extraction Benchmark:** 39 AMI test meetings (38,072 utterances) comparing the frozen Hybrid Model (`dialogue_model_v1.npz`, SHA-256 verified) against the rule-based baseline (`rules_v2`).
* **Semantic Verification Benchmark:** 6 held-out AMI test meetings (77 claims across known-good, negated, foreign, owner-swapped, and miscited variants) and matching 6 dev meetings (69 claims) using multilingual NLI (`mDeBERTa-v3-base`) and dense retrieval (`paraphrase-multilingual-MiniLM-L12-v2`).
* **Fusion Calibration:** Empirical threshold tuning on dev, reported on test (`fusion_calibration.json`).
* **Regression & Build Verification:** 189 backend tests passed (`pytest_phase2.xml`), production database SHA-256 confirmed unchanged, and frontend test suite (10 tests) and production Vite build succeeded.

---

## 2. Held-Out Extraction Evaluation (39 AMI Meetings)

### Reconciled Aggregate Metrics

Matching follows [`evaluation/harness.py`](file:///c:/Users/Asmee%20jadhav/OneDrive/Desktop/Meet2MoM/evaluation/harness.py). A prediction matches gold if their cited utterance IDs overlap (**Strict**) or fall within a 2-utterance window (**Near $\pm 2$**).
* $\text{Recall} = \text{TP}_{\text{gold}} / \text{Gold}$ ($\text{FN} = \text{Gold} - \text{TP}_{\text{gold}}$).
* $\text{Precision} = \text{TP}_{\text{pred}} / \text{Pred}$ ($\text{FP} = \text{Pred} - \text{TP}_{\text{pred}}$).
* Discrepancies between $\text{TP}_{\text{gold}}$ and $\text{TP}_{\text{pred}}$ arise from many-to-one clustering or multi-evidence spanning.

| Task & Tolerance | Model | Gold | Pred | $\text{TP}_{\text{gold}}$ | $\text{TP}_{\text{pred}}$ | **FP** | **FN** | **Precision** | **Recall** | **F1 Score** | Recall (Any-Type) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Decision (Strict)** | Rules Baseline | 172 | 871 | 24 | 28 | 843 | 148 | 3.21% | 13.95% | **5.23%** | 16.86% |
| | **Hybrid (Frozen)** | 172 | 1,251 | 57 | 67 | 1,184 | 115 | **5.36%** | **33.14%** | **9.22%** | **35.47%** |
| **Decision (Near $\pm 2$)** | Rules Baseline | 172 | 871 | 41 | 52 | 819 | 131 | 5.97% | 23.84% | **9.55%** | 27.91% |
| | **Hybrid (Frozen)** | 172 | 1,251 | 87 | 110 | 1,141 | 85 | **8.79%** | **50.58%** | **14.98%** | **52.33%** |
| **Action (Strict)** | Rules Baseline | 71 | 95 | 1 | 1 | 94 | 70 | 1.05% | 1.41% | **1.20%** | 8.45% |
| | **Hybrid (Frozen)** | 71 | 189 | 27 | 25 | 164 | 44 | **13.23%** | **38.03%** | **19.63%** | **40.85%** |
| **Action (Near $\pm 2$)** | Rules Baseline | 71 | 95 | 8 | 8 | 87 | 63 | 8.42% | 11.27% | **9.64%** | 18.31% |
| | **Hybrid (Frozen)** | 71 | 189 | 38 | 40 | 149 | 33 | **21.16%** | **53.52%** | **30.33%** | **56.34%** |

### Citation Quality on Matched Claims
* **Rules Baseline:** Citation Precision = **36.39%**, Citation Recall = **24.61%**
* **Hybrid Model:** Citation Precision = **48.47%**, Citation Recall = **40.02%**
* Extraction speed: 39 meetings processed in **497.7s (~8.3 min)** on CPU.

---

## 3. Analysis of Precision, False Positives & Prediction Volume

### A. The Precision Ceiling Under Granularity Mismatch
* **Target Set Size:** The AMI gold standard contains only 172 decisions and 71 action items across 39 meetings (~4.4 decisions and ~1.8 actions per meeting).
* **Under strict one-to-one matching**, predicting 1,251 decisions against 172 gold targets mathematically caps precision at **13.75%** ($\frac{172}{1,251}$), even with hypothetical 100% recall. For actions, precision is capped at **37.56%** ($\frac{71}{189}$).
* **Under many-to-one matching**, predictions do not heavily cluster: the 57 recalled gold decisions matched 67 predicted decisions (ratio of $1.18 : 1$). Effective precision remains strictly constrained by prediction volume.

### B. Investigation of High Prediction Volume
We evaluated potential architectural artifacts behind the 1,440 total predictions:
1. **Internal Duplication:** Only **38 of 1,440 predictions (2.64%)** shared identical evidence utterance sets within the same meeting. Only **92 (6.39%)** shared $\ge 1$ evidence utterance with another claim. Duplication loops are not a factor.
2. **Cross-Type Confusion:** In `harness.py`, `recall_any_type` tracks gold items matched by predictions of the opposite type.
   * Decisions: Matching any prediction type yielded 61 strict hits (+4 items, +2.33%) and 90 near hits (+3 items, +1.74%).
   * Actions: Matching any prediction type yielded 29 strict hits (+2 items, +2.82%) and 40 near hits (+2 items, +2.82%).
   * Cross-type confusion accounts for only ~2% to 3% of misses.
3. **Conversational Extraction Scope:** The hybrid model extracts claims across all discussion states:
   * `proposed`: **942 claims (75.3%)**
   * `accepted`: **201 claims (16.1%)**
   * `conditional`: **58 claims (4.6%)**
   * `rejected`: **50 claims (4.0%)**
   Over 75% of extracted decisions are deliberative proposals that human annotators omitted from final summary minutes.

### C. Spatial Distribution of Predictions (Transcript Distance)
Measuring minimum utterance offset $d = \min |u_{\text{pred}} - u_{\text{gold}}|$ to the closest gold decision in the same meeting:
* **$d = 0$ (Strict Match):** 67 predictions (5.4%)
* **$d \in [1, 2]$ (Near Match / Boundary Shift):** 43 predictions (3.4%)
* **$d \in [3, 5]$ (Close Context):** 60 predictions (4.8%)
* **$d \in [6, 15]$ (Same Agenda Topic):** 122 predictions (9.8%)
* **$d \in [16, 50]$ (Medium Distance):** 292 predictions (23.3%)
* **$d > 50$ (Distant / Remote):** 667 predictions (53.3%)

*Empirical Note:* In sampled distant cases ($d > 50$), predictions frequently captured procedural discussions (e.g., presentation timing, seating, meeting management). However, without full content annotation across all 667 distant items, they are formally classified as unaligned false positives.

---

## 4. Semantic Verification & Threshold Calibration (6 Meetings)

Semantic verification evaluates whether factual corruptions and ungrounded claims can be detected before minutes generation.

### Calibration Results (`evaluation/results/fusion_calibration.json`)
Evaluated across known-good (`true`), negated (`negated`), foreign (`foreign`), owner-swapped (`owner_swap`), and miscited (`wrong_citation`) variants:

* **Dev Catch Rate (Corruption Detection):** **1.000 (100%)** across all corruption variants.
* **Test Catch Rate (Held Out):** **1.000 (100%)** across all corruption variants.
  * Foreign claim rejection rate: 100% (18/18)
  * Negated claim rejection rate: 100% (18/18)
  * Owner swap rejection rate: 100% (5/5)
  * Wrong citation rejection rate: 100% (18/18)
* **False Hold Rate on Known-Good True Claims:**
  * Dev: 76.47% held for human review
  * Test: 94.44% held for human review (conservative bias: claims with weak direct entailment are routed to human review rather than silently published).

### Calibrated Thresholds:
```json
{
  "min_support_margin": 0.15,
  "mismatch_delta": 0.20,
  "content_min": 0.50,
  "cited_strict_min": 0.30,
  "x_reject": 0.70,
  "x_review": 0.40,
  "owner_min": 0.50
}
```

---

## 5. Major Limitations & Academic Integrity Statement

> [!WARNING]
> **Low Extractor Precision is a Critical Operational Constraint:**
> * Standalone extraction precision is **5.36% (strict) / 8.79% (near)** for decisions, and **13.23% (strict) / 21.16% (near)** for actions.
> * **The utility of unmatched claims is unproven.** In this evaluation, all unmatched claims are strictly false positives. While some capture genuine conversational proposals, claiming they represent "useful intelligence" without formal human utility studies is an unsupported hypothesis.
> * **Architectural Necessity:** Meet2MoM cannot function as a single-stage end-to-end generator. High conversational recall ($>50\%$ near) provides adequate candidate coverage, but downstream semantic verification and Phase 3 human reviewer sign-off are mandatory to filter noise.

---

## 6. Regression & Integrity Sign-Off

* **Backend Tests:** `pytest -q` passed **189 of 189 tests** in 428.2s (`pytest_phase2.xml`).
* **Production Database Integrity:** SHA-256 hash of `storage/meet2mom.db` was verified identical before and after test execution (`True`).
* **Frontend Tests & Build:** Vitest passed **10 of 10 tests**; production Vite bundle built successfully in 1.01s.
* **Master Script Completion:** `evaluation/run_remaining_phase2.ps1` ran to completion with exit code 0.
