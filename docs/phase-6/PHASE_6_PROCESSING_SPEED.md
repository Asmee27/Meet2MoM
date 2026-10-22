# Phase 6 — Processing speed

Branch `phase-6/processing-speed`, stacked on `phase-5/baseline`. 22 October 2026.

## What the measurements showed

Stage timings from Phase 5 for the real Gemini meetings of 22 Oct
(`docs/phase-5/PHASE_5_BASELINE.md`):

| Live meeting `7abf88ad` (21.1 s, 2 tracks) | Time | Share of 187 s |
|---|---|---|
| Speech subprocess start-up (imports) | 48.7 s | 26% |
| Transcription (incl. 5.9 s Whisper load) | 70.4 s | 38% |
| Gemini extraction (provider wait) | 34.3 s (34.0 s) | 18% |
| NLI verification | 27.2 s | 15% |

The upload `6582304f` (53.6 s) spent 151.6 s in pyannote diarization.

Two causes were confirmed in the code and by measurement:

1. **Repeated start-up.** `recorded_pipeline_adapter.py` starts a new Python
   process for every meeting (`scripts/run_recorded_pipeline.py`), which
   re-imports torch, faster-whisper and pyannote (20–49 s) and reloads Whisper
   (3–9 s) and pyannote (9 s) each time.
2. **Per-track language detection.** Each participant track had only about
   2.7 s of speech after VAD, yet each Whisper pass took 22–29 s. Timing one
   track: automatic language detection 30.3 s, `language="en"` 16.3 s, with an
   identical transcript. Detection costs about 14 s per track.

## Changes

| Change | Where | Default |
|---|---|---|
| In-process speech pipeline: stages 1-9 run inside the job runner and reuse loaded libraries and models | `recorded_pipeline_adapter.py` (`SPEECH_PIPELINE`), `scripts/run_recorded_pipeline.py` (`run()`) | `auto`: in-process in `scripts/worker.py`, the old subprocess in the API process |
| Whisper and pyannote loaded once per process | `model_registry.get_cached_model`, `transcriber.load_whisper_model`, `diarizer.load_diarization_pipeline` | always (a subprocess loads once anyway) |
| Worker preloads speech libraries, Whisper, embeddings, NLI and pyannote at start | `scripts/worker.py` `preload_models()` | `WORKER_PRELOAD_MODELS=true` |
| Optional fixed transcription language | `WHISPER_LANGUAGE` → `config.FORCE_LANGUAGE` | empty = automatic detection (unchanged) |
| `ENABLE_PIPELINE_CACHE=false` now also disables the content-hash result cache | `recorded_pipeline_adapter.py` | cache on (unchanged) |
| Relocatable data folder | `MEET2MOM_STORAGE_ROOT` | `storage/` (unchanged) |
| Repeatable benchmark that never touches real data | `scripts/benchmark_speech_pipeline.py` | — |

Why `auto` keeps the subprocess in the API process: live recordings are held in
the API process. Running heavy ASR there could delay the recorder's audio
callbacks, so the in-process path is used by the separate worker only.

Nothing that affects correctness changed: no threshold, verifier, NLI,
diarization, consent or evidence rule was modified. The subprocess path is
unchanged and remains available (`SPEECH_PIPELINE=subprocess`).

## Benchmark

`scripts/benchmark_speech_pipeline.py 7abf88ad… --modes … --runs …` copies the
meeting's `recording/` folder to a temporary storage root, disables every cache,
and runs stages 1-9 only (no LLM, no verification). Same 21.1-second, two-track
recording, same laptop (AMD Ryzen 3 3250U, 2 cores/4 threads, Windows 11), same
models (Whisper `small` int8, beam 5, VAD), nothing else running. Raw results:
`G:\Meet2MoM_evidence\phase6\` (outside Git).

| Run | Language | Speech pipeline | Start-up | Transcription (model load) | Transcript fingerprint |
|---|---|---|---|---|---|
| subprocess #1 | auto | 86.0 s | 23.7 s | 58.6 s (7.3 s) | — |
| subprocess #2 | auto | 82.5 s | 22.9 s | 56.1 s (3.4 s) | — |
| subprocess #3 | auto | 99.1 s | 31.5 s | 63.6 s (4.7 s) | `80db2f54c90b37da` |
| in-process cold | auto | 87.9 s / 86.1 s | 19.6 s / 27.2 s | 66.8 s / 56.9 s | `80db2f54c90b37da` |
| in-process warm | auto | 53.1 s / 64.9 s | cached | 51.5 s / 62.8 s (reused) | `80db2f54c90b37da` |
| in-process cold | en | 74.0 s | 20.3 s | 50.2 s (8.5 s) | `80db2f54c90b37da` |
| **in-process warm** | **en** | **30.6 s** | cached | 29.0 s (reused) | `80db2f54c90b37da` |

* Subprocess average 89.2 s. Warm worker 59.0 s on average (−34%). Warm worker
  with `WHISPER_LANGUAGE=en` 30.6 s (−66%).
* Every run produced the same transcript (same fingerprint of speaker, start
  and text), so these changes did not alter the output.
* Cold in-process runs cost about the same as a subprocess; that is why the
  worker preloads the models at start.
* Run-to-run variation on this laptop is ±15%. These are single short
  recordings, not a statistical benchmark.

## Not yet measured / remaining bottlenecks

* **End to end on a new real meeting** with the worker. Estimated for a meeting
  like `7abf88ad`: speech about 31 s + Gemini 7–34 s + verification 28 s, about
  70–95 s instead of 187 s. This is an estimate, not a measurement.
* **Roadmap target** "minutes within 3 minutes after a 5-minute, two-person
  meeting": **not tested**. ASR time grows with the amount of speech, so a
  5-minute meeting must be measured.
* **NLI verification** (27 s for 2 claims) is now the largest local stage after
  transcription for live meetings.
* **pyannote diarization** dominates uploads (151.6 s for 53.6 s). It is not
  used for live meetings, which use the participant tracks.
* Gemini latency depends on the provider (6.5–34 s per request observed).

## How to run the fast configuration

`.env` (each on its own line):

```
JOB_RUNNER=worker
SPEECH_PIPELINE=auto
WORKER_PRELOAD_MODELS=true
WHISPER_LANGUAGE=en        # only if meetings are in English
```

Then start the API and the worker in two terminals; the worker logs
`Preload … loaded` for each model before it takes jobs.
