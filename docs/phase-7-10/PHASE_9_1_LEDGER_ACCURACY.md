# Phase 9.1: commitment-ledger accuracy

Work date: 25 Oct 2026. Branch `phase-9.1/ledger-accuracy`, from `main` at `1e79d12`.

## 1. Why proposals were wrong

The reported case: the statement *"I will finalize the backend API deployment and email the access links."* was proposed as **Delayed**.

**Cause 1: "not done" was treated as "delayed".**

- `propose_updates` proposed *delayed* whenever the hypothesis "<task> is done." was **contradicted**.
- A promise of future work, "not started", and "still working on it" all contradict "done", yet none of them says the work is delayed.

**Cause 2: task-specific "delayed" hypotheses misfire.** With the real NLI model (mDeBERTa), measured on 25 Oct:

| Premise | Hypothesis | Entailment |
|---|---|---|
| "I will submit the front end validation report by Friday." (a promise) | "Submit the front end validation report **is delayed**." | **0.76** |
| "The backend API deployment is postponed until next week." (an explicit delay) | "Finalize the backend API deployment and email the access links is delayed." | **0.00** (the premise does not restate the whole compound task) |

**Cause 3: whole windows were scored and cited.**

- Retrieval returns 3-utterance windows. The whole window was scored and cited, so a neighbouring sentence about another task was cited too.
- In the synthetic demo, the old code proposed **Done** for "share the test results" because the window included "I have submitted the front end validation report."

## 2. Fix

**How evidence is chosen**

- Each utterance of the **follow-up meeting** in the retrieved windows is gated on its own: it must share enough of the commitment's content words.
- Only those utterances are scored and cited. Utterances from another meeting are skipped explicitly, and the scorer only ever sees the follow-up meeting's text.

**How each status is decided** (the threshold is still 0.60; nothing was loosened):

| Proposal | Required evidence |
|---|---|
| **Done** | Task-specific completion entailment ("<task> is done." / "…has been completed.") ≥ 0.60, and the same utterance does not say the work is unfinished or postponed |
| **Delayed** | The utterance itself entails "The work has been postponed to a later date." or "The work is late or blocked." at ≥ 0.60, **and** that score beats "The work will be done in the future." by ≥ 0.30. A promise with a date scores about the same on both (0.45 vs 0.47), so it is not a delay |
| **No update** | No such evidence. The reason says which case applies: "not discussed in this meeting", "mentioned without progress information (the promise is repeated)", or "said to be not finished, but no delay or new date is stated". Those utterances are listed as *context*, not as evidence |
| **Needs review** | Completion evidence together with not-finished or delay evidence. Stored as `no_update` with `needs_review: true` and both citations. The schema's status list was left unchanged; the reviewer must choose |

**Citations**

- Utterance IDs restart in every meeting, so every citation the API returns carries `meeting_id`, `utt_id` and `ref` (`<meeting>:<utt>`).
- The source evidence of a commitment names the source meeting.

**Review**

- Schema **v11** adds `commitment_updates.previous_status`.
- A review records the AI proposal, the reviewer's final status, *confirmed* or *corrected*, the previous status, the reviewer, the time, the follow-up meeting and its evidence.
- A proposal never changes a status.

**User interface** (minutes page panel and Commitments page)

- **Assignee:** "Assigned in the transcript: Asmee (identity not linked to an account)". A name is never mapped to an account.
- **Status:** "Current status" is the confirmed status, and the page says who confirmed it, when, in which meeting, and what it was before.
- **AI proposal:** shown as "AI proposal: Done / Delayed / No update / needs review", together with its reason.
- **Reviewer:** shown as "Reviewer confirmed: …" or "Reviewer set: … (changed the AI proposal)", kept separate from the proposal.
- **A pending "No update" while the confirmed status is Done:** the page says "This meeting gave no new status evidence. The confirmed status stays Done."
- **Evidence:** labelled "This meeting · u00003" or "<source meeting> · u00005".

## 3. Results

### Real NLI model: `tests/test_ledger_status_accuracy.py::test_real_model_scenarios` (`-m slow`), passed

| Follow-up statement | Proposal |
|---|---|
| "I have submitted the front end validation report." | done |
| "The front end validation report is postponed until next week." | delayed |
| "I will submit the front end validation report by Friday." | no_update |
| "I have not started the front end validation report yet." | no_update |

### Synthetic demo dataset (`demo/fallback_meetings.json`), real models, isolated store

The data is written for the demo; it is not a real meeting.

**Meeting 1:** after approval, three `open` commitments, each with its source meeting and evidence.

**Meeting 2 proposals, before and after:**

| Commitment | Follow-up statement | Before (`1e79d12`) | After |
|---|---|---|---|
| Submit the front end validation report | "I have submitted the front end validation report." | done, citing 3 utterances (2 about other tasks) | **done**, citing only u00003 |
| Check the mobile layout | "…not done yet, it slipped to Monday." | delayed | **delayed**, citing u00002 |
| Share the test results | "I will share the test results at the next meeting." (repeated promise) | **done**, citing the validation-report sentence | **no update**: "mentioned without progress information" |

