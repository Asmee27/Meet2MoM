# Meet2MoM — Phase 0 Closure Report

**Date:** 2026-10-16 · **Base:** `9e9548d` plus your pre-existing uncommitted work (preserved) · **Nothing committed or pushed.**
This closes the issues raised in `PHASE_0_FINAL_REPORT.md`. Every result below was executed in this session; anything not executed is marked UNVERIFIED.

---

## 1. Files changed and why

| File | Change | Reason |
|---|---|---|
| `app/core/jobs.py` *(your WIP file)* | (a) Moved the verification body back inside `_run_verification`, so it runs through `_run_stage` and the `done` emission is reachable. The whitespace-insensitive diff against your WIP is **empty**: indentation only, your logic is unchanged. (b) Added a catch-all in `run_pipeline` that records `failed` at the last emitted stage for any exception not raised inside a wrapped stage. (c) Imported `get_job_status` for that. | P0-1 |
| `app/core/console.py` *(new, 25 lines)* | `make_stdio_unicode_safe()`: keeps the stream encoding but sets `errors="backslashreplace"` on non-UTF-8 stdout/stderr | P1-1 |
| `app/api/main.py` | Calls `make_stdio_unicode_safe()` before the routes are imported (API process: job runner, `/extract`, `/verify`) | P1-1 |
| `scripts/run_recorded_pipeline.py` | Calls `make_stdio_unicode_safe()` at start (direct CLI runs; the API-spawned subprocess already got `PYTHONIOENCODING=utf-8`) | P1-1 |
| `requirements.txt` | `torch>=2.10.0` → `torch==2.11.0`, `torchaudio>=2.10.0` → `torchaudio==2.11.0`, added `torchcodec==0.16.0` | A clean resolve picked torch 2.14.1 with torchaudio 2.11.0 and torchcodec 0.17.0, an untested mix of compiled extensions. Pinned to the versions verified in this audit. |
| `.gitignore` | Added `.pytest_cache/` | A normal `pytest` run creates it at the repo root and it wasn't ignored |
| `tests/test_job_runner.py` *(new)* | 5 tests: success → `done` with real verification; core failure → `failed@merge`; missing claims → `failed@verification`; `KeyError`/`ValueError` from an unwrapped stage → `failed@extraction` | P0-1 regression. **Fails on both HEAD and your original WIP (4/5), passes on the fix** |
| `tests/test_console_encoding.py` *(new)* | 3 subprocess tests with a real cp1252 pipe: a control that reproduces the crash, the fix prevents it (stdout and logging), UTF-8 streams are untouched | P1-1 regression |
| `tests/test_golden_demo_01.py` *(new)* + `tests/fixtures/golden/demo_01/` (8 JSON + README) | Golden regression fixture: byte-copies of `storage/demo_01` outputs; 4 tests re-derive the index, rule decisions/actions, verifier statuses and DB claims (FK-grounded) | Roadmap item "freeze golden fixtures" |
| `docs/phase-0/FEATURE_STATUS.md` | Rows 22–23 updated (job orchestration now WORKING), closure note | Status accuracy |
| `docs/phase-0/PHASE_0_CLOSURE.md`, `db_cleanup_manifest.json` *(new)* | This report and the cleanup manifest | Deliverables |
| *(earlier audit changes, still uncommitted)* `tests/__init__.py`, `tests/conftest.py`, `tests/test_meeting_isolation.py` | Test isolation (see the final report §8) | — |

**Not touched:** `claim_writer.py` (your WIP, including the stray `from scipy import stats`), `App.jsx`/`App.css`/`LiveMeeting.jsx`, `.env`, all of `storage/`, the DB, `output/`, the zips, `member1_audio/`, and the new `Meet2Mom — Research & MVP Roadmap.md`.
**Backup of your WIP before editing:** `<scratchpad>/wip_backup/jobs.py.wip` (SHA-256 `bb220ab2…`) and `claim_writer.py.wip`.

---

## 2. Tests executed (actual results)

| Run | Result |
|---|---|
| New job-runner tests on the **fix** | 5 passed |
| Same tests on **original WIP** `jobs.py` | 4 failed, 1 passed (proves the test detects P0-1) |
| Same tests on **HEAD** `jobs.py` | 4 failed, 1 passed |
| Encoding tests | 3 passed (the control confirms `UnicodeEncodeError` without the fix) |
| Golden `demo_01` tests | 4 passed |
| **Full suite (working tree, audit venv)** | **110 passed, 0 failed, 0 skipped**, 58.8 s (98 original + 12 new) |
| Fresh-clone equivalent in a **clean venv** from `requirements.txt` | *see §5* |
| Frontend `vite build` (to scratch outDir) | Success, 244.46 kB JS; no `frontend/dist` created |
| Frontend `oxlint` | 0 errors (123 pre-existing warnings, unchanged) |

