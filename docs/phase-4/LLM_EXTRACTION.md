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
| `LLM_PROVIDER=stub`, missing key, missing model, unsupported provider | No network call. The local fallback runs | "Extraction status … (extractor used: hybrid/rules) · LLM_PROVIDER=… has no API key configured" |
| HTTP error, timeout, blocked response, non-JSON or wrong-schema output | Retries where sensible: 429/5xx/network errors up to 3 times; timeouts only `LLM_TIMEOUT_RETRIES` times (default 1); one JSON repair attempt. Then the local fallback runs | "LLM extraction failed: LLM request timed out after 180s (2 attempts). Raise LLM_TIMEOUT_SECONDS or choose a faster LLM_MODEL." (or the provider message) |
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

Other settings:

* `LLM_TIMEOUT_SECONDS` (default 180): the timeout for each request.
* `LLM_TIMEOUT_RETRIES` (default 1): extra attempts after a timeout. These
  are fewer than the 3 retries for HTTP 429/5xx because a model slower than
  the timeout usually times out again, and each attempt is a full billed
  request.

### Latency (measured 20 October 2026, standard tier)

| Request | `gemini-3.8-flash` | `gemini-3.6-flash` |
|---|---|---|
| Tiny synthetic prompt (57 tokens) | 67.3 s, 68.5 s; 51.1 s with `thinkingLevel: low` | 3.6 s |
| Synthetic 60-utterance window, real prompt | 59.1 s | HTTP 503 "high demand" (transient) |

Network setup took under 1 s (DNS 0.03 s, TCP 0.02 s, model metadata 0.6 s).
The time is spent generating the response, mostly before thinking starts.
With `gemini-3.8-flash`, a 60 s timeout fails roughly at random, which is why
the default is 180 s. A faster model can use a shorter timeout.
* `EXTRACTOR=llm`: use the LLM, with automatic fallback.
* `FALLBACK_EXTRACTOR` (default `hybrid`): the local extractor to use when the
  LLM can't be used.

## Configure Gemini (exact steps)

1. Create an API key in Google AI Studio (<https://aistudio.google.com/apikey>).
   Read the Gemini API terms on data use before you send real meetings to a
   free tier.
2. Edit your local `.env`. Never commit it.
   ```
   LLM_PROVIDER=gemini
   LLM_MODEL=gemini-3.8-flash
   GEMINI_API_KEY=<your key>
   EXTRACTOR=llm
   LLM_TIMEOUT_SECONDS=180
   LLM_TIMEOUT_RETRIES=1
   ```
3. Restart the API (and `scripts/worker.py` if `JOB_RUNNER=worker`). The
   settings are read at startup.
4. Check the configuration without processing a meeting:
   ```
   python -c "from app.services.intelligence.extraction_pipeline import llm_unavailable_reason as r; print(r() or 'LLM ready')"
   ```

## Manual acceptance test

Use a **new** meeting. Don't reprocess existing meetings.

1. Record or upload a short meeting that contains one explicit decision, one
   task with a stated owner and deadline, and some small talk.
2. Process it. Then open the Summary step and check that:
   * the page shows **"Extracted by LLM: gemini / gemini-3.8-flash"**;
   * the decision and the task are present, and each cites utterances you can
     play back;
   * the owner is the speaker who committed, and the deadline is the words
     actually said;
   * "Discussion Summary (LLM, cited, not verified)" lists cited points.
3. Approve the meeting and download the PDF. It shows the same "Extracted by
   LLM" line and the cited summary section.
4. Failure check: set `GEMINI_API_KEY` to an invalid value, restart and
   process another short meeting. The minutes must show "LLM extraction
   failed: LLM provider HTTP 400/403 …" and "(extractor used: hybrid)". They
   must not claim LLM extraction.
5. `storage/<meeting_id>/intelligence/claims.json` → `stats.llm_grounding`
   lists any details the LLM stated without citing them.

## Limitations

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
