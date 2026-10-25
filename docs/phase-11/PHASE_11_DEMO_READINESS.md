# Phase 11: final demo readiness and end-to-end acceptance

Work date: 25 Oct 2026 (IST). Branch `phase-11/demo-readiness`, from `main` at `84398a8`, which includes Phase 8.4.

Every result below was observed on this machine on that date. Anything not observed is listed under **Not verified**.

## 1. LiveKit diagnosis

### Root cause: this computer's clock is 8.5 hours wrong

| Check | Result |
|---|---|
| LiveKit Cloud `Date` header | Sun 25 Oct 2026 05:37:18 GMT |
| This PC, in UTC | Sat 24 Oct 2026 21:05:47 GMT |
| Measured offset | **30,691 s**: the PC is 8 h 31 min behind |
| `w32tm /query /status` | "Leap Indicator: 3 (not synchronized)", "Source: Local CMOS Clock", "Last Successful Sync Time: unspecified" |

**Why this breaks LiveKit**

- LiveKit tokens carry not-before and expiry times signed with this computer's clock. Meet2MoM tokens are valid for 6 hours with 2 minutes of leeway.
- With the clock 8.5 hours behind, every token has already expired in LiveKit's time.
- **Server API:** LiveKit returned `ServerError(status=401)` when the server tried to create a room.
- **Participants:** connecting failed with `401 Unauthorized - invalid token`.
- Browsers are affected in exactly the same way, because the backend issues their tokens.

**The real fix** is to sync the Windows clock (section 9). This cannot be done from a non-administrator shell, so it was not changed.

### Defects found and fixed

| Defect | Before | After |
|---|---|---|
| Room-creation 401 swallowed | A 401/403 from LiveKit was logged as "Using lazy creation on join", the request returned 200, and the failure only reappeared later as "invalid token" | A 401/403 raises `LiveKitAuthError`, and `POST /api/live/rooms` returns **503** with the reason and, when the clock is off, how to fix it. Other errors keep the lazy-creation fallback |
| "Ending the LiveKit room failed: HTTP 404, requested room does not exist" | LiveKit closes a room by itself shortly after its last participant leaves. Deleting it afterwards returns 404, and the API turned that into a 500 even though the recording was already stopped and the meeting already ended in the database | The 404 is reported as `status: "already_closed"` with an explanation. Every other error (401, 500…) is still raised. Recording stop and the database end happen first, so no audio is discarded |
| No clock diagnosis | — | `GET /api/live/config` returns `clock_skew_seconds` and `clock_warning`. The live-meeting page shows the warning, and a 503 from room creation is shown instead of being ignored |
| Ended screen could contradict itself | "No consented audio was recorded" appeared whenever there was no finalized file | That message now appears only when there are **no segments**. If segments exist without a file, the screen says so |

**What the 404 was:** harmless cleanup in itself, because the room had already closed. It was reported misleadingly as a failure.

**Yesterday's two scheduled meetings with 0 segments** (`9cf5c73c…`, `2cb265b9…`):

- Their event logs contain **no** `recording_requested` or `recording_blocked` event. Start recording was never accepted, so "No consented audio was recorded" was true.
- The logs show participants repeatedly leaving and rejoining.
- The cause of those drops is **not proven**. A badly wrong clock is consistent with it, but the size of yesterday's clock offset is unknown.
- The consent gate behaved correctly. Nothing about consent was changed.

### Regression tests

`tests/test_livekit_lifecycle.py` (7 tests) covers:

- an already-closed room;
- that other end errors are still raised;
- that the meeting still ends correctly after a 404;
- the 401 → 503 behaviour, including the clock hint;
- that the lazy fallback remains for other errors;
- measuring clock skew;
- that an unreachable clock check never breaks the config endpoint.

## 2. Real LiveKit end-to-end acceptance

**Setup**

