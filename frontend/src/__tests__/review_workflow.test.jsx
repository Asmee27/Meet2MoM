// @vitest-environment jsdom
// Phase 3 UI tests: review editing, approval, locking, reopening, audit log, JSON export.
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { ClaimReviewCard, ReviewBadge } from "../components/ClaimVerification";
import VerifyMeeting from "../pages/VerifyMeeting";
import Summary from "../pages/Summary";
import * as api from "../services/api";
import * as contextModule from "../context/MeetingFlowContext";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const utt = (id, speaker, start, text) => ({
  utt_id: id,
  speaker,
  speaker_id: speaker,
  start,
  end: start + 4,
  text,
  asr_conf: 0.9,
  spk_conf: 0.9,
  overlap: false,
});

function makeVerification(label = "verified") {
  return {
    deterministic: { label },
    semantic: {
      label: "supported",
      cited_entailment: 0.9,
      best_support: 0.9,
      contradiction: 0.01,
      best_evidence: [],
    },
    final: { label, reasons: [] },
  };
}

function makeClaim(overrides = {}) {
  return {
    claim_id: "m1_d00001",
    item_id: "m1_d00001",
    claim_type: "decision",
    text: "We will release on Friday",
    state: "accepted",
    evidence_ids: ["u00000"],
    evidence: [utt("u00000", "Alice", 10, "Let us release on Friday.")],
    exportable: true,
    review_status: "original",
    review_text: null,
    review_owner: null,
    review_deadline: null,
    excluded: false,
    verification: makeVerification("verified"),
    suggestion: null,
    contradiction: null,
    correction: null,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// 1. ReviewBadge Unit Tests
// ---------------------------------------------------------------------------

describe("ReviewBadge", () => {
  it("renders nothing for status original", () => {
    const { container } = render(<ReviewBadge reviewStatus="original" />);
    expect(container.firstChild).toBeNull();
  });

  it("renders Human Edited badge", () => {
    render(<ReviewBadge reviewStatus="human_edited" />);
    const badge = screen.getByTestId("review-badge");
    expect(badge.textContent).toContain("Human Edited");
  });

  it("renders Reviewer Approved badge", () => {
    render(<ReviewBadge reviewStatus="reviewer_approved" />);
    const badge = screen.getByTestId("review-badge");
    expect(badge.textContent).toContain("Reviewer Approved");
  });

  it("renders Excluded badge", () => {
    render(<ReviewBadge reviewStatus="excluded" />);
    const badge = screen.getByTestId("review-badge");
    expect(badge.textContent).toContain("Excluded");
  });
});

// ---------------------------------------------------------------------------
// 2. ClaimReviewCard Unit Tests
// ---------------------------------------------------------------------------

describe("ClaimReviewCard — read mode & status display", () => {
  it("shows claim text and status badge in read mode", () => {
    render(
      <ClaimReviewCard
        claim={makeClaim()}
        onSuggestionDecision={vi.fn()}
        busy={false}
      />
    );
    expect(screen.getByText("We will release on Friday")).toBeTruthy();
    expect(screen.getByTestId("claim-status").textContent).toContain("Verified");
  });

  it("shows Human Edited badge and review_text when review_status is human_edited", () => {
    render(
      <ClaimReviewCard
        claim={makeClaim({
          review_status: "human_edited",
          review_text: "Release on Monday",
        })}
        onSuggestionDecision={vi.fn()}
        busy={false}
      />
    );
    expect(screen.getByTestId("review-badge").textContent).toContain("Human Edited");
    expect(screen.getByText("Release on Monday")).toBeTruthy();
  });

  it("does not render edit button if onEdit prop is omitted", () => {
    render(
      <ClaimReviewCard
        claim={makeClaim()}
        onSuggestionDecision={vi.fn()}
        busy={false}
      />
    );
    expect(screen.queryByTestId("open-edit")).toBeNull();
  });

  it("renders edit button when onEdit is provided and draft is open", () => {
    render(
      <ClaimReviewCard
        claim={makeClaim()}
        onSuggestionDecision={vi.fn()}
        busy={false}
        onEdit={vi.fn()}
        isApproved={false}
      />
    );
    expect(screen.getByTestId("open-edit")).toBeTruthy();
  });

  it("hides edit button and shows approved lock note when isApproved is true", () => {
    render(
      <ClaimReviewCard
        claim={makeClaim()}
        onSuggestionDecision={vi.fn()}
        busy={false}
        onEdit={vi.fn()}
        isApproved={true}
      />
    );
    expect(screen.queryByTestId("open-edit")).toBeNull();
    expect(screen.getByTestId("approved-note").textContent).toContain("Approved");
  });
});

describe("ClaimReviewCard — inline editing", () => {
  it("opens inline edit form with prefilled values on Edit click", () => {
    render(
      <ClaimReviewCard
        claim={makeClaim()}
        onSuggestionDecision={vi.fn()}
        busy={false}
        onEdit={vi.fn()}
        isApproved={false}
      />
    );
    fireEvent.click(screen.getByTestId("open-edit"));
    expect(screen.getByTestId("edit-form")).toBeTruthy();
    expect(screen.getByTestId("edit-text").value).toBe("We will release on Friday");
  });

  it("cancels editing and returns to read mode when Cancel is clicked", () => {
    render(
      <ClaimReviewCard
        claim={makeClaim()}
        onSuggestionDecision={vi.fn()}
        busy={false}
        onEdit={vi.fn()}
        isApproved={false}
      />
    );
    fireEvent.click(screen.getByTestId("open-edit"));
    expect(screen.getByTestId("edit-form")).toBeTruthy();
    fireEvent.click(screen.getByTestId("edit-cancel"));
    expect(screen.queryByTestId("edit-form")).toBeNull();
  });

  it("renders owner and deadline fields for action item claims", () => {
    render(
      <ClaimReviewCard
        claim={makeClaim({
          claim_type: "action",
          owner: "Alice",
          deadline_text: "Next Friday",
        })}
        onSuggestionDecision={vi.fn()}
        busy={false}
        onEdit={vi.fn()}
        isApproved={false}
      />
    );
    fireEvent.click(screen.getByTestId("open-edit"));
    expect(screen.getByTestId("edit-owner").value).toBe("Alice");
    expect(screen.getByTestId("edit-deadline").value).toBe("Next Friday");
  });

  it("does not render owner and deadline inputs for decision claims", () => {
    render(
      <ClaimReviewCard
        claim={makeClaim({ claim_type: "decision" })}
        onSuggestionDecision={vi.fn()}
        busy={false}
        onEdit={vi.fn()}
        isApproved={false}
      />
    );
    fireEvent.click(screen.getByTestId("open-edit"));
    expect(screen.queryByTestId("edit-owner")).toBeNull();
    expect(screen.queryByTestId("edit-deadline")).toBeNull();
  });

  it("submits updated values and closes form on successful save", async () => {
    const onEdit = vi.fn().mockResolvedValue(undefined);
    render(
      <ClaimReviewCard
        claim={makeClaim()}
        onSuggestionDecision={vi.fn()}
        busy={false}
        onEdit={onEdit}
        isApproved={false}
      />
    );
    fireEvent.click(screen.getByTestId("open-edit"));

    fireEvent.change(screen.getByTestId("edit-text"), {
      target: { value: "Release on Monday" },
    });
    fireEvent.change(screen.getByTestId("edit-reason"), {
      target: { value: "Team agreed on Monday instead" },
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("edit-save"));
    });

    await waitFor(() => {
      expect(onEdit).toHaveBeenCalledWith(
        "m1_d00001",
        expect.objectContaining({
          text: "Release on Monday",
          reason: "Team agreed on Monday instead",
        }),
        "Team agreed on Monday instead"
      );
    });

    await waitFor(() => {
      expect(screen.queryByTestId("edit-form")).toBeNull();
    });
  });

  it("handles exclude checkbox and passes excluded: true on save", async () => {
    const onEdit = vi.fn().mockResolvedValue(undefined);
    render(
      <ClaimReviewCard
        claim={makeClaim()}
        onSuggestionDecision={vi.fn()}
        busy={false}
        onEdit={onEdit}
        isApproved={false}
      />
    );
    fireEvent.click(screen.getByTestId("open-edit"));
    fireEvent.click(screen.getByTestId("edit-excluded"));

    await act(async () => {
      fireEvent.click(screen.getByTestId("edit-save"));
    });

    await waitFor(() => {
      expect(onEdit).toHaveBeenCalledWith(
        "m1_d00001",
        expect.objectContaining({ excluded: true }),
        expect.any(String)
      );
    });
  });

  it("displays inline error and keeps form open when onEdit rejects", async () => {
    const onEdit = vi.fn().mockRejectedValue(new Error("Cannot edit: draft is locked."));
    render(
      <ClaimReviewCard
        claim={makeClaim()}
        onSuggestionDecision={vi.fn()}
        busy={false}
        onEdit={onEdit}
        isApproved={false}
      />
    );
    fireEvent.click(screen.getByTestId("open-edit"));

    await act(async () => {
      fireEvent.click(screen.getByTestId("edit-save"));
    });

    await waitFor(() => {
      expect(screen.getByTestId("edit-error").textContent).toContain("Cannot edit: draft is locked.");
    });
    expect(screen.getByTestId("edit-form")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// 3. VerifyMeeting Workflow & Integration Tests
// ---------------------------------------------------------------------------

function makeContextMock(overrides = {}) {
  return {
    currentStep: 4,
    goBack: vi.fn(),
    goToExport: vi.fn(),
    backendFileId: "meet_test_123",
    verificationStage: "manual",
    setVerificationStage: vi.fn(),
    verificationProgress: 100,
    automaticCheckComplete: true,
    verificationData: {
      claims: [
        makeClaim({ claim_id: "c1", text: "Ship Phase 3 today", exportable: true }),
      ],
      counts: { verified: 1, review: 0, rejected: 0 },
      verification_run: { status: "completed", seconds_total: 1.2 },
    },
    isVerifying: false,
    verificationError: "",
    reviewSuggestion: vi.fn(),
    suggestionBusy: false,
    suggestionError: "",
    isUploading: false,
    isTranscribing: false,
    isExtracting: false,
    isGeneratingMom: false,
    isDownloadingPdf: false,
    exportStatus: { pdf: false, word: false, email: false },
    // Phase 3
    reviewData: {
      meeting_id: "meet_test_123",
      draft_status: "open",
      items: [
        {
          item_id: "c1",
          original_claim_id: "c1",
          item_type: "decision",
          text: "Ship Phase 3 today",
          review_status: "original",
          is_excluded: false,
        },
      ],
      latest_approved_snapshot: null,
      approved_snapshot: null,
    },
    isLoadingReview: false,
    reviewError: "",
    reviewerName: "",
    setReviewerName: vi.fn(),
    isApproving: false,
    approveError: "",
    setApproveError: vi.fn(),
    isReopening: false,
    reopenError: "",
    setReopenError: vi.fn(),
    auditLog: [],
    isLoadingAudit: false,
    loadReviewData: vi.fn(),
    editReviewItem: vi.fn().mockResolvedValue({}),
    addNewReviewItem: vi.fn().mockResolvedValue({}),
    approveMeeting: vi.fn().mockResolvedValue({ status: "approved" }),
    reopenMeetingReview: vi.fn().mockResolvedValue({ status: "open" }),
    loadAuditLog: vi.fn(),
    downloadApprovedJson: vi.fn(),
    ...overrides,
  };
}

describe("VerifyMeeting Page — Review, Approval & Reopening", () => {
  it("renders reviewer bar and claims in manual review stage", () => {
    const ctx = makeContextMock();
    vi.spyOn(contextModule, "useMeetingFlow").mockReturnValue(ctx);

    render(<VerifyMeeting />);

    expect(screen.getByText("Review claims and their evidence")).toBeTruthy();
    expect(screen.getByTestId("reviewer-bar")).toBeTruthy();
    expect(screen.getByTestId("reviewer-name-input")).toBeTruthy();
    expect(screen.getByTestId("approve-button")).toBeTruthy();
  });

  it("disables approve button when reviewer name is empty", () => {
    const ctx = makeContextMock({ reviewerName: "" });
    vi.spyOn(contextModule, "useMeetingFlow").mockReturnValue(ctx);

    render(<VerifyMeeting />);

    const approveBtn = screen.getByTestId("approve-button");
    expect(approveBtn.disabled).toBe(true);
  });

  it("calls approveMeeting when reviewer name is provided and approve is clicked", async () => {
    const approveMeeting = vi.fn().mockResolvedValue({ status: "approved" });
    const ctx = makeContextMock({
      reviewerName: "Dr. Reviewer",
      approveMeeting,
    });
    vi.spyOn(contextModule, "useMeetingFlow").mockReturnValue(ctx);

    render(<VerifyMeeting />);

    const approveBtn = screen.getByTestId("approve-button");
    expect(approveBtn.disabled).toBe(false);

    await act(async () => {
      fireEvent.click(approveBtn);
    });

    expect(approveMeeting).toHaveBeenCalledWith("Dr. Reviewer", "");
  });

  it("displays approval lock banner and hides reviewer bar when draft is approved", () => {
    const ctx = makeContextMock({
      reviewData: {
        meeting_id: "meet_test_123",
        draft_status: "approved",
        latest_approved_snapshot: {
          version: "v1",
          approved_by: "Alice Reviewer",
          approved_at: "2026-10-17T12:00:00Z",
          notes: "Approved after team sign-off",
        },
        items: [],
      },
    });
    vi.spyOn(contextModule, "useMeetingFlow").mockReturnValue(ctx);

    render(<VerifyMeeting />);

    expect(screen.getByTestId("approval-lock-banner")).toBeTruthy();
    expect(screen.getByTestId("approval-lock-banner").textContent).toContain("v1");
    expect(screen.getByTestId("approval-lock-banner").textContent).toContain("Alice Reviewer");
    expect(screen.queryByTestId("reviewer-bar")).toBeNull();
  });

  it("opens reopen modal and requires reason before confirming reopen", async () => {
    const reopenMeetingReview = vi.fn().mockResolvedValue({ status: "open" });
    const ctx = makeContextMock({
      reviewData: {
        meeting_id: "meet_test_123",
        draft_status: "approved",
        latest_approved_snapshot: { version: "v1", approved_by: "Alice" },
        items: [],
      },
      reopenMeetingReview,
    });
    vi.spyOn(contextModule, "useMeetingFlow").mockReturnValue(ctx);

    render(<VerifyMeeting />);

    fireEvent.click(screen.getByTestId("reopen-trigger"));
    expect(screen.getByTestId("reopen-modal")).toBeTruthy();

    const confirmBtn = screen.getByTestId("reopen-confirm");
    expect(confirmBtn.disabled).toBe(true);

    // Enter reason
    fireEvent.change(screen.getByTestId("reopen-reason"), {
      target: { value: "Need to update budget decision" },
    });
    expect(confirmBtn.disabled).toBe(false);

    // Enter reviewer name
    fireEvent.change(screen.getByTestId("reopen-reviewer"), {
      target: { value: "Bob Lead" },
    });

    await act(async () => {
      fireEvent.click(confirmBtn);
    });

    expect(reopenMeetingReview).toHaveBeenCalledWith("Bob Lead", "Need to update budget decision");
  });

  it("toggles audit history drawer and displays entries", async () => {
    const loadAuditLog = vi.fn();
    const ctx = makeContextMock({
      loadAuditLog,
      auditLog: [
        {
          action: "approve",
          reviewer: "Alice Lead",
          timestamp: "2026-10-17T10:00:00Z",
          reason: "Meeting concluded",
        },
      ],
    });
    vi.spyOn(contextModule, "useMeetingFlow").mockReturnValue(ctx);

    render(<VerifyMeeting />);

    const drawerToggle = screen.getByText(/Audit history/i);
    fireEvent.click(drawerToggle);

    expect(loadAuditLog).toHaveBeenCalledWith("meet_test_123");
    expect(screen.getByTestId("audit-log")).toBeTruthy();
    expect(screen.getByTestId("audit-entry").textContent).toContain("approve");
    expect(screen.getByTestId("audit-entry").textContent).toContain("Alice Lead");
  });

  it("allows adding a new manual review item", async () => {
    const addNewReviewItem = vi.fn().mockResolvedValue({});
    const ctx = makeContextMock({ addNewReviewItem });
    vi.spyOn(contextModule, "useMeetingFlow").mockReturnValue(ctx);

    render(<VerifyMeeting />);

    fireEvent.click(screen.getByTestId("add-item-trigger"));
    expect(screen.getByTestId("add-item-form")).toBeTruthy();

    fireEvent.change(screen.getByTestId("add-item-text"), {
      target: { value: "Follow up with client by Tuesday" },
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("add-item-submit"));
    });

    expect(addNewReviewItem).toHaveBeenCalledWith(
      expect.objectContaining({
        item_type: "decision",
        text: "Follow up with client by Tuesday",
      })
    );
  });
});

// ---------------------------------------------------------------------------
// 4. Summary Page & JSON Export Tests
// ---------------------------------------------------------------------------

describe("Summary Page — Phase 3 exports", () => {
  it("shows approved snapshot metadata and Download JSON button when approved", () => {
    const downloadApprovedJson = vi.fn();
    const ctx = makeContextMock({
      currentStep: 5,
      momData: {
        meeting_title: "Sprint Review",
        decisions: ["Decision 1"],
        action_items: [],
      },
      reviewData: {
        meeting_id: "meet_test_123",
        draft_status: "approved",
        latest_approved_snapshot: {
          version: "v1",
          approved_by: "Charlie Reviewer",
          approved_at: "2026-10-17T14:30:00Z",
          notes: "Approved snapshot",
        },
      },
      downloadApprovedJson,
    });
    vi.spyOn(contextModule, "useMeetingFlow").mockReturnValue(ctx);

    render(<Summary />);

    expect(screen.getByTestId("snapshot-strip")).toBeTruthy();
    expect(screen.getByTestId("snapshot-strip").textContent).toContain("v1");
    expect(screen.getByTestId("snapshot-strip").textContent).toContain("Charlie Reviewer");

    const jsonBtn = screen.getByTestId("download-json-button");
    expect(jsonBtn).toBeTruthy();

    fireEvent.click(jsonBtn);
    expect(downloadApprovedJson).toHaveBeenCalled();
  });

  it("shows unapproved warning and does not show Download JSON button when draft is not approved", () => {
    const ctx = makeContextMock({
      currentStep: 5,
      momData: {
        meeting_title: "Sprint Review",
        decisions: ["Decision 1"],
        action_items: [],
      },
      reviewData: {
        meeting_id: "meet_test_123",
        draft_status: "open",
        latest_approved_snapshot: null,
      },
    });
    vi.spyOn(contextModule, "useMeetingFlow").mockReturnValue(ctx);

    render(<Summary />);

    expect(screen.getByTestId("unapproved-warning")).toBeTruthy();
    expect(screen.queryByTestId("download-json-button")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 5. API Service Layer Export Verification
// ---------------------------------------------------------------------------

describe("API Service Layer — Phase 3 exports", () => {
  it("exports all required Phase 3 review API functions", () => {
    expect(typeof api.getReview).toBe("function");
    expect(typeof api.updateReviewItem).toBe("function");
    expect(typeof api.addReviewItem).toBe("function");
    expect(typeof api.approveReview).toBe("function");
    expect(typeof api.reopenReview).toBe("function");
    expect(typeof api.getAuditLog).toBe("function");
    expect(typeof api.exportApprovedJson).toBe("function");
    expect(typeof api.getAudioUrl).toBe("function");
  });
});
