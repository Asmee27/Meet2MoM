# Meet2MoM — Phase 0 Architecture Audit

Audit date: 2026-10-16 · Branch: `main` @ `9e9548d` plus uncommitted changes (listed in §7).
Everything below was derived from the source code and from runtime experiments. It was not taken from the README or earlier reports.

---

## 1. Technology stack (as actually used)

| Layer | Technology | Evidence |
|---|---|---|
| Backend language | Python 3.11.0 (only interpreter installed) | `python --version`, `py -0` |
| API framework | FastAPI (pinned 0.104.1, **installed 0.141.1**), Starlette 1.6.0, served by uvicorn (pinned 0.24.0, **installed 0.52.3**) | `app/api/main.py`, `pip list` |
| Frontend | React 19 + Vite 8 (JavaScript, no TypeScript), oxlint | `frontend/package.json` |
| ASR | faster-whisper 1.2.1, model `small`, CPU, int8, VAD on, beam 5 (retry beam 8) | `app/services/transcription/transcriber.py`, `app/core/config.py:137` |
| Diarization | pyannote.audio 4.0.7, `pyannote/speaker-diarization-community-1`, needs `HF_TOKEN`/`HUGGINGFACE_TOKEN` | `app/services/diarization/diarizer.py` |
| Topic segmentation | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` embeddings, TF-IDF fallback (scikit-learn) | `app/services/intelligence/topic_segmenter.py:8-65` |
| Decision / action extraction | (a) regex rule engines; (b) LLM client (stub / OpenAI / Anthropic / Gemini / local over raw `urllib`) | `decision_extractor.py`, `action_item_extractor.py`, `app/core/llm_client.py` |
| Verification | (a) `EvidenceVerifier`: string similarity (difflib) plus timestamp/speaker heuristics; (b) NLI scorer (`MoritzLaurer/mDeBERTa-v3-base-mnli-xnli`) with BM25 + embedding retrieval | `app/services/verification/*` |
| Resolution | rapidfuzz (owner), python-dateutil plus a custom table (deadline, English/Hindi/Marathi words) | `owner_resolver.py`, `deadline_resolver.py` |
| PDF | reportlab 4.4.10 | `app/api/meeting_routes.py:2107-2478` |
| Database | SQLite (`storage/meet2mom.db`), raw `sqlite3`, no ORM, no migration tool | `app/core/db.py` |
| File storage | Local filesystem, `storage/<meeting_id>/…` | `app/core/storage.py` |
| Media tooling | FFmpeg/ffprobe on PATH (installed via winget) | `which ffmpeg` |
| Tests | pytest 9.0.2 (pinned, **not installed in the global env**), `slow` marker | `pytest.ini`, `requirements.txt` |
| Dependency management | `requirements.txt` (pip, partly pinned), `package.json` + `package-lock.json` (npm) | — |
| Deployment | **None.** No Dockerfile, compose file, CI workflow or process manager config | `ls` of repo root |

---

## 2. Entry points

| Entry point | Purpose | Status |
|---|---|---|
| `uvicorn app.api.main:app` | FastAPI app (CORS limited to `localhost:5173` / `127.0.0.1:5173`) | Starts; `/api/health` answered after ~8 s (verified) |
| `python -m scripts.run_recorded_pipeline <file> --meeting-id <id>` | Stages 1–9 (validate → report) plus post-transcript stages. Also spawned as a **subprocess** by the API | Ran successfully on `input/test_meeting.mp4` (9 m 09 s) |
| `scripts/report_meeting.py <id>` | Diagnostic report. The **only** runtime consumer of the NLI/retrieval verification layer. Reads the real DB directly | Not executed (it reads the production DB) |
| `scripts/clean_test_meetings.py` | Deletes test-pattern rows from the DB (not files) | Not executed (destructive) |
| `npm run dev` / `vite build` (in `frontend/`) | UI | Build succeeds (verified to a scratch outDir) |
| Root scripts `chunk_audio.py`, `transcribe_chunks.py`, `resume_transcription.py`, `generate_chunk_metadata.py`, `test_audio.py`, `member1_audio/run_pipeline.py` | Early prototypes writing to `output/` | Abandoned (see §6) |

---

## 3. API surface (verified from the live OpenAPI document)

| Method | Path | Implementation | Used by frontend? |
|---|---|---|---|
| POST | `/api/upload`, `/api/meetings/upload` | Streams the file into `storage/<id>/recording/recording.<ext>`, inserts the `meetings` row, queues `run_pipeline` as a BackgroundTask, returns 202 | Yes |
| GET | `/api/meetings` | Lists meetings with utterances (hard-coded test-name filters) | Yes (existing-meeting loader) |
| GET | `/api/meetings/{id}/status` | Reads the `jobs` row; falls back to `pipeline_status.json` | Yes (polling) |
| GET | `/api/meetings/{id}/transcript` | Utterances from the DB | Defined in `api.js`; Transcript page uses the context data |
| POST | `/api/process/{id}` | Runs the whole recorded pipeline **synchronously** | No |
| GET | `/api/status/{id}` | File-based pipeline status | Fallback only |
| POST | `/api/extract-audio/{id}` | **No-op.** Only confirms the file exists | Yes |
| POST | `/api/transcribe/{id}` | Returns the cached `frontend_transcript.json`, **otherwise runs the full pipeline synchronously** | Yes |
| POST | `/api/extract/{id}` | Legacy JSON intelligence path: TopicSegmenter → DecisionExtractor → ActionItemExtractor | Yes |
| POST | `/api/verify/{id}` | `EvidenceVerifier` on the legacy JSON outputs | Yes |
| POST | `/api/generate-mom/{id}` | Builds `mom.json` and the PDF | Yes |
| GET | `/api/download-mom/{id}` | Serves the PDF, generating it if missing | Yes |
| GET | `/`, `/api/health` | Liveness | Yes |

No endpoint requires authentication. There are no delete, update or speaker-rename endpoints.

---

## 4. Component and data flow (actual)

```
Browser (React)                    FastAPI process                                   Subprocess
───────────────                    ───────────────                                   ──────────
Upload ──POST /api/upload──► save recording, meetings row, BackgroundTask run_pipeline
                                   │
                                   ├─► jobs.run_pipeline (app/core/jobs.py)
                                   │     emits status rows to `jobs` table
                                   │     └─► run_recorded_pipeline(run_post_transcript=False)
                                   │            sha256 → content-hash cache check
                                   │            └─► python -m scripts.run_recorded_pipeline ──► validate → ffmpeg normalize
                                   │                                                          → audio quality → chunk (300 s/8 s)
                                   │                                                          → faster-whisper → pyannote
                                   │                                                          → speaker merge → report
                                   │                                                          → run_post_transcript_stages  (#1)
                                   │     └─► run_post_transcript_stages (#2: index → topics → claims)
                                   │     └─► verification stage  ✗ BROKEN (see §5.1)
Poll /status  ◄────────────────────┘
"Continue" (not gated on job state)
  └─POST /api/extract-audio  (no-op)
  └─POST /api/transcribe ────► returns frontend_transcript.json (or re-runs the pipeline if not there yet)
  └─POST /api/extract ───────► TopicSegmenter (#3) → regex DecisionExtractor → regex ActionItemExtractor
  └─POST /api/verify ────────► EvidenceVerifier (string similarity)
  └─POST /api/generate-mom ──► template summary + verified items → mom.json + PDF
  └─GET  /api/download-mom
```

Observed in the real end-to-end run: topic segmentation executed **three times** for one upload (`e2e_log.txt`: 3 × "Semantic Topic Segmentation").

### Two parallel intelligence pipelines

| | Pipeline A: "claims" (DB) | Pipeline B: "legacy JSON" |
|---|---|---|
| Triggered by | background job and the CLI runner | frontend via `/api/extract` |
| Code | `extraction_pipeline.py` → `llm_extractor.py` **or** `rules_adapter.py` → `span_validator.py` → `claim_writer.py` | `topic_segmenter.py`, `decision_extractor.py`, `action_item_extractor.py` |
| Output | `claims`, `evidence_links`, `dropped_claims` tables plus `intelligence/claims.json` (and, uncommitted, `intelligence/extraction.json`) | `intelligence/{topics,decisions,action_items,extraction}.json` |
| Evidence grounding | Strict: every claim needs `u#####` IDs validated against the DB (FK-enforced) | Approximate: text matching and interpolated timestamps |
| Default output with the repo's `.env` (`LLM_PROVIDER=stub`, `EXTRACTOR=llm`) | **Always 0 claims** (the stub returns an empty list) | Real regex results |
| Feeds the MoM? | **No** | **Yes** |

Rules mode in Pipeline A wraps the *same* regex extractors as Pipeline B (`rules_adapter.py`). So the project's only working decision/action logic is the regex engine, whichever path runs.

The NLI verification layer (`nli_scorer.py`, `retrieval_index.py`, `candidate_builder.py`, introduced in commit `9e9548d`) is **not called by any API route or by the job runner**. Only `scripts/report_meeting.py` and tests call it.

---

## 5. Major architectural concerns

1. **The background job never reaches `done`.** (P0, details in `PHASE_0_FINAL_REPORT.md`.) The committed version calls the non-existent `EvidenceVerifier.verify()`. The uncommitted version mis-indents `_run_verification` (`app/core/jobs.py:180-311`), so the code after the `return` at line 305 never runs, and an unhandled `NameError` leaves the job at `verification/running` forever.
2. **Two sources of truth.** The UI and MoM read Pipeline B's JSON files. `/api/meetings` shows Pipeline A's DB `claim_count`. In the E2E run the MoM listed 2 decisions while the DB held 0 claims.
3. **Uncommitted file collision.** The uncommitted `claim_writer.py:156` writes `intelligence/extraction.json` in the *claims* schema. `/api/extract` writes the same path in the *legacy* schema, and `create_mom()` reads whichever wrote last, expecting `topics`.
4. **Duplicate execution and a race.** The CLI subprocess and the job runner both run the post-transcript stages. The UI's "Continue to Transcribe" button is shown whenever a file is selected (`frontend/src/pages/UploadMeeting.jsx:291`), so `/api/transcribe` can start a *second* full pipeline while the background job is still running.
5. **Global mutable configuration.** `config.set_active_meeting()` rewrites module globals across every loaded `app.*`/`scripts.*` module and `os.environ`. That works only because the heavy work runs in a separate subprocess under `_PIPELINE_LOCK`. Any in-process concurrency would cross-contaminate meetings.
6. **Configuration is mostly hard-coded.** `.env.example` advertises `WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_COMPUTE_TYPE`, `ENABLE_PIPELINE_CACHE`, `CACHE_*`, `HOST`, `PORT`, `FRONTEND_URL`, but `app/core/config.py` never reads them. Only `LLM_*`, `EXTRACTOR`, `GEMINI_API_KEY`, `HF_TOKEN`/`HUGGINGFACE_TOKEN` and `MEETING_ID` are read from the environment. `STORAGE_ROOT`/`DB_PATH` are fixed to the repo directory.
7. **Import-time side effects.** Importing `meeting_routes` creates `storage/`. Importing the pipeline adapter creates `storage/_pipeline_cache/`. Many services `mkdir` in their constructors.
8. **Platform fragility.** The adapter prints `✓`/`⚠` with plain `print()`. When the server's stdout isn't UTF-8 (redirected output on Windows, cp1252), the first print raises `UnicodeEncodeError` and the job fails at `merge` within 0.3 s (reproduced: `e2e_report_attempt1_cp1252.json`).
9. **Monolithic route module.** `app/api/meeting_routes.py` (2,579 lines) mixes HTTP handling, MoM assembly, summary templating and PDF layout.
10. **No deployment story.** No container, no process supervisor, no job queue (BackgroundTasks run in the API process, so a restart loses running jobs and leaves `jobs.state='running'` forever; the real DB has 4 such real rows plus 54 test-polluted ones).

---

## 6. Unused, duplicated, abandoned or conflicting code

| Item | Finding |
|---|---|
| `member1_audio/` | Only `run_pipeline.py` plus **committed `.pyc` files** for modules that no longer exist there (`audio_processor`, `diarizer`, …). Abandoned predecessor of `app/services/*`. |
| `chunk_audio.py`, `transcribe_chunks.py`, `resume_transcription.py`, `generate_chunk_metadata.py`, `test_audio.py` (repo root) | Prototype scripts that hard-code the `output/` paths. `test_audio.py` matches the pytest name pattern but sits outside `testpaths`, so it isn't collected. Running `pytest .` would execute Whisper at import. |
| `output/` (31 MB, untracked) | Artifacts from the prototype scripts (`transcript.json`, `test_meeting.wav`, `chunks/`, …). Not used by the current app. |
| `app.zip`, `scripts.zip`, `frontend/src.zip` (untracked) | Manual backups. `app.zip/app/core/jobs.py` is byte-identical to `HEAD`. Can serve as rollback copies, otherwise redundant. |
| `frontend/src/pages/Dashboard.jsx`, `ActionItems.jsx`, `Topics.jsx` | 3-line re-exports / `return null`. Not routed. |
| `react-router-dom`, `lucide-react` | Declared dependencies, never imported. |
| `MeetingFlowContext.jsx:844-881` (`exportWordDocument`, `sendMeetingEmail`) | Hard-coded **fabricated** content ("Project Development Meeting", a fixed summary paragraph). Destructured in `Summary.jsx`/`Decisions.jsx` but never called, so unreachable today. |
| `POST /api/extract-audio/{id}` | No-op kept for frontend compatibility. |
| `POST /api/process/{id}`, `GET /api/status/{id}` | Legacy synchronous path, duplicating the job runner. |
| `app/schemas/verification.py::VerificationResult` | Not referenced by any runtime code. |
| `storage/<id>/…` vs `storage/<id>/results/…` and root-level duplicates | Three overlapping layouts. Every route writes JSON twice (subfolder plus meeting root). |
| `app/core/llm_client.py` providers | Stub verified. OpenAI, Anthropic, Gemini and local are unexercised in this audit (no keys). |

---

## 7. Uncommitted work present at audit start

| File | Change | Assessment |
|---|---|---|
| `app/core/jobs.py` | Rewrites the verification stage to read `claims.json` | **Broken**: mis-indented, unreachable tail, NameError (reproduced) |
| `app/services/intelligence/claim_writer.py` | Adds a second write to `intelligence/extraction.json`; adds `from scipy import stats` above the module docstring | Schema collision with `/api/extract`. The stray import is shadowed by a local `stats` variable and turns the docstring into a plain string |
| `frontend/src/App.jsx`, `App.css`, untracked `LiveMeeting.jsx` | "Choose meeting source" screen plus browser recording (MediaRecorder → upload) | Builds. LiveMeeting hard-codes `http://127.0.0.1:8000` (`LiveMeeting.jsx:293`) instead of `VITE_API_BASE_URL` |

None of these files were modified during Phase 0.