- Statuses after the proposals: unchanged (all `open`).
- **Reviews:**
  - validation report confirmed done (open → done);
  - mobile layout confirmed delayed (open → delayed);
  - test results **corrected** from no update to delayed, with a note.
- The history keeps the AI proposal next to each decision.

### Real recordings from Phase 11 (TTS voices), re-classified without writing

| Commitment | Before | After |
|---|---|---|
| User guide | delayed, citing the *backend* sentence | **no update** ("not finished yet, no delay stated") |
| Report | done, 3 utterances | done, 1 utterance |
| Backend API | delayed | delayed |

### Separate finding, not changed in this phase

In the demo's planning meeting, the verifier holds "update the login page … by Friday" with `possible_miscitation`. Its correct citation (u00002) scores 0.42, and its suggested replacement evidence is wrong.

- This also happens with the original dataset.
- The item correctly stays *held for review* and is not turned into a commitment.
- The demo can use it to show a held item. Do not accept the suggestion.

## 4. Transcript correction → minutes

Already in place since Phase 8:

- the ASR text is kept;
- each correction is audited;
- claims verified before a correction are held;
- approval is refused until verification is run again.

**New:** a claim whose text still contains a word the reviewer removed from a cited utterance is held with `claim_wording_from_uncorrected_transcript`.

- **Example:** "Deliver the front end vibration test" after the utterance was corrected to "validation".
- **The reason names the word.** No word list is used and nothing is replaced automatically.
- **Clearing the hold:** the hold lifts once the reviewer edits the item text. The approved minutes, the JSON export, the evidence appendix and the review counts then all use the reviewed text (`tests/test_correction_to_minutes.py`).

## 5. Meeting date

The minutes used to show "Not specified" for every meeting. The date now comes from facts that exist:

| Meeting type | Date shown |
|---|---|
| Scheduled | The scheduled start, in the meeting's timezone |
| Live | The time recording capture started (UTC), or else when the room opened |
| Uploaded | Still "Not specified", because the upload time is not the meeting date |

The date is shown on the minutes page, in the PDF ("Meeting date: … (source)") and in the minutes email.

## 6. Tests (25 Oct 2026)

| Command | Result |
|---|---|
| `pytest tests/test_ledger_status_accuracy.py tests/test_commitment_ledger.py tests/test_ledger_ownership.py` | 22 passed |
| `pytest -m slow tests/test_ledger_status_accuracy.py` (real model) | 1 passed |
| `pytest tests/test_correction_to_minutes.py` | 3 passed |
| `pytest -m "not slow"` | **567 passed**, 11 deselected |
| `npx vitest run` | **107 passed** |
| `npx vite build` | built |

**Changed tests**

- `test_commitment_ledger.py`: the "delayed" case of the keyword-fake test was removed. It passed only through the old "not done → delayed" rule. Delay is now tested with real-model scores and with the real model itself.
- The hypothesis test now checks the "completed" hypothesis instead of the removed "delayed" one.

## 7. Demo steps

1. Load the synthetic demo data with `python scripts\seed_demo_meetings.py --owner-email <your account>`, then restart the frontend.
2. Open **Sprint planning (demo)**:
   1. Show the held "update the login page" item and its reason. Leave it held.
   2. Approve the minutes.
   3. On Commitments, show three **Open** commitments, each with "Assigned in the transcript: … (identity not linked to an account)" and the source meeting's evidence.
3. Open **Sprint follow-up (demo)** → Minutes → Commitment ledger → **Check open commitments**. Show the three proposals, each citing "This meeting · uXXXXX":
   - **Done** for the report;
   - **Delayed** for the mobile layout;
   - **No update** for the test results, with the reason "promise repeated".
4. Point out that the current status is still **Open**: nothing changed on its own.
5. **Review:**
   - Confirm Done.
   - Confirm Delayed.
   - Mark the test results **Delayed**, with a note, as a correction.
6. On Commitments, show for each commitment:
   - the confirmed status, and who set it, when, and what it was before;
   - the AI proposal kept separately.

## Files

**Backend**

- `app/services/ledger/commitment_ledger.py`: utterance-level evidence, the status classification, citations that name their meeting, and the previous status on review.
- `app/core/db.py`: schema v11.
- `app/services/verification/claims_view.py`: the outdated-wording hold.
- `app/services/meeting_dates.py` (new): the meeting date. Used by `app/api/meeting_routes.py` (minutes and PDF) and `app/services/email/minutes.py`.

**Frontend**

- `frontend/src/components/CommitmentPanel.jsx`
- `frontend/src/pages/Commitments.jsx`

**Demo data**

- `demo/fallback_meetings.json`: adds the validation-report commitment and the three follow-up outcomes.

**Tests**

- New: `tests/test_ledger_status_accuracy.py`, `tests/test_correction_to_minutes.py`, `frontend/src/__tests__/ledger_display.test.jsx`.
- Updated: `tests/test_commitment_ledger.py`, `frontend/src/__tests__/ledger_and_delete.test.jsx`.
