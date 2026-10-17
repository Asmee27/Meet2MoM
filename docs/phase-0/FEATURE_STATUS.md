# Meet2MoM — Phase 0 Feature Status

> **Superseded for current status by `docs/phase-1/FEATURE_STATUS.md`** (Phase 1, 2026-10-16). This file is kept as the Phase 0 record.

> **Closure update (2026-10-16):** rows 22–23 changed after the P0-1 fix, and the pipeline no longer crashes on non-UTF-8 stdout (P1-1). Everything else is unchanged from the audit. See `PHASE_0_CLOSURE.md`. In the claims path the verifier now runs, but claim *actions* carry `text` rather than the `task`/`evidence_text` fields `EvidenceVerifier` reads, so they are matched against empty text (inferred from `evidence_verifier.py:1115-1135`, not separately measured). That's a known Phase 1 integration item, not fixed here.

Status legend:
- **WORKING**: executed successfully in this audit (real run or meaningful test).
- **PARTIAL**: runs, but incomplete or unreliable on realistic input.
- **BROKEN**: demonstrably fails.
- **NOT IMPLEMENTED**: no functional implementation.
- **UNVERIFIED**: code exists but couldn't be validated here.

## Evidence sources

| ID | Description |
|---|---|
| **E2E** | Real end-to-end run of `input/test_meeting.mp4` (248 s, English) through `POST /api/upload` (BackgroundTask executed), then `/api/transcribe`, `/api/extract`, `/api/verify`, `/api/generate-mom`, `/api/download-mom`. Ran on an isolated copy of the code with its own `storage/` and DB. |
| **PROBE** | 12 hand-written edge-case utterances run through both intelligence pipelines in a temp storage root. |
| **JOBS** | `run_pipeline` executed with stubbed ASR/diarization outputs, for both the `HEAD` and the working-copy `jobs.py`. |
| **HTTP** | `TestClient` probes against a temp storage root. |
| **DB** | Read-only queries on a byte-copy of `storage/meet2mom.db`. |

All artifacts (scripts and logs) are listed in `TEST_VERIFICATION.md` §6.

---

## 1. Summary matrix

| # | Feature | Status | Key evidence |
|---|---|---|---|
| 1 | Recording upload / ingestion | **WORKING** (weak validation) | E2E 202 in 0.26 s; HTTP: text bytes named `.mp4` accepted and queued |
| 2 | Audio validation, normalization, quality analysis, chunking | **WORKING** (untested) | E2E stages 1–5 completed; no unit tests |
| 3 | Speech-to-text (English) | **WORKING** | E2E: 34 segments, language `en`, ASR 99.97 s for 248 s (RTF ≈ 0.40) |
| 4 | Multilingual / code-switched ASR (Hindi, Marathi) | **UNVERIFIED** | No Hindi/Marathi recording in repo; only script-detection helpers exist |
| 5 | Speaker diarization | **WORKING** (accuracy unmeasured) | E2E: 5 speakers, 376.8 s; no ground truth, no DER |
| 6 | Speaker attribution / overlap flags | **WORKING** (untested) | E2E: `overlap`, `spk_conf` populated; "Uncertain speakers: 6" |
| 7 | Speaker-name resolution | **NOT IMPLEMENTED** | `speaker_name=None` hard-coded (`transcript_index.py:220`); MoM participants = `SPEAKER_00…04` |
| 8 | Transcript index → DB | **WORKING** | E2E: 34 utterances in DB; 7 passing tests |
| 9 | Topic segmentation | **PARTIAL** | E2E labels such as "Training / Exactly"; tests only exercise the TF-IDF fallback |
| 10 | Meeting summarization | **NOT IMPLEMENTED** (template) | `build_summary()` (`meeting_routes.py:1443`) concatenates topic labels and counts |
| 11 | Decision extraction, legacy regex path (feeds the MoM) | **PARTIAL** | E2E 2 decisions; PROBE: conditional accepted as final, reversal keeps both |
| 12 | Decision/action extraction, claims path, `llm` mode with default `.env` | **BROKEN by configuration** (always empty) | E2E and PROBE: `accepted: 0`; stub provider returns `[]` |
| 13 | Claims path, `rules` mode | **PARTIAL** | PROBE: 4 claims, the same regex results as #11/#14 |
| 14 | Action-item extraction | **PARTIAL** (very low recall) | PROBE: 1 of 5 genuine commitments found; E2E: 0 |
| 15 | Live LLM providers (OpenAI / Anthropic / Gemini / local) | **UNVERIFIED** | No API keys configured; `docs/baseline_report.md` records a Gemini 404 |
| 16 | Owner resolution | **PARTIAL** | Logic tested; in practice owners are `SPEAKER_xx` only |
| 17 | Deadline extraction / resolution | **PARTIAL** | Resolver tested; anchored to *upload* date; legacy path keeps raw text only |
| 18 | Transcript-grounded verification (`EvidenceVerifier`) | **PARTIAL** (untested) | PROBE: verified a conditional and two contradictory decisions |
| 19 | NLI verification layer (retrieval + mDeBERTa) | **WORKING in isolation, NOT INTEGRATED** | 5 real-model tests pass; not called by API or job |
| 20 | MoM generation (JSON) | **WORKING** (placeholder fields) | E2E: generated; `meeting_title="Meeting Minutes"`, `meeting_date=null` |
| 21 | PDF export | **WORKING** | E2E: valid `%PDF`, 3,960 bytes |
| 22 | Background job orchestration | **WORKING** (fixed at Phase 0 closure; was BROKEN) | `tests/test_job_runner.py` (5 tests: done / failed at merge / failed at verification / unwrapped errors). Closure E2E: `done/done/1.0` after a real 614 s run |
| 23 | Job status polling | **WORKING** (was: reported the wrong state) | Closure E2E: `/status` returned `done` |
| 24 | Content-hash pipeline cache | **PARTIAL / cache-hit UNVERIFIED** | E2E saved a cache entry; the hit path wasn't exercised |
| 25 | Persistence (SQLite + JSON) | **PARTIAL** | Data persists; `meetings.status` stale, `duration` never stored, no migrations |
| 26 | Existing-meeting listing / loading | **WORKING** | E2E `/api/meetings` returned the meeting (status still "processing") |
| 27 | Live meeting capture | **PARTIAL** (0 of 11 real uploads completed) | Browser MediaRecorder → upload of a `.webm`; not real-time; real DB: 9 failed, 2 stuck |
| 28 | Frontend application | **PARTIAL** | Builds; lint 0 errors / 123 warnings; job-state gating missing; no tests |
| 29 | Word export / e-mail export | **NOT IMPLEMENTED** (fabricated, unreachable) | Hard-coded "Project Development Meeting" summary (`MeetingFlowContext.jsx:844-881`) |
| 30 | Users / authentication / authorization | **NOT IMPLEMENTED** | No auth on any route; no users table |

