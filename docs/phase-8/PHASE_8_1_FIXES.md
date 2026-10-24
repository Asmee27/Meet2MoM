# Phase 8.1: demo bugs found in testing

Investigated on 24 October 2026 against the real database and recordings. All audio experiments ran on copies; no stored meeting was reprocessed or changed.

## 1. Invited guest could not join ("host already joined")

There were three causes. Each was confirmed in code; the first two were also confirmed in the real database.

1. **Create-room ownership check never ran (backend).**
   - `create_room` read `owner_user_id` from `db.get_meeting()`, which does not select that column, so the check always passed.
   - A second account entering the room code was made a second **owner**.
   - Its token request was then treated as a host request and refused by `claim_live_meeting_host` with "This meeting already has a host".
   - The real database shows exactly this: meeting `7d5a14c5…` (room `livekit-m2m-8385`) lists two owners, the host and the guest account `u_19aa039262304a3f`.

   Fix:
   - `db.get_meeting_owner_ids()` is used for the check.
   - The host role goes to the primary owner only, so meetings already damaged by this bug admit the extra "owner" as a participant.
2. **The invite link lost its parameters (frontend, Phase 8 router).**
   - On start-up the URL sync ran before live mode was applied and rewrote `?room=…&invite=…` to the plain start page.
   - The guest saw the host's "Create a Live Meeting" form with host defaults, and the invitation token was gone.

   Fix: the URL is not rewritten until the start-up route has been applied, and the live room reads the room and invitation once at mount.
3. **The join form kept the host role (frontend).** "Have a Room Code? Join" did not clear `isHost`, so a joining guest:
   - called create-room;
   - skipped accepting the invitation;
   - asked for the host role;
   - was shown host controls.

   Fix:
   - Joining never requests the host role.
   - The invitation's own room is joined.
   - The server's `is_host` decides the role shown.
   - Specific messages cover: invalid or expired invitation, not a member ("a room code alone does not grant access"), no active meeting, and a room code owned by someone else.
   - A guest is not offered "Copy invite link" (only the owner can create invitations).
   - Before the meeting exists, the copy button is disabled, because a plain `?room=` link admits nobody when sign-in is on.
   - LiveKit's `DUPLICATE_IDENTITY` disconnect (same account in two tabs) now explains itself.

**Data not changed.** The extra owner on `7d5a14c5…` is left as it is; it is harmless now because only the primary owner hosts. To remove it, delete that `meeting_members` row (role `owner`, user `u_19aa039262304a3f`).

**Tests.**
- `tests/test_live_invite_join.py` (6) covers:
  - a guest joining while the host is in the room;
  - host reconnect;
  - a guest attempting the old create-room path;
  - a second owner left by the bug;
  - a third account with only the room code;
  - a cross-meeting invitation;
  - invalid and repeated invitations;
  - the owner opening their own link.
- `live_meeting.test.jsx` has 6 new tests for the invite flow.

**Not tested.** Two real browsers against LiveKit Cloud. That needs two accounts in two separate browser profiles (cookies are per browser, so two tabs share one account).

## 2. Lost sentence and the `ml` language

**Same audio, two meetings.**
- `d75ebb7857e84d1b9ddea4fc0567bb68`: a live meeting with two participant tracks, processed on 19 October.
- `eaba38a74c8e4380884c9fa7bda3f253`: today's upload of the same `recording.wav` (identical SHA-256). This is the meeting whose PDF showed `ml`, `SPEAKER_00` and no action items.

**Where the sentence went.**

- **Live meeting (`d75ebb…`).** Track transcription recovered both sentences, including "The quarterly budget review is scheduled for Tuesday." (Om). Rule extraction (the LLM provider was then the test stub) produced no claim for it, because it is a scheduling statement rather than a decision or a task.
- **Upload (`eaba38…`).**
  - It was a **pipeline cache hit** (0.46 s). The cache key was the recording hash only, so it reused a transcript made on 22 October with automatic language detection and without the pause split.
  - That run detected `ml` and decoded one 23.9–45.4 s segment containing only "Thank you. I will submit the marketing report by Friday." The budget sentence was lost, and the one merged segment was attributed to `SPEAKER_00`, although diarization had found two speakers.

