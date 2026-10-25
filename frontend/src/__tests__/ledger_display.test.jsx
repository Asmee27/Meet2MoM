// @vitest-environment jsdom
// Phase 9.1: the ledger keeps five things apart - the task's assignee as
// stated in the transcript (never linked to an account by name), the current
// confirmed status, the AI proposal, the reviewer's decision, and citations
// that name their meeting as well as the utterance.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import CommitmentPanel from "../components/CommitmentPanel";
import Commitments from "../pages/Commitments";
import * as api from "../services/api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const followUp = (extra) => ({
  commitment_id: "c_1", meeting_id: "m2meeting", text: "Submit the front end validation report",
  assignee: "Asmee", assignee_user_id: null, source_meeting_id: "m1meeting00",
  source_evidence: [{ meeting_id: "m1meeting00", utt_id: "u00000", ref: "m1meeting00:u00000" }],
  commitment_status: "open", proposed_status: "done", review_status: "proposed", needs_review: false,
  reason: "A statement in this meeting says the work is done.",
  evidence: [{ meeting_id: "m2meeting", utt_id: "u00001", ref: "m2meeting:u00001", speaker: "Asmee", start: 7.8,
    text: "I have submitted the front-end validation report." }],
  context: [], ...extra,
});

describe("Follow-up proposals on a meeting", () => {
  it("labels the citation with its meeting, the assignee as unlinked, and keeps the proposal apart", async () => {
    vi.spyOn(api, "getMeetingCommitments").mockResolvedValue({ created_here: [], follow_ups: [followUp()] });
    render(<CommitmentPanel meetingId="m2meeting" reviewerName="Rita" />);
    const item = await screen.findByTestId("follow-up-c_1");
    expect(within(item).getByText(/Assigned in the transcript: Asmee/)).toBeTruthy();
    expect(within(item).getByText(/identity not linked to an account/)).toBeTruthy();
    expect(within(item).getByTestId("current-status").textContent).toMatch(/Current status:\s*Open/);
    expect(within(item).getByText("AI proposal: Done")).toBeTruthy();
    expect(within(item).getByText("Awaiting reviewer decision")).toBeTruthy();
    const cite = item.querySelector('[data-ref="m2meeting:u00001"]');
    expect(cite.textContent).toMatch(/This meeting · u00001 Asmee \(0:08\): “I have submitted/);
    expect(within(item).getByRole("button", { name: "Confirm: Done" })).toBeTruthy();
  });

  it("shows conflicting evidence as needing review, with no proposal to confirm", async () => {
    vi.spyOn(api, "getMeetingCommitments").mockResolvedValue({
      created_here: [],
      follow_ups: [followUp({ proposed_status: "no_update", needs_review: true,
        reason: "Conflicting evidence: one statement says the work is done, another that it is not finished or delayed." })],
    });
    render(<CommitmentPanel meetingId="m2meeting" reviewerName="Rita" />);
    const item = await screen.findByTestId("follow-up-c_1");
    expect(within(item).getByText("AI proposal: needs review")).toBeTruthy();
    expect(within(item).getByTestId("proposal-reason").textContent).toMatch(/Conflicting evidence/);
    expect(within(item).queryByRole("button", { name: /^Confirm:/ })).toBeNull();
    expect(within(item).getByRole("button", { name: "Mark Done" })).toBeTruthy();
  });

  it("says the confirmed status stays when a pending proposal finds no new change", async () => {
    vi.spyOn(api, "getMeetingCommitments").mockResolvedValue({
      created_here: [],
      follow_ups: [followUp({ commitment_status: "done", proposed_status: "no_update", evidence: [],
        reason: "This commitment is not discussed in this meeting." })],
    });
    render(<CommitmentPanel meetingId="m2meeting" reviewerName="Rita" />);
    const item = await screen.findByTestId("follow-up-c_1");
    expect(within(item).getByTestId("current-status").textContent).toMatch(/Done/);
    expect(within(item).getByText("AI proposal: No update")).toBeTruthy();
    expect(within(item).getByTestId("status-unchanged").textContent).toMatch(/confirmed status stays Done/);
  });

  it("distinguishes a reviewer correction from the AI proposal", async () => {
    vi.spyOn(api, "getMeetingCommitments").mockResolvedValue({
      created_here: [],
      follow_ups: [followUp({ review_status: "corrected", final_status: "delayed", reviewer: "Rita", commitment_status: "delayed" })],
    });
    render(<CommitmentPanel meetingId="m2meeting" reviewerName="Rita" />);
    const item = await screen.findByTestId("follow-up-c_1");
    expect(within(item).getByText("AI proposal: Done")).toBeTruthy();
    expect(within(item).getByText(/Reviewer set: Delayed · Rita \(changed the AI proposal\)/)).toBeTruthy();
  });
});

describe("Ledger page", () => {
  it("shows the confirmed status with who set it and the latest AI proposal separately", async () => {
    vi.spyOn(api, "listMeetings").mockResolvedValue([
      { meeting_id: "m1meeting00", filename: "planning.wav" },
      { meeting_id: "m2meeting", filename: "follow-up.wav" },
      { meeting_id: "m3meeting", filename: "second-follow-up.wav" },
    ]);
    vi.spyOn(api, "getLedger").mockResolvedValue({
      commitments: [{
        commitment_id: "c_1", text: "Submit the front end validation report", assignee: "Asmee", assignee_user_id: null,
        deadline: "2026-10-30", evidence_ids: ["u00000"], status: "done", source_meeting_id: "m1meeting00",
        created_at: "2030-01-01T00:00:00",
        history: [
          { meeting_id: "m2meeting", proposed_status: "done", review_status: "confirmed", final_status: "done",
            previous_status: "open", reviewer: "Rita", reviewed_at: "2030-01-02T10:00:00", evidence_ids: ["u00001"],
            scores: { reason: "A statement in this meeting says the work is done." } },
          { meeting_id: "m3meeting", proposed_status: "no_update", review_status: "proposed", evidence_ids: [],
            scores: { reason: "This commitment is not discussed in this meeting.", needs_review: false } },
        ],
      }],
    });
    render(<Commitments onOpenMeeting={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: /^All/ }));         // done is not "outstanding"
    await screen.findByText("Submit the front end validation report");
    const confirmed = await screen.findByTestId("confirmed-by");
    expect(confirmed.textContent).toMatch(/Confirmed by Rita/);
    expect(confirmed.textContent).toMatch(/in follow-up · was Open/);
    expect(screen.getByText("AI proposal: No update")).toBeTruthy();
    expect(screen.getByText(/Awaiting reviewer decision · the confirmed status stays Done/)).toBeTruthy();
    expect(screen.getByTitle("Cited utterances of the source meeting").textContent).toMatch(/planning · u00000/);
    expect(screen.getByText(/identity not linked to an account/)).toBeTruthy();
  });
});