---

## 2. Feature details

### 1. Upload / ingestion — WORKING (weak validation)
- **Files:** `app/api/meeting_routes.py:292-377`, `app/core/storage.py`
- **Implementation:** streams in 1 MB chunks, 2 GB cap (a partial file is deleted on overflow), stores as `recording.<ext>` under a UUID meeting ID, writes `meeting.json` twice, inserts the `meetings` row, queues `run_pipeline`.
- **Evidence:** E2E 202 in 0.26 s. HTTP: filename `../../evil.wav` is stored as `recording.wav` (traversal neutralised); invalid meeting IDs return 400.
- **Limitations:** extension-only check (plain text named `.mp4` → 202, queued). `.avi` is allowed by the API but rejected later by `AudioProcessor` (`SUPPORTED_EXTENSIONS` lacks it). DB insert failures are silently swallowed (`meeting_routes.py:355-363`).
- **Tests:** `test_async_jobs_api.py` (202 timing, `.exe` rejected). The background job is **never executed** by the tests.
- **Next action:** ffprobe-based content validation at upload; align the extension lists.

### 2. Audio processing — WORKING (untested)
- **Files:** `app/services/ingestion/audio_processor.py`, `audio_quality.py`
- **Implementation:** ffprobe inspection, ffmpeg normalization to 16 kHz mono with 80–7600 Hz band-pass and denoise, quality metrics, 300 s chunks with 8 s overlap.
- **Evidence:** E2E stages [1/9]–[5/9] completed and wrote `metadata/audio_quality.json`, `chunk_metadata.json`.
- **Next action:** unit tests with a short synthetic WAV.

### 3–4. ASR — WORKING (English), multilingual UNVERIFIED
- **Files:** `app/services/transcription/transcriber.py`, `language_utils.py`
- **Implementation:** faster-whisper `small`, auto language detection per chunk, initial prompt built from `TECHNICAL_VOCABULARY` + `PARTICIPANT_NAMES`, selective retry with beam 8 when >20 % of segments are low-confidence.
- **Evidence:** E2E transcript is coherent English ("Paul is the Human Resources Manager at Quartz Power Group…"), 34 segments.
- **Limitations:** model and device are hard-coded (`config.py:137`); `.env` `WHISPER_*` keys are ignored. Language is detected per 5-minute chunk, so intra-chunk code-switching depends entirely on Whisper. `asr_conf` and `no_speech_prob` are coarse (u00000 and u00001 share 0.8733 / 0.5448). Not tested at all.
- **Next action:** a short Hindi/Marathi/Hinglish recording as a regression fixture; measure WER.