### Real end-to-end re-run (both fixes, original failure conditions)
Isolated code copy, own storage and DB, **stdout redirected to a file with cp1252 encoding** and no `PYTHONIOENCODING`, which is exactly the setup that crashed in 0.3 s during the audit:

| Step | Result |
|---|---|
| Upload + background job (ffmpeg, Whisper, pyannote, merge, index, topics, claims, verification) | Completed in 614.3 s |
| `/api/meetings/{id}/status` | **`stage=done, state=done, progress=1.0, error=null`** (audit: stuck `verification/running`) |
| `UnicodeEncodeError` in the log | **0**; symbols written as `\u2713 Recording hash`, etc. |
| Verification stage output | `verified_intelligence.json` written (0 claims processed; stub LLM, see §7) |
| `/transcribe`, `/extract`, `/verify`, `/generate-mom`, `/download-mom` | All 200; valid PDF (3,960 bytes) |
| `uvicorn app.api.main:app` under a cp1252 redirect | `/api/health` 200 after ~8 s, 0 errors in the log |

---

## 3. Storage and database integrity

| Check | Result |
|---|---|
| `storage/` + `output/` tree before vs after the full suite (path, size, mtime) | **Identical** |
| `storage/meet2mom.db` SHA-256 before/after all work in this session | `abf8f46c…` both times; byte-identical to the audit backup |
| `PRAGMA integrity_check` / `foreign_key_check` (live DB, read-only) | `ok` / 0 violations |
| Claims without evidence / evidence → missing utterance | 0 / 0 |
| FK-enforcement tests (`test_insert_claim_missing_evidence_raises_and_rolls_back`, `test_foreign_keys_remain_enabled`, `test_atomic_rollback_on_missing_evidence_invariant`, `test_orphan_check_query_returns_zero_rows`, `test_claim_empty_evidence_rejected`) | All pass |
| Golden test re-inserting `demo_01` claims through the FK-checked path | Pass (evidence `u00030`, `u00033`) |
| User recordings, transcripts, MoMs, DB rows | Unchanged (nothing deleted or modified) |

### `.gitignore`
Excludes `.env`, `storage/`, `output/`, `node_modules/`, `frontend/dist/`, `__pycache__/`, `*.db`, media, and now `.pytest_cache/`. Checked with `git check-ignore`: all source, tests, fixtures, docs, `.env.example` and `package-lock.json` remain trackable. Tracked despite matching ignore rules: `input/test_meeting.mp4` (intentional fixture) and `member1_audio/__pycache__/*.pyc` (legacy, see §6).

---

## 4. Golden fixtures

| Fixture | Status |
|---|---|
| `demo_01` | **Frozen.** 8 output files copied byte-for-byte to `tests/fixtures/golden/demo_01/`, with provenance and SHA-256 in its `README.md`. Before freezing, every deterministic stage was confirmed to reproduce the stored outputs exactly from the stored inputs. Expected outputs are **regression snapshots, not ground truth** (decision 1 cites the suspected mis-citation `u00030`; 0 action items despite real commitments). The original `storage/demo_01/` is untouched. |
| `ami_es2002a` | **BLOCKED: not present on this machine.** No `storage/ami_es2002a/` and no DB rows. The roadmap refers to it on `G:\Meet2MoM`. No expected outputs were fabricated. |

---

## 5. Reproducibility

| Step | Result |
|---|---|
| Clean venv (`python -m venv`, **no** system packages), `pip install --dry-run -r requirements.txt` with the *original* file | Resolves, but selects **torch 2.14.1 + torchaudio 2.11.0 + torchcodec 0.17.0**, a combination never tested (led to the pin above) |
| Full `pip install -r requirements.txt` (pinned) in the clean venv | **Success**; `pip check`: "No broken requirements found"; torch 2.11.0 / torchaudio 2.11.0 / torchcodec 0.16.0 |
| Fresh-clone-equivalent tree (all tracked files plus the new untracked source/test files; no `.env`, `storage/`, `output/`, zips) + clean venv: `pytest` | **110 passed, 0 failed**, 41.2 s; no `storage/` folder created |
| Same tree + clean venv: `uvicorn app.api.main:app` | `/api/health` 200 after ~8 s; `/api/meetings` 200; `/openapi.json` 200; 0 errors |