- Real LiveKit Cloud project from `.env`, with an isolated backend on port 8010 and isolated storage under `G:\m2m_scratch_83\p11\storage`. Your `storage/` was not touched.
- Two real accounts, and two participants connected with the LiveKit Python SDK. This is real WebRTC through LiveKit Cloud, but **not browsers**.
- **Voices:** synthetic Windows TTS (Microsoft David = host "Samarth", Microsoft Zira = guest "Asmee"), reading a scripted English dialogue. The speech is synthetic, not human.
- **Clock workaround:** to work around the PC clock, *only that test server* shifted the clocks used to sign and verify LiveKit tokens by the measured offset. The Meet2MoM code is unchanged by this. The real app needs the Windows clock synced.

**Meeting 1, `1c9bacd8…`, "Login portal release":** every check passed. Recorded results:

| Check | Result |
|---|---|
| Host creates a real room | Room SID returned |
| Host token | host, consented on the join form |
| Room code alone (guest not yet invited) | **403** |
| Guest accepts invitation | joins the same room |
| Guest asks for the host role | **refused**; `is_host: false` |
| Both in the same room | each sees the other |
| Start before guest consents | **409**, `recording_blocked` naming the guest |
| Guest consents in the room | consent 2/2, `can_start` true |
| Recording | 202, then "recording" with real frames |
| Stop | segment 1 `finalized`, `host_stopped`, **66.3 s, 2 tracks** (Samarth 63.2 s, Asmee 63.9 s) |
| End meeting | meeting ended. The audio was kept and could still be processed |
| Processing | completed (see the timing note below) |

**Transcript:** English, 11 utterances. Speaker attribution comes from the participant tracks (`spk_conf` 1.0), and every timestamp falls in the right turn.

- **ASR error:** one. The guest's "Good morning." was transcribed as "Good." Nothing was invented.
- **Extraction:** `llm:gemini:gemini-3.8-flash:v2`, `mode_used: llm`, **no fallback**.
- **Claims:** 4.

| Claim | Owner | Deadline | Evidence | Verification |
|---|---|---|---|---|
| Decision: Launch the new login portal next Monday | — | — | u00004, u00005 | verified |
| Submit the front end validation report | Asmee | by Friday → 2026-10-30 | u00005 | verified |
| Finalize the backend API and the deployment | Samarth | by Thursday → 2026-10-29 | u00007 | verified |
| Update the user guide for the new portal | Asmee | none | u00009 | verified with warning `no_deadline` |

**Approval, signed in as the host:** version 1.

- **Consistency:** the review counts, the approved payload, `GET /export/json` and the evidence appendix agree. The confirmed IDs equal the appendix's `confirmed` rows. All speakers are marked `participant`.
- **Nothing held or rejected:** there are no held, rejected or excluded items in this meeting, so the "held item" demo uses the evidence-minutes fixture or a meeting with an unclear speaker.
- **LLM summary:** printed as "Discussion Summary (LLM, cited, not verified) … only the confirmed sections below are decisions or commitments of record".
- **PDF text extraction found:** the decision, the three tasks with owners and dates, "No deadline was stated", "Text match + NLI", u00004/u00005, "participant", and Gemini.
- **Fixed in this phase:** the approved PDF never said it was approved. It now opens with `APPROVED MINUTES v1 - approved by Samarth on <time> UTC`, and a draft opens with `DRAFT - these minutes have not been approved by a reviewer.` A regression test covers both.

**Meeting 2, `50f3607d…`, "Login portal follow-up":** the same two accounts, a new room and a new invitation. Again every check passed.

- **Recording:** 41.3 s, 2 tracks.
- **Ending:** this time the room still existed, and LiveKit returned a normal `ended`.
- **Transcript:** 7 utterances.
- **Extraction:** Gemini, with no fallback.

### Processing time (meeting 1, a 66 s recording, cold start)

