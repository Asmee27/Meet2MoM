// @vitest-environment jsdom
// Phase 9/10 UI tests: the commitment panel only proposes until a reviewer
// decides, and a meeting is deleted only after confirmation.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import CommitmentPanel from "../components/CommitmentPanel";
import Dashboard from "../pages/Dashboard";
import * as api from "../services/api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const proposal = {
  commitment_id: "c_1",
  text: "Set up the database cluster",
  proposed_status: "done",
  review_status: "proposed",
  final_status: null,
  evidence: [{ utt_id: "u00003", speaker: "Bob", start: 65, text: "The cluster is done now." }],
};

describe("CommitmentPanel", () => {
  it("shows a cited proposal and records the reviewer's correction", async () => {
    vi.spyOn(api, "getMeetingCommitments").mockResolvedValue({ created_here: [], follow_ups: [] });
    vi.spyOn(api, "proposeCommitmentUpdates").mockResolvedValue({ follow_ups: [proposal] });
    const review = vi.spyOn(api, "reviewCommitmentUpdate").mockResolvedValue({
      follow_ups: [{ ...proposal, review_status: "corrected", final_status: "delayed", reviewer: "Rita" }],
    });

    render(<CommitmentPanel meetingId="m2" reviewerName="Rita" />);
    fireEvent.click(await screen.findByRole("button", { name: "Check open commitments" }));

    expect(await screen.findByText(/The cluster is done now/)).toBeTruthy();
    expect(screen.getByText("u00003")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Note for Set up the database cluster"), {
      target: { value: "Only partly done" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Mark Delayed" }));

    await waitFor(() =>
      expect(review).toHaveBeenCalledWith("m2", "c_1", "delayed", "Only partly done", "Rita"));
    expect(await screen.findByText(/Corrected to Delayed by Rita/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Mark Delayed" })).toBeNull();
  });
});

// Phase 8: meeting history lives on the dashboard; deletion is confirmed in
// an accessible dialog instead of window.confirm.
describe("Previous meetings", () => {
  const meetings = [{
    meeting_id: "abcdef1234", filename: "standup.wav", created_at: "2026-10-22T09:00:00",
    status: "completed", utterance_count: 12, claim_count: 3,
  }];

  it("deletes a meeting only after confirmation", async () => {
    vi.spyOn(api, "listMeetings").mockResolvedValue(meetings);
    vi.spyOn(api, "getLedger").mockResolvedValue({ commitments: [] });
    const del = vi.spyOn(api, "deleteMeeting").mockResolvedValue({ deleted: true });

    render(<Dashboard onOpenMeeting={() => {}} onUpload={() => {}} onStartLive={() => {}} onOpenCommitments={() => {}} />);
    const button = await screen.findByRole("button", { name: "Delete meeting abcdef1234" });

    // Cancelling the dialog deletes nothing.
    fireEvent.click(button);
    const dialog = screen.getByRole("dialog", { name: "Delete this meeting?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(del).not.toHaveBeenCalled();
    expect(screen.getByText("standup")).toBeTruthy();

    fireEvent.click(button);
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete meeting" }));
    await waitFor(() => expect(del).toHaveBeenCalledWith("abcdef1234"));
    await waitFor(() => expect(screen.queryByText("standup")).toBeNull());
  });

  it("keeps the meeting and shows the server's reason when deletion is refused", async () => {
    vi.spyOn(api, "listMeetings").mockResolvedValue(meetings);
    vi.spyOn(api, "getLedger").mockResolvedValue({ commitments: [] });
    vi.spyOn(api, "deleteMeeting").mockRejectedValue(new Error("The meeting is being processed. Wait until processing finishes."));

    render(<Dashboard onOpenMeeting={() => {}} onUpload={() => {}} onStartLive={() => {}} onOpenCommitments={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete meeting abcdef1234" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete meeting" }));

    expect(await screen.findByText(/being processed/)).toBeTruthy();
    expect(screen.getByText("standup")).toBeTruthy();
  });
});
