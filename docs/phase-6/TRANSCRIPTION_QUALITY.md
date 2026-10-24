# Transcription quality: "by Friday" heard as "by FFmpeg", overlapping timestamps

Investigated on 22–23 October 2026 for meeting `be3a1a638f254000a1071305bd87f8ed`, a 59-second live meeting with two participant tracks. All experiments ran on copies of the recording. The stored meeting was not reprocessed.

## Symptoms (stored transcript)

| Speaker track | Start–end (s) | Text |
|---|---|---|
| 33cssp | 0.6–8.5 | If you need the login page before publishing the dashboard, let's make sure we cover loading |
| 33cssp | 8.5–**42.3** | states and error messages too. Please take the form validation and make sure it works on |
| nnh398 | 17.3–**54.5** | I will update the login approach and include the loading and error states. I will submit the changes by **FFmpeg**. |
| 33cssp | 42.3–45.5 | mobile. We will review the results at the next meeting. |
| nnh398 | 54.5–59.5 | I will take the validation, check the mobile layout, and share the test results by **FFmpeg**. |

There were two cross-speaker overlaps. Two utterances span 34 s and 37 s while the other person was talking.

## What was checked

| Check | Result |
|---|---|
| Track levels | nnh398: peak −13.9 dBFS, speech RMS −30.5 dBFS. 33cssp: peak −13.0 dBFS, speech RMS −28.3 dBFS. No clipping, silent noise floor. Quiet, but not abnormal. |
| Enhancement / gain | Not a cause. A synthetic fixture decodes identically at normal level and at −20 dB (WER 0.000 for both). |
| Tracks / attribution | Correct. Each word is on the right speaker's track. |
| VAD | Correct speech regions. faster-whisper merges VAD chunks back into one timeline, and a segment can then contain words 29–31 s apart. |
| Model / parameters | `small`, int8, CPU, beam 5, language `en`. |
| Initial prompt | **Cause of "FFmpeg".** The prompt included `TECHNICAL_VOCABULARY`, which lists "FFmpeg". |
| Raw vs indexed transcript | Identical. The transcript index does not change the words. |

## Root cause: "FFmpeg"

The same track, decoded three ways (on a copy):

| Prompt | Word decoded where "Friday" is expected |
|---|---|
| Original prompt, including the vocabulary list | "by **FFmpeg**" (both times) |
| No prompt | "submit the changes **back** and …", "share the test results **back** at afternoon" |
| Neutral prompt, no vocabulary | "submit the changes by …" (word dropped), "… **back** at afternoon" |

The vocabulary prompt biases Whisper towards listed terms. When a word is acoustically unclear, the decoder picks a listed term. No model or gain variant produced "Friday". **The spoken word cannot be recovered from this recording.** The pipeline must not guess it. A reviewer has to correct it against the audio.

## Root cause: overlapping timestamps

faster-whisper's VAD drops silence, transcribes the speech, and maps timestamps back. When two speech regions are close in the compressed audio, one segment covers both. Its start and end then span the other speaker's turn (gaps of 29.4 s and 31.2 s between consecutive words).

## Changes (smallest safe fix)

1. **`WHISPER_PROMPT`** (default `none`). The vocabulary prompt is opt-in (`vocabulary`); `language` gives a neutral prompt. See `app/services/transcription/transcriber.py: whisper_initial_prompt()`.
2. **Split at pauses.** Whisper now returns word timestamps. A segment is split where two words are more than `WHISPER_SPLIT_GAP_SECONDS` (default 1.5 s) apart, and each part gets its own start and end. Words are never added, removed or changed. See `split_at_pauses()`.

Nothing hard-codes "Friday". The fixture test checks the general behaviour.

## Result on the same meeting (copy, current code)

| Speaker track | Start–end (s) | Text |
|---|---|---|
| 33cssp | 0.6–8.4 | If you need the login page before publishing the dashboard, let's make sure we cover loading |
| 33cssp | 8.4–10.0 | states and error messages too. |
| nnh398 | 17.3–20.9 | I will update the login page and include the loading and error states. |
| nnh398 | 21.2–23.4 | I will submit the changes back and |
| 33cssp | 39.4–42.6 | Please take the form validation and make sure it works on mobile. |
| 33cssp | 43.2–45.3 | We will review the results at the next meeting. |
| nnh398 | 54.5–57.0 | will take the validation, check the mobile layout and |
| nnh398 | 57.0–59.0 | share the test results back at afternoon. |

- Cross-speaker overlaps went from 2 to 0. There are 8 utterances instead of 5.
- "approach" became "page", which matches the other speaker's wording.
- **Still wrong:** "back" / "back at afternoon" where the speaker probably said "by Friday". It is now an obviously odd word instead of a plausible technical term. The low-confidence flag and the reviewer still have to catch it. Transcript correction in the UI is a follow-up item.

## Fixed fixture (WER)

`scripts/make_asr_fixture.ps1` generates a text-to-speech WAV containing "… I will submit the report by Friday …". The audio is not committed (`*.wav` is ignored). `tests/test_asr_fixture.py` (marked slow) checks WER ≤ 0.10 at normal level and at −20 dB, and skips when the fixture is missing.

| Configuration | Clear | −20 dB |
|---|---|---|
| Before: vocabulary prompt, no split | WER 0.000, 1 segment | WER 0.000, 2 segments |
| After: no prompt, split at 1.5 s | WER 0.000, 3 segments | WER 0.000, 3 segments |

The fixture is clean synthetic speech, so it cannot reproduce the real-meeting error. It shows that the change does not damage clear speech. The real-meeting comparison is the evidence for the fix.

## Limits

- Accuracy on quiet, accented or overlapping real speech is not measured beyond this one meeting.
- A larger model (`medium`) was not adopted: it is too slow for the Phase 6 time target on the demo laptop.
