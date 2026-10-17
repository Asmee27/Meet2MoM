# Meet2MoM — Phase 0 Test Verification

Every number below comes from a test run executed during this audit (2026-10-16). Nothing is copied from earlier documentation.

---

## 1. Environment

| Item | Value |
|---|---|
| OS | Windows 11 Home Single Language 10.0.26200 |
| Python | 3.11.0 (the only interpreter installed) |
| Node / npm | 23.4.0 / 10.9.2 |
| FFmpeg / ffprobe | on PATH (winget install) |
| Test runner | pytest 9.0.2 |
| Hardware path | CPU only (Whisper `int8`, pyannote on CPU) |
| Cached HF models | faster-whisper small/base, pyannote community-1, mDeBERTa-v3-base-mnli-xnli, paraphrase-multilingual-MiniLM-L12-v2, all-MiniLM-L6-v2, cross-encoder nli-deberta-v3-base |

### Dependency blockers found

- **`pytest` and `rank-bm25` are pinned in `requirements.txt` but were not installed** in the global interpreter. `sentencepiece` (also pinned) is missing too, but no test needed it.
- To avoid modifying the global environment, the audit used an isolated virtualenv in the session scratchpad:
  ```
  python -m venv --system-site-packages <scratchpad>/venv
  <scratchpad>/venv/Scripts/python -m pip install pytest==9.0.2 rank-bm25==0.2.2
  ```
- **Requirements drift** (pinned vs installed): fastapi 0.104.1 vs **0.141.1**, uvicorn 0.24.0 vs **0.52.3**, python-multipart 0.0.6 vs **0.0.32**, pydantic 2.12.5 vs **2.13.4**, scikit-learn 1.6.1 vs **1.7.2**, python-dotenv 1.0.0 vs **1.2.2**. `httpx` (required by `fastapi.testclient`) and `scipy` (imported by the uncommitted `claim_writer.py`) aren't listed.
- A third-party package (**ultralytics**) installed a top-level `tests` package in global `site-packages`. Because the repo's `tests/` had no `__init__.py`, Python's import system preferred that regular package over the repo's namespace directory, so `from tests.test_retrieval_index import _meeting` failed. This caused 8 of the 9 baseline failures.

---

## 2. Commands executed

```bash
# discovery
<venv>/python -m pytest --collect-only -q                        # 98 tests collected in 21.9 s

# baseline (no changes)
<venv>/python -m pytest -rA --durations=15 -p no:cacheprovider --junitxml=baseline.xml

# targeted re-runs after each fix
<venv>/python -m pytest -p no:cacheprovider -q tests/test_candidate_builder.py tests/test_meeting_isolation.py \
    tests/test_storage.py tests/test_transcript_index.py tests/test_async_jobs_api.py tests/test_claim_writer.py \
    tests/test_extraction_pipeline.py tests/test_llm_extractor.py
<venv>/python -m pytest -p no:cacheprovider -q tests/test_storage.py tests/test_meeting_isolation.py tests/test_candidate_builder.py

# final full run
<venv>/python -m pytest -rA --durations=10 -p no:cacheprovider --junitxml=final.xml

# frontend
cd frontend && npx oxlint && npx vite build --outDir <scratchpad>/fe_dist
```

`-p no:cacheprovider` was used so the runs didn't create `.pytest_cache/` in the repo.

---

## 3. Results

### 3.1 Baseline (before any change)

| Metric | Value |
|---|---|
| Collected | **98** |
| Passed | **89** |
| Failed | **9** |
| Skipped | 0 |
| Errors (collection/setup) | 0 |
| Duration | 264.8 s pytest-reported (4 m 31 s wall) |
| Warnings | 8 (HF symlink cache warning, `torch.jit.script` deprecation, …) |

| Failing test | Root cause |
|---|---|
| `test_candidate_builder.py` (8 of 9 tests) | `ModuleNotFoundError: No module named 'tests.test_retrieval_index'`. The global `site-packages/tests` package (ultralytics) shadowed the repo folder. **Environment/test-config problem, not a product bug.** |
| `test_meeting_isolation.py::test_two_meetings_fully_independent` | `assert not (output/transcripts/meeting_transcript_ready.json).exists()` failed because `output/` holds **pre-existing** files from the September prototype scripts. The test asserted on workspace state rather than on its own behaviour. |

Per-file baseline results:

