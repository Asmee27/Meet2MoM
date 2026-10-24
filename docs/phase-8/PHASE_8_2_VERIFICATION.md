# Phase 8.2: false contradiction and missing deadline (login-portal meeting)

Investigated on 24 October 2026. The meeting is `a20950156fc04f22825330e7a9af450a` (live room `livekit-m2m-8232`, approved v1):

- host: Samarth (meeting owner);
- guest: Asmee.

Speaker attribution came from the LiveKit participant tracks, confidence 1.0. All experiments ran on copies; the approved meeting and its PDF/JSON were not changed.

## What the recording contains

Stored transcript (`WHISPER_LANGUAGE=en`, fresh ASR, no cache hit):

| Utterance | Speaker | Text |
|---|---|---|
| u00000 | Samarth | We have decided to launch the new login portal next Monday. |
| u00001 | Asmee | I agree with the decision. I will complete the front-end validation and submit the bug report by Friday. |
| u00002 | Samarth | Perfect. |
| u00003 | Samarth | I will finalize the backend API, deployment and email the access links **back on to you**. |

Gemini (`gemini-3.8-flash`) extracted:

- the decision (citing u00000 and u00001);
- Asmee's action, with "by Friday";
- Samarth's action, with no deadline.

## 1. False contradiction (Asmee's action held, "contradiction 0.97")

The 0.968 came from u00002–u00003, a span the verifier retrieved, not cited. The NLI model scored Samarth's own task ("I will finalize the backend API…") as contradicting Asmee's task ("Asmee will complete the front-end validation…"). Two different people's different tasks look mutually exclusive to an NLI model.

The cited utterance entailed the claim (0.94, contradiction 0.005). Neither the decision agreement in u00001 nor the claim segmentation was involved.

**Fix** (`app/services/verification/nli_scorer.py`):

- A premise found only by retrieval may count as a contradiction only if it is about the claim: at least 34 % of the claim's content words must occur in it (`CONTRADICTION_TOPIC_MIN`, using the existing `content_coverage` measure).
- Cited evidence, and spans overlapping it, are always checked as before.
- The thresholds and fusion rules are unchanged.
- The strongest gated-out contradiction is kept in the verification details (`x_off_topic`, `x_off_topic_utt_ids`) for transparency.

**Real-model check** (mDeBERTa-v3-base-mnli-xnli and the multilingual embedder, on a copy):

| | Asmee's action | Contradiction used |
|---|---|---|
| Gate disabled (original behaviour) | review, contradicting_evidence_elsewhere | 0.968 from u00002–u00003 |
| With the fix | verified | 0.008 (on-topic); 0.968 recorded as off-topic |

The decision and Samarth's action are unchanged.

## 2. Missing Thursday deadline

The deadline is not reliably in the audio as transcribed. Re-decoding copies of the host's track:

| Decode | Last words | Word confidence |
|---|---|---|
| Host track, beam 5 and beam 1 | "…links back on to you" | back 0.60, on 0.31, to 0.44 |
| Last phrase only (30–40 s) | "…links by title" | by 0.83, title 0.06 |
| Mixed recording | "…links by title" | |

"By" is likely, but the following word is unclear and never decodes as "Thursday". Gemini therefore had no deadline to extract. The extractor's grounding rule (a deadline must appear in the cited words) would also have removed an invented one. Nothing was changed to force a deadline.

The task stays confirmed with the explicit warning "No deadline was stated."

**To record Thursday**, a reviewer listens at 0:35 and uses the transcript correction. Verification is then stale until it runs again, and the reviewer sets the deadline on the item. The reviewer-supplied deadline clears the warning (Phase 8.1). This path is covered by a test.

## Tests

`tests/test_login_portal_regression.py` (14) uses the real utterances, speaker identities and Gemini output. It replays the one real-model score that caused the defect, because the test NLI stub cannot produce off-topic contradictions. It covers:

- one decision verified against u00000;
- owners from the participant tracks;
- Friday resolved from the meeting date;
- no invented deadline: an LLM "by Thursday" or "next Monday" not in the cited words is removed;
- a spoken "by Thursday" kept and resolved;
- weekday resolution and a missing deadline;
- agreement not contradicting the decision;
- a genuine reversal ("I will not complete the front-end validation…") still held;
- the original behaviour reproduced without the gate;
- review, approved minutes and JSON agreeing;
- the correction, stale-verification and reviewer-deadline path.

## Not done or not verified

- A new real Gemini request and a new LiveKit recording were not run. The diagnosis used the stored transcript, the stored Gemini output and local models.
- The 34 % topic threshold is provisional, like the other fusion thresholds. A reversal phrased with entirely different words than the task ("let's not do that") is now caught only through its neighbouring context (premises include one utterance on each side).
- Claim text keeps ASR artefacts (the host's action ends in "…links back"). Reviewers can edit it.