**Experiment on copies** (faster-whisper `small`, int8, CPU, VAD as configured):

| Input | Settings | Detected | Result |
|---|---|---|---|
| Mixed recording | auto language, no split (old) | `ml` 0.72 | avg log-prob −1.14, low enough to trigger temperature fallback, so the output is not deterministic. One run returned both sentences; the run that crashed printed Malayalam script for the first sentence. |
| Mixed recording | `en`, split 1.5 s (current) | `en` | "The quarterly budget review is scheduled for Tuesday." / "Thank you." / "I will submit the marketing report by Friday." (log-prob −0.39) |
| Host track | `en` | `en` | "The quarterly budget review is scheduled for Tuesday" |
| Guest track | auto | `ml` 0.97 | English text, labelled Malayalam |

**Conclusion.**
- The audio is clear.
- The sentence was lost because the upload reused a stale auto-detected transcript.
- `ml` is Whisper's language identification mislabelling short accented English.

**Fixes.**
- The pipeline cache key now includes the ASR settings: language, prompt, pause split, model and compute type. Changing `WHISPER_LANGUAGE` makes the next run transcribe again.
- With `WHISPER_LANGUAGE` set, the meeting language is the configured one, and the transcript records `language_source: configured`; otherwise it is `detected`.
- Minutes and PDF show "English (en), set" or "Malayalam (ml), detected", plus the per-segment languages when they disagree. Transcripts made before this change carry no source and are shown without one.

**Not done.** `eaba38…` was not reprocessed; its approved snapshot stays as approved. Uploading the recording again, or reprocessing it, will now transcribe with the current settings.

**Speakers.** The mixed recording has two speakers (diarization: 23.9–27.2 s and 42.2–45.3 s). The single `SPEAKER_00` came from the merged ASR segment. The PDF now marks bare `SPEAKER_xx` labels as unnamed diarization labels, and owners are never mapped to accounts by name.

## 3. Minutes said "no action items" while the summary described one

On approval, every unconfirmed item was filed as `excluded_from_mom`, and `needs_review` was always empty. The PDF of `eaba38…` therefore showed:

- a green ✓ "No reliable action items detected";
- "0 held for review";
- the LLM summary "SPEAKER_00 stated they will submit the marketing report by Friday."

The candidate had been held, for an uncertain speaker and low ASR confidence.

**Fix.**
- Approval now separates reviewer **exclusions**, **rejections** and items **held for review**.
- Held items are listed with their reasons in the snapshot, the JSON export, the minutes page and the PDF.
- The counts match the review screen.
- The PDF shows an amber "No action items are confirmed. N candidate(s) are held for review" instead of the green tick.
- The LLM summary is labelled as a report of what was said, not commitments of record.
- A reviewer-supplied deadline clears only the stale "No deadline was stated" warning.
- Verification rules are unchanged.

**Tests.** `tests/test_minutes_consistency.py` (7): held vs excluded vs rejected, counts across review / approval / JSON / regenerated minutes, the deadline warning, configured vs detected language, the cache key.

## 4. UI

- **Review:** a compact header with held / confirmed / rejected / corrected counts. The approval panel sits in the right rail above the transcript, so it no longer covers claims. Claim cards are tighter, evidence is a quiet quoted list, and the transcript edit control appears on hover or focus.
- **Minutes:** a document layout with a navy title band, a facts strip, uppercase section headings and a held-for-review panel.
- **Live form:** a role notice ("You are joining as a participant" / "You will host"), the signed-in account, and a visible mode switch.

Screenshots were taken with headless Edge at 1440, 768 and 390 px against an isolated copy of the demo data.
