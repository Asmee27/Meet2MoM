# Phase 8.3: evidence-accurate minutes and scheduled meetings

## Part A: unsupported deadlines, fallback extraction and the evidence appendix

### What went wrong (uploaded meeting `fad3ce321ee64d8c922887e3f194c247`)

- The mixed recording was transcribed as "…email the access links **by Twitter**". The speech was unclear; ASR guessed a word.
- Gemini timed out (2 attempts), so the hybrid extractor ran. It kept "by Twitter" in the task text and set no deadline.
- Fusion then added "No deadline was stated." beside a task that visibly ends in "by Twitter". The PDF never said the LLM had not run.
- Both voices were diarized as `SPEAKER_00`. Nothing linked them to people.
- The decision was held by an off-topic contradiction (0.867). The running backend predated the Phase 8.2 topic gate because the long-lived worker had not been restarted. On current code, a copy of the meeting verifies the decision (on-topic contradiction 0.0003).

All experiments used copies in an isolated store. The original meeting and its artifacts were not changed.

### Changes

- **Unrecognised deadlines.**
  - A task ending in "by / before / until X", where X is not a date, weekday, relative or event-anchored deadline, gets the warning `deadline_unrecognized` instead of `no_deadline`. The warning names the phrase and asks the reviewer to correct the transcript against the audio.
  - Means ("by email") and people in the meeting ("approved by Priya") are excluded. No phrase is hard-coded.
  - The task stays valid and exportable, and its deadline stays empty.
- **Unresolved LLM deadlines.** A deadline the resolver cannot interpret is shown as `Unresolved: "<text>"`, never as a date, and a "Deadline unclear" follow-up is added.
- **Reviewer deadlines.** A deadline the reviewer types is shown as typed. It is recorded in the review audit log, and the item is re-verified through the existing review flow.
- **Evidence appendix.**
  - There is one block per confirmed, held or rejected item. Each block shows the outcome, the claim, its warnings, and every cited utterance with ID, speaker, time and quote.
  - Each speaker is marked "participant" (a linked LiveKit track or named speaker) or "unnamed label" (a diarization label such as `SPEAKER_00`).
  - Items the reviewer excluded are listed separately and are never presented as evidence.
- **Verifier name.** The PDF shows "Text match + NLI (fusion-v1)". Full model and config IDs are in `verifier_versions` in the JSON export.
- **Fallback extraction.** When the LLM did not run, the minutes payload carries `extraction.fallback_note`, which names the extractor and the reason. The PDF prints it as an amber banner before the summary, and the minutes page shows it as an alert.
- **Consistent counts.** The minutes, appendix outcomes and review counts are built from the same claims view. JSON exports carry the same appendix as the approved snapshot.

Tests: `tests/test_evidence_minutes.py`.

## Part B: scheduled meetings with several invitees

### Data (schema v8, additive migration)

**`scheduled_meetings`**

- Columns: `meeting_id` (the meeting's own ID), title, agenda, `scheduled_start_utc`, IANA `timezone`, optional `duration_minutes`, a reserved `room_name`, `status`, `created_by`, `request_id`.
- `status` is `scheduled`, `started` or `cancelled`.
- `request_id` makes a repeated submit idempotent.

**`meeting_invitees`**

- Columns: one row per (meeting, email), `status`, `token_hash`, `delivery`, `accepted_user_id`.
- `status` is `pending`, `accepted` or `revoked`.
- `delivery` is always `manual`.

**Existing tables**

- `meetings.status` is `scheduled` or `cancelled` until the meeting starts. After that the existing statuses apply.
- Ownership uses `meeting_members` (role `owner`).
- Link tokens use `meeting_invitations`, which stores hashes only.
- Meeting deletion also removes the two new tables' rows.

### Lifecycle

1. **Schedule.** `POST /api/schedule` validates the title, the timezone (via `zoneinfo`) and that the start is in the future. A wall-clock time that does not exist in that timezone (DST gap) is refused. It also validates and de-duplicates emails, case-insensitively, and refuses the host's own address.

   It creates the meeting, the schedule and one link per invitee. It creates no LiveKit room, recording, job or extraction.

2. **Links.**
   - **Format.** Each link is `/?room=<reserved room>&invite=<token>`, the same shape as live invitations.
   - **Expiry.** A link expires at the later of 2 days from now and 1 day after the scheduled end.
   - **Copy link.** Only hashes are stored, so a link cannot be shown twice. "Copy link" issues a new one, and the previous link stops working.
   - **Revoke.** Revoking closes the link. If that person already joined, it also removes their membership.
   - **Cancel.** Cancelling revokes every link.
3. **Accepting.** Opening a link while signed in makes that account a member of this meeting only. The invitee row records which account accepted it. If that account's email differs from the invited address, the host sees a note. The typed email is never treated as proof of identity.
4. **Start.**
   - Only the owner can open the reserved room (`POST /api/live/rooms`). The live meeting then uses the scheduled meeting's ID, so members and links carry over.
   - Before the start, members get "has not started yet". Anyone else gets 404.
   - A cancelled meeting's room returns 410.
   - Consent and recording work exactly as in other live meetings.
5. **Owner-only actions.** Edit (`PATCH …/schedule`), add invitees, copy link, revoke and cancel. Other members can read the time and agenda but not the invitee list.

### Email

Meet2MoM has no email configuration, so it never claims that an email was sent:

- `app/services/invitations/delivery.py` defines the delivery interface. The only channel, `manual`, returns `sent: false` with "No email was sent".
- API responses carry `email_sent: false`.
- The UI says the host must share each link themselves.

### UI

- **Dashboard:**
  - A **Schedule meeting** button.
  - **Upcoming** and **Live** filters and badges. Scheduled rows show the start in the meeting's own timezone and the invitee count.
  - Cancelled meetings are shown as **Cancelled**.
- **Schedule form:**
  - Fields: title, agenda, date, time, explicit timezone (defaults to the browser's) and optional duration.
  - Invitee entry accepts several addresses at once, shows duplicate and invalid-address errors inline, and can remove an address.
  - A review step comes before submitting.
  - The success page lists one copyable link per invitee and states that no email was sent.
- **Scheduled meeting page:**
  - Shows status, details and the room code, noting that the room code alone does not grant access.
  - Lists invitees with their status and the account they joined as.
  - Actions: copy (re-issue) a link, revoke, invite more, cancel with confirmation, and start the meeting, which opens the live room with the reserved code and title.

Tests: `tests/test_scheduled_meetings.py` and `frontend/src/__tests__/scheduling.test.jsx`.

### Not covered

- No email or reminders are sent.
- The LiveKit room itself and in-browser joining were not exercised against a real LiveKit server. The tests stub room creation.
- After pulling, restart the backend. The long-lived worker keeps old modules loaded.