| File | Pass | Fail | Time |
|---|---:|---:|---:|
| test_async_jobs_api | 5 | 0 | 0.1 s |
| test_candidate_builder | 1 | 8 | 5.5 s |
| test_claim_writer | 5 | 0 | 0.4 s |
| test_config | 1 | 0 | 0.0 s |
| test_context_builder | 4 | 0 | 0.1 s |
| test_context_builder_scale | 1 | 0 | 0.0 s |
| test_db | 8 | 0 | 0.3 s |
| test_extraction_pipeline | 4 | 0 | 0.4 s |
| test_llm_extractor | 5 | 0 | 0.0 s |
| test_meeting_isolation | 0 | 1 | 0.3 s |
| test_model_registry | 2 | 0 | 0.0 s |
| test_nli_scorer | 19 | 0 | 253.0 s |
| test_resolvers | 6 | 0 | 0.0 s |
| test_retrieval_index | 7 | 0 | 0.6 s |
| test_schemas | 4 | 0 | 0.1 s |
| test_span_validator | 6 | 0 | 0.9 s |
| test_storage | 4 | 0 | 0.0 s |
| test_transcript_index | 7 | 0 | 0.2 s |

Slowest: `test_nli_scorer.py::test_real_model_supported` 209.4 s (loads the real mDeBERTa model). The four other real-model tests took 9.7–11.9 s each.

### 3.2 Final (after Phase 0 fixes)

| Metric | Value |
|---|---|
| Collected | **98** |
| Passed | **98** |
| Failed | **0** |
| Skipped | 0 |
| Errors | 0 |
| Duration | 167.7 s pytest-reported |
| Warnings | 7 |

All 9 previously failing tests now pass. No previously passing test changed state. Model-load time varies between runs (`test_nli_scorer` took 253 s at baseline and 135 s in the final run).

### 3.3 Frontend

| Check | Result |
|---|---|
| `vite build` | **Success**: 26 modules, `index.js` 244.5 kB (73.0 kB gzip), 3.05 s |
| `oxlint` | **0 errors, 123 warnings**: 119 `no-unused-vars`, 3 `react(set-state-in-effect)`, 1 `react(only-export-components)` |
| Frontend tests | **None exist** |

---

## 4. Comparison with existing documentation

| Claim | Source | Actual |
|---|---|---|
| "54 passed" | `docs/baseline_report.md` §Validation | Stale. 98 tests exist today |
| "Full test suite: 56 passed" | `docs/baseline_report.md` §Task 3.0 | Stale. 98 tests exist today |
| "98 tests pass" | earlier claim referenced in the Phase 0 brief | **98 collected, but only 89 passed** on this machine before the fixes; 98/98 after the Phase 0 test-infrastructure fixes |
| "LLM-stub mode produced zero claims … expected stub behavior" | `docs/baseline_report.md` | Confirmed (E2E and probe: `accepted: 0`). But the repo `.env` makes this the *default* mode |
| ASR real-time ratio 1.722 | `docs/baseline_report.md` | Measured 99.97 s for 248.13 s audio (RTF ≈ 0.40) in this audit's E2E run. Different conditions, so not directly comparable |
| Diarization 1274–1731 s | `docs/baseline_report.md` | Measured 376.8 s in this audit |

---

## 5. Test quality assessment

### 5.1 What the suite does validate well
- DB constraints: FK enforcement, rollback on bad evidence, idempotent re-persistence (`test_db.py`, `test_claim_writer.py`).
- Span validation: unresolvable IDs, empty evidence, out-of-window citations, duplicates across overlapping windows (`test_span_validator.py`).
- Context windowing at 300-utterance scale (`test_context_builder_scale.py`).
- Transcript index ID stability and confidence handling (`test_transcript_index.py`).
- Deadline/owner resolver tables, including Hindi/Marathi day words (`test_resolvers.py`).
- NLI scoring with the **real** model on 5 cases (`test_nli_scorer.py::test_real_model_*`, marked `slow`).

### 5.2 Tests that pass without validating the claimed behaviour

| Test | Issue |
|---|---|
| `test_async_jobs_api.py` (module docstring) | Claims to test "non-blocking job execution" and "corrupt/invalid file handling". The `BackgroundTasks` object is never run, so **`run_pipeline` is never executed by any test**. The upload test sends fake bytes (`b"fake-audio-content…"`) and asserts 202, which effectively locks in the missing content validation. |
| `test_upload_returns_202_under_two_seconds` | Timing-based (`< 2.0 s`). Potentially flaky on a loaded machine. |
| `test_meeting_isolation.py` | Exercises extract → verify → MoM on a 2-sentence transcript. It asserts only that files exist and keywords stay in their own meeting. It never checks extracted content: in that very run the action extractor found **0** action items for "Bob will deploy the PostgreSQL cluster by Friday." and the test still passed. |
| `test_extraction_pipeline.py`, `test_llm_extractor.py` | All LLM paths use the stub client with canned responses. They verify plumbing, not extraction quality. |

