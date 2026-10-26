// @vitest-environment jsdom
// Phase 8.3: scheduling a meeting with several invitees, the scheduled
// meeting page, and upcoming/live meetings on the dashboard.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import ScheduleMeeting from "../pages/ScheduleMeeting";
import ScheduledMeeting from "../pages/ScheduledMeeting";
import Dashboard from "../pages/Dashboard";
import { meetingStatus, meetingTitle } from "../format";
import * as api from "../services/api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const schedule = {
  meeting_id: "sched1234567",
  title: "Sprint planning",
  agenda: "Scope",
  status: "scheduled",
  scheduled_start_utc: "2030-01-15T05:00:00+00:00",
  scheduled_start_local: "2030-01-15T10:30",
  timezone: "Asia/Kolkata",
  duration_minutes: 45,
  room_name: "m2m-abc123def0",
  created_at: "2029-12-01T00:00:00",
  cancelled_at: null,
  invitee_count: 3,
};

const link = (id, email) => ({
  invitee_id: id, email, token: `tok${id}`, room_name: schedule.room_name,
  link_path: `/?room=${schedule.room_name}&invite=tok${id}`, expires_at: "2030-01-17T05:45:00+00:00",
  delivery: { channel: "manual", sent: false, message: "No email was sent." },
});

function addInvitees(text) {
  const input = screen.getByLabelText("Invitees");
  fireEvent.change(input, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
}

describe("Schedule meeting form", () => {
  it("collects several invitees, reports duplicates and invalid entries, and lets one be removed", () => {
    render(<ScheduleMeeting hostEmail="host@example.com" onOpenScheduled={() => {}} onCancel={() => {}} />);
    addInvitees("asha@example.com, Ben@example.com; chen@example.com");
    expect(screen.getByText("asha@example.com")).toBeTruthy();
    expect(screen.getByText("ben@example.com")).toBeTruthy();

    addInvitees("BEN@example.com");
    expect(screen.getByRole("alert").textContent).toMatch(/ben@example.com is already in the list/);
    addInvitees("not-an-email");
    expect(screen.getByRole("alert").textContent).toMatch(/not a valid email/);
    addInvitees("host@example.com");
    expect(screen.getByRole("alert").textContent).toMatch(/you do not need to invite yourself/);

    fireEvent.click(screen.getByRole("button", { name: "Remove chen@example.com" }));
    const chips = within(screen.getByRole("list", { name: "Invitees added" })).getAllByRole("listitem");
    expect(chips.map((c) => c.textContent)).toEqual(["asha@example.com", "ben@example.com"]);
  });

  it("asks for a title and time before the review step", () => {
    render(<ScheduleMeeting onOpenScheduled={() => {}} onCancel={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(screen.getByText("Enter a title.")).toBeTruthy();
    expect(screen.getByText("Choose a date and a start time.")).toBeTruthy();
  });

  it("reviews, schedules once with an explicit timezone, and shows one link per invitee without claiming email", async () => {
    const call = vi.spyOn(api, "scheduleMeeting").mockResolvedValue({
      meeting: { ...schedule, invitees: [] },
      links: [link(1, "asha@example.com"), link(2, "ben@example.com"), link(3, "chen@example.com")],
      duplicate_request: false, duplicates_ignored: [], email_sent: false,
    });
    const onOpen = vi.fn();
    render(<ScheduleMeeting onOpenScheduled={onOpen} onCancel={() => {}} />);

    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Sprint planning" } });
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2030-01-15" } });
    fireEvent.change(screen.getByLabelText("Start time"), { target: { value: "10:30" } });
    fireEvent.change(screen.getByLabelText("Timezone"), { target: { value: "Asia/Kolkata" } });
    addInvitees("asha@example.com ben@example.com chen@example.com");
    fireEvent.click(screen.getByRole("button", { name: "Review" }));

    expect(screen.getByRole("heading", { name: "Check the details" })).toBeTruthy();
    expect(screen.getByText("asha@example.com, ben@example.com, chen@example.com")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Schedule meeting" }));

    await screen.findByTestId("no-email-notice");
    expect(call).toHaveBeenCalledTimes(1);
    const body = call.mock.calls[0][0];
    expect(body).toMatchObject({
      title: "Sprint planning", start_local: "2030-01-15T10:30", timezone: "Asia/Kolkata",
      invitees: ["asha@example.com", "ben@example.com", "chen@example.com"],
    });
    expect(body.request_id).toBeTruthy();
    const urls = screen.getAllByRole("textbox").map((i) => i.value);
    expect(urls).toEqual([1, 2, 3].map((n) => `${window.location.origin}/?room=${schedule.room_name}&invite=tok${n}`));
    expect(screen.queryByText(/email (was|has been) sent to/i)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Open meeting page" }));
    expect(onOpen).toHaveBeenCalledWith("sched1234567");
  });

  it("shows the server's refusal and keeps the form", async () => {
    vi.spyOn(api, "scheduleMeeting").mockRejectedValue(new Error("The start time is in the past. Pick a future time."));
    render(<ScheduleMeeting onOpenScheduled={() => {}} onCancel={() => {}} />);
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Retro" } });
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2030-01-15" } });
    fireEvent.change(screen.getByLabelText("Start time"), { target: { value: "10:30" } });
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    fireEvent.click(screen.getByRole("button", { name: "Schedule meeting" }));
    expect(await screen.findByText(/start time is in the past/)).toBeTruthy();
    expect(screen.getByLabelText("Title").value).toBe("Retro");
  });
});

describe("Scheduled meeting page", () => {
  const ownerView = {
    is_owner: true,
    meeting: {
      ...schedule,
      invitees: [
        { invitee_id: 1, email: "asha@example.com", status: "accepted", delivery: "manual",
          accepted_by: { display_name: "Asha", email: "asha.k@example.org", email_matches_invite: false } },
        { invitee_id: 2, email: "ben@example.com", status: "pending", delivery: "manual", accepted_by: null },
        { invitee_id: 3, email: "chen@example.com", status: "revoked", delivery: "manual", accepted_by: null },
      ],
    },
  };

  it("shows each invitee's status and re-issues a link on copy", async () => {
    vi.spyOn(api, "getSchedule").mockResolvedValue(ownerView);
    const copy = vi.spyOn(api, "copyInviteeLink").mockResolvedValue({ ...link(2, "ben@example.com"), previous_link_revoked: true });
    render(<ScheduledMeeting meetingId="sched1234567" onStart={() => {}} />);

    const table = await screen.findByTestId("invitee-table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[1].textContent)).toEqual(["Accepted", "Not joined yet", "Revoked"]);
    expect(screen.getByTestId("email-mismatch")).toBeTruthy();
    expect(within(rows[2]).queryByRole("button", { name: "Copy link" })).toBeNull();
    expect(screen.getByText(/Copy link is a fallback/)).toBeTruthy();

    fireEvent.click(within(rows[1]).getByRole("button", { name: "Copy link" }));
    await waitFor(() => expect(copy).toHaveBeenCalledWith("sched1234567", 2));
    expect(await screen.findByLabelText("Invitation link for ben@example.com")).toBeTruthy();
  });

  it("cancels only after confirmation and starts with the reserved room", async () => {
    vi.spyOn(api, "getSchedule").mockResolvedValue(ownerView);
    const cancel = vi.spyOn(api, "cancelScheduledMeeting").mockResolvedValue({
      meeting: { ...ownerView.meeting, status: "cancelled" },
    });
    const onStart = vi.fn();
    render(<ScheduledMeeting meetingId="sched1234567" onStart={onStart} />);

    fireEvent.click(await screen.findByRole("button", { name: "Start meeting" }));
    expect(onStart).toHaveBeenCalledWith(expect.objectContaining({ room_name: "m2m-abc123def0", title: "Sprint planning" }));

    fireEvent.click(screen.getByRole("button", { name: "Cancel meeting" }));
    const dialog = screen.getByRole("dialog", { name: "Cancel this meeting?" });
    expect(cancel).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel meeting" }));
    await waitFor(() => expect(cancel).toHaveBeenCalledWith("sched1234567"));
    expect((await screen.findByTestId("schedule-status")).textContent).toBe("Cancelled");
    expect(screen.queryByRole("button", { name: "Start meeting" })).toBeNull();
  });

  it("shows members the time and agenda but not the invitee list", async () => {
    vi.spyOn(api, "getSchedule").mockResolvedValue({ is_owner: false, meeting: schedule });
    render(<ScheduledMeeting meetingId="sched1234567" onStart={() => {}} />);
    expect(await screen.findByText("Waiting for the host")).toBeTruthy();
    expect(screen.queryByTestId("invitee-table")).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel meeting" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Start meeting" })).toBeNull();
  });
});

describe("Dashboard meeting states", () => {
  it("separates upcoming, live, awaiting-review and approved meetings", async () => {
    vi.spyOn(api, "listMeetings").mockResolvedValue([
      { meeting_id: "up1", filename: null, status: "scheduled", created_at: "2029-12-01T00:00:00",
        utterance_count: 0, claim_count: 0, schedule: { ...schedule, invitee_count: 3 } },
      { meeting_id: "live1", filename: "live-room.wav", status: "live", live_status: "active",
        created_at: "2029-12-01T00:00:00", utterance_count: 0, claim_count: 0, schedule: null },
      { meeting_id: "rev1", filename: "standup.wav", status: "completed", created_at: "2029-12-01T00:00:00",
        utterance_count: 5, claim_count: 2, schedule: null },
      { meeting_id: "ok1", filename: "retro.wav", status: "approved", review_status: "approved",
        created_at: "2029-12-01T00:00:00", utterance_count: 5, claim_count: 2, schedule: null },
    ]);
    vi.spyOn(api, "getLedger").mockResolvedValue({ commitments: [] });
    const onSchedule = vi.fn();
    render(<Dashboard onOpenMeeting={() => {}} onUpload={() => {}} onStartLive={() => {}} onSchedule={onSchedule} onOpenCommitments={() => {}} />);

    const table = await screen.findByRole("table");
    const badges = within(table).getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[5].textContent);
    expect(badges).toEqual(["Upcoming", "Live", "Needs review", "Approved"]);
    expect(within(table).getByText("Sprint planning")).toBeTruthy();
    expect(within(table).getByText("3 invited")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Upcoming/ }));
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(2);

    fireEvent.click(screen.getAllByRole("button", { name: "Schedule meeting" })[0]);
    expect(onSchedule).toHaveBeenCalled();
  });

  it("derives the status from backend fields only", () => {
    expect(meetingStatus({ status: "scheduled" }).key).toBe("upcoming");
    expect(meetingStatus({ status: "cancelled" }).key).toBe("cancelled");
    expect(meetingStatus({ status: "live", live_status: "active", utterance_count: 0 }).key).toBe("live");
    expect(meetingStatus({ status: "live", live_status: "active", utterance_count: 0, job_state: "running" }).key)
      .toBe("processing");
  });

  it("names a live meeting by the title the host gave, not its room", () => {
    const live = { meeting_id: "1b521fc8b024", filename: "live-livekit-m2m-2473.wav", live_title: "Final Project Review" };
    expect(meetingTitle(live)).toBe("Final Project Review");
    expect(meetingTitle({ ...live, live_title: "  " })).toBe("live-livekit-m2m-2473");
    expect(meetingTitle({ ...live, schedule: { title: "Product Review" } })).toBe("Product Review");
  });
});
