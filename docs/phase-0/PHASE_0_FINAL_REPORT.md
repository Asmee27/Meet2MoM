# Meet2MoM — Phase 0 Final Report

**Audit date:** 2026-10-16 · **Commit:** `9e9548d` (main) plus pre-existing uncommitted changes
**Scope:** the full repository (FastAPI backend, React frontend, SQLite DB, `storage/`), test suite, and one real end-to-end run.
Companion documents: `ARCHITECTURE_AUDIT.md`, `FEATURE_STATUS.md`, `TEST_VERIFICATION.md`, `DATABASE_STORAGE_AUDIT.md`.

Conventions: **Verified** = observed in this audit. **Inferred** = read from code, not executed. Recommendations are marked as such.

---

## 1. Executive summary

Meet2MoM has a solid recorded-meeting front half. A real 4-minute English recording went through upload → ffmpeg normalization → faster-whisper ASR → pyannote diarization → speaker attribution → DB index in about 9.5 minutes on CPU. That produced a coherent 34-segment transcript with 5 diarized speakers, and the legacy extract → verify → MoM → PDF path then produced a valid PDF (**verified**).

The intelligence half is much weaker than the codebase's size suggests:

- **The background job never finishes.** The committed code always ends `failed` at `verification`. Your uncommitted edit leaves it stuck at `running` forever. The production DB already records this failure for real meetings.
- **The MoM comes from regex extraction only.** The "summary" is a template sentence. Speakers are never named (`SPEAKER_00…`). Action-item recall is very low (1 of 5 clear commitments in a probe; 0 in the real meeting). Conditional and reversed decisions are presented as final.
- **The newer evidence-grounded claims pipeline yields zero claims under the repo's `.env`.** `LLM_PROVIDER=stub` returns an empty list. The NLI verification layer works in isolation but isn't connected to the API.
- **Tests:** 98 exist, **89 passed** on this machine at baseline. Both failure groups were test-infrastructure problems, now fixed: **98/98 pass**. The tests also wrote 12 meeting folders into the real `storage/` per run; they no longer do.
- **No live LLM provider, multilingual recording or frontend browser flow** could be verified. These remain UNVERIFIED.

---

## 2. Feature status overview (details in `FEATURE_STATUS.md`)

**Verified working**
- Upload and per-meeting storage isolation (ID validation, traversal guard, 2 GB cap)
- Audio validation, normalization, quality analysis, chunking
- English ASR (faster-whisper small, CPU int8; RTF ≈ 0.40 measured)
- Speaker diarization (pyannote community-1; 5 speakers detected; accuracy unmeasured)
- Speaker attribution with overlap and confidence flags
- Transcript index → SQLite with FK-enforced evidence grounding
- MoM JSON assembly and PDF export (reportlab)
- Existing-meeting listing
- NLI scorer with the real mDeBERTa model (in isolation)
- API startup (`uvicorn`, `/api/health` within ~8 s) and frontend production build

**Partial**
- Topic segmentation (noisy labels; semantic path untested)
- Decision extraction (explicit English phrases only; conditionals and reversals mishandled; speaker `UNKNOWN`; interpolated timestamps)
- Action-item extraction (very low recall; misses third-person, group, unassigned and Hindi commitments)
- Owner and deadline resolution (logic tested; anchored to upload date; never reaches the MoM)
- `EvidenceVerifier` (checks text presence, not meaning; verified contradictory and conditional decisions)
- Content-hash cache (save verified; hit path not exercised)
- Persistence (stale `meetings.status`, `duration` never stored, no migrations)
- Live meeting capture (record-then-upload; 0 of 11 real live uploads completed)
- Frontend (works through legacy endpoints; no job-state gating; 123 lint warnings; no tests)

**Broken**
- Background job orchestration (never reaches `done`)
- Claims pipeline in the default configuration (stub LLM → always 0 claims)
- Pipeline under non-UTF-8 stdout (crashes in 0.3 s)

**Not implemented**
- Real summarization (template only)
- Speaker-name resolution
- Users, authentication, authorization
- Real-time live transcription
- Word/e-mail export (dead code containing **fabricated** content)
- A participants/speakers/topics/MoM data model in the DB
- Deployment configuration

**Unverified**
- Multilingual and code-switched ASR on real audio
- OpenAI/Anthropic/Gemini/local LLM providers (no keys; the earlier report records a Gemini 404)
- Diarization accuracy
- Cache-hit path
- Frontend behaviour in a real browser

---

## 3. Actual test results

