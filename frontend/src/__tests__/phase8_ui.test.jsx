// @vitest-environment jsdom
// Phase 8 UI tests: transcript correction, stale verification after a
// correction, evidence → transcript focus, honest processing stages, routing
// with browser history, the mobile navigation drawer and status mapping.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import App from "../App";
import ProcessingStages from "../components/ProcessingStages";
import TranscriptPanel from "../components/TranscriptPanel";
import VerifyMeeting from "../pages/VerifyMeeting";
import * as api from "../services/api";
import * as contextModule from "../context/MeetingFlowContext";
import { meetingStatus } from "../format";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

beforeEach(() => {
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

const UTTERANCES = [
  {
    utt_id: "u00001", speaker_id: "SPK_1", speaker_name: "Daniel", start: 17.3, end: 20.9,
    text: "I will update the login page.", asr_text: "I will update the login page.",
    corrected: false, correction: null, asr_conf: 0.9,
  },
  {
    utt_id: "u00002", speaker_id: "SPK_1", speaker_name: "Daniel", start: 21.2, end: 23.4,
    text: "I will submit the changes by FFmpeg.", asr_text: "I will submit the changes by FFmpeg.",
    corrected: false, correction: null, asr_conf: 0.42,
  },
];

function flowContext(overrides = {}) {
  return {
    currentStep: 4,
    goBack: vi.fn(),
    goToExport: vi.fn(),
    backendFileId: "m_ffmpeg",
    verificationStage: "manual",
    setVerificationStage: vi.fn(),
    automaticCheckComplete: true,
    verificationData: { claims: [], counts: {}, verification_run: { status: "completed" } },
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
    reviewData: { meeting_id: "m_ffmpeg", draft_status: "draft", items: [] },
    isLoadingReview: false,
    reviewError: "",
    reviewerName: "Rita",
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
    editReviewItem: vi.fn(),
    addNewReviewItem: vi.fn(),
    approveMeeting: vi.fn().mockResolvedValue({}),
    reopenMeetingReview: vi.fn(),
    loadAuditLog: vi.fn(),
    reverifyMeeting: vi.fn().mockResolvedValue({}),
    correctUtterance: vi.fn(),
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Transcript correction
// ---------------------------------------------------------------------------

describe("TranscriptPanel correction", () => {
  it("never changes words on its own and saves the reviewer's text with a reason", async () => {
    vi.spyOn(api, "getTranscript").mockResolvedValue(UTTERANCES);
    const corrected = {
      ...UTTERANCES[1],
      text: "I will submit the changes by Friday.",
      corrected: true,
      correction: { correction_id: 1, reviewer: "Rita", reason: "Checked the audio", created_at: "2026-10-24T09:00:00" },
    };
    const correctUtterance = vi.fn().mockResolvedValue({ utterance: corrected });
    vi.spyOn(contextModule, "useMeetingFlow").mockReturnValue(flowContext({ correctUtterance }));

    render(<TranscriptPanel meetingId="m_ffmpeg" />);

    // The ASR text is shown as transcribed, flagged by the verifier's threshold.
    const row = await screen.findByTestId("utterance-u00002");
    expect(row.textContent).toContain("by FFmpeg");
    expect(within(row).getByText("Low ASR confidence")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Correct text of u00002" }));
    const textarea = screen.getByLabelText("Corrected text");
    expect(textarea.value).toBe("I will submit the changes by FFmpeg.");   // prefilled, not replaced
    expect(screen.getByRole("button", { name: "Save correction" }).disabled).toBe(true);

    fireEvent.change(textarea, { target: { value: "I will submit the changes by Friday." } });
    fireEvent.change(screen.getByLabelText(/Reason/), { target: { value: "Checked the audio" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    });

    expect(correctUtterance).toHaveBeenCalledWith("u00002", "I will submit the changes by Friday.", "Checked the audio");
    const updated = screen.getByTestId("utterance-u00002");
    expect(within(updated).getByText("Corrected by reviewer")).toBeTruthy();
    expect(updated.textContent).toContain("by Friday");

    // The original ASR output stays available.
    fireEvent.click(within(updated).getByRole("button", { name: /Original speech-to-text/ }));
    expect(within(updated).getByText("I will submit the changes by FFmpeg.")).toBeTruthy();
    expect(updated.textContent).toContain("Checked the audio");
  });

  it("keeps the form open and shows the server's refusal", async () => {
    vi.spyOn(api, "getTranscript").mockResolvedValue(UTTERANCES);
    const correctUtterance = vi.fn().mockRejectedValue(new Error("Cannot correct the transcript of approved minutes. Please reopen review first."));
    vi.spyOn(contextModule, "useMeetingFlow").mockReturnValue(flowContext({ correctUtterance }));

    render(<TranscriptPanel meetingId="m_ffmpeg" />);
    fireEvent.click(await screen.findByRole("button", { name: "Correct text of u00002" }));
    fireEvent.change(screen.getByLabelText("Corrected text"), { target: { value: "Something else." } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    });

    expect(screen.getByRole("alert").textContent).toContain("reopen review");
    expect(screen.getByLabelText("Corrected text")).toBeTruthy();
  });

  it("offers no correction when the minutes are locked", async () => {
    vi.spyOn(api, "getTranscript").mockResolvedValue(UTTERANCES);
    vi.spyOn(contextModule, "useMeetingFlow").mockReturnValue(flowContext());
    render(<TranscriptPanel meetingId="m_ffmpeg" editable={false} lockedReason="Approved minutes are locked." />);
    await screen.findByTestId("utterance-u00002");
    expect(screen.queryByRole("button", { name: /Correct text of/ })).toBeNull();
    expect(screen.getByText(/Approved minutes are locked/)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// Review workspace after a correction
// ---------------------------------------------------------------------------

const staleClaim = {
  claim_id: "m_ffmpeg_a00000",
  item_id: "m_ffmpeg_a00000",
  claim_type: "action",
  text: "Submit the changes by FFmpeg",
  owner: "Daniel",
  evidence_ids: ["u00002"],
  evidence: [{ utt_id: "u00002", speaker: "Daniel", start: 21.2, end: 23.4, text: "I will submit the changes by Friday." }],
  exportable: false,
  verification_stale: true,
  verification: {
    deterministic: { label: "verified" },
    semantic: { label: "supported", best_evidence: [] },
    final: {
      label: "review",
      reasons: [{
        code: "transcript_corrected_after_verification", severity: "review", utt_ids: ["u00002"],
        message: "The transcript was corrected after this claim was verified. Run verification again before approving.",
      }],
    },
  },
  suggestion: null,
  contradiction: null,
  correction: null,
};

describe("VerifyMeeting after a transcript correction", () => {
  it("holds stale claims, blocks approval and offers re-verification", async () => {
    vi.spyOn(api, "getTranscript").mockResolvedValue(UTTERANCES);
    const reverifyMeeting = vi.fn().mockResolvedValue({});
    vi.spyOn(contextModule, "useMeetingFlow").mockReturnValue(flowContext({
      reverifyMeeting,
      verificationData: {
        claims: [staleClaim],
        counts: { review: 1 },
        verification_run: { status: "completed" },
        transcript: { corrected_utterances: ["u00002"], stale_claims: 1 },
      },
    }));

    render(<VerifyMeeting />);

    const banner = screen.getByTestId("stale-verification");
    expect(banner.textContent).toContain("1 claim held");
    expect(screen.getByTestId("approve-button").disabled).toBe(true);
    expect(screen.getByTestId("approve-blocked").textContent).toContain("Run verification again");

    // The claim is not presented as verified, and says why.
    const card = screen.getByTestId("claim-m_ffmpeg_a00000");
    expect(within(card).getByTestId("claim-status").textContent).toBe("Needs review");
    expect(within(card).getByText("Why it is held for review")).toBeTruthy();
    expect(within(card).getByText(/corrected after this claim was verified/)).toBeTruthy();

    await act(async () => {
      fireEvent.click(within(banner).getByRole("button", { name: "Run verification again" }));
    });
    expect(reverifyMeeting).toHaveBeenCalledTimes(1);
  });

  it("jumps from a cited utterance to the transcript line and highlights it", async () => {
    vi.spyOn(api, "getTranscript").mockResolvedValue(UTTERANCES);
    vi.spyOn(contextModule, "useMeetingFlow").mockReturnValue(flowContext({
      verificationData: { claims: [staleClaim], counts: {}, verification_run: { status: "completed" } },
    }));

    render(<VerifyMeeting />);
    const row = await screen.findByTestId("utterance-u00002");
    expect(row.className).not.toContain("is-focused");

    fireEvent.click(screen.getByRole("button", { name: "Show u00002 in transcript" }));
    await waitFor(() => expect(screen.getByTestId("utterance-u00002").className).toContain("is-focused"));
  });
});

// ---------------------------------------------------------------------------
// Processing stages
// ---------------------------------------------------------------------------

const timing = (stage, status, duration, extra = {}) => ({
  stage, parent_stage: null, status, started_at: "2026-10-24T09:00:00+00:00",
  finished_at: status === "running" ? null : "2026-10-24T09:01:00+00:00", duration_seconds: duration, detail: {}, ...extra,
});

describe("ProcessingStages", () => {
  it("shows real stage states, fallback and on-demand minutes without a percentage", () => {
    render(
      <ProcessingStages
        job={{
          stage: "done", state: "done",
          stage_timings: {
            stages: [
              timing("transcription", "cached", 0.4),
              timing("speaker_attribution", "completed", 12.0),
              timing("topic_segmentation", "completed", 1.1),
              timing("extraction", "fallback", 3.2, { detail: { mode_used: "rules", fallback_reason: "LLM request timed out" } }),
              timing("verification", "completed", 30.5),
              timing("minutes_generation", "skipped", null, { detail: { note: "Runs on demand when the minutes are opened or exported" } }),
            ],
          },
        }}
      />,
    );
    expect(screen.getByTestId("group-speech").textContent).toContain("Reused from cache");
    expect(screen.getByTestId("group-extraction").textContent).toContain("Fallback used");
    expect(screen.getByTestId("group-extraction").textContent).toContain("timed out");
    expect(screen.getByTestId("fallback-note").textContent).toContain("not the configured LLM");
    expect(screen.getByTestId("group-minutes").textContent).toContain("Runs on demand");
    expect(screen.getByText("Processing complete")).toBeTruthy();
    expect(screen.getByTestId("processing-stages").textContent).not.toMatch(/\d+\s?%/);
  });

  it("marks only the running stage as running and later stages as waiting", () => {
    render(<ProcessingStages job={{ stage: "topics", state: "running", stage_timings: null }} />);
    expect(screen.getByTestId("group-speech").textContent).toContain("Completed");
    expect(screen.getByTestId("group-topics").textContent).toContain("Running");
    expect(screen.getByTestId("group-extraction").textContent).toContain("Waiting");
    expect(screen.queryByText("Processing complete")).toBeNull();
  });

  it("explains a failure and offers the retry action", () => {
    const retry = vi.fn();
    render(<ProcessingStages job={{ stage: "asr", state: "failed", error: "ffmpeg not found" }} onRetry={retry} retryLabel="Upload a different file" />);
    expect(screen.getByRole("alert").textContent).toContain("ffmpeg not found");
    expect(screen.getByTestId("group-speech").textContent).toContain("Failed");
    fireEvent.click(screen.getByRole("button", { name: "Upload a different file" }));
    expect(retry).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Shell navigation, URLs and history
// ---------------------------------------------------------------------------

describe("App shell", () => {
  beforeEach(() => {
    vi.spyOn(api, "getMe").mockResolvedValue({ auth_required: true, user: { user_id: "u1", email: "rita@example.test", display_name: "Rita Rao" } });
    vi.spyOn(api, "listMeetings").mockResolvedValue([]);
    vi.spyOn(api, "getLedger").mockResolvedValue({ commitments: [] });
  });

  it("navigates with meaningful URLs and supports the browser Back button", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { level: 1, name: "Meetings" })).toBeTruthy();

    const nav = screen.getAllByRole("navigation", { name: "Main" })[0];
    fireEvent.click(within(nav).getByRole("link", { name: "Commitments" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Commitments" })).toBeTruthy();
    expect(window.location.search).toBe("?view=commitments");
    expect(within(nav).getByRole("link", { name: "Commitments" }).getAttribute("aria-current")).toBe("page");

    await act(async () => {
      window.history.back();
      await new Promise((resolve) => window.addEventListener("popstate", resolve, { once: true }));
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Meetings" })).toBeTruthy();
    expect(window.location.search).toBe("");
  });

  it("shows the signed-in user with a working sign-out control", async () => {
    const logout = vi.spyOn(api, "logout").mockResolvedValue({});
    render(<App />);
    await screen.findByRole("heading", { level: 1, name: "Meetings" });
    expect(screen.getAllByText("Rita Rao").length).toBeGreaterThan(0);
    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: "Sign out" })[0]);
    });
    expect(logout).toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeTruthy();
  });

  it("opens the mobile navigation drawer and closes it with Escape", async () => {
    render(<App />);
    await screen.findByRole("heading", { level: 1, name: "Meetings" });
    const menu = screen.getByRole("button", { name: "Open navigation" });
    expect(menu.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(menu);
    const drawer = screen.getByRole("dialog", { name: "Navigation" });
    expect(within(drawer).getByRole("link", { name: "Meetings" })).toBeTruthy();
    expect(menu.getAttribute("aria-expanded")).toBe("true");

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Navigation" })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Meeting status comes only from backend fields
// ---------------------------------------------------------------------------

describe("meetingStatus", () => {
  it("maps job and review state without guessing", () => {
    expect(meetingStatus({ job_state: "running" }).label).toBe("Processing");
    expect(meetingStatus({ job_state: "queued" }).label).toBe("Queued");
    expect(meetingStatus({ job_state: "failed", review_status: "approved" }).label).toBe("Failed");
    expect(meetingStatus({ job_state: "done", review_status: "approved" }).label).toBe("Approved");
    expect(meetingStatus({ job_state: "done", review_status: "reopened" }).label).toBe("Reopened");
    expect(meetingStatus({ job_state: "done", review_status: null }).label).toBe("Needs review");
  });
});
