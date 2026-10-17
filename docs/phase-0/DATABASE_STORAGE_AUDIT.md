# Meet2MoM — Phase 0 Database & Storage Audit

Production data was never modified. All DB queries ran read-only (`mode=ro`) against a byte copy of `storage/meet2mom.db` (SHA-256 `abf8f46c…`, unchanged before and after the audit). All write experiments used temporary storage roots or an isolated copy of the code.

---

## 1. Schema (from `app/core/db.py::init_db`)

```
meetings        (meeting_id PK, filename, duration, created_at, status)
utterances      (meeting_id FK→meetings, utt_id, start, end, speaker_id, speaker_name, text,
                 lang_tags JSON, script, asr_conf, spk_conf, no_speech_prob, conf_source,
                 overlap, snr, topic_id,                    PK(meeting_id, utt_id))
claims          (claim_id PK, meeting_id FK→meetings, claim_type, text, payload JSON, created_at)
evidence_links  (claim_id FK→claims ON DELETE CASCADE,
                 (meeting_id, utt_id) FK→utterances ON DELETE RESTRICT, PK(claim_id, utt_id))
dropped_claims  (drop_id PK AUTOINCREMENT, meeting_id FK→meetings, window_id, claim_type,
                 text, cited_evidence JSON, reason, detail, created_at)
jobs            (meeting_id PK, stage, progress, state, error, updated_at)      -- no FK
```

`PRAGMA foreign_keys = ON` is set on every connection (`get_conn`). Each `get_conn` block is one transaction (commit on success, rollback on exception).

### Entity coverage vs. the product concept

| Entity | Exists? | Where it lives |
|---|---|---|
| Users | ✘ | — |
| Meetings | ✔ | `meetings` |
| Participants / speakers | ✘ (no table) | `utterances.speaker_id` strings only; `speaker_name` never populated by the pipeline |
| Transcripts / segments | ✔ | `utterances` (plus JSON files) |
| Topics | ✘ (no table) | `intelligence/topics.json`; `utterances.topic_id` is always NULL (E2E) |
| Decisions / action items | ✔ (generic) | `claims` with `claim_type` and a JSON `payload` (Pydantic `Decision` / `ActionItem`) |
| Verification results | ✘ | `verification/*.json` only; `VerificationResult` schema unused |
| Generated MoM | ✘ | `mom/mom.json` and PDF only |
| Jobs | ✔ | `jobs` |

