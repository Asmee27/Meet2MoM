# Meet2MoM — Phase 1 Implementation Report: Semantic Verification in the Product

**Date:** 2026-10-16 · **Base:** `9e9548d` plus Phase 0 changes (all uncommitted) · **Nothing committed or pushed.**
**Product name:** kept as **Meet2MoM** (per your instruction); no rebranding was done.

Every number here comes from a run in this session. Where something could not be run, it is marked **UNVERIFIED** or **BLOCKED**.

---

## 1. Baseline and starting point

- Git state before Phase 1: the Phase 0 changes (uncommitted), your WIP (`claim_writer.py`, `App.jsx`/`App.css`, `LiveMeeting.jsx`), and the renamed roadmap `Meet2MoM — Research & MVP Roadmap.md`. All preserved.
- Baseline test run before any Phase 1 change: **110 passed, 0 failed** (69.8 s).
- Production execution path confirmed:
  - Upload → `jobs.run_pipeline`.
  - Subprocess for ASR and diarization.
  - In-process post-transcript stages: index → topics → claims extraction.
  - Verification.
  - On-demand `/extract` → `/verify` → `/generate-mom` from the UI.
- Root causes found before coding:
  1. Two competing intelligence paths; the MoM used the regex JSON path.
  2. `LLM_PROVIDER=stub` silently produced 0 claims.
  3. `rules_adapter.py` hard-coded `state="accepted"` for every decision, so proposals and conditionals became "accepted".
  4. The rules adapter cited one utterance chosen by an interpolated timestamp. That is the origin of `demo_01`'s `u00030` citation.
  5. The NLI layer existed but was not called by the product.

---

## 2. What was built (and what was reused)

### 2.1 One authoritative claims pipeline
- **Source of truth:** the DB `claims` + `evidence_links` tables (FK to utterances, unchanged invariant). `/api/extract`, `/api/verify`, `/api/meetings/{id}/claims`, the Review UI and the MoM/PDF all read the same records through `app/services/verification/claims_view.py`.
- **Legacy regex extractors kept only as the explicit fallback**, behind the existing `rules_adapter`. `/api/extract` no longer runs them as a separate path, and the duplicate writer of `intelligence/extraction.json` (from your uncommitted `claim_writer.py`) was removed, so each file has one writer.
- **Extraction outcome is persisted** in `extraction_runs`:
  - `completed`: claims found.
  - `no_claims`: an extractor really ran and found none.
  - `extractor_unavailable`: no fallback was allowed.
  - `failed`: no transcript, or an exception.

  `mode_requested`, `mode_used` and `fallback_reason` are recorded with it.
- **The `stub` provider is treated as unavailable** in production (`llm_unavailable_reason`). With your `.env` (`EXTRACTOR=llm`, `LLM_PROVIDER=stub`) the rules fallback runs, and the reason is shown in the API, the UI and the PDF. Providers without an API key are treated the same way. A failed extraction now fails the background job instead of reporting success.
- **Rules adapter fixed** (`rules_adapter.py`):
  - Keeps the extractor's own classification: `proposed`, `conditional`, `accepted`, `rejected`, and `tentative` for conditional actions.
  - Unconfirmed and rejected proposals are kept as non-final claims, so they are reviewed rather than dropped.
  - Decisions cite the shortest run of utterances that contains the statement or proposal, plus its confirmation. ASR often splits one sentence across utterances.
- **Duplicate work removed:** the API-spawned subprocess now runs with `--skip-post-transcript`, so extraction runs once per attempt (Phase 0 measured three topic-segmentation passes). `/extract` and `/verify` reuse current results unless `?force=true`.

### 2.2 Semantic verification integrated (`app/services/verification/claim_verification.py`)
For every claim:

| Method | Implementation | Reused |
|---|---|---|
| `deterministic` | Existing `EvidenceVerifier` (text and timestamp match plus reliability), applied to claim records | Yes, unchanged |
| `semantic_nli` | Existing `RetrievalIndex` (multilingual MiniLM embeddings + BM25) → `build_candidates` (cited ∪ independently retrieved spans) → `NLIScorer` (mDeBERTa-v3 MNLI/XNLI): cited entailment, best alternative, contradiction, background noise floor, content coverage | Yes, extended (see 2.3) |
| `fusion` | New transparent rules (`fusion.py`, `fusion-v1-provisional`) | New |

