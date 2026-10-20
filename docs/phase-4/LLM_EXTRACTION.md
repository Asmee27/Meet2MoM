# Phase 4.4 — Real LLM extraction

Meet2MoM can extract decisions, action items and cited discussion points with
a real LLM. Grounding and independent verification stay in place, so the LLM
proposes and the transcript decides. Without a usable provider, every meeting
is still processed by the local extractor, and the minutes say so.

> **Roadmap note.** `Meet2MoM — Research & MVP Roadmap.md` names its own
> Phase 4 "Imports" (transcript files, then Google Meet). LLM extraction is
> part of the existing extraction path in that roadmap ("Gemini free tier for
> development"). This document covers hardening that path. It isn't the
> roadmap's Phase 4.

## How extraction works

```
transcript index → windows (≤ 60 utterances, 10 overlap) → LLM per window
   → schema check (decisions / actions / discussion_points must be lists)
   → grounding:
       deadline_text must appear in the cited utterances, otherwise removed
       decision proposer must be named or speaking in them, otherwise removed
       discussion points must cite utterances of that window
   → span validation (evidence ids must exist and lie in the window)
   → owner/deadline resolution (first-person owner = the evidence speaker)
   → independent verification (deterministic + retrieval/NLI + fusion)
   → MoM: only verified claims are confirmed; the rest are held for review
```

| Situation | What happens | Where it shows |
|---|---|---|
| Real provider configured and working | `mode_used = llm`, `extractor_version = llm:<provider>:<model>:v2` | MoM, PDF and Summary page: "Extracted by LLM: gemini / gemini-3.8-flash" |
| `LLM_PROVIDER=stub`, missing key, missing or malformed model, unsupported provider | No network call. The local fallback runs | "Extraction status … (extractor used: hybrid/rules) · LLM_PROVIDER=… has no API key configured" |
| HTTP error, timeout, blocked response, non-JSON or wrong-schema output | Retries where sensible: 429/5xx/network errors up to 3 times; timeouts only `LLM_TIMEOUT_RETRIES` times (default 1); one JSON repair attempt; everything within `LLM_EXTRACTION_BUDGET_SECONDS` (default 600). Then the local fallback runs | "LLM extraction failed: LLM request timed out after 180s (2 attempts). Raise LLM_TIMEOUT_SECONDS or choose a faster LLM_MODEL." (or the provider message) |
| An LLM detail is not in its own citations | The detail is removed and the claim is kept. Logged in `claims.json` → `stats.llm_grounding` | The claim is shown without the invented date or proposer |
| An LLM cites a non-existent or out-of-window utterance | The claim is dropped (`unresolvable_utt_id` / `out_of_window`) | Dropped-claims counts |

Discussion points appear in the minutes as **"Discussion Summary (LLM, cited,
not verified)"**, with their utterance ids. They are never listed as decisions
or tasks, and they are only shown for the extraction run that produced them.

The `stub` provider remains the deterministic test fixture. It refuses to run
outside tests and is always reported as unavailable in production.

## Supported providers

| `LLM_PROVIDER` | Key variable | `LLM_MODEL` | Notes |
|---|---|---|---|
| `gemini` (**recommended**) | `GEMINI_API_KEY` or `LLM_API_KEY` | optional; defaults to `gemini-3.8-flash` | JSON response mode, key sent as the `x-goog-api-key` header |
| `openai` | `OPENAI_API_KEY` or `LLM_API_KEY` | **required** | `LLM_BASE_URL` for OpenAI-compatible endpoints |
| `anthropic` | `ANTHROPIC_API_KEY` or `LLM_API_KEY` | **required** | |
| `local` | none | **required** | OpenAI-compatible server, e.g. Ollama at `http://localhost:11434/v1` |

### Which model is used

The model is chosen in this order:

1. A model passed explicitly to `LLMClient(model=...)`, which code and tests can do.
2. `LLM_MODEL` from `.env`, unless it is empty or the placeholder `mock-v1`.
3. The provider default, which only Gemini has: `gemini-3.8-flash`. It is
   defined once as `DEFAULT_GEMINI_MODEL` in `app/core/llm_client.py` and
   comes from Google's [Gemini models list](https://ai.google.dev/gemini-api/docs/models),
   where it is marked stable. OpenAI, Anthropic and local have no default.
   Without `LLM_MODEL` they report "has no LLM_MODEL configured" and fall back.

To use another Gemini model, set it in `.env`, for example
`LLM_MODEL=gemini-3.6-flash`, and restart. The model in use is part of
`extractor_version` and is shown as "Extracted by LLM: gemini / <model>".

### Timeouts, retries and the maximum wait