The MoM shown to users is assembled from JSON files that the DB knows nothing about. The DB `claims` table is populated only by the claims pipeline, which yields 0 claims under the default `.env` (see `FEATURE_STATUS.md` #12).

---

## 2. Integrity checks on the production DB copy

| Check | Result |
|---|---|
| `PRAGMA integrity_check` | `ok` |
| `PRAGMA foreign_key_check` | 0 violations |
| Row counts | meetings 228 · utterances 8,478 · claims 8 · evidence_links 13 · dropped_claims 2 · jobs 96 |
| Utterances without meeting | 0 |
| Claims without meeting | 0 |
| Evidence → missing claim / missing utterance | 0 / 0 |
| Evidence meeting ≠ claim meeting | 0 |
| Claims without any evidence | 0 (the "every claim is grounded" invariant holds) |
| Duplicate claims (meeting, type, text) | 0 |
| **`jobs` rows without a meeting** | **54** (`jobs` has no FK) |
| `PRAGMA user_version` / migration table | 0 / none |

### Data quality findings

| Finding | Evidence |
|---|---|
| **Test pollution in the production DB** | 181 meetings with filename `test.mp4` and 28 with `test_audio.mp3` (matching `test_async_jobs_api.py`); 4,097 utterances named "Priya" from synthetic fixtures; 54 orphan `jobs` rows at `asr/running`. These predate the current `conftest.py` temp-DB fixture. `scripts/clean_test_meetings.py` doesn't match these filenames. |
| Hard-coded test filters in production code | `GET /api/meetings` excludes `synthetic.wav`, `background-%`, `mismatch-%`, … (`meeting_routes.py:407-414`). This works around earlier pollution instead of preventing it. |
| `jobs` states | done 27 · running/asr 54 (test) · failed/merge 8 · failed/verification 3 · running/merge 2 · running/verification 2 |
| Stuck "running" jobs | 4 real ones. No recovery runs on restart; BackgroundTasks die with the process. |
| `meetings.status` never reaches a terminal state | Values seen: created 123 · queued 28 · processing 15 · transcribed 32 · indexed 30. Nothing writes `done`/`failed`. In the E2E run, `/api/meetings` still showed `status: "processing"` after a completed run. |
| `meetings.duration` never written | No caller passes `duration=` to `insert_meeting`. |
| `meetings.filename` sometimes an absolute path | 2 rows store `G:\Meet2MoM\input\test_meeting.mp4` (from an earlier machine). |
| Real failure history (proves the job bug in production) | `'EvidenceVerifier' object has no attribute 'verify'` ×2, `extraction.json not found` ×1, `No module named 'rapidfuzz'` ×2, `No module named 'faster_whisper'` ×2, "no usable speech segments" ×3, native crash ×1 |
| Storage ↔ DB mismatch | 29 storage meeting folders vs 228 DB meetings: **16 folders have no DB row** and **215 DB meetings have no folder** |

---

## 3. Relationships, cascades, transactions

| Aspect | Finding | Risk |
|---|---|---|
| Claim → evidence | `ON DELETE CASCADE` from claims; `RESTRICT` toward utterances | Good: an utterance can't be deleted while cited |
| Meeting → children | Plain FKs, **no cascade** | Deleting a meeting fails while children exist; there's no delete API, so no current impact |
| `jobs` | No FK to `meetings` | Orphans accumulate (54 today) |
| Re-index idempotency | `persist_transcript_index` deletes evidence → claims → dropped → utterances in one transaction, then re-inserts | Verified by tests and the E2E run |
| Claim persistence | `persist_extraction` deletes and re-inserts all claims in one transaction; any FK error rolls back the batch | Verified by `test_claim_writer.py` |
| Upsert semantics | `insert_meeting` ON CONFLICT updates **only** `status`; filename/duration/created_at are frozen at first insert | Silent data staleness |
| Swallowed failures | Upload ignores DB errors (`meeting_routes.py:355-363`); `_emit` logs and continues when the job status write fails | API can report success with no DB row |
| Mixed timestamps | `meetings`/`claims` use naive `datetime.utcnow()` (deprecated); `jobs` uses timezone-aware UTC | Inconsistent comparisons |
| Schema evolution | `ALTER TABLE … ADD COLUMN` wrapped in `try/except OperationalError` | No versioning or downgrade; future changes are risky |
| Fresh database | `GET /api/meetings/{id}/status` and `/transcript` raise `OperationalError: no such table: jobs` (HTTP 500) until something has called `init_db()` | Reproduced in a temp DB; harmless once any upload happened |
| Concurrency | SQLite, a new connection per call, no WAL, no busy timeout configured | Concurrent jobs plus polling may hit `database is locked` (not reproduced) |

### Persistence after restart
SQLite and JSON files persist across restarts (the real DB holds data since 2026-09-09). In-flight jobs do not survive: there's no queue, no resume, and no reaper for `running` rows.

### API ↔ stored-data consistency (E2E)
- `/api/meetings/{id}/transcript` returned the 34 DB utterances; `frontend_transcript.json` has 34 segments. **Consistent.**
- `/api/meetings` reported `claim_count: 0` while `mom.json` contained 2 decisions. **Inconsistent**, because the two come from different pipelines.
- `/api/meetings/{id}/status` reported `verification/running` after all work had completed. **Wrong.**

---

## 4. Storage layout and behaviour

### 4.1 Layouts in use
```
storage/
├── meet2mom.db
├── _pipeline_cache/<sha256>/{transcript,diarization,metadata}/…   # content-hash cache (389 KB, 6 entries)
├── demo_01/                                                        # manual demo data (16 MB)
└── <meeting_id>/
    ├── recording/recording.<ext>, meeting.json
    ├── audio/recording_normalized.wav
    ├── chunks/chunk_###.wav
    ├── transcript/{meeting_transcript_ready,speaker_transcript,final_transcript,frontend_transcript,transcript_index}.json
    ├── diarization/speaker_turns.json
    ├── metadata/{audio_quality,chunk_metadata,pipeline_cache,processing_report,recording_info}.json
    ├── intelligence/{topics,decisions,action_items,extraction,claims}.json, llm_cache/
    ├── verification/{verified_intelligence,verification_summary}.json, nli_cache/, retrieval/, inputs/
    ├── mom/{mom.json, Meet2MoM-Meeting-Minutes.pdf}
    ├── pipeline_status.json
    ├── meeting.json, frontend_transcript.json, extraction.json,
    │   verification_summary.json, mom.json, Meet2MoM-Meeting-Minutes.pdf   # root-level DUPLICATES
    └── results/{transcripts,diarization,metadata}/                       # legacy layout (older meetings)
```
Current `storage/` (untracked, gitignored): 203 MB, 29 meeting folders (including `demo_01`). The 10.4 MB `test_meeting.mp4` is stored again for every upload (≥ 7 copies); the content-hash cache doesn't deduplicate recordings. Normalized WAV and chunk files account for 32 MB across 24 files.

### 4.2 Files generated during application execution (E2E run, one upload)
`recording/{recording.mp4, meeting.json}`, `meeting.json`, `audio/recording_normalized.wav`, `chunks/chunk_001.wav`, five transcript JSONs, `diarization/speaker_turns.json`, five metadata JSONs, `intelligence/{claims,extraction,topics,decisions,action_items}.json` + `llm_cache/<hash>.json`, `verification/{verified_intelligence,verification_summary}.json`, `mom/{mom.json,PDF}`, root-level duplicates of five of those, `pipeline_status.json`, plus `_pipeline_cache/<sha256>/`. **Nothing is ever cleaned up:** normalized audio and chunks stay forever.

### 4.3 Files generated during tests
Before the Phase 0 fix, one baseline `pytest` run created these in the **real** `storage/` (diffed against a pre-run snapshot):

| Created folder (12 total) | Produced by |
|---|---|
| 4 × `<uuid>/intelligence/llm_cache/` | `test_claim_writer.py` / `test_extraction_pipeline.py` / `test_llm_extractor.py`. They patch `config.STORAGE_ROOT` and `claim_writer.STORAGE_ROOT` but **not** `app.core.storage.STORAGE_ROOT`, which `llm_extractor` uses through `MeetingStorage` |
| 2 × full meeting trees with topics, decisions, verification, MoM, PDF | `test_meeting_isolation.py` (its cleanup line is never reached when an assertion fails) |
| `<uuid>/recording/recording.mp3` + skeleton | `test_async_jobs_api.py::test_upload_returns_202…` |
| `cache-check-*/`, `cited-none-*/`, `mismatch-*/` with `nli_cache/pairs.json` | `test_nli_scorer.py` |
| `<uuid>/verification/retrieval/{embeddings.npy,spans.json}` | `test_retrieval_index.py` / `test_candidate_builder.py` |
| `<uuid>/transcript/transcript_index.json` | `test_transcript_index.py` |

**After the fix** (conftest redirects `STORAGE_ROOT` and `DB_PATH` to a temp directory before any app module is imported, and removes it at session end), the final full run created **zero** files in `storage/` or `output/`, and the DB checksum was unchanged. The 12 folders from my baseline run were removed after checking that each one was absent from the pre-run snapshot and timestamped during that run. `storage/` now matches the original snapshot exactly.

### 4.4 Temporary-file cleanup
| Item | Cleaned? |
|---|---|
| `tests/conftest.py` temp DB dir (before the fix) | ✘ never removed (`tempfile.mkdtemp`, no teardown). Fixed: removed at session end |
| Normalized audio / chunks after a successful run | ✘ kept forever |
| Recording + intermediates after a failed run | ✘ kept. 10 real folders contain a recording but no transcript (e.g. `2a0448bc…`, `907b3f23…`) |
| Partial upload when the client disconnects mid-stream | ✘ partial file and folder kept (only the >2 GB overflow path deletes) |
| `_pipeline_cache/.<hash>.tmp` | ✔ replaced atomically on save |
| `verification/inputs/` (uncommitted jobs.py) | ✘ written on every run |

### 4.5 Side effects of read requests
`get_storage()` calls `MeetingStorage.ensure_dirs()` (`meeting_routes.py:132-146`), so **every GET/POST on `/api/download-mom|extract|verify|generate-mom|transcribe/{id}` creates a 10-folder skeleton** for any syntactically valid ID, even if the meeting doesn't exist. Reproduced: `GET /api/download-mom/neverexisted01` returned 404 and left `storage/neverexisted01/{audio,chunks,diarization,intelligence,metadata,mom,recording,transcript,verification}`. One pre-existing folder in the real `storage/` contains no files at all (`3973bf17…`), consistent with this behaviour. Import-time `mkdir` of `storage/` and `storage/_pipeline_cache/` happens on every server start.

### 4.6 Upload safety and path validation
| Control | Status |
|---|---|
| Meeting-ID validation | ✔ `^[A-Za-z0-9_-]{1,64}$`; `../`, dots and over-long IDs → 400/404 (verified) |
| Path traversal guard | ✔ `MeetingStorage._guard` resolves and checks `relative_to(root)` |
| Client filename | ✔ never used as a path (`recording.<ext>`); `../../evil.wav` → `recording.wav` (verified) |
| Size limit | ✔ 2 GB, enforced while streaming |
| Content validation | ✘ extension only; text bytes named `.mp4` → 202 queued (verified) |
| Extension lists | ✘ API allows `.avi`; `config.SUPPORTED_EXTENSIONS` doesn't (the job fails later) |
| Authentication / rate limiting | ✘ none |
| Information leakage | ✘ the job `error` column stores the full subprocess output (absolute paths, tracebacks), returned by `/status` |
| Minor | `path_for()` returns a *resolved* path while the properties return unresolved ones; they only compare equal when `STORAGE_ROOT` is canonical (seen with an 8.3 short temp path) |

### 4.7 Can cleanup remove important files?
- No storage cleanup code exists in the app. Only the API's stale-artifact deletion runs: `/api/extract` deletes `verification/*` and `mom/*` for **that meeting** when intelligence is regenerated, and `/api/verify` deletes `mom/*`. Both are scoped through `MeetingStorage` and the ID regex, so they can't escape the meeting folder.
- `_save_results_to_cache` does `shutil.rmtree(cache_dir)` on `_pipeline_cache/<sha256>`. That's scoped to a hex hash, so it's safe.
- `scripts/clean_test_meetings.py` deletes DB rows only, by filename/ID pattern. It never touches files. It doesn't cover the actual 209 polluted rows (`test.mp4`, `test_audio.mp3`).
- **Recommendation (needs your approval, not done):** remove the 209 test-polluted meetings, 4,097 "Priya" utterances and 54 orphan jobs from the real DB after taking a backup, and decide on a retention policy for `audio/`, `chunks/` and failed-run folders.