Semantic labels:
- `supported`
- `supported_with_context`: the citation is incomplete.
- `supported_elsewhere`: a possible mis-citation, with a suggested span.
- `unsupported`
- `contradicted`
- `unavailable` / `error`: explicit, and never given fabricated scores.

### 2.3 Changes to the existing NLI scorer (backwards compatible; existing tests pass)
- **Strict cited score:** the cited utterances are also scored *without* the ±1-utterance context. Real-model runs showed that the context expansion made the `u00033` citation look supported only because the neighbour `u00034` contained the claim.
- **Contradiction location:** the result now reports the utterances of the premise that was actually scored, including context. Previously it reported only the span's own IDs, which pointed reviewers at the wrong utterances; a synthetic reversal test found this.
- **Performance:**
  - Secondary hypotheses (state, owner, deadline) are scored only on the cited span and the top 3 candidates.
  - The 5 background pairs go through the pair cache in one batch.
  - Pairs are sorted by length before batching, to cut padding.
  - None of this changes any score for a pair that is computed.
- Models are still loaded once per process through `model_registry`, and the existing `nli_cache` and retrieval caches are reused.

### 2.4 Fusion v1 (transparent, provisional, uncalibrated)
Each input produces reasons with a severity (`reject` > `review` > `warning` > `info`). The worst severity decides the status, and **a claim is never `verified` without positive semantic support**.

| Condition | Effect |
|---|---|
| No resolvable cited evidence; cited span contradicts the claim (≥ 0.80 and above entailment) | **Rejected** |
| Semantic verifier unavailable or error; not entailed; possible mis-citation; incomplete citation; contradiction ≥ 0.50 elsewhere (possible reversal, never assumed to supersede) | **Review** |
| Decision `proposed` / `conditional` / `deferred` / `rejected`; action hypothetical, past or tentative; owner not supported by evidence; first-person owner with uncertain speaker | **Review** |
| Deterministic text match weak or missing; no owner; group owner; no or ambiguous deadline; unknown ASR or speaker confidence; low ASR or speaker confidence; overlapping speech; possible non-speech; unknown speaker | **Verified with warning** |
| Semantic support, no warnings | **Verified** |

Thresholds live in `FUSION_CONFIG` and are stored with every row (`config_version`). **They are not calibrated probabilities**; calibration is Phase 2.

### 2.5 Persistence: versioned migration (`app/core/db.py`)
- `PRAGMA user_version` 0 → 1. The migration **only adds** these tables:

| Table | Contents |
|---|---|
| `extraction_runs` | Latest extraction outcome per meeting |
| `verification_results` | PK (claim, method): cited IDs, cited entailment, best IDs, best support, contradiction score and IDs, label, reasons, details, model version, config version, latency, timestamp |
| `verification_runs` | Status `completed` / `no_claims` / `semantic_unavailable`, timings, versions |
| `evidence_corrections` | Reviewer accept/reject, original and proposed IDs |

- Verification and correction rows cascade from `claims`, so a re-extraction can never attach stale results to new claims. Positional claim IDs made that a real risk.
- Re-runs replace a meeting's rows atomically, so retries never duplicate.
- **Verified on a byte copy of your production DB:** the migration ran, all 228 meetings / 8,478 utterances / 8 claims were preserved, `integrity_check` was ok, 0 FK violations, and it is idempotent. The live DB will be migrated the first time the app runs (it only adds tables).

### 2.6 API (`app/api/meeting_routes.py`)

| Endpoint | Change |
|---|---|
| `POST /api/extract/{id}` | Authoritative extraction (indexes a bare transcript if needed); returns the extraction outcome, legacy-shaped `decisions`/`action_items`, and `claims`. Errors for no transcript (404), failed (422), unavailable (503) |
| `POST /api/verify/{id}` | Runs or reuses verification; returns persisted claims with deterministic/semantic/final results, counts and the run status; 409 before extraction |
| `GET /api/meetings/{id}/claims` | **New.** Claims with the verification payload; read-only, with no storage side effects |
| `POST /api/meetings/{id}/claims/{claim_id}/evidence-suggestion` | **New.** `{"action": "accept"\|"reject"}`. Accept replaces evidence links (FK-checked, atomic) and re-verifies; reject is recorded and shown as a reason. 409 if already reviewed or no suggestion |
| `POST /api/generate-mom/{id}`, `GET /api/download-mom/{id}` | Built only from claims and fusion rows (see §2.7); 409 if verification is missing or stale |

