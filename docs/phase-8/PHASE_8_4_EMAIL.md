# Phase 8.4: invitation emails and approved-minutes delivery

## Summary

- Scheduled-meeting invitees are emailed their own link. Each invitee has one tracked delivery status.
- Approved minutes can be emailed to people in the meeting.
- Requests only queue messages. A background dispatcher sends them through a provider.
- Development mode sends nothing and says so everywhere.

## Provider: Brevo transactional API

Reference: <https://developers.brevo.com/docs/send-a-transactional-email>

### Send request

`POST https://api.brevo.com/v3/smtp/email` with these headers:

- `api-key`
- `content-type: application/json`
- `accept: application/json`

The body carries:

- `sender {name, email}` and `to [{email, name}]`
- `subject` and `htmlContent`
- `headers {"X-Mailin-custom": "m2m-outbox-<id>"}` and `tags`
- optionally `attachment [{name, content(base64)}]`

### Responses

- **HTTP 201 `{messageId}`:** Brevo **accepted** the message. This is shown as "Sent to provider", never as delivered.
- **HTTP 429 and 5xx:** retried with backoff, up to `EMAIL_MAX_ATTEMPTS`. `Retry-After` is honoured, and the wait is shown in the status.
- **400 / 401 / 402 / 403:** fail at once, with a reason. The API key is never included.
- **Timeout:** marked failed with "may or may not have been accepted" and is **not** retried automatically. This avoids duplicate emails. Check the Brevo logs, then retry by hand.

### Delivery events

Delivery events arrive through the transactional webhook:

- `delivered`
- `soft_bounce` and `hard_bounce`
- `invalid_email`
- `blocked` and `spam`
- `deferred`
- `error`

They map to the statuses in the next section. `request` and `unsubscribed` are stored in the history only. Opens and clicks are ignored and not stored.

## Statuses

| Status | Meaning | Shown as |
|---|---|---|
| queued / sending | waiting / being handed to the provider | Queued / Sending |
| not_sent_dev | development mode, nothing sent | Not sent (development mode) |
| provider_accepted | Brevo accepted it | Sent to provider (not green) |
| deferred, soft_bounced | provider still trying | Delayed / Soft bounce |
| delivered | Brevo reported delivery to the recipient's server | Delivered (green) |
| hard_bounced, invalid, blocked, spam | permanent problem | Bounced / Invalid / Blocked / Marked as spam |
| failed | not sent, or unknown after a timeout | Failed (+ reason, Retry) |
| cancelled | invitation revoked, meeting cancelled, or minutes reopened before sending | Cancelled |

Status rules:

- A later event never moves a message back to a weaker state. For example, "sent" never overrides "delivered".
- Only `failed` can be retried. A retry re-queues the **same** record, so it never creates a duplicate row, membership or meeting.

## Worker and outbox

**`email_outbox` table (schema v9)**

- One row per message.
- Each row has a unique **idempotency key**:
  - `invite:<invitee>:<n>`
  - `minutes:<meeting>:v<version>:<recipient>[:pdf]`
- Repeating a request therefore never queues the same email twice.

**`email_events` table:** holds the provider events for each row.

**Dispatch**

| `EMAIL_DISPATCH` | Who sends |
|---|---|
| `api` (default) | A daemon thread in the API process. It is woken when something is queued and polls every 20 s for retries. |
| `worker` | `scripts/worker.py`, on its own thread so long pipeline jobs never delay email. |
| `off` | Nothing sends. |

- Rows claimed by a process that stopped mid-send become `failed` with "may or may not have been sent" after 10 minutes. They are never treated as delivered.
- A job error never marks a message sent or delivered.

**Link tokens**

- Tokens are **not** stored in the outbox or in logs.
- An invitation or minutes link is issued when the message is actually sent. Only its hash is stored in `meeting_invitations`, tagged with the invitee and the channel (`copy`, `email` or `minutes`). The link exists only in the email body.
- If the message is not sent (development mode or failure), that token is deleted.
- Issuing a new link on one channel replaces only that channel's previous link. Copying a link never breaks the emailed one.
- Logs show only the masked recipient (`a***@example.com`) and the subject.

## Invitations

- Addresses are checked with **email-validator**, for syntax only and without a DNS check. They are lower-cased and de-duplicated.
  - Reserved domains such as `.test` are refused.
  - A well-formed address can still bounce. Only delivery events show that.
- Scheduling saves the meeting and invitees, issues a copy link per invitee, and queues one invitation email per invitee.
  - Each email contains the title, the date and time in the meeting's timezone with its UTC offset, the duration, the host's name, the agenda and that invitee's own link.
  - Scheduling never creates a LiveKit room or starts recording.
- Revoking an invitee or cancelling the meeting cancels queued emails. Every link for that invitee or meeting stops working.

### Recipient verification

A scheduled-meeting invitation is addressed to one email:

- An account with **that email** that opens the link joins as a participant.
- **Any other account** is not admitted. It sees "This invitation was sent to a***@…" and waits.
  - The host sees the waiting account on the meeting page and chooses **Admit** (participant) or **Turn away**.
  - An account is never silently treated as the invited person.
- Accounts are never made owners by an invitation, and the host keeps the host role.
- Meet2MoM does not verify account emails at registration. The email match is a consistency check on top of possessing the emailed link, not proof of identity on its own.
- Plain live-room invitations from Phase 7 (the in-room "Copy invite link") are not tied to an email. They are unchanged.

## Approved minutes

- **Who can send:** "Email approved minutes" on the minutes page is shown only for approved minutes, and only to:
  - the meeting owner, or
  - the member whose account approved the current version.
