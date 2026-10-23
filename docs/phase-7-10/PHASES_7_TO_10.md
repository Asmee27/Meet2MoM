# Phases 7–10: accounts, UI, commitment ledger, deletion

Status as of 23 October 2026. Automated tests are listed with each phase. Anything not covered by a test or a recorded run is listed under "Not verified".

## Phase 7: accounts and meeting access

**Accounts.** Registration and sign-in use email and password (`/api/auth/register`, `/login`, `/logout`, `/me`).
- Passwords are stored as scrypt hashes with a random salt. The minimum length is 8 characters.
- The session is a random token in an HttpOnly, SameSite=Lax cookie `m2m_session`. Only its SHA-256 hash is stored, and it expires after 7 days.
- `SESSION_COOKIE_SECURE=true` adds the Secure flag for HTTPS.

**Ownership and membership.** Uploading a recording or creating a live room makes the user its owner (`meeting_members`, `meetings.owner_user_id`).

Every `/api` meeting route checks membership in one router dependency (`auth.require_meeting_access`):

| Request | Response |
|---|---|
| Signed out | 401 |
| Signed in, not a member | 404 (does not reveal that the meeting exists) |

The meeting list shows only the user's meetings. Deleting a meeting and creating invitations are owner-only (403 for other members).

**Invitations.** The live-meeting host's "Copy invite" link carries a single-meeting token that is valid for 2 days. A signed-in guest accepts it before joining and becomes a member of that one meeting.

**LiveKit identity.** With sign-in on, the token endpoint ignores the identity, name and host flag sent by the client:
- identity = account id;
- name = account display name;
- host = the meeting owner.

A non-member gets 403, so spoofing another participant's identity or claiming host is not possible.

**Audit.** Review edits, approvals and reopenings record the signed-in user as reviewer ("Name <email>"), whatever name the request contains.

**Local demo path.** `AUTH_REQUIRED=false` restores the previous open single-user mode.

**Migration.** Schema v5 adds `users`, `sessions`, `meeting_members` and `meeting_invitations`, plus `meetings.owner_user_id`. Existing meetings have no owner. With sign-in on, they are hidden until an owner claims them: `scripts/claim_meetings.py --owner-email … --apply` claims only meetings without an owner. With sign-in off, they stay visible.

**Tests.** `tests/test_auth_access.py` (29) covers:
- signed-out access (every meeting endpoint);
- non-member access;
- list filtering;
- uploader ownership;
- the reviewer identity in the audit log;
- server-chosen identity and host role, including spoofing attempts;
- invitation validation;
- the open mode.

`frontend/src/__tests__/auth_gate.test.jsx` (4) covers the sign-in screen.

**Not included.** Email verification, password reset, rate limiting of sign-in attempts, and account deletion.

## Phase 8: UI

The UI is kept to React and CSS, with no new dependencies.

**Sign-in screen.** `AuthGate` shows a sign-in screen only when the API answers 401. The header shows the user and a Sign out button.

**Meeting list.** It shows the file name, local date and time, status, a short id and the counts. Delete asks for confirmation first.

**Summary page.** It gains the commitment ledger panel.

Processing timings (Phase 5) and the review workspace (Phase 3) are unchanged.

**Not done.** A separate dashboard page, a redesigned processing view, and in-place transcript correction.

## Phase 9: commitment ledger (two-meeting slice)

**Meeting 1.** Approving the minutes records each approved action item as a commitment in the owner's ledger. A commitment holds:
- text and assignee as written;
- deadline;
- the cited utterance ids;
- status `open`.

The assignee is never linked to an account by name (`assignee_user_id` stays empty).

**Meeting 2.** "Check open commitments" proposes `done`, `delayed` or `no_update` for each open or delayed commitment from the owner's earlier meetings:
1. Hybrid retrieval returns up to 5 utterances.
2. Only utterances that share at least 40 % of the commitment's content words (and at least two of them) are kept.
3. The NLI model scores "<task> is done." and "<task> is delayed." against each one. A trailing deadline is removed from the task first.
4. A contradicted "done" counts towards delayed.
5. Below 0.60, the proposal is `no_update`, with no evidence.

Every proposal cites its utterances, with speaker and time.

**Review.** A proposal changes nothing. The reviewer confirms it or corrects it, with an optional note. Only then does a `done` or `delayed` result change the commitment status. Every review is kept as history: proposed status, final status, confirmed or corrected, reviewer, note and time.

**Migration.** Schema v6 adds `commitments` and `commitment_updates`.

**Tests.**
- `tests/test_commitment_ledger.py` (10) covers: approval to commitment; cited proposals for done, delayed and no update; that a proposal alone changes nothing; correction and history; a delayed item followed up again; invalid reviews; the ledger being private to its owner; and two regressions found on the real model.
- `frontend/src/__tests__/ledger_and_delete.test.jsx` covers the panel.

**Real-model check.** This used the synthetic demo meetings (`demo/fallback_meetings.json`), real retrieval and NLI models, and isolated storage. Output: `G:\Meet2MoM_evidence\seedtest_ledger_output.txt`.

The first version proposed `delayed` for both commitments, each citing the *other* commitment's utterance. "The mobile layout check is not done" contradicted "the login page is done". It also scored "… by Friday is done." as neutral. The topic gate and the deadline removal fix both problems. After them:

| Commitment | Proposal | Cited evidence |
|---|---|---|
| update the login page with loading and error states by Friday | done | u00001 "The login page update with loading and error states is done now." |
| check the mobile layout before the demo | delayed | u00002 "The mobile layout check is not done yet, it slipped to Monday." |

The proposal took 30 s, most of it loading the NLI model.

This is one synthetic pair, not an accuracy measurement. The 0.60 threshold and the 40 % gate are provisional.

## Phase 10: deletion, restart and demo data

**Delete.** `DELETE /api/meetings/{id}` (owner only) removes:
- every database row of the meeting (transcript, claims, verification, review, approvals, live-meeting records, stage timings, membership, invitations);
- the commitments created from it, with their history;
- its storage folder (recording, tracks, transcripts, minutes, PDFs, LLM, NLI and retrieval caches);
- the shared pipeline-cache entry, unless another meeting has the same recording.

**Refused cases.** Deletion is refused (409) while the meeting is being recorded, has an open recording segment, or has a running job. A queued job is removed in the same transaction. The job runner also skips meetings that no longer exist, so a late worker cannot recreate one.

**Tests.** `tests/test_meeting_deletion.py` (6).

**Restart.** Interrupted jobs and recordings are recovered at start-up (Phase 4 tests). Deleted meetings do not come back.

**Fallback demo data.** `scripts/seed_demo_meetings.py --owner-email you@example.com` loads two synthetic meetings from `demo/fallback_meetings.json`. They run through indexing, topics, rule extraction and verification without a recording or network access. Run in isolated storage, it gave:
- planning meeting: 4 extracted items, verification completed;
- follow-up meeting: 0 items.

**Secrets.** `.env`, recordings (`*.wav`, `*.webm`, …), `storage/` and `.vscode/` are not tracked. `.env.example` documents every setting without values.

**Not done.** A public deployment and backups.