Caveats (not hidden):
- Not a literal `git clone`, because the fixes are uncommitted. The tree was built from `git ls-files` plus the new files, which is what a clone would contain after commit.
- HF models came from the shared `~/.cache/huggingface`; a **cold** model download wasn't tested (`HF_TOKEN` is needed for pyannote).
- The declared versions differ from the globally installed ones you develop with (fastapi 0.104.1 vs 0.141.1, starlette 0.27.0 vs 1.6.0, numpy 2.4.6 vs 2.3.1, uvicorn 0.24.0 vs 0.52.3, …). **Both** environments pass the suite and start the API, so I left those pins unchanged rather than upgrading frameworks.
- `fastapi.testclient` doesn't work in the declared environment: starlette 0.27 vs transitive httpx 0.28 gives `TypeError: Client.__init__() got an unexpected keyword argument 'app'`. No test in the suite uses `TestClient`, so this affects only future HTTP tests. Fix options for later: pin `httpx<0.28` or upgrade fastapi/starlette. Not changed.
- `sentencepiece` is declared and now installs cleanly; the *global* interpreter still lacks `pytest`, `rank-bm25` and `sentencepiece`. I didn't install anything globally.

---

## 6. Database cleanup — proposed manifest (NOT executed)

Full list with per-meeting reasons: `docs/phase-0/db_cleanup_manifest.json` (generated read-only from a byte copy of the DB, SHA-256 `abf8f46c…`).

