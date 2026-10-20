# Meet2MoM — Feature Status after Phase 1

Supersedes `docs/phase-0/FEATURE_STATUS.md`, which is kept as the Phase 0 record. Only rows that changed in Phase 1, or that Phase 1 depends on, are re-evaluated; all other rows keep their Phase 0 status and evidence.

Status legend: **WORKING** (executed successfully here), **PARTIAL**, **BROKEN**, **NOT IMPLEMENTED**, **UNVERIFIED** (code exists, not validated).

Evidence tags used below:

| Tag | Meaning |
|---|---|
| **REAL-E2E** | Real recording `input/test_meeting.mp4` through upload, background job, API and PDF, on an isolated code copy with real models (details in the implementation report §5) |
| **REAL-NLI** | Real embedding and mDeBERTa models on the `demo_01` golden fixture (`tests/test_semantic_real_models.py`) |
| **SYNTH** | Synthetic transcripts with deterministic model fakes (scenario/API/job tests) |
| **UNIT** | Pure unit tests |

| # | Feature | Phase 0 | Phase 1 status | Evidence |
|---|---|---|---|---|
| 11–13 | Decision/action extraction: one authoritative path (DB claims) | Two competing paths; claims path empty by default | **WORKING** as an explicit rules fallback. **LLM path UNVERIFIED** (no provider key) | SYNTH `test_extraction_scenarios.py` (13); REAL-E2E; `extraction_runs` table |
| 12 | Default config (`LLM_PROVIDER=stub`) | **BROKEN** (silently 0 claims) | **FIXED**: stub is reported as unavailable; rules fallback is used and recorded (`mode_used`, `fallback_reason`) | SYNTH, REAL-E2E |
| — | Extraction outcome reporting (`completed` / `no_claims` / `extractor_unavailable` / `failed`) | Not implemented | **WORKING** | SYNTH (7 tests), job test (extraction failure fails the job) |
| — | Decision state from the rule extractor (proposed / conditional / accepted / rejected) | Forced to `accepted` | **WORKING** for the extractor's patterns | SYNTH; golden rules-v2 |
| — | Claim citations: proposal plus confirmation span | Single utterance by interpolated timestamp | **WORKING** (`demo_01`: `u00030`–`u00032`, `u00034`–`u00035`) | golden test |
| 18 | Deterministic verifier (`EvidenceVerifier`) | PARTIAL, not on claims | **WORKING** as one input per claim; its label never verifies a claim by itself | UNIT, SYNTH, REAL-NLI |
| 19 | Semantic verifier (retrieval + NLI) | WORKING in isolation, not integrated | **WORKING and integrated** (job, `/verify`, persisted) | REAL-NLI (3), REAL-E2E, SYNTH |
| — | Mis-citation detection with suggested evidence | Not implemented | **WORKING**: `supported_elsewhere` and `citation_incomplete` | REAL-NLI Gate C (`u00030`, `u00033`); SYNTH |
| — | Contradiction / possible reversal surfaced for review | Not implemented | **PARTIAL**: flagged when NLI contradiction ≥ 0.50 (provisional); never auto-supersedes | SYNTH; real-model precision not measured (Phase 2) |
| — | Trust-status fusion with reasons | Not implemented | **WORKING** (`fusion-v1-provisional`, uncalibrated) | UNIT `test_fusion.py` (13); SYNTH |
| — | Verification persistence (`verification_results`, `verification_runs`, `evidence_corrections`), versioned migration | Not implemented | **WORKING**; migration verified on a copy of the production DB | `test_verification_persistence.py` (7) |
| — | Evidence-suggestion Accept/Reject (persisted, FK-checked, re-verifies) | Not implemented | **WORKING** (minimal; full reviewer sign-off and audit log are Phase 3) | API tests, UI tests |
| 20–21 | MoM / PDF from verified claims only, with warnings and a "held for review" section | Built from regex JSON | **WORKING** | API tests (Gate B/E), REAL-E2E |
| 22 | Background job: extraction + semantic verification | WORKING (P0 fix) | **WORKING** (one extraction pass and one verification pass per attempt) | `test_job_runner.py` (7), REAL-E2E |
| 28 | Frontend Step 4 (both methods + fused status) and Review (evidence, suggestions) | Deterministic-only view | **WORKING** (component tests; not browser-tested) | `frontend/src/__tests__/verification.test.jsx` (10) |
| — | "Continue" gated on job completion | Not gated | **WORKING** | code change; not browser-tested |
| — | Source independence (imported transcripts, unknown confidence) | — | **WORKING** at the index/verification level (unknown confidence becomes a warning, never 1.0). Importers themselves are Phase 4 | SYNTH `test_unknown_confidence…` |
| — | Semantic verification latency | — | **MEASURED** (implementation report §6); slow on this CPU | REAL-NLI, REAL-E2E |
| 10 | Summarization | Template | **Unchanged** (deterministic overview from verified counts; labelled `summary_method: deterministic_overview`) | — |
| 7 | Speaker-name resolution | NOT IMPLEMENTED | **Unchanged** | — |
| 4 | Multilingual ASR / Hinglish extraction | UNVERIFIED | **Unchanged** (Phase 2 evaluation) | — |
