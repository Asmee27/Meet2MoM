# Phase 5 — Close Phase 4 and establish a baseline

Branch `phase-5/baseline` (from `main` `a4aaa0d`), October 2026.

## Status

| Item | Status |
|---|---|
| A. Real Gemini extraction acceptance | **Exercised on real meetings, not yet approved.** On 22 Oct 2026 `scripts/check_llm.py --live` reported provider `gemini`, model `gemini-3.8-flash`, ready, one synthetic request successful in 9.88 s (1 decision, 1 action, cited `u00000`, `u00001`) — as reported by the user; the key was never printed. Two meetings were then extracted by Gemini with no fallback (`extractor_version = llm:gemini:gemini-3.8-flash:v2`): live meeting `7abf88ad…` (2 speakers, 2 claims: 1 verified, 1 verified with warning) and upload `6582304f…` (1 claim held for review). **Neither has been approved yet**, so approved-PDF/JSON evidence is still missing. |
| B. Stage-by-stage timings | **Done** and recorded for both meetings above (tables below). |
| C. `.env.example` and `TOPIC_TITLES=llm` | **Done** |
| D. Validation | Automated: done (386 backend, 59 frontend tests at the end of Phase 5). Manual: Gemini path confirmed; approval + export of a Gemini-extracted live meeting still to do. |

## Measured stage timings (22 Oct 2026, Ryzen 3 3250U laptop, API inline mode)