Module-level imports of the legacy extractors were removed from the routes, so API import no longer loads sentence-transformers.

### 2.7 MoM / PDF export rules
- **Confirmed decisions** = accepted decisions with status Verified / Verified with warning; **confirmed action items** = actions with those statuses. Warnings are printed under each item.
- **Held for review (not confirmed):** every Review or unverified claim, with its reasons, in a separate, explicitly labelled section.
- **Rejected:** excluded and counted.
- **Extraction fallback, failure and verifier status** are printed in the PDF.
- The overview text is deterministic (`summary_method: deterministic_overview`), built from topic labels and verified counts, and says so. "No decisions could be confirmed" is printed when that is true. No hard-coded content.

### 2.8 UI (Step 4 and Review; no redesign)
- **Step 4 (automatic):** counts per status; for each claim, the deterministic result, semantic result (with scores) and final status side by side, plus the top reasons; the verifier-unavailable or extraction-fallback notice; total verification time.
- **Review (manual):** sections "Needs review", "Confirmed" and "Rejected". Each card shows:
  - the claim, with owner and due date for actions
  - cited utterances with speaker, timestamp and ID
  - possibly contradicting evidence
  - independently retrieved evidence
  - all reasons
  - the reviewer correction, if any
  - a **Suggested evidence** card with working Accept / Reject (persisted through the new endpoint)
- Step 5 shows warnings and the "Held for review (not confirmed)" block.
- **Removed:** the old Step 4 Edit/Save text fields. They were never persisted or exported (the server builds the MoM), so "Save" was misleading. Reviewer edits and sign-off are Phase 3.
- "Continue to Transcribe" is disabled until the background job finishes or fails, which prevents a second concurrent pipeline run.
- New styles are in `components/ClaimVerification.css`; your `App.css` was not touched.

---

## 3. Files changed

**Backend:**
- New: `app/services/verification/fusion.py`, `claim_verification.py`, `claims_view.py`, `app/services/pipeline/intelligence_service.py`
- Modified: `app/core/db.py`, `app/core/jobs.py` (verification stage, removed now-unused imports), `app/api/meeting_routes.py`, `app/services/intelligence/extraction_pipeline.py`, `rules_adapter.py`, `claim_writer.py` (removed duplicate `extraction.json` write only; the stray `from scipy import stats` was left as it is your WIP), `app/services/verification/nli_scorer.py`, `app/services/pipeline/recorded_pipeline_adapter.py` (one flag), `scripts/run_recorded_pipeline.py` (flag, and verification for direct CLI runs)
- `requirements.txt`: `httpx==0.27.2` (see §7)

**Frontend:**
- New: `components/ClaimVerification.jsx`, `components/ClaimVerification.css`, `components/verificationFormat.js`, `src/__tests__/verification.test.jsx`, `vitest.config.js`
- Modified: `pages/VerifyMeeting.jsx`, `pages/Summary.jsx`, `pages/UploadMeeting.jsx`, `context/MeetingFlowContext.jsx` (CRLF preserved), `services/api.js`, `package.json` / `package-lock.json` (dev-only: `vitest 5.0.5`, `@testing-library/react 16.3.3`, `@testing-library/dom 10.4.1`, `jsdom 26.1.0`; `test` script)

**Tests:**
- New: `tests/test_fusion.py`, `test_verification_scenarios.py`, `test_extraction_scenarios.py`, `test_claims_api.py`, `test_verification_persistence.py`, `test_semantic_real_models.py`, helpers `semantic_fakes.py`, `scenario_helpers.py`, `real_model_checks.py`
- Rewritten to the new contract: `tests/test_job_runner.py`
- Deliberate golden update: `tests/test_golden_demo_01.py` + fixture README change log (the v1 `claims.json` file is unchanged)

**Docs:** `docs/phase-1/PHASE_1_IMPLEMENTATION_REPORT.md`, `docs/phase-1/FEATURE_STATUS.md`; pointer added to `docs/phase-0/FEATURE_STATUS.md`.

---

## 4. Tests (actual results)