| Stage | Seconds |
|---|---|
| Speech pipeline subprocess start-up (torch, Whisper, pyannote imports) | 151.6 |
| Audio preparation | 11.3 |
| Transcription (2 tracks, Whisper `small` int8, `en`) | 58.8 |
| Speaker attribution + merge | 1.1 |
| Topics | 13.6 |
| Gemini extraction | 90.4 |
| Verification (retrieval 6.1 + NLI load 8.1 + NLI 42.6) | 57.5 |
| **Total recorded processing time** | **392.3** |

- The first run was interrupted when the test server stopped mid-merge. It was finished with the same `run_pipeline` the worker uses, in 421 s. This is a cold start in a fresh process.
- The everyday setup (`JOB_RUNNER=worker`, `WORKER_PRELOAD_MODELS=true`) keeps models loaded, which avoids most of the 151.6 s start-up. That warm-path time was **not measured** today.

## 3. Commitment ledger (the USP) on the two real meetings

**Defect found:** `ledger_owner()` read `owner_user_id` from `db.get_meeting()`, which never returns that column.

- Every commitment was stored with **no owner**. Your real database had 1 such commitment and the test store had 3.
- As a result, a signed-in user's ledger was always empty, and a follow-up meeting found nothing to propose.

**Fix:**

- `ledger_owner()` now uses the meeting's primary owner.
- **Schema v10** gives each existing owner-less commitment the owner of its source meeting. Nothing else changes.
- In local mode without sign-in, the whole ledger is shown.
- Tested in `tests/test_ledger_ownership.py`, and on a **copy** of your real database: v9 → v10, 1 of 1 commitments got an owner, integrity ok, 5 approved snapshots kept.

**Demo flow, all observed:**

1. **Meeting 1 approved:** three `open` commitments with source meeting, assignee, deadline and evidence:
   - "Submit the front end validation report", Asmee, 2026-10-30, u00005;
   - "Finalize the backend API and the deployment", Samarth, 2026-10-29, u00007;
   - "Update the user guide for the new portal", Asmee, no deadline, u00009.
2. **Meeting 2 proposals** (`POST /api/meetings/{m2}/commitments/propose`), each with the evidence used:
   - validation report → **done** ("I have submitted the front-end validation report." / "That task is done.");
   - backend API → **delayed** ("…the deployment is delayed until next week.");
   - user guide → **delayed** ("I have not started the user guide yet.").
   - Statuses were checked and were **unchanged** after the proposals.
3. **Reviewer decisions:**
   - confirmed done → status `done`;
   - confirmed delayed → status `delayed`;
   - **corrected** the third to `no_update` with the note "Not started; no deadline was set, so not delayed" → it stays `open`.
   - Each commitment keeps its history: proposed status, review action, final status, follow-up meeting ID and reviewer.

Both meetings were real LiveKit recordings with synthetic TTS voices. The follow-up statements were scripted.

## 4. Scheduling and email

**What your real database shows** (read-only; recipients masked):

- Invitations 1–2 failed with Brevo HTTP 401. This was before the key was fixed.
- Invitations 3–5 were `provider_accepted`.
- No delivery events were received, because the webhook needs a public URL. These therefore correctly still show "Sent to provider", not "Delivered".
- One accepted invitation went to a `…@gmil.com` address, a likely typo of gmail.com. A syntax check cannot catch this; only a bounce event would reveal it.
- **No approved-minutes email has been sent yet.**

**Read-only Brevo check today:** the API key is accepted (free plan, 300 emails per day). Listing senders is forbidden for this key's scope.

**Not done today:** no new email was sent. Sending to inboxes needs addresses you control and confirm (section 9).

**Automated coverage, passing:** scheduling and acceptance rules, delivery statuses, retries without duplicates, failures never changing approval, and minutes sent only to entitled recipients.

**Security task:** if `BREVO_WEBHOOK_TOKEN` was ever shown in a screenshot or chat, replace it with a new random value in `.env` and in the Brevo webhook header.

## 5. Tests run on 25 Oct 2026