Exported read-only to `G:\Meet2MoM_evidence\phase5\` (outside Git).

| Stage | Live `7abf88ad` (21.1 s, 2 tracks) | Upload `6582304f` (53.6 s, mixed audio) |
|---|---|---|
| Speech pipeline (total) | 124.5 s | 216.9 s |
| ↳ start-up (subprocess imports) | 48.7 s | 20.6 s |
| ↳ audio preparation | 2.0 s | 5.8 s |
| ↳ transcription (model load) | 70.4 s (5.9 s) | 35.4 s (3.9 s) |
| ↳ speaker attribution | 0.0 s (LiveKit tracks) | 151.6 s (pyannote; load 9.1 s) |
| ↳ transcript merge | 0.2 s | 0.2 s |
| Utterance index / topics | 0.3 s / 0.3 s | 0.2 s / 0.2 s |
| Extraction (Gemini wait) | 34.3 s (34.0 s, 1 request) | 6.9 s (6.5 s, 1 request) |
| Verification (NLI load / NLI) | 28.0 s (0.0 s / 27.2 s) | 19.4 s (11.4 s / 4.5 s) |
| Minutes / PDF (on demand) | 0.14 s / 0.09 s | 0.14 s / 0.11 s |
| **Processing total (top-level stages)** | **187.3 s** | **243.6 s** |

Observations that set the Phase 6 priorities: every meeting pays the speech
subprocess start-up and model loading; for live meetings transcription of very
short speech is slow; uploads are dominated by pyannote diarization.

## Environment loading and restarts

* `app/core/config.py` loads the repository-root `.env` once, at process start
  (`python-dotenv`, `override=False`). Variables already set in the shell win
  over `.env`.
* The API, `scripts/worker.py` and the pipeline subprocess each read it when
  they start. **After editing `.env`, restart the API** (and the worker when
  `JOB_RUNNER=worker`). The subprocess is started fresh for every meeting.
* Put every setting on its own line. A joined line such as
  `LLM_MODEL=…GEMINI_API_KEY=…` disables the LLM (explicit fallback) and the
  value is never printed.
* Vite reads `frontend/.env`, not the root `.env`.

Check the LLM configuration without printing the key:

```powershell
.\.venv\Scripts\python.exe scripts\check_llm.py           # no network call
.\.venv\Scripts\python.exe scripts\check_llm.py --live    # ONE synthetic request
```

## Stage timings

`app/core/stage_timings.py` times stages with `time.monotonic()` and saves each
stage when it starts and when it ends. Rows are stored in
`processing_stage_timings` (migration v4), keyed by meeting and run. Saving is
best effort: a failure is logged and never affects processing.

| Stage | Parent | Measured in |
|---|---|---|
| `speech_pipeline` | — | job runner (hashing, cache, subprocess) |
| `pipeline_startup` | speech_pipeline | subprocess: importing torch, Whisper, pyannote |
| `audio_preparation` | speech_pipeline | validate, inspect, enhance, quality, chunk |
| `transcription` | speech_pipeline | ASR; `detail.model_load_seconds`; `cached` when reused |
| `speaker_attribution` | speech_pipeline | LiveKit tracks or pyannote; model load; `cached` |
| `transcript_merge` | speech_pipeline | merge, report, frontend transcript |
| `transcript_index` | — | utterance rows |
| `topic_segmentation` | — | `fallback` when LLM titles were requested but not used |
| `extraction` | — | `fallback` when the LLM was requested but a local extractor produced the claims; provider, model, requests, provider wait time, cache hits |
| `verification` | — | `fallback` when the semantic verifier was unavailable |
| `evidence_retrieval`, `nli_model_load`, `nli_verification` | verification | from the verifier's own timings |
| `minutes_generation`, `pdf_export` | — | on demand (open, approve, download); `skipped` until they run |

Statuses: `running`, `completed`, `failed`, `fallback`, `cached`, `skipped`.
A skipped stage has no duration; nothing after a failed stage is recorded.
`GET /api/meetings/{id}/status` and the `processing` object of
`GET /api/live/meetings/{id}/recording` gain a `stage_timings` field; all
existing fields are unchanged.

## Baseline (before the new instrumentation)

The same 67.8-second, two-speaker recording (identical SHA-256) was processed
twice. Times come from database timestamps and file modification times.

| | 20 Oct, live meeting `a67bec95` (before fixes) | 21 Oct, upload `0480e440` (after `c5ecbff`) |
|---|---|---|
| Speaker attribution path | LiveKit tracks | pyannote on the mixed audio |
| Speech pipeline | 5 min 27 s | 5 min 13 s (diarization about 3 min 26 s) |
| Extraction | 4 min 10 s (Gemini timed out four times, then fallback) | 3.2 s (explicit fallback: invalid `LLM_MODEL`) |
| Verification | **11 min 35 s** (NLI in float16) | about 72 s in the job, including loading the models; **8.8 s** when re-run with the models loaded |
| Job total | **21 min 50 s** | **6 min 44 s** |
| Approved | no | yes (snapshot v1) |

The next real meeting will record exact per-stage timings automatically.

## `.env.example` and topic titles

* `.env.example` now lists `LIVEKIT_URL`, `LIVEKIT_API_KEY`,
  `LIVEKIT_API_SECRET`, `JOB_RUNNER`, `TOPIC_TITLES` and
  `TOPIC_TITLE_LLM_BUDGET_SECONDS`.
* It no longer lists settings that nothing reads (`APP_NAME`, `APP_ENV`, `HOST`,
  `PORT`, `FRONTEND_URL`, `GROQ_API_KEY`). It explains that
  `VITE_API_BASE_URL` belongs in `frontend/.env`.
* `WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_COMPUTE_TYPE` and the three cache
  flags are now really read by `config.py`. The defaults are unchanged.
* `TOPIC_TITLES=llm` used to call `LLMClient.complete()`, which does not exist,
  and silently fell back. It now uses `complete_json`, with a 60 s title budget.
  Every fallback reason (LLM unavailable, provider error, title rejected by the
  word check) is stored on the topic as `title_fallback_reason` and reported by
  the `topic_segmentation` stage. The default `extractive` mode makes no LLM
  call.

## Manual acceptance steps

See the final report of this phase for the exact PowerShell commands.
Evidence to keep for the black book:

* the meeting ID;
* the stage timings from the processing screen or `/status`;
* the approved PDF (`storage/<id>/mom/Meet2MoM-Meeting-Minutes.pdf`) and the
  JSON export;
* screenshots of the consent screen, REC, the ended screen with timings, the
  Verify step and the Summary step showing "Extracted by LLM: gemini / <model>".
