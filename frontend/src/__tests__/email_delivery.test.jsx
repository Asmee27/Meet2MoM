// @vitest-environment jsdom
// Phase 8.4: invitation and approved-minutes email in the UI. Statuses are
// shown in the provider's own terms: only "Delivered" is green, and
// development mode never claims anything was sent.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { EmailStatusBadge } from "../components/EmailStatus";
import MinutesEmailPanel from "../components/MinutesEmailPanel";
import ScheduleMeeting from "../pages/ScheduleMeeting";
import ScheduledMeeting from "../pages/ScheduledMeeting";
import * as api from "../services/api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const LIVE = { provider: "brevo", live: true, configured: true, message: "Emails are sent through Brevo." };
const DEV = { provider: "console", live: false, configured: true, message: "Development mode" };

const delivery = (status, extra = {}) => ({
  outbox_id: 7, kind: "invitation", recipient_email: "ben@example.com", status, attempts: 1, last_error: null,
  can_retry: status === "failed", created_at: "2030-01-01T00:00:00", events: [], ...extra,
});

describe("Email status labels", () => {
  it("separates sent-to-provider from delivered", () => {
    const { rerender } = render(<EmailStatusBadge delivery={delivery("provider_accepted")} />);
    let badge = screen.getByTestId("email-status");
    expect(badge.textContent).toBe("Sent to provider");
    expect(badge.className).not.toMatch(/success/);
    expect(screen.getByText(/Delivery to the recipient is not confirmed yet/)).toBeTruthy();

    rerender(<EmailStatusBadge delivery={delivery("delivered")} />);
    badge = screen.getByTestId("email-status");
    expect(badge.textContent).toBe("Delivered");
    expect(badge.className).toMatch(/success/);

    rerender(<EmailStatusBadge delivery={delivery("not_sent_dev")} />);
    expect(screen.getByTestId("email-status").textContent).toBe("Not sent (development mode)");

    rerender(<EmailStatusBadge delivery={delivery("failed", { last_error: "Brevo rate limit reached (HTTP 429)" })} />);
    expect(screen.getByText(/HTTP 429/)).toBeTruthy();
  });
});

