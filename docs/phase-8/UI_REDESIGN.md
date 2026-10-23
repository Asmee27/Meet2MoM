# Phase 8: UI redesign and transcript correction

Status as of 24 October 2026. Each item lists how it was checked. Anything without a test or a recorded check is listed under "Not verified".

## Design direction

Meet2MoM's identity is evidence and review. The UI shows what was said, what was extracted from it, and what a person has confirmed.

- **Palette.** The existing navy (`#202858`) for navigation and an indigo accent (`#4f46e5`) for actions, on a neutral canvas. A small warm-orange mark is the only decorative colour.
- **Status colours.** Green for verified or approved, amber for needs review or warnings, red for errors and failures, blue/cyan for running or informational states.
- **Never colour alone.** Every status has an icon and a text label. Recording status in the live room was already text ("REC 0:42", "Not recording").
- **Theme.** Light only. The earlier pages hard-code light colours throughout, so a dark mode would have been partial.

The design system lives in `frontend/src/styles/theme.css`:

- semantic tokens: surfaces, text, borders, accent, status, evidence highlight, radii, elevation, spacing, motion;
- `ui-` components: buttons, badges, cards, fields, alerts, empty states, skeletons, tables, tabs, tooltip, dialog, toasts.

React primitives are in `frontend/src/components/ui.jsx`. No dependency was added.

Earlier page classes (claim cards, reviewer bar, audit log, minutes, live room) are re-skinned onto the tokens in `styles/legacy.css`. Their markup and behaviour are unchanged.

## Screens

| Screen | Change |
|---|---|
| Shell | Sidebar (Meetings, Commitments), "New meeting", user and Sign out; top bar and drawer below 900 px. |
| URLs | `?view=upload`, `?view=live`, `?view=commitments`, `?meeting=<id>&step=transcript\|extract\|review\|minutes`. Back and Forward work. Opening a step link re-runs the same backend calls as the workflow (extract and verify reuse current results). |
| Meetings | Search, status filter and real counts. Meeting history shows name, date, duration, speakers, items, status and the next action. Meetings that are queued, running or failed are listed too. Deleting a meeting asks in a dialog. |
| Commitments | New page showing the ledger (`GET /api/commitments`). An AI proposal ("AI proposal: Done", awaiting reviewer) is shown separately from a reviewer's decision ("Confirmed: Done by …"). |
| Processing | The job's real stages, grouped, with the Phase 5 timings. States shown: cached, fallback (with reason), failed (with error), skipped. Minutes and PDF show as "Runs on demand". No percentage is shown. The automatic verification bar is indeterminate instead of the earlier fixed 15 % / 100 %. |
| Transcript | Utterances with speaker, time, id and low ASR confidence (same 0.50 threshold as the verifier), plus correction (below). |
| Extract | Fixed: when nothing was extracted, Continue is no longer disabled forever. |
| Review | Two panes: claims beside the transcript. Clicking an evidence id scrolls to and highlights the utterance. "Why it is held for review" label. A stale-verification banner offers "Run verification again". Approval is disabled with the reason while verification is stale. The reviewer name is prefilled when signed in. |
| Minutes | Export bar (approval state, PDF, JSON), document layout and an action-item table with evidence ids. |
| Sign in | Split layout. Labels and behaviour unchanged. |

## Transcript correction (fully functional)

`PUT /api/meetings/{id}/transcript/{utt_id}` with `{text, reason}`.