- **Recipients come from the meeting itself:**
  - members (host and participants);
  - scheduled-meeting invitees who have not joined. Their link carries a fresh invitation, so they sign in with the invited address and join first.
  - Revoked invitees, and accounts still waiting for approval, cannot be chosen.
  - No address can be typed in. The server re-checks every recipient and refuses anything else.
- **The exact approved snapshot is used:**
  - The client sends the version it showed. A different current version is refused.
  - If the minutes are reopened or re-approved before sending, queued messages are cancelled.
- **Content:**
  - title, date, approval line (version, approver, time) and the summary;
  - **confirmed** decisions and action items only;
  - "N items were held for review and are not part of the confirmed minutes". Held items are never listed as commitments;
  - a link to the minutes. That link still requires sign-in and membership, so no other meeting's minutes can be reached by changing the URL.
- **PDF attachment:** off by default. It needs the "Attach" choice **and** a confirmation checkbox, and the server enforces both.
- **Result:** the panel shows queued and "already sent or in progress for this version, not sent again". The history table shows each recipient's status, with **Retry** for failures.
- **Audit log entries** (`review_audit_log`):
  - `minutes_email_requested`: who, which version, recipients, PDF yes/no, queued counts and provider;
  - `minutes_email_status`: each delivery result;
  - `minutes_email_retry`.
  - No tokens, links, passwords or keys are recorded.
- Provider failure never changes the approval or the snapshot.

## Configuration

These settings go in `.env`. They are documented in `.env.example`.

| Setting | Purpose |
|---|---|
| `EMAIL_PROVIDER` | `console` (default, development mode) or `brevo` |
| `BREVO_API_KEY` | Brevo API key (never commit) |
| `EMAIL_SENDER_ADDRESS` / `EMAIL_SENDER_NAME` | verified Brevo sender |
| `BREVO_WEBHOOK_TOKEN` | shared secret required on delivery webhooks |
| `PUBLIC_APP_URL` | frontend address used in emailed links (default `http://localhost:5173`) |
| `EMAIL_DISPATCH` | `api` / `worker` / `off` |
| `EMAIL_TIMEOUT_SECONDS`, `EMAIL_MAX_ATTEMPTS` | provider timeout and retry bound |

`provider=brevo` with a missing key or sender has these effects:

- The UI says email is unavailable.
- Each message fails with that reason.
- Nothing is reported as sent.

## Manual setup you must do

1. Create a Brevo account. Free plans have a daily sending limit; check your plan.
2. **Verify the sender:** Senders, Domains & Dedicated IPs → Senders. Add the address you will send from and confirm it.
3. **Authenticate the domain** (recommended for real inbox delivery):
   1. Add Brevo's DKIM and the DMARC/SPF records it shows to your domain's DNS.
   2. Wait for Brevo to mark the domain authenticated.
   - Free mailbox domains such as gmail.com cannot be authenticated and are often filtered.
4. **Create an API key:** SMTP & API → API keys. Put it in `.env` as `BREVO_API_KEY`, then set `EMAIL_PROVIDER=brevo`, `EMAIL_SENDER_ADDRESS` and `PUBLIC_APP_URL`.
5. **Delivery webhook** (optional but needed for Delivered/Bounced):
   1. Expose `POST <api-url>/api/email/webhooks/brevo` over public HTTPS, for example a tunnel to port 8000 during testing.
   2. In Brevo (Transactional → Settings → Webhook, or `POST /v3/webhooks`), choose these events: delivered, soft bounce, hard bounce, invalid, blocked, spam, deferred and sent.
   3. Add a custom header `X-Meet2MoM-Webhook-Token: <your BREVO_WEBHOOK_TOKEN>`.
   - Without the token setting, the endpoint answers 503. With a wrong token it answers 401.
6. Restart the API (and `scripts/worker.py` if `EMAIL_DISPATCH=worker`).
7. **Test with real inboxes you own:**
   1. Schedule a meeting with three of your addresses.
   2. Check each inbox, including spam.
   3. Open a link while signed in with the matching account, and with a different account.
   4. Approve minutes and send them to a member and to an invitee who has not joined.
   5. Watch the statuses change from "Sent to provider" to "Delivered".

## Not verified

- No real email was sent and no real Brevo account was used. The Brevo request format and error handling were tested against a fake HTTP layer, and every automated test uses a fake sender.
- Real inbox placement, the Brevo webhook from a live account, and LiveKit rooms were not exercised.

## Files

**Backend**

- Config and data: `app/core/config.py`, `app/core/db.py` (v9).
- Accounts and invitations: `app/core/auth.py`, `app/api/schedule_routes.py`, `app/api/auth_routes.py` (unchanged).
- Email API and startup: `app/api/email_routes.py`, `app/api/main.py`.
- Email services: `app/services/email/` (addresses, providers, templates, outbox, minutes) and `app/services/invitations/delivery.py`.
- Supporting: `app/services/meeting_deletion.py`, `scripts/worker.py`, `requirements.txt`, `.env.example`.

**Frontend**

- Shared pieces: `components/EmailStatus.jsx`, `components/MinutesEmailPanel.jsx`, `services/api.js`, `styles/shell.css`.
- Pages: `pages/ScheduleMeeting.jsx`, `pages/ScheduledMeeting.jsx`, `pages/Summary.jsx`.
- State: `context/MeetingFlowContext.jsx` (accepts the invitation in an emailed minutes link before loading).

**Tests**

- `tests/test_email_delivery.py` (new).
- `tests/test_scheduled_meetings.py` (updated for email matching).
- `frontend/src/__tests__/email_delivery.test.jsx` (new) and `scheduling.test.jsx` (updated).