| | Baseline | After Phase 0 fixes |
|---|---|---|
| Collected | 98 | 98 |
| Passed | **89** | **98** |
| Failed | 9 | 0 |
| Skipped / errors | 0 / 0 | 0 / 0 |
| Duration | 264.8 s | 167.7 s |
| Files written to real `storage/` | **12 meeting folders** | **0** |
| Production DB changed | no | no |

Documentation claims of "54 passed" and "56 passed" (`docs/baseline_report.md`) are stale. Before the fixes, "98 passing" did **not** hold on this machine. The suite has no end-to-end test, never executes the background job, and has no tests for ASR, diarization, audio processing, `EvidenceVerifier`, MoM/PDF content, or the frontend. Full details are in `TEST_VERIFICATION.md`.

---

## 4. Ranked unresolved issues

### P0 (critical)

| ID | Issue | Evidence | Location |
|---|---|---|---|
| **P0-1** | **The background pipeline never reaches `done`.** In HEAD, the verification stage fails (it looks for `extraction.json`, which isn't written, then calls the non-existent `EvidenceVerifier.verify()`). In the uncommitted working copy, `_run_verification` is mis-indented: its body ends after the imports, the remaining code runs inline, `return` at line 305 makes the `done` emission (line 328) unreachable, and `NameError: EvidenceVerifier` escapes `run_pipeline`, leaving the status `verification/running` forever. | JOBS experiment (both versions reproduced). E2E: status `verification/running/0.8182` after a 577 s successful run. Production DB: `'EvidenceVerifier' object has no attribute 'verify'` ×2, `extraction.json not found` ×1, 2 jobs stuck `running/verification`. | `app/core/jobs.py:180-328`; `git show HEAD:app/core/jobs.py` line 186 |

### P1 (high)

| ID | Issue | Evidence | Location |
|---|---|---|---|
| P1-1 | The pipeline crashes when stdout isn't UTF-8 (`print("✓ …")` → `UnicodeEncodeError` under cp1252). Any deployment that redirects server output to a file or pipe on Windows fails at `merge`. | E2E attempt 1 failed in 0.3 s; passed with `PYTHONIOENCODING=utf-8` | `app/services/pipeline/recorded_pipeline_adapter.py:893` (and other `print` calls) |
| P1-2 | Two parallel intelligence pipelines with different outputs and sources of truth. The MoM uses regex JSON; the DB claims are unused by the MoM. The uncommitted `claim_writer.py` writes `intelligence/extraction.json` in a schema that collides with `/api/extract`, and `create_mom()` reads whichever wrote last. | E2E: MoM had 2 decisions while `/api/meetings` reported `claim_count: 0`; the file was overwritten by `/api/extract` | `claim_writer.py:156`, `meeting_routes.py:910-921`, `1599-1615` |
| P1-3 | The default config silently produces zero claims (`LLM_PROVIDER=stub`, `EXTRACTOR=llm`). No warning, no fallback. | E2E and PROBE: `accepted: 0` | `.env`, `app/core/llm_client.py` stub branch, `extraction_pipeline.py` |
| P1-4 | The summary is a deterministic template, not summarization. | E2E summary text | `meeting_routes.py:1443` |
| P1-5 | Speaker names are never resolved. The MoM lists `SPEAKER_00…04`, and owners are diarization labels. | E2E; `speaker_name=None` | `transcript_index.py:220` |
| P1-6 | Decision/action extraction quality: action recall 1/5 on clear commitments; conditional decision emitted as final; reversal keeps both contradictory decisions; English-only rules; interpolated timestamps; fixed confidences. | PROBE table; E2E 0 action items | `decision_extractor.py`, `action_item_extractor.py` |
| P1-7 | Verification checks textual similarity only, so it can't catch wrong state or owner. The NLI layer that can isn't wired in. | PROBE: all 4 claims `verified` | `evidence_verifier.py`; `nli_scorer.py` used only by `scripts/report_meeting.py` |
| P1-8 | Duplicate and concurrent processing. "Continue to Transcribe" isn't gated on job completion, so `/api/transcribe` can start a second full pipeline. Post-transcript stages run 2–3× per upload. | E2E: 3 topic-segmentation runs | `UploadMeeting.jsx:291`, `run_recorded_pipeline.py:318`, `jobs.py:162` |
| P1-9 | Jobs aren't durable. BackgroundTasks die with the process, and there's no resume or reaper, so rows stay `running` forever. | Production DB: 4 real stuck jobs | `app/core/jobs.py`, `meeting_routes.py:366` |
| P1-10 | Environment isn't reproducible. `pytest`, `rank-bm25` and `sentencepiece` are pinned but not installed. 6 pins drift from installed versions. `httpx`/`scipy` aren't listed. A global `tests` package shadows the repo tests. | `TEST_VERIFICATION.md` §1 | `requirements.txt` |

### P2 (medium)

| ID | Issue | Location / evidence |
|---|---|---|
| P2-1 | Production DB polluted by earlier test runs: 181 `test.mp4` + 28 `test_audio.mp3` meetings, 4,097 synthetic "Priya" utterances, 54 orphan `jobs` rows. Production code hard-codes test-name filters as a workaround. | `DATABASE_STORAGE_AUDIT.md` §2; `meeting_routes.py:407-414` |
| P2-2 | No migration system (`user_version=0`, try/except `ALTER TABLE`). `jobs` has no FK. No meeting cascade. `meetings.status` never reaches a terminal value. `duration` never stored. Mixed naive/aware timestamps. | `app/core/db.py` |
| P2-3 | GET/POST on `/{id}` routes create a 10-folder skeleton for any valid ID, even a non-existent one. | HTTP probe; `meeting_routes.py:132-146` |
| P2-4 | Upload validates by extension only (text accepted as `.mp4`). `.avi` is accepted by the API but rejected by the pipeline. | HTTP probe; `meeting_routes.py:96-104` vs `config.SUPPORTED_EXTENSIONS` |
| P2-5 | No storage retention or cleanup: normalized WAVs, chunks and failed-run folders are kept forever; recordings are duplicated per upload; every JSON is written twice (subfolder + root). | `DATABASE_STORAGE_AUDIT.md` §4 |
| P2-6 | `.env` keys `WHISPER_*`, `ENABLE_PIPELINE_CACHE`, `CACHE_*`, `HOST`, `PORT`, `FRONTEND_URL` are ignored. `STORAGE_ROOT`/`DB_PATH` aren't configurable. | `app/core/config.py` |
| P2-7 | `/status` returns full subprocess output and tracebacks (absolute paths) in `error`. | `recorded_pipeline_adapter.py` failure branch |
| P2-8 | Placeholder MoM fields: `meeting_title` is always "Meeting Minutes", `meeting_date` is always `null`. `pipeline_features` flags are hard-coded `true` regardless of what ran. Deadlines are anchored to the upload date. | `meeting_routes.py:1911-1915`; `recorded_pipeline_adapter.py:780`; `claim_writer.py:50-53` |
| P2-9 | Fabricated export content (Word/e-mail: "Project Development Meeting" and a fixed summary paragraph). Unreachable today, but one wiring change from shipping fake minutes. | `MeetingFlowContext.jsx:844-881` |
| P2-10 | Test gaps: the background job is never executed by tests; the `sentence_transformers` stub leaks session-wide (semantic topic path untested); no tests for ASR/diarization/audio/verifier/MoM/frontend. | `TEST_VERIFICATION.md` §5 |
| P2-11 | Live meeting: hard-coded API URL; 0 of 11 real uploads completed. | `LiveMeeting.jsx:293`; production DB |
| P2-12 | No authentication on any endpoint. **Raise to P1 if the API is exposed beyond localhost.** | All routes |

### P3 (low)

| ID | Issue | Location |
|---|---|---|
| P3-1 | Abandoned code and artifacts: `member1_audio/` (committed `.pyc`), root prototype scripts, `output/` (31 MB), `app.zip`/`scripts.zip`/`frontend/src.zip`, stub pages, unused `react-router-dom`/`lucide-react` | `ARCHITECTURE_AUDIT.md` §6 |
| P3-2 | Stray `from scipy import stats` above the module docstring (uncommitted) | `claim_writer.py:3` |
| P3-3 | Fresh DB: `/status` and `/transcript` return 500 (`no such table: jobs`) until `init_db()` runs | `meeting_routes.py:426-490` |
| P3-4 | `MeetingStorage.path_for()` returns resolved paths, properties return unresolved ones | `app/core/storage.py:73-117` |
| P3-5 | `KeyboardInterrupt` in the CLI runner exits with code 0, so the adapter treats an interrupted run as success | `scripts/run_recorded_pipeline.py:370` |
| P3-6 | Topic labels contain fillers ("Training / Exactly") | `topic_segmenter.py` |
| P3-7 | Timing-based upload test (`< 2.0 s`) | `test_async_jobs_api.py` |
| P3-8 | 123 frontend lint warnings (119 unused variables) | `frontend/src` |
| P3-9 | Hindi "kal" is mapped only to "tomorrow" | `deadline_resolver.py:21` |

---

## 5. Architecture concerns (summary)
1. Two intelligence pipelines; the MoM uses the weaker one (P1-2).
2. The API process runs heavy jobs via BackgroundTasks, with no queue, durability or concurrency control beyond a process-local lock (P1-9).
3. Global mutable config (`set_active_meeting` patches module globals across the process). It's only safe because the heavy work runs in a subprocess.
4. A 2,579-line route module mixes HTTP, business logic, templating and PDF layout.
5. Three overlapping storage layouts, plus duplicate root-level copies.
6. Import-time filesystem side effects (`storage/`, `_pipeline_cache/`).
7. No deployment artifacts.

## 6. Security and data-integrity risks
- No authentication or authorization; transcripts and MoMs are readable by any client that can reach the port (P2-12).
- Internal paths and tracebacks are exposed via `/status` (P2-7).
- The upload accepts arbitrary bytes, which are then passed to ffmpeg/ffprobe (P2-4).
- Positive: the DB evidence invariant holds (0 orphans, 0 FK violations, 0 ungrounded claims). Path traversal is blocked. CORS is limited to the dev origin. `.env` (with the HF token) has never been committed (`git log --all -- .env` is empty).
- Test pollution in the production DB, and the hard-coded filters that hide it (P2-1).
- Job and meeting statuses in the DB are unreliable (P0-1, P2-2).

---

## 7. Real-world reliability assessment (Task 7)

| Scenario | Support | Basis |
|---|---|---|
| Overlapping speech / cross-talk | **Partial** | Overlap ≥ 0.15 s flagged per segment (`speaker_merger.py`); one speaker assigned per segment; no source separation. Verifier applies a reliability penalty. |
| Poor microphones, noise, echo | **Partial / unverified** | Fixed ffmpeg band-pass + denoise and quality metrics; no adaptive handling; no noisy test audio. |
| Short utterances, interruptions | **Partial** | VAD (500 ms min silence) and segment-level attribution merge rapid turns. "Yes/agreed" acceptance only links to the previous proposal within a topic. |
| Speaker identity drift / unknown speakers | **Partial** | Per-recording clustering; `UNKNOWN` when there's no overlap; no enrollment or cross-meeting identity; no names. |
| Shared microphones | **Unsupported** | Voice-only diarization; no handling. |
| Multilingual / code-switched speech | **Partial / unverified** | Per-chunk language detection, script tagging, Hindi/Marathi deadline words, NLI code-switch test passes. Regex extractors are English-only (Hindi commitment missed in the probe). |
| Chat-only / non-verbal responses | **Unsupported** | Audio-only input; no chat ingestion. |
| Rejected / reversed / conditional decisions | **Partial** | Rejection excluded ✔. Reversal doesn't supersede the earlier decision ✘. Conditional emitted as accepted ✘ (both pipelines in rules mode). The schema has `conditional`/`rejected` states; only the (stubbed) LLM path would use them. |
| Hypotheticals mistaken for commitments | **Supported (probe case)** | "Hypothetically, if I had time I would…" excluded. LLM `commitment_type=hypothetical` is unverified. |
| Implicit / third-party / group ownership | **Mostly unsupported** | "Bob will deploy…", "We all need to…" and "Someone needs to…" were all missed. `owner_resolver` handles these types, but the rule extractor never produces them. |
| Relative / approximate / ambiguous deadlines | **Partial** | Resolver handles tomorrow, weekdays, EOD/EOW/EOM, next week and Hindi/Marathi words. Anchored to the upload date. "kal" ambiguity. The legacy MoM path keeps raw text only. |
| Missing information / uncertain predictions | **Partial** | `follow_ups` lists missing deadlines; `review`/`rejected` items are excluded from the MoM. Confidences are fixed constants, not calibrated. |

No edge-case functionality was implemented during Phase 0.

---

## 8. Fixes completed during Phase 0

Only test infrastructure was changed. **No production code was modified**, and the uncommitted WIP files were left untouched.

| # | File | Change | Why | Verified by |
|---|---|---|---|---|
| 1 | `tests/__init__.py` (new, empty) | Makes `tests` a regular package so it wins over the `site-packages/tests` package installed by ultralytics | 8 `test_candidate_builder` failures (`ModuleNotFoundError`) | 9/9 pass in isolation and in the full run |
| 2 | `tests/conftest.py` | Redirects `config.STORAGE_ROOT` **and** `config.DB_PATH` to a canonical (`resolve()`d) temp dir *before* any other app module is imported; removes it at session end. The existing temp-DB fixture is preserved. | Each run wrote 12 meeting folders into the real `storage/`; the temp dir was never cleaned | Final full run: 0 new paths in `storage/` and `output/`, DB SHA-256 unchanged |
| 3 | `tests/test_meeting_isolation.py` | Replaced the "these `output/` paths must not exist" assertion with a before/after snapshot of every file (size + mtime) under `output/` | The test failed on pre-existing September prototype files, not on anything it wrote. The snapshot is stricter: it catches *any* new or modified file. | Test passes; a separate check confirmed the snapshot detects both new and modified files |
| 4 | `storage/` | Removed exactly the 12 folders my own baseline run created (each confirmed absent from the pre-run snapshot and timestamped during that run) | Restore your data to its original state | `storage/` tree identical to the pre-audit snapshot |

No assertion was weakened, no test was skipped or deleted, and no real functionality was replaced with mocks. Nothing has been committed.

---

## 9. Final verification (Task 10)

| Check | Result |
|---|---|
| Full suite re-run | 98 passed, 0 failed, 0 skipped (167.7 s) |
| Compared to baseline | +9 passing; no regressions; same 98 tests collected |
| Unwanted storage artifacts | None created (tree diff empty; DB checksum `abf8f46c…` unchanged) |
| Application startup | `uvicorn app.api.main:app` serves `/api/health` and `/openapi.json` (15 routes) |
| Frontend | `vite build` succeeds; `oxlint` 0 errors |
| Modified files reviewed | `tests/__init__.py`, `tests/conftest.py`, `tests/test_meeting_isolation.py`, plus the 5 docs in `docs/phase-0/` |
| Functionality removed | None |

---

## 10. Remaining blockers

| Blocker | Impact | Needed |
|---|---|---|
| No LLM API key configured | Can't verify LLM extraction quality or any non-stub provider | A key for one provider (paid service, so your decision) |
| No Hindi/Marathi/code-switched recording | Multilingual claims unverifiable | A short consented sample recording |
| No labelled ground truth | Can't measure WER, DER, or extraction precision/recall | A small annotated set (could start from the 4-min test meeting) |
| P0-1 is in your uncommitted WIP | Fixing it means editing your in-progress `jobs.py` | **Your approval** |
| Browser-level frontend test | UI flow verified only via HTTP | Manual run or a Playwright setup (new dependency) |

### Approval requested before I proceed
1. **Fix P0-1** in `app/core/jobs.py`: re-indent `_run_verification` so the claims-based body is inside the function and the stage is wrapped by `_run_stage`, then add a test that runs `run_pipeline` with stubbed stages. *(Edits your WIP file.)*
2. **Fix P1-1**: make the adapter/runner output UTF-8-safe (e.g. `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` at process start). *(Production code.)*
3. **Clean the production DB** of the 209 test-polluted meetings, their utterances and the 54 orphan jobs, after a backup. *(Destructive.)*
4. **Archive or remove legacy artifacts** (`app.zip`, `scripts.zip`, `frontend/src.zip`, `output/`, `member1_audio/`). *(Destructive.)*
5. **Commit** the Phase 0 test fixes and `docs/phase-0/` on a branch.

---

## 11. Recommended Phase 1 priorities (recommendations)

1. **Make the pipeline finish reliably.** Fix P0-1 and P1-1, mark `meetings.status` terminal, gate the UI on job completion, and run post-transcript stages exactly once. Add a `run_pipeline` integration test with stubbed models.
2. **Choose one intelligence pipeline.** Make the evidence-grounded claims path (DB) the single source for the UI and MoM, retire the duplicate `extraction.json`, and fail loudly when the provider is `stub` outside tests.
3. **Build an evaluation set before tuning.** Use 20–50 annotated utterances covering the Task 7 scenarios (the Phase 0 probe is a starting point), and track decision/action precision and recall per change.
4. **Implement the missing core features:** real summarization (grounded, with citations), speaker-name mapping (UI rename and/or self-introduction detection), and decision state handling (conditional, superseded).
5. **Wire NLI verification into the flow** in place of, or alongside, string matching.
6. **Data hygiene:** add migrations (or a schema version), a `jobs` FK, a retention policy, and a cleanup of the polluted DB rows; stop creating folders on GET.
7. **Reproducible environment:** a correct `requirements.txt` (or lock file), a venv-based setup script, and a CI job running `pytest -m "not slow"`.
8. **Security baseline before any shared deployment:** authentication, upload content validation, and sanitized error responses.