| Run | Environment | Result |
|---|---|---|
| Baseline before Phase 1 | audit venv | 110 passed |
| **Full backend suite, including the 8 slow real-model tests** | audit venv (global packages + pytest) | **173 passed, 0 failed, 0 skipped** (525.7 s) |
| Fresh-clone-equivalent tree (no `.env`, `storage/`, `output/`) | **clean venv from `requirements.txt` only** (fastapi 0.104.1, starlette 0.27.0, httpx 0.27.2, torch 2.11.0) | **165 passed** (all non-slow tests, incl. the `TestClient` API tests), 8 slow tests deselected; no `storage/` created |
| Frontend unit tests (`npm test` → vitest) | jsdom | **10 passed** |
| Frontend production build (`vite build`, to scratch outDir) | — | success (247.6 kB JS) |
| Frontend lint (`oxlint`) | — | 0 errors; the new files add no warnings |

Backend tests by file (173 total; new or rewritten files in bold):

| File | Tests | File | Tests |
|---|---:|---|---:|
| **test_verification_scenarios** | 14 | **test_extraction_scenarios** | 13 |
| **test_fusion** | 13 | **test_claims_api** | 10 |
| **test_verification_persistence** | 7 | **test_job_runner** | 7 |
| **test_semantic_real_models** (slow) | 3 | **test_golden_demo_01** (1 added, 1 updated deliberately) | 5 |
| test_nli_scorer (incl. 5 slow) | 19 | test_candidate_builder | 9 |
| test_db | 8 | test_retrieval_index / test_transcript_index | 7 / 7 |
| test_resolvers / test_span_validator | 6 / 6 | test_async_jobs_api / test_claim_writer / test_llm_extractor | 5 / 5 / 5 |
| test_context_builder / test_extraction_pipeline / test_schemas / test_storage | 4 each | test_console_encoding | 3 |
| test_model_registry | 2 | test_config / test_context_builder_scale / test_meeting_isolation | 1 each |

**What each test layer proves:**
- **Synthetic fixtures with deterministic model fakes** (`tests/semantic_fakes.py`: token-coverage "NLI", hash embedder) test the *decision logic* end to end: extraction → validation → DB → retrieval → NLI scorer → fusion → persistence → API → MoM. They do **not** measure model accuracy.
- **Real models**: `test_semantic_real_models.py` (Gate C on `demo_01`), the 5 pre-existing `test_real_model_*` NLI tests, and the real-recording E2E (§5).
- **Changed assertions, and why:**
  - `test_golden_demo_01.py`: rules-mode claims intentionally changed; documented in the fixture README change log.
  - `test_job_runner.py`: rewritten to the DB-backed verification contract, with every terminal-state check kept and two failure paths added.
  - One newly written scenario test was relaxed from `review` to `review or rejected` for a group-rejected proposal. Its exportability assertion was kept.

  No pre-existing assertion was weakened or deleted.

---

## 5. Real-recording end-to-end run (Gate A)

`input/test_meeting.mp4` (248 s, English). Isolated code copy with its own storage and DB, real models, stdout redirected with cp1252 encoding. The steps were: upload → background job → claims API → `/transcribe` → `/extract` → `/verify` → `/generate-mom` → `/download-mom`. No manual scripts.

| Step | Result |
|---|---|
| Background job (ASR, diarization, index, topics, extraction, semantic verification) | **`done / done / 1.0`**, 826 s total; 0 `UnicodeEncodeError` |
| Extraction run | `completed`; `mode_requested=llm`, `mode_used=rules`, fallback reason "LLM_PROVIDER=stub is a test fixture, not a real extractor"; 3 claims, 0 dropped |
| Verification run | `completed`, 3 claims, 199.7 s total (183.0 s semantic) |
| `/extract`, `/verify` after the job | 200, **`reused_existing: true`** (no second extraction or verification pass) |
| MoM | 2 confirmed decisions (1 verified, 1 verified with warning, with warnings printed), **1 proposal held for review**, 0 action items; summary states those counts |
| PDF | 200, valid `%PDF`, 5,352 bytes |

Per-claim results on the real recording:

| Claim | Cited | Deterministic | Semantic (cited entailment) | Final | Reasons |
|---|---|---|---|---|---|
| "Start with a review of the current training approach…" (accepted) | u00027–u00028 | verified | supported (0.97) | **Verified with warning** | low speaker confidence (0.48), overlapping speech |
| "Look at the report together." (accepted) | u00030–u00031 | verified | supported (0.99) | **Verified** | cited evidence entails |
| "Perhaps we could start with the highlights first." (proposed) | u00033 | verified | supported (0.59) | **Review** | proposal without agreement |