| Command | Result |
|---|---|
| `pytest tests/test_livekit_lifecycle.py tests/test_live_routes.py tests/test_live_recording.py tests/test_live_invite_join.py tests/test_scheduled_meetings.py` | 70 passed |
| `pytest tests/test_ledger_ownership.py tests/test_commitment_ledger.py` | 12 passed |
| `pytest tests/test_evidence_minutes.py` | 16 passed |
| `pytest -m "not slow"` (full backend) | **555 passed**, 10 deselected |
| `npx vitest run` | **102 passed** |
| `npx vite build` | built (the existing chunk-size warning only) |
| Real LiveKit acceptance (scratch harness, section 2) | meeting 1 and meeting 2: every check passed |

## 6. Demo sequence (about 10 minutes)

1. **Dashboard:** meetings and their states; Schedule meeting.
2. **Live meeting:**
   1. The host starts a meeting and copies the invite link.
   2. The guest opens it in a second browser profile, signs in and joins as a participant.
   3. Show the consent count 1/2 and the blocked Start recording.
   4. The guest consents, giving 2/2.
   5. Start recording, speak alternately, then stop and end the meeting.
3. **Process**, then show:
   - the transcript with both names and timestamps;
   - the Verify step with statuses, reasons and evidence;
   - "Extracted by LLM: gemini".
4. **Minutes:**
   - Approve, then show the approved PDF, with its status line and evidence appendix, and the JSON.
   - Show a held item using a meeting that has one, for example an uncertain speaker. If none exists, say that this meeting had none.
5. **Scheduling and email:**
   - The schedule form with timezone and invitees.
   - The scheduled meeting page with each invitee's status: "Sent to provider" versus "Delivered".
   - Send approved minutes, confirming the version and recipients.
6. **Commitment ledger:**
   - Commitments from meeting 1 are `open`.
   - Meeting 2 proposes done, delayed or no update, with evidence.
   - The reviewer confirms one and corrects one.
   - The ledger history shows both decisions.

## 7. Examination checklist

- [ ] **Windows clock synced** (Settings > Time & language > Date & time > Set time automatically: On, then **Sync now**). Then restart the backend and the worker.
- [ ] `GET http://127.0.0.1:8000/api/live/config` shows `clock_warning: null`.
- [ ] Backend restarted after pulling, so schema v10 is applied and the ledger fix is live.
- [ ] Two browser profiles, each signed in to a different account, and microphones allowed.
- [ ] Your own two-browser rehearsal completed with real voices.
- [ ] One invitation and one approved-minutes email sent to inboxes you own, and checked including spam.
- [ ] A backup demo: the approved PDF and JSON of a rehearsal meeting are saved locally.
- [ ] `.env` never shown on screen.

## 8. Not verified

- A browser-based two-person meeting with human voices on this code. It was verified with SDK participants and TTS voices only.
- Real inbox delivery of an approved-minutes email, and Brevo delivery webhooks (no public URL).
- Warm worker processing time.
- Transcription accuracy for real human speech and accents in this session.
- The LiveKit behaviour after the clock is synced, in your normal backend on port 8000 (expected to work; not observed).

## 9. Files changed in Phase 11

**LiveKit lifecycle**

- `app/services/live/livekit_service.py`: auth errors on room creation, an already-closed room on end, and the clock-skew check.
- `app/api/live_routes.py`: the 503 response, and clock fields in `/config`.
- `frontend/src/pages/LiveMeeting.jsx`: shows the clock warning and a 503 from room creation, and never says "no audio" when segments exist.

**Ledger**

- `app/services/ledger/commitment_ledger.py`: the owner lookup and the local-mode ledger.
- `app/core/db.py`: schema v10 owner backfill.

**Minutes**

- `app/api/meeting_routes.py`: the PDF approval status line.

**Tests and docs**

- New: `tests/test_livekit_lifecycle.py`, `tests/test_ledger_ownership.py`.
- Extended: `tests/test_evidence_minutes.py` (PDF status).
- This document.