### 5–6. Diarization and speaker attribution — WORKING, accuracy unmeasured
- **Files:** `app/services/diarization/diarizer.py`, `speaker_merger.py`
- **Evidence:** E2E "Speakers detected: 5", 376.8 s on CPU. The merger assigns each ASR segment the speaker with maximum overlap and flags overlaps ≥ 0.15 s.
- **Limitations:** segment-level (not word-level) attribution, so speaker changes inside a Whisper segment are lost. No DER evaluation. Diarization is ~1.5× real time on CPU. Not tested.
- **Next action:** a labelled clip (e.g. AMI) and DER measurement.

### 7. Speaker-name resolution — NOT IMPLEMENTED
- `transcript_index.py:220` sets `speaker_name=None`. No rename API or UI. `PARTICIPANT_NAMES` (`config.py`) is used only as an ASR prompt. The real DB contains 4,097 utterances named "Priya"; all belong to synthetic test meetings.

### 9. Topic segmentation — PARTIAL
- **Files:** `topic_segmenter.py` (801 lines)
- **Evidence:** E2E produced 6 topics; labels are keyword pairs, some with fillers ("Training / Exactly", "Look Report / Highlights").
- **Tests:** none direct. The two tests that run it set `sys.modules["sentence_transformers"] = None` (`test_async_jobs_api.py:14`, `test_meeting_isolation.py:3`), and that **leaks to the whole session**, so only the TF-IDF fallback is ever tested.

### 10. Summarization — NOT IMPLEMENTED (deterministic template)
- `build_summary()` (`meeting_routes.py:1443`) returns "The meeting covered A, B and C. N confirmed decisions were identified. No reliable action items were detected." E2E output was exactly that pattern. No abstractive or extractive summarizer exists; the docstring says it "can be replaced by a verified LLM summarizer" later.

### 11/13/14. Rule-based decision and action extraction — PARTIAL
- **Files:** `decision_extractor.py` (1,084 lines), `action_item_extractor.py` (1,436 lines), `rules_adapter.py`
- **PROBE results** (both pipelines produced the same items):

| Utterance | Expected | Got |
|---|---|---|
| "We decided to migrate our database to PostgreSQL." | decision | ✔ decision (speaker `UNKNOWN`) |
| "Bob will deploy the PostgreSQL cluster by Friday." | action (Bob, Friday) | ✘ **missed** |
| "I will send the report by tomorrow." | action (speaker, tomorrow) | ✔ action, owner SPEAKER_00, resolved 2026-10-17 |
| "Maybe we could switch to Kubernetes next quarter." | proposal only | ✔ not a decision |
| "No, that won't work, we are not going with Kubernetes." | rejection | ✔ not a decision |
| "If the budget is approved, we will hire two more engineers." | conditional | ✘ **accepted as final decision** (`state=accepted`, conf 0.65) |
| "Hypothetically, if I had time I would rewrite the parser." | not a commitment | ✔ excluded |
| "Actually, let's go with MySQL instead of PostgreSQL." | reversal (supersedes) | ⚠ accepted, **but the PostgreSQL decision is also kept**, so the MoM has contradictory decisions |
| "Someone needs to update the documentation sometime next week." | unassigned action | ✘ missed |
| "मैं शुक्रवार तक script ready कर दूँगी।" | first-person action (Hindi) | ✘ missed |
| "We all need to review the security checklist before launch." | group action | ✘ missed |
| "Last week I already fixed the login bug." | past action, not a task | ✔ excluded |

- **E2E:** 2 decisions ("Start with a review of the current training approach…", "Look at the report together."), 0 action items, for a meeting containing explicit commitments.
- **Other limitations:** decision timestamps are **linearly interpolated** within a topic (`estimate_timestamp`), e.g. 24.33 s reported for an utterance at 25.0 s. Speaker is `UNKNOWN` unless the topic has a single speaker. Confidences are fixed constants (0.9 / 0.78 / 0.65). English-only patterns.
- **Next action:** build a small labelled evaluation set (precision/recall) before changing extractors.

### 12/15. LLM extraction — BROKEN by default configuration, providers UNVERIFIED
- **Files:** `llm_extractor.py`, `app/core/llm_client.py`, `extraction_pipeline.py`
- **Implementation:** well-structured prompt with mandatory `u#####` evidence citations, a per-window disk cache, span validation that drops invented IDs. Falls back to rules on `LLMClientError`.
- **Evidence:** the repo `.env` has `LLM_PROVIDER=stub`, `EXTRACTOR=llm`, so the stub returns `{"decisions":[],"actions":[]}` → `accepted: 0` (E2E and PROBE). Nothing warns the user that this mode is a no-op.
- **Next action:** fail loudly (or default to `rules`) when `provider=stub` outside tests; verify one real provider.