1. **The ASR text is never overwritten.** Schema v7 adds `transcript_corrections`, an append-only history with `asr_text`, `previous_text`, `corrected_text`, reviewer, reason and time. `db.get_utterances` shows the latest correction only while the stored ASR text still equals the text it corrected, so a reprocessed meeting cannot receive a correction meant for other words.
2. **Audit.** Every correction is also a `transcript_corrected` event in the review audit log, with the old and new text. The audit drawer shows the change.
3. **Stale results are held.** `build_claims_view` treats a fused verification result older than the latest correction as stale. The claim is shown as `review` (held), is not exportable, and carries the reason `transcript_corrected_after_verification`. The minutes, the review UI and the approval all read this view.
4. **Refresh.** `verification_is_current` becomes false, so the next verification re-runs on the corrected text. The retrieval-index cache (keyed by utterance count) and stale minutes files are deleted when a correction is saved.
5. **Refused cases.**
   - Approval is refused (409) while any claim is stale.
   - Corrections are refused (409) while the minutes are approved (reopen first) or while a job is queued or running.
   - Empty, unchanged or over-long text is refused (422).
6. **No automatic wording.** Nothing replaces "FFmpeg" or any other word. Claim text is not re-extracted. A claim that still says "by FFmpeg" is re-verified against the corrected utterance, and the reviewer edits it with the existing item editor.

`GET /api/meetings/{id}/transcript` now also returns `asr_text`, `corrected` and `correction` for each utterance. `GET /api/meetings/{id}/transcript-corrections` returns the full history. Deleting a meeting removes its corrections.

## Fixed while redesigning

- **Approved minutes were rebuilt as a draft.** Opening the Minutes step called `POST /generate-mom`, which rebuilt a draft from claims and overwrote the PDF; "Download approved PDF" could then serve that draft. While a review is approved, `generate-mom` and `download-mom` now use the approved snapshot. After reopening, drafts are generated as before.
- **Reviewer names were missing from audit entries.** The UI read `reviewer`, but the API returns `reviewer_id`.
- **Audit and approval times were off.** They are stored in UTC without a zone and were shown as local time without conversion.

## Performance

| Build | Main JS | Notes |
|---|---|---|
| Before | 848 kB (one bundle) | |
| After | 257 kB (79 kB gzip) | LiveKit's client is in a 566 kB chunk loaded only for the live room. Vite still warns about that chunk; the warning limit was not raised. |

Review and minutes are separate chunks of 30 kB and 14 kB.

## Motion and accessibility

- **Motion.** CSS only:
  - page and dialog entrance (≤ 260 ms);
  - sliding sidebar indicator and stepper underline;
  - button and card feedback;
  - evidence highlight flash and skeleton shimmer;
  - stage-state colour transitions.

  `prefers-reduced-motion: reduce` stops animations and transitions; status is still shown by text and icons.
- **Keyboard and focus.** Skip link, visible focus ring, dialogs that trap focus, close on Escape and restore focus, and a drawer with `aria-expanded`.
- **Labels and live regions.** Labelled form fields, `aria-current` for navigation and steps, and polite live regions for toasts and stage changes.
- **Narrow screens.** Tables stack into labelled rows below 860 px.

## Tests

| Suite | Result |
|---|---|
| Backend `tests/test_transcript_correction.py` | 8 tests: persistence, audit, stale claims, approval refused until re-verified, cache invalidation, invalid input, locked states, revert, reprocessed utterance, deletion |
| Backend `tests/test_approved_minutes_served.py` | 3 tests |
| Frontend `phase8_ui.test.jsx` | 12 tests: correction form, refusal, locked transcript, stale banner and blocked approval, evidence focus, processing states (no percentage), URL plus Back, sign-out, mobile drawer, status mapping |
| Frontend, updated | Three tests moved from copy that no longer exists to the same behaviour: both entry points on the start screen, the live entry opening the room, delete only after confirmation. One test was added: a refused deletion keeps the meeting. |

## Not verified

- No automated browser or screen-reader run. Screenshots were taken with headless Edge at 1440, 768 and 390 px (390 via an iframe, because headless Edge does not go below a 504 px viewport). A keyboard-only pass and contrast checks with a tool are still manual.
- The live room was restyled through CSS only and was not exercised against a running LiveKit server.
- If a correction is saved while a `/verify` request is already running, the results of that run can be timestamped after the correction. Corrections are refused while a background job runs, but not during a synchronous `/verify` call.