| Class | Rows | How it was identified |
|---|---|---|
| **A: conclusively synthetic** | **205 meetings**: 177 `test.mp4` whose *every* utterance matches a fixture string in `tests/*.py` ("Utterance number N discussion content.", "Hello world", "Intro", "Decision item", the PostgreSQL/Redis sentences…), plus 28 `test_audio.mp3` with 0 utterances, status `queued`, no job and no folder (the upload test's filename). Children: 8,108 utterances (incl. 3,979 "Priya"), 1 claim, 1 evidence link, 2 dropped claims, 27 jobs | Text and filename match test sources |
| **A: orphan jobs** | **54** `jobs` rows | No `meetings` row; all `asr/running/progress=0.33`. 0.33 is the value hard-coded in `test_async_jobs_api.py`; the real pipeline writes 0.2727 for `asr` |
| **B: likely synthetic, needs your confirmation** | **5 meetings**, 237 utterances (incl. 118 "Priya"): 4 × `test.mp4` using the template "This is utterance number N in the meeting." (not found in current tests or git history), and `real_recording_test_audit` (filename `test_meeting.mp4`, but its 2 utterances are the isolation test's Alice/Bob sentences) | Structural evidence only |
| **C: genuine, keep** | **18 meetings**: `demo_01`, `smoke_rules`, `smoke_llm`, 4 × `test_meeting.mp4` uploads, 11 × `live-meeting-*.webm` | Real recordings |

Totals reconcile: 205 + 5 + 18 = 228 meetings. All 27 `done` jobs in the DB belong to Class A, so **no real recording's job has ever reached `done`** in the production DB (consistent with P0-1).

Only one Class A/B row has a storage folder: `3973bf17…` (empty skeleton, no files).

### Proposed procedure (requires your explicit approval)
1. **Stop** the backend, so nothing writes to the DB.
2. **Back up:** copy `storage/meet2mom.db` to `storage/meet2mom.db.pre-cleanup-<date>` and record its SHA-256 (it should be `abf8f46c…` if unchanged).
3. **Delete in one transaction, child tables first** (FKs are enforced): `evidence_links` → `claims` → `dropped_claims` → `utterances` → `jobs` → `meetings`, restricted to the IDs in `db_cleanup_manifest.json` (Class A only, unless you also approve Class B), plus the 54 orphan job IDs.
4. **Verify:** `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, expected counts (228 → 23 meetings with Class A removed, or 18 if B is removed too), and spot-check `/api/meetings`.
5. **Rollback:** stop the backend and copy the backup file over `meet2mom.db`. SQLite is a single file, so this is a complete restore.

`scripts/clean_test_meetings.py` is **not** suitable as-is: its patterns (`synthetic.wav`, `background-%`, …) match none of these rows.

---

## 7. Legacy artifacts — classification (NOT modified)

| Artifact | Size | Classification | Evidence |
|---|---|---|---|
| `app.zip` | 220 KB | **Potentially obsolete** | 43/46 files identical to the current tree; the 3 differences are the pre-WIP (HEAD) versions of `jobs.py`, `claim_writer.py` and `main.py`, all recoverable from git HEAD. Contains `__pycache__`. |
| `scripts.zip` | 8 KB | **Potentially obsolete** | 3/4 identical; `run_recorded_pipeline.py` = HEAD version |
| `frontend/src.zip` | 36 KB | **Potentially obsolete** | 13/15 identical; `App.jsx`/`App.css` byte-equal to HEAD |
| `output/` | 31 MB | **Potentially obsolete** | Written only by the services' default `output_dir` and standalone `__main__` demos plus the root prototype scripts; the API and job always use `storage/<id>/`. Unknown whether teammates use those demos. |
| `member1_audio/` | 84 KB | **Potentially obsolete** | `run_pipeline.py` + committed `.pyc` for modules that now live in `app/services/*`; nothing imports it |
| Root scripts (`chunk_audio.py`, `transcribe_chunks.py`, `resume_transcription.py`, `generate_chunk_metadata.py`, `test_audio.py`) | — | **Potentially obsolete** | Hard-code `output/`; not imported by the app |
| `storage/demo_01/` | 16 MB | **Required** (golden fixture source) | Keep |
| `storage/_pipeline_cache/` | 389 KB | **Required** (runtime cache) | Keep |
| `input/test_meeting.mp4` | 10 MB | **Required** (E2E input, golden source) | Keep |
| `Meet2Mom — Research & MVP Roadmap.md` (new, untracked) | 34 KB | **Unknown**: appears to be your planning doc | Keep; your decision whether to commit it |

---

## 8. Confirmed fixes, remaining issues, approvals

### Confirmed fixed (verified)
- **P0-1:** jobs reach `done` on success and `failed` with an error on failure; no exception path leaves a job `running` (unit tests plus real E2E).
- **P1-1:** no crash on cp1252 stdout/stderr (subprocess tests plus real E2E plus uvicorn under redirect).
- **Test hygiene:** 110/110 pass; zero writes to `storage/`, `output/` or the production DB.
- **Dependency pin:** torch/torchaudio/torchcodec now pinned to the verified combination.

### Needs your decision (Phase 0 items)
1. **Meet2Mom naming: not done, conflict.** No "Meet2Mom" string exists in the code, and your `.env` explicitly sets `APP_NAME=Meet2MoM`. The visible name is hard-coded in about 25 places across 9 files, including your untracked `LiveMeeting.jsx`; `APP_NAME` is declared but never read, and the frontend can't see root `.env` values. Proposal: read `APP_NAME` in `config.py` and expose it to the frontend via `vite.config.js` (`loadEnv('..')`), replacing only **user-visible** strings (page title, header, API title, PDF heading/footer). Internal names, file names (`Meet2MoM-Meeting-Minutes.pdf`) and storage paths stay unchanged. Needs: the name to use, and permission to edit `LiveMeeting.jsx`.
2. **DB cleanup:** approve Class A (and optionally Class B) under the procedure in §6.
3. **Legacy artifacts:** approve archiving or removing the "potentially obsolete" items in §7.
4. **`ami_es2002a`:** provide the source data, or drop it as a fixture.
5. **Commit:** approve committing these changes on a branch.

### Deferred to later phases (intentionally not done)
NLI wiring into the pipeline and verifier field mapping for claim actions (Phase 1); decision/action extraction quality, real summarization and speaker naming (Phase 1+); stub-LLM default and live providers; job durability/queue; auth; storage retention; schema migrations; GET-creates-folders; upload content validation; frontend job-state gating and duplicate processing. All are listed with priorities in `PHASE_0_FINAL_REPORT.md` §4.

---

## 9. Phase 0 gate assessment

**Verdict: BLOCKED.** Every engineering exit criterion passes; the gate is held open only by two items that need input from you.

| Gate criterion (roadmap + your brief) | Result | Evidence |
|---|---|---|
| Successful jobs reach `done`; failed jobs reach `failed` with an error; nothing stays `running` | **PASS** | `test_job_runner.py` 5/5; real E2E `done/done/1.0` |
| No crash on non-UTF-8 output | **PASS** | `test_console_encoding.py` 3/3; real E2E and uvicorn under cp1252 redirect |
| `pytest` passes on a fresh clone | **PASS** | 110/110 in a fresh-clone-equivalent tree with a clean venv (§5) |
| Original 98 tests still pass | **PASS** | All 98 within the 110 |
| `storage/` unchanged after a test run; production DB untouched | **PASS** | Tree diff empty (incl. mtimes); DB SHA-256 unchanged |
| FK evidence integrity enforced | **PASS** | 5 FK tests + live DB checks |
| `.gitignore` correct | **PASS** | §3 |
| Status document evidence-based, no unverified "working" rows | **PASS** | `FEATURE_STATUS.md` |
| Golden fixture `demo_01` with expected outputs | **PASS** | §4 |
| Golden fixture `ami_es2002a` | **BLOCKED** | Data not on this machine |
| User-facing Meet2Mom naming in one config place | **BLOCKED** | Conflicts with `.env` `APP_NAME=Meet2MoM` and touches your WIP file; needs your decision (§8) |
| DB cleanup / legacy artifacts | Inspection **complete**; actions await approval | §6, §7 (not gate blockers) |

**To close the gate:** (1) decide the naming question (or move it out of Phase 0); (2) provide `ami_es2002a` data (or drop it as a fixture). Then commit (with your approval).

Phase 1 has **not** been started.