### 16–17. Owner and deadline resolution — PARTIAL
- **Files:** `owner_resolver.py`, `deadline_resolver.py`, applied in `claim_writer.py:48-87` (claims path only)
- **Evidence:** 6 tests pass (weekday arithmetic, Hindi/Marathi day words, event-anchored deadlines, group/unassigned). PROBE resolved "by tomorrow" → 2026-10-17.
- **Limitations:** the anchor date is `meetings.created_at` (upload time, `claim_writer.py:50-53`), not the recording date. Hindi "kal" is mapped only to *tomorrow* (it also means *yesterday*, `deadline_resolver.py:21`). These resolvers never reach the MoM, because the MoM uses the legacy path.

### 18. EvidenceVerifier — PARTIAL
- **Files:** `app/services/verification/evidence_verifier.py` (1,821 lines)
- **Implementation:** difflib/token similarity between claim text and transcript segments, timestamp tolerance, speaker-reliability penalties.
- **Evidence:** E2E verified 2/2 decisions (1 with warning). PROBE marked all 4 claims `verified`, including the conditional and both contradictory decisions. It checks *textual presence*, not entailment or state.
- **Tests:** none.

### 19. NLI verification layer — WORKING in isolation, NOT INTEGRATED
- **Files:** `nli_scorer.py`, `retrieval_index.py`, `candidate_builder.py`, `app/core/model_registry.py`
- **Evidence:** 5 `@slow` tests with the real mDeBERTa model pass (supported / unsupported / contradicted / wrong owner / code-switch). Retrieval tests use a hash-based fake embedder.
- **Gap:** only `scripts/report_meeting.py` calls it, and that script reads the production DB.

### 20–21. MoM JSON and PDF — WORKING with placeholder fields
- **Evidence:** E2E `mom.json` with 5 participants (`SPEAKER_xx`), 6 discussion points, 2 decisions, 0 actions; PDF valid.
- **Placeholders:** `meeting_title` is always "Meeting Minutes", `meeting_date` is always `null` (`meeting_routes.py:1911-1915`), and the summary is the template from #10.

### 22–23. Background job and status — BROKEN
- **Files:** `app/core/jobs.py`
- **JOBS evidence:**
  - `HEAD`: final status `stage=verification, state=failed, error="extraction.json not found — run extraction first"`. Even with the file present, `HEAD` calls `EvidenceVerifier.verify()` (`git show HEAD:app/core/jobs.py`, line 186), which does not exist. The real DB records this exact failure for two real meetings: `'EvidenceVerifier' object has no attribute 'verify'`.
  - Working copy: `NameError: name 'EvidenceVerifier' is not defined` escapes `run_pipeline`; status stays `verification/running`; the `done` emission (line 328) is unreachable after `return` (line 305).
- **E2E:** after a 577 s job, status remained `verification / running / progress 0.8182`.
- **Next action:** fix the indentation and scope (needs approval, since it's your WIP file); add a test that runs `run_pipeline` with stubbed stages.

### 24. Content-hash cache — PARTIAL
- E2E saved `storage/_pipeline_cache/579fdd33…` (the same hash as the existing real cache entry for this recording). The cache-hit path wasn't exercised in this audit. `docs/baseline_report.md` reports ASR cache hits. Only transcript/diarization/metadata are cached; the recording itself is duplicated per upload.

### 27. Live meeting — PARTIAL
- **Files:** `frontend/src/pages/LiveMeeting.jsx` (untracked)
- `getUserMedia` + `MediaRecorder`, then the blob is uploaded to the hard-coded `http://127.0.0.1:8000/api/upload`. Processing starts only after recording stops; no streaming ASR.
- Real DB: 11 `live-meeting-*.webm` uploads. **None completed**: 9 `failed`, 2 stuck `running`. Failure causes: 3 × "transcript artifact did not contain usable speech segments", 3 × the verification-stage job bug, 2 × missing `rapidfuzz` dependency at the time, 1 × native crash (KERNEL32 trace).

### 28. Frontend — PARTIAL
- `vite build` succeeds (244 kB JS). `oxlint`: 0 errors, 123 warnings (119 unused variables). No frontend tests.
- "Continue to Transcribe" isn't gated on `jobStage === "done"` (`UploadMeeting.jsx:291`).
- Dead stub pages and unused dependencies (see Architecture §6).

### 30. Security / users — NOT IMPLEMENTED
- No authentication; any client that can reach the API can list meetings and download transcripts and MoMs. CORS is restricted to localhost:5173 (verified: a foreign origin isn't echoed).