(Utterance IDs differ from `demo_01` because this run's ASR segmented the audio into 34 utterances.)

**Known limitation visible here:** a careful human reading shows that the response to the first proposal was *conditional* ("Yes. We could do that. But I would need to look at the full report first"). Neither the rule extractor nor NLI detects this, so it is shown as *verified with warning*, not Review. This is a real false-confirmation risk on this recording, deferred to Phase 2 (labelled evaluation) and to better extraction.

---

## 6. Performance (Gate G): measured on this machine (CPU only, 4 torch threads)

| Measurement | Value |
|---|---|
| Real E2E, semantic time per claim | 78.7 s, 67.7 s, 36.6 s (mean 61.0 s); whole verification stage 199.7 s for 3 claims |
| Single claim, isolated profile, cold pair cache, after optimizations | 36.0 s (19 NLI pairs); 48.0 s (24 pairs) before |
| `demo_01` real-model run before optimizations | mean 100–135 s per claim (cold) |
| Warm re-run (pair and retrieval caches), before optimizations | 11–18 s per claim |
| Model and index load | embedding index build 23–47 s on first use (includes model load); NLI model load 4–9 s; once per process |
| mDeBERTa raw throughput | ~0.5 s per short pair, ~1.9 s per context-expanded pair |
| Gate C pytest fixture (6 claims, cold) | 449 s |
| API startup (`/api/health`) | ~2 s (was ~8 s, because legacy extractor imports were removed from the routes) |

The cost is dominated by the NLI forward pass. Safe optimizations were applied (fewer secondary-hypothesis pairs, cached and batched background pairs, length-sorted batches) **without changing any computed score**. Further speed-ups would change what the model sees: shorter premises, fewer candidates, quantization or a smaller model. These need the Phase 2 evaluation set before they can be chosen responsibly. A GPU would also remove most of this latency.

Per-claim latency is persisted (`verification_results.latency_ms`) and the run total in `verification_runs`. The E2E ran while light frontend tasks were also running, so its numbers may be slightly high.

---

## 7. Integrity (Gate H) and environment notes

| Check | Result |
|---|---|
| Real `storage/` + `output/` tree (path, size, mtime) after the full suite | **Identical** to the Phase 1 start snapshot |
| `storage/meet2mom.db` SHA-256 | `abf8f46c…` before and after; **not migrated or modified** during this work (the migration was tested on a copy) |
| Golden fixture files (8) | All match their recorded SHA-256 |
| Your WIP files | `App.jsx`, `App.css`, `LiveMeeting.jsx` untouched; `claim_writer.py`: only the duplicate `extraction.json` write removed |

Environment findings:
- **`NODE_ENV=production`** is exported in this shell. With it set, `npm install` omits devDependencies, and my first install *removed* the previously installed `vite`/`oxlint`. I restored the full set with `npm ci --include=dev` from the lockfile (vite 8.2.2, @vitejs/plugin-react 6.1.1, oxlint 1.81.0); the build and lint work again. `vitest.config.js` forces `NODE_ENV=test` for tests. Use `npm ci --include=dev` (or unset `NODE_ENV`) when installing.
- **`NODE_TLS_REJECT_UNAUTHORIZED=0`** is set in your environment, which disables TLS certificate checks for npm. Not changed by me, but worth removing.
- `jsdom 30` does not run on Node 23.4, so `jsdom 26.1.0` is pinned. npm 10.9 crashed during peer resolution, so `--legacy-peer-deps` was used and the peers were installed explicitly.
- `npm audit`: 1 high-severity issue in `source-map-js` (dev/build-time only, through vite→postcss and jsdom; untrusted source maps). Not auto-fixed, since that would rewrite lockfile pins.
- `requirements.txt`: added `httpx==0.27.2` (starlette 0.27's `TestClient` is incompatible with httpx 0.28). Verified in the clean venv.

---

## 8. Phase 1 acceptance gates

| Gate | Verdict | Evidence |
|---|---|---|
| **A: Pipeline** | **PASS** | Real recording: job `done`; extraction and semantic verification ran in the background job without manual scripts (§5) |
| **B: Evidence** | **PASS** | `test_mom_requires_verification_and_exports_only_confirmed_claims`: every exported decision and action has a claim row, non-empty evidence equal to its `evidence_links`, and a fusion row whose label matches. Real E2E: both exported decisions carry evidence IDs and statuses. The MoM refuses to generate (409) without current verification |
| **C: Mis-citation** | **PASS (UI shown at component level)** | Real models on `demo_01` v1 claims: `u00030` is semantic `supported_elsewhere` (cited 0.0006 vs best 0.953), final **Review**, suggestion `u00029`–`u00031`. Decision 2 (`u00033`) is also caught (`citation_incomplete`, 0.45 alone vs 0.93 with context; suggestion `u00032`–`u00034`). The suggestion card and Accept/Reject are rendered and wired (UI tests; the API persists the decision). Not clicked through in a real browser |
| **D: UI** | **PASS (component and API level); browser UNVERIFIED** | Step 4 and Review render the persisted payload: methods side by side, statuses, reasons, cited, retrieved and contradicting evidence, suggestions, corrections (10 UI tests + API contract tests). No manual or automated browser session was run |
| **E: Abstention** | **PASS** | API tests: Review claims appear only under "held for review", rejected claims never appear; proposals, conditionals and rejected states are never exportable (scenario tests). Real E2E: the proposal was held for review |
| **F: Reliability** | **PASS for logic; real-model accuracy not measured** | 14 verification + 13 extraction scenarios (proposal, acceptance, rejection, conditional, reversal, hypothetical, contradiction, wrong citation, correct evidence elsewhere, incomplete citation, wrong owner, ambiguous speaker, missing or ambiguous deadline, no decisions, no actions, stub/failed/unavailable LLM, failed extraction, verifier unavailable) + 13 fusion unit tests; 173/173 with no regressions. These use deterministic fakes except Gate C; catch rates on real data are Phase 2 |
| **G: Performance** | **PASS (measured and documented)** | §6. Latency is high on this CPU (mean ~61 s per claim in the real E2E); recorded rather than hidden |
| **H: Integrity** | **PASS** | §7 |

**Overall: all eight gates pass at the level stated.** The qualified items are not hidden:
- the UI wasn't exercised in a real browser;
- adversarial behaviour is tested with fakes;
- latency is slow on this machine.

---

## 9. Distinctions requested

**Implemented and tested:**
- authoritative claims pipeline and extraction outcome reporting;
- stub-provider handling;
- rules state and citation fixes;
- deterministic + semantic verification per claim;
- fusion with reasons;
- persistence and migration;
- API endpoints;
- evidence-correction persistence;
- MoM/PDF filtering;
- Step 4 and Review UI components;
- job integration and single-pass execution.

**Tested only with synthetic fixtures (deterministic fakes):**
- the adversarial scenarios: reversal surfacing, wrong owner, ambiguous speaker, deadline warnings, hypothetical/tentative commitments, verifier-unavailable;
- evidence-suggestion accept/reject end to end.

**Tested on real recordings / real models:**
- the full pipeline on `input/test_meeting.mp4` (§5);
- Gate C mis-citation detection on `demo_01` (real retrieval + mDeBERTa);
- the 5 pre-existing real NLI tests.

**Unverified:**
- any real LLM provider (no API key; the stub is now reported as unavailable);
- behaviour in a real browser;
- Hinglish/Marathi meetings;
- real-model precision for contradiction/reversal flags;
- `ami_es2002a` (not on this machine).

**Known limitations, deferred:**

| Limitation | Deferred to |
|---|---|
| Thresholds are provisional and uncalibrated; the conditional-agreement false confirmation shown in §5 | Phase 2 |
| Slow CPU latency | Phase 2 options, or a GPU |
| Rule extractor's low action recall and English-only patterns | needs a real LLM provider or extraction work |
| Reviewer sign-off, audit log, claim text editing, audio playback from evidence | Phase 3 |
| Evidence corrections are lost if the meeting is re-extracted (claims are replaced; cascade by design) | — |
| Transcript importers and Google Meet | Phase 4 |
| Speaker names and real summarization | Phase 3+ |

Opening a pre-Phase-1 meeting and running `/extract` will re-extract it with rules-v2, replacing that meeting's old claims. Recordings and transcripts are not touched.

**Not done in this phase (per instructions):** no commits or pushes, no production DB cleanup, no Phase 2+ work.