describe("Invitation emails on the scheduled meeting page", () => {
  const view = {
    is_owner: true,
    meeting: {
      meeting_id: "sched1", title: "Release", agenda: null, status: "scheduled",
      scheduled_start_utc: "2030-01-15T05:00:00+00:00", timezone: "Asia/Kolkata", duration_minutes: 30,
      room_name: "m2m-abc", invitee_count: 3, created_at: "2029-12-01", cancelled_at: null,
      invitees: [
        { invitee_id: 1, email: "asha@example.com", status: "pending", delivery: "manual", accepted_by: null,
          approval_request: null, email_delivery: delivery("delivered", { recipient_email: "asha@example.com" }) },
        { invitee_id: 2, email: "ben@example.com", status: "pending", delivery: "manual", accepted_by: null,
          approval_request: null, email_delivery: delivery("failed", { last_error: "Brevo error (HTTP 503)" }) },
        { invitee_id: 3, email: "chen@example.com", status: "pending", delivery: "manual", accepted_by: null,
          approval_request: { display_name: "Mallory", email: "mallory@example.org", requested_at: "2030-01-01" },
          email_delivery: delivery("provider_accepted", { recipient_email: "chen@example.com" }) },
      ],
    },
  };

  it("shows one email status per invitee, retries failures and asks before admitting another account", async () => {
    vi.spyOn(api, "getEmailStatus").mockResolvedValue(LIVE);
    vi.spyOn(api, "getSchedule").mockResolvedValue(view);
    const send = vi.spyOn(api, "sendInvitationEmail").mockResolvedValue({ email_delivery: delivery("queued") });
    const approve = vi.spyOn(api, "approveInviteeAccount").mockResolvedValue(view);
    render(<ScheduledMeeting meetingId="sched1" onStart={() => {}} />);

    const table = await screen.findByTestId("invitee-table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getByTestId("email-status").textContent))
      .toEqual(["Delivered", "Failed", "Sent to provider"]);

    fireEvent.click(await within(rows[1]).findByRole("button", { name: "Retry email" }));
    await waitFor(() => expect(send).toHaveBeenCalledWith("sched1", 2));

    expect(within(rows[2]).getByTestId("approval-request").textContent).toMatch(/Mallory/);
    fireEvent.click(within(rows[2]).getByRole("button", { name: "Admit" }));
    await waitFor(() => expect(approve).toHaveBeenCalledWith("sched1", 3, true));
    expect(within(rows[0]).getByRole("button", { name: "Copy link" })).toBeTruthy();
  });

  it("says plainly when email is in development mode", async () => {
    vi.spyOn(api, "getEmailStatus").mockResolvedValue(DEV);
    vi.spyOn(api, "getSchedule").mockResolvedValue(view);
    render(<ScheduledMeeting meetingId="sched1" onStart={() => {}} />);
    expect(await screen.findByTestId("email-dev-mode")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry email" })).toBeNull();
  });
});

describe("Schedule result", () => {
  async function scheduleWith(email) {
    vi.spyOn(api, "scheduleMeeting").mockResolvedValue({
      meeting: { meeting_id: "m1", title: "Release", status: "scheduled", timezone: "UTC",
        scheduled_start_utc: "2030-01-15T05:00:00+00:00", invitees: [] },
      links: [{ invitee_id: 1, email: "asha@example.com", token: "t", link_path: "/?room=r&invite=t",
        expires_at: "2030-01-17T00:00:00+00:00" }],
      duplicate_request: false, duplicates_ignored: [], email_sent: false, email,
    });
    render(<ScheduleMeeting onOpenScheduled={() => {}} onCancel={() => {}} />);
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Release" } });
    fireEvent.change(screen.getByLabelText("Date"), { target: { value: "2030-01-15" } });
    fireEvent.change(screen.getByLabelText("Start time"), { target: { value: "10:30" } });
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    fireEvent.click(screen.getByRole("button", { name: "Schedule meeting" }));
  }

  it("reports queued emails without claiming delivery", async () => {
    await scheduleWith({ ...LIVE, queued: 1 });
    const notice = await screen.findByTestId("email-queued-notice");
    expect(notice.textContent).toMatch(/queued for 1 people/);
    expect(notice.textContent).toMatch(/not the same as delivery/);
    expect(screen.queryByText(/email (was|has been) (sent|delivered)/i)).toBeNull();
  });

  it("never claims sending in development mode", async () => {
    await scheduleWith({ ...DEV, queued: 0 });
    const notice = await screen.findByTestId("no-email-notice");
    expect(notice.textContent).toMatch(/development mode, so nothing was sent/);
  });
});

describe("Send approved minutes", () => {
  const options = {
    approved: true, version: 2, approved_at: "2030-01-16T10:00:00", approved_by: "Samarth <s@example.com>",
    can_send: true, provider: LIVE,
    recipients: [
      { key: "user:u1", kind: "owner", name: "Samarth", email: "s@example.com", eligible: true, reason: null },
      { key: "user:u2", kind: "member", name: "Asmee", email: "asmee@example.com", eligible: true, reason: null },
      { key: "invitee:4", kind: "invitee", name: null, email: "ravi@example.com", eligible: true,
        reason: "Invited, not joined yet." },
      { key: "invitee:5", kind: "invitee", name: null, email: "gone@example.com", eligible: false,
        reason: "Invitation revoked." },
    ],
    history: [
      { ...delivery("failed", { last_error: "Brevo error (HTTP 503)" }), outbox_id: 9, kind: "minutes",
        recipient_email: "priya@example.com", minutes_version: 2 },
    ],
  };

  it("confirms the version and recipients, keeps the PDF off unless confirmed, and reports the result", async () => {
    vi.spyOn(api, "getMinutesEmail").mockResolvedValue(options);
    const send = vi.spyOn(api, "sendMinutesEmail").mockResolvedValue({
      version: 2, queued: [delivery("queued")], already: [delivery("provider_accepted")], provider: LIVE,
      history: options.history,
    });
    render(<MinutesEmailPanel meetingId="m1" shownVersion={2} />);

    fireEvent.click(await screen.findByRole("button", { name: "Send approved minutes" }));
    const dialog = screen.getByTestId("minutes-email-dialog");
    const boxes = within(dialog).getAllByRole("checkbox");
    expect(boxes.map((b) => b.checked)).toEqual([false, true, true, false, false]);   // host unticked, PDF off
    expect(boxes[3].disabled).toBe(true);                                          // revoked invitee
    fireEvent.click(boxes[4]);                                                     // attach PDF
    fireEvent.click(within(dialog).getByRole("button", { name: /Review 2 recipients/ }));

    const confirm = screen.getByTestId("minutes-email-confirm");
    expect(confirm.textContent).toMatch(/version 2, with the PDF attached/);
    expect(within(confirm).getByRole("list", { name: "Selected recipients" }).textContent)
      .toMatch(/Asmee \(asmee@example.com\).*ravi@example.com/);
    const sendButton = within(confirm).getByRole("button", { name: "Send to 2" });
    expect(sendButton.disabled).toBe(true);                                        // PDF needs confirmation
    fireEvent.click(within(confirm).getByRole("checkbox"));
    fireEvent.click(sendButton);

    await waitFor(() => expect(send).toHaveBeenCalledWith("m1", {
      recipients: ["user:u2", "invitee:4"], version: 2, attachPdf: true, confirmAttachment: true,
    }));
    const summary = await screen.findByTestId("minutes-email-summary");
    expect(summary.textContent).toMatch(/1 queued/);
    expect(summary.textContent).toMatch(/1 already sent or in progress for version 2, so not sent again/);
    expect(summary.textContent).toMatch(/not yet delivery/);
  });

  it("retries a failed delivery from the history", async () => {
    vi.spyOn(api, "getMinutesEmail").mockResolvedValue(options);
    const retry = vi.spyOn(api, "retryEmail").mockResolvedValue({ email: delivery("queued") });
    render(<MinutesEmailPanel meetingId="m1" shownVersion={2} />);
    const history = await screen.findByTestId("minutes-email-history");
    expect(within(history).getByTestId("email-status").textContent).toBe("Failed");
    fireEvent.click(within(history).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(retry).toHaveBeenCalledWith("m1", 9));
  });

  it("is hidden from people who may not send, and for unapproved minutes", async () => {
    vi.spyOn(api, "getMinutesEmail").mockResolvedValueOnce({ ...options, can_send: false, recipients: [] });
    const { container, unmount } = render(<MinutesEmailPanel meetingId="m1" shownVersion={2} />);
    await waitFor(() => expect(api.getMinutesEmail).toHaveBeenCalled());
    expect(container.textContent).toBe("");
    unmount();

    vi.spyOn(api, "getMinutesEmail").mockResolvedValueOnce({ approved: false, can_send: false, recipients: [], history: [] });
    const second = render(<MinutesEmailPanel meetingId="m1" />);
    await waitFor(() => expect(api.getMinutesEmail).toHaveBeenCalledTimes(2));
    expect(second.container.textContent).toBe("");
  });

  it("warns that development mode sends nothing", async () => {
    vi.spyOn(api, "getMinutesEmail").mockResolvedValue({ ...options, provider: DEV, history: [] });
    render(<MinutesEmailPanel meetingId="m1" shownVersion={2} />);
    expect(await screen.findByTestId("email-dev-mode")).toBeTruthy();
  });
});