### 5.3 Heavy mocking

| Area | Mock | Consequence |
|---|---|---|
| Embeddings (`test_retrieval_index.py`, `test_candidate_builder.py`) | `RetrievalIndex._load_model` patched to a SHA-256 bag-of-words model | Real semantic retrieval is never tested |
| NLI logic tests (12 of 19) | `get_hf_pipeline` patched to a fake pipeline | Fine for logic; the 5 slow tests cover the real model |
| `sentence_transformers` | `sys.modules["sentence_transformers"] = None` at import in `test_async_jobs_api.py:14` and `test_meeting_isolation.py:3` | **Leaks to the whole session**: every test that touches `TopicSegmenter` uses the TF-IDF fallback, never the production embedding path. Order-dependent by construction |

### 5.4 Untested components (0 direct tests)
`transcriber.py`, `diarizer.py`, `speaker_merger.py`, `audio_processor.py`, `audio_quality.py`, `cache_manager.py`, `processing_report.py`, `recorded_pipeline_adapter.py`, `jobs.run_pipeline`, `topic_segmenter.py` (semantic path), `decision_extractor.py` / `action_item_extractor.py` (only indirectly), `evidence_verifier.py` (only indirectly), `rules_adapter.py` (only indirectly), `create_mom` / `build_pdf` content, `/api/extract`, `/api/verify`, `/api/generate-mom`, `/api/download-mom` over HTTP, and the entire frontend.

### 5.5 Flakiness and order dependence
- After the fixes, one full run and one subset run (files in non-alphabetical order) passed consistently. An earlier subset run exposed a bug in my own fixture (an 8.3 short path), which was fixed before the final run. A randomized-order run was **not** performed (no `pytest-randomly` installed; not added, to avoid new dependencies).
- Known order-dependence: the `sentence_transformers` stub above, and `test_candidate_builder` importing helpers from `test_retrieval_index`.

### 5.6 Side effects of running the tests (baseline)
- The DB **was** isolated: the `storage/meet2mom.db` SHA-256 was unchanged (`abf8f46c…`).
- Storage was **not** isolated. One baseline run created **12 new meeting folders in the real `storage/`**: `llm_cache/` folders from the claims tests, full extract/verify/MoM output from the isolation test (left behind because the test failed before its cleanup line), an uploaded `recording.mp3` from the async upload test, NLI caches, and retrieval embeddings.
- Earlier, pre-conftest test runs had already polluted the real DB: 181 meetings named `test.mp4`, 28 named `test_audio.mp3`, 54 orphan `jobs` rows (see `DATABASE_STORAGE_AUDIT.md`).
- The first real-model NLI test downloads/loads the model into `~/.cache/huggingface` (outside the repo).

---

## 6. Runtime verification artifacts (outside the repo, session scratchpad)

| Artifact | Purpose |
|---|---|
| `e2e_driver.py`, `e2e_report.json`, `e2e_log.txt` | Real upload → job → extract → verify → MoM → PDF on `input/test_meeting.mp4` in an isolated code copy |
| `e2e_report_attempt1_cp1252.json` | First E2E attempt, which failed in 0.3 s with `UnicodeEncodeError` when stdout was cp1252 |
| `e2_jobs.py`, `jobs_head.py` | `run_pipeline` with stubbed stages, HEAD vs working copy |
| `e3_extract.py` | 12-utterance edge-case probe through both intelligence pipelines |
| `e4_http.py` | HTTP side-effect and validation probes |
| `baseline.txt/.xml`, `final.txt/.xml` | Raw pytest output |
| `storage_before.tsv`, `storage_after_baseline.tsv`, `storage_*_final.tsv` | Storage tree snapshots used for the before/after comparisons |
| `meet2mom.db.backup` | Byte copy of the production DB, used for all read-only DB queries |

### Clearly separated: genuine E2E vs isolated tests
- **Genuine E2E (real models, real file):** upload, BackgroundTask, subprocess pipeline (ffmpeg, Whisper, pyannote, merge, report), index, topics, claims extraction (stub LLM), then `/api/transcribe`, `/api/extract` (regex), `/api/verify`, `/api/generate-mom`, PDF download. Ran with `PYTHONIOENCODING=utf-8`; without it the pipeline fails immediately.
- **Not real in that run:** LLM extraction (stub provider), NLI verification (not wired into the flow), frontend UI interaction (exercised via HTTP only, not in a browser).
- **The pytest suite contains no end-to-end test.** `test_meeting_isolation.py` is the closest, and it starts from a hand-written transcript.