| Setting | Default | Meaning |
|---|---|---|
| `LLM_TIMEOUT_SECONDS` | 180 | The longest wait for one request. |
| `LLM_TIMEOUT_RETRIES` | 1 | Extra attempts after a timeout. HTTP 429/5xx and network errors keep up to 3 retries, with 1, 2 and 4 s pauses between them. |
| `LLM_EXTRACTION_BUDGET_SECONDS` | 600 | A hard cap on all LLM calls for one meeting, covering every window, retry, pause and JSON repair. Each request's timeout is cut to the time left, and no request starts once it's used up. `0` turns the cap off. |
| `EXTRACTOR` | `llm` | Use the LLM, with automatic fallback. |
| `FALLBACK_EXTRACTOR` | `hybrid` | The local extractor used when the LLM can't be used or fails. |

**Maximum wait for the LLM stage** is the smaller of two numbers:
`LLM_EXTRACTION_BUDGET_SECONDS`, or the number of windows times the worst case
per window. With the defaults, the worst case per window is
(1 + 1) × 180 s + 1 s = 361 s. So the LLM stage never takes more than
**10 minutes**, and a short meeting of up to 60 utterances (one window) never
more than about 6 minutes. When the budget runs out, the minutes say "LLM
extraction failed: LLM extraction stopped: its 600s time budget
(LLM_EXTRACTION_BUDGET_SECONDS) ran out". The local extractor's results are
used for the **whole** meeting; LLM and local results are never mixed.

### Latency (measured 20 October 2026, standard tier)

| Request | `gemini-3.8-flash` | `gemini-3.6-flash` |
|---|---|---|
| Tiny synthetic prompt (57 tokens) | 67.3 s, 68.5 s; 51.1 s with `thinkingLevel: low` | 3.6 s |
| Synthetic 60-utterance window, real prompt | 59.1 s | HTTP 503 "high demand" (transient) |

Network setup took under 1 s (DNS 0.03 s, TCP 0.02 s, model metadata 0.6 s).
The time is spent generating the response. `gemini-3.5-flash-lite` was not
measured, because the key wasn't readable at the time (see "Configure
Gemini", step 2). Google's model list marks it as stable.

## Processing time: the 22-minute job (20 October 2026)

Meeting `a67bec95…`: a 68 s recording with 2 speakers, 7 utterances and 4
claims, processed inline by the API. Process was clicked at 15:50:58. The job
finished at 16:12:48, after **21 min 50 s**, and the minutes were opened at
16:15:32. Times come from the database, the files' timestamps and the saved
verification timings:

| Stage | Took | Cause |
|---|---|---|
| Recording pipeline (normalize, per-track transcription) | 5 min 27 s, 3 min 40 s of it before the first output | The transcription subprocess loads torch, Whisper and pyannote on a busy 2-core laptop CPU. The previous meeting needed 1 min 49 s for the same start-up. **Not changed.** |
| Transcript index and topics | 41 s | Normal. |
| LLM extraction | 4 min 10 s | `gemini-3.8-flash` answered more slowly than `LLM_TIMEOUT_SECONDS=60`. The code at the time retried the timeout 3 times (4 × 60 s plus 7 s of pauses), then fell back to hybrid. Fixed in `2ae7b86` (one retry) and now also capped by the budget. |
| **Verification (NLI)** | **11 min 35 s** | **The NLI model ran in float16 on the CPU:** 58 sentence pairs at about 10 s each. |
| Opening review and summary | 2 min 44 s | The reviewer clicking through the pages. Nothing ran again; all verification results share one timestamp. |

**Why verification was slow.** The pinned `transformers==5.16.1` loads a model
in the precision it was saved in. The NLI checkpoint
(`MoritzLaurer/mDeBERTa-v3-base-mnli-xnli`) is saved in float16, and a CPU
without float16 hardware runs that very slowly. This PC has an AMD Ryzen 3
3250U. Measured on synthetic pairs:

| Precision | Time per pair |
|---|---|
| float16 | 8.77 s |
| float32 | 0.51 s, **17× faster** |

The two precisions differed by at most 0.0044 in score and gave the same label
for all 16 pairs. `app/core/model_registry.py` now loads Hugging Face pipelines
in float32 unless the caller asks otherwise. For that meeting, verification
would take about 30–40 s instead of 11.5 minutes. The NLI model, thresholds and
verifier logic are unchanged.

Other findings:

* **Two API servers** were listening on port 8000, one from `.venv` and one
  from the system Python 3.11, plus two `scripts/worker.py` processes. On
  Windows both servers can bind the port, so requests reach either, possibly
  running different code. Whenever either server starts, it marks every
  running inline job as failed, including one the other server is processing.
  Run exactly one API.
* **Workers beside an inline API.** With `JOB_RUNNER=inline` (the default) the
  API runs jobs itself. A worker running beside it could claim the job before
  the API starts it. It could also requeue a long inline stage as "stale",
  because heartbeats only update between stages. Either way, the meeting would
  be processed twice. This didn't happen to `a67bec95` (no worker claim is
  recorded), but `scripts/worker.py` now refuses to start unless
  `JOB_RUNNER=worker`.
* Each pipeline stage now logs `Stage <name> finished in N s`. LLM extraction
  logs its window count and duration. Neither logs transcript text or keys.

## How to tell real LLM extraction from fallback

| Check | Real LLM extraction | Fallback |
|---|---|---|
| Summary page and PDF | "Extracted by LLM: gemini / <model>" | "Extraction status … (extractor used: hybrid)" and the reason |
| `extractor_version` | starts with `llm:gemini:<model>:v2` | e.g. `rules-v2+dialogue-v1` |
| `extraction_runs` | `mode_used = llm`, no `fallback_reason` | `mode_used = hybrid`/`rules`, `fallback_reason` set |
| `storage/<id>/intelligence/llm_discussion_points.json` | exists | absent |

## Configure Gemini (exact steps)

1. Create an API key in Google AI Studio (<https://aistudio.google.com/apikey>).
   Read the Gemini API terms on data use before you send real meetings to a
   free tier.
2. Edit your local `.env`. Put **each setting on its own line**, and never
   commit the file.
   ```
   LLM_PROVIDER=gemini
   LLM_MODEL=gemini-3.5-flash-lite
   GEMINI_API_KEY=<your key>
   EXTRACTOR=llm
   LLM_TIMEOUT_SECONDS=180
   LLM_TIMEOUT_RETRIES=1
   LLM_EXTRACTION_BUDGET_SECONDS=600
   ```
   If two lines run together (for example `LLM_MODEL=…GEMINI_API_KEY=…`), the
   app reports "LLM_MODEL is not a valid model name", sends nothing and falls
   back. It never displays the value. The configured key is also removed from
   any provider error message before that message is logged or saved.
3. Run **exactly one** API process. With `JOB_RUNNER` unset (inline), don't
   start `scripts/worker.py`; it exits with an explanation. Settings are read
   at start-up, so restart the API after editing `.env`.
4. Check the configuration without processing a meeting:
   ```
   python -c "from app.services.intelligence.extraction_pipeline import llm_unavailable_reason as r; print(r() or 'LLM ready')"
   ```

## Manual acceptance test (for the tester)

Use a **new** meeting. Don't reprocess existing meetings.

Before you start:

1. The configuration check above prints `LLM ready`.
2. Stop every running API and worker. In PowerShell, this command must list
   **nothing**:
   ```
   Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'uvicorn|worker.py' } | Select-Object ProcessId, CommandLine
   ```
   Stop anything listed with `Stop-Process -Id <ProcessId>`.
3. Start one API from `G:\Meet2MoM`:
   `.venv\Scripts\python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000`.
   Start the frontend with `cd frontend; npm run dev`.

Test:

1. Hold a two-browser meeting (host and guest). Both consent. Record 1–2
   minutes containing:
   * one explicit decision;
   * one task with a spoken owner and deadline, for example "I will send the
     report by Friday";
   * some small talk;
   * one vague remark that should not become a task.
2. Stop the recording, end the meeting and click Process. Note the time. On
   this laptop, expect roughly 4–8 minutes, mostly the transcription
   subprocess, but record the actual time. If it's much longer, copy the API
   console's `Stage … finished in` lines.
3. On the Summary page, check that:
   * it shows **"Extracted by LLM: gemini / gemini-3.5-flash-lite"**;
   * there is no "(extractor used: hybrid)" and no fallback reason;
   * each decision and action cites utterances that play back the right words;
   * each owner is the speaker who committed, and each deadline is the words
     actually spoken;
   * anything unsupported, including the vague remark if it appears at all,
     is held for review and not confirmed;
   * "Discussion Summary (LLM, cited, not verified)" lists cited points.
4. Approve the meeting and download the PDF. It must show the same "Extracted
   by LLM" line and the same decisions and actions as the approved minutes.
5. Record the meeting ID, the processing time, and screenshots of the Summary
   page and PDF. Never screenshot `.env`.
6. Optional failure check: set `GEMINI_API_KEY` to an invalid value, restart,
   and process another short meeting. The minutes must show "LLM extraction
   failed: LLM provider HTTP 400/403 …" and "(extractor used: hybrid)". They
   must not claim LLM extraction.

## Limitations

* Real-provider acceptance on a recorded meeting is still pending. Nobody has
  verified `gemini-3.5-flash-lite` against the API from this project.
* Start-up of the transcription subprocess takes 2–4 minutes on a 2-core
  laptop. This work doesn't address it.
* Gemini latency depends on the model and on demand. A transient "high demand"
  HTTP 503 has been seen.
* Discussion points are cited and their citations are validated, but they
  aren't NLI-verified. That's why they are labelled "not verified".
* Grounding is lexical. A deadline paraphrased by the LLM (for example "end
  of week" for "by Friday") is removed rather than trusted.
* LLM responses are cached per window, model and prompt version under
  `intelligence/llm_cache/`. Those caches hold transcript text, so a future
  delete-meeting feature must remove them.
* `TOPIC_TITLES=llm` calls a `complete()` method that `LLMClient` doesn't
  have, so LLM topic titles silently fall back to extractive titles. This is
  pre-existing and out of scope here.
