// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import App from "../App";
import LiveMeeting from "../pages/LiveMeeting";
import UploadMeeting from "../pages/UploadMeeting";
import { MeetingFlowProvider } from "../context/MeetingFlowContext";
import * as api from "../services/api";

// ----------------------------------------------------------------------------
// LiveKit Mock Implementation using vi.hoisted for Vitest jsdom Environment
// ----------------------------------------------------------------------------
const { MockRoom, MockParticipant } = vi.hoisted(() => {
  class MockParticipant {
    constructor(identity, name, isHost = false, consented = true) {
      this.identity = identity;
      this.name = name;
      this.metadata = JSON.stringify({
        is_host: isHost,
        name,
        consented,
        role: isHost ? "host" : "participant",
      });
      this.trackPublications = new Map();
      this.listeners = new Map();
    }

    on(event, handler) {
      if (!this.listeners.has(event)) this.listeners.set(event, []);
      this.listeners.get(event).push(handler);
    }

    off(event, handler) {
      if (this.listeners.has(event)) {
        this.listeners.set(
          event,
          this.listeners.get(event).filter((h) => h !== handler)
        );
      }
    }

    setMicrophoneEnabled = vi.fn().mockResolvedValue(true);
    setCameraEnabled = vi.fn().mockResolvedValue(true);
    setMetadata = vi.fn().mockImplementation((meta) => {
      this.metadata = meta;
      return Promise.resolve();
    });
  }

  class MockRoom {
    constructor() {
      this.eventHandlers = new Map();
      this.localParticipant = new MockParticipant("alex-morgan-id", "Alex Morgan", true, true);
      this.remoteParticipants = new Map([
        ["priya-sharma-id", new MockParticipant("priya-sharma-id", "Priya Sharma", false, true)],
        ["david-chen-id", new MockParticipant("david-chen-id", "David Chen", false, true)],
      ]);
    }

    on(event, handler) {
      if (!this.eventHandlers.has(event)) this.eventHandlers.set(event, []);
      this.eventHandlers.get(event).push(handler);
    }

    off(event, handler) {
      if (this.eventHandlers.has(event)) {
        this.eventHandlers.set(
          event,
          this.eventHandlers.get(event).filter((h) => h !== handler)
        );
      }
    }

    emit(event, ...args) {
      const handlers = this.eventHandlers.get(event) || [];
      handlers.forEach((h) => h(...args));
    }

    connect = vi.fn().mockImplementation(async () => {
      this.emit("connected");
      return Promise.resolve();
    });

    disconnect = vi.fn().mockImplementation(() => {
      this.emit("disconnected", "user_initiated");
    });
  }

  return { MockRoom, MockParticipant };
});

vi.mock("livekit-client", () => {
  return {
    Room: MockRoom,
    RoomEvent: {
      Connected: "connected",
      Reconnecting: "reconnecting",
      Reconnected: "reconnected",
      Disconnected: "disconnected",
      ParticipantConnected: "participantConnected",
      ParticipantDisconnected: "participantDisconnected",
      ActiveSpeakersChanged: "activeSpeakersChanged",
      TrackSubscribed: "trackSubscribed",
      TrackUnsubscribed: "trackUnsubscribed",
      TrackMuted: "trackMuted",
      TrackUnmuted: "trackUnmuted",
      ParticipantMetadataChanged: "participantMetadataChanged",
      LocalTrackPublished: "localTrackPublished",
      LocalTrackUnpublished: "localTrackUnpublished",
    },
    Track: {
      Kind: {
        Audio: "audio",
        Video: "video",
      },
    },
  };
});

// Polyfill HTMLMediaElement methods in jsdom
beforeEach(() => {
  window.HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined);
  window.HTMLMediaElement.prototype.pause = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

beforeEach(() => {
  // Phase 7: the app first asks who is signed in; here sign-in is off.
  vi.spyOn(api, "getMe").mockResolvedValue({ auth_required: false, user: null });
  vi.spyOn(api, "listMeetings").mockResolvedValue([]);
  vi.spyOn(api, "getReview").mockResolvedValue({
    draft: { draft_status: "draft", meeting_title: "Sprint Planning" },
    items: [],
  });

  // Mock LiveKit API services
  vi.spyOn(api, "createLiveRoom").mockResolvedValue({
    room_name: "livekit-m2m-test",
    title: "Engineering Sync",
    status: "active",
  });
  vi.spyOn(api, "getLiveToken").mockImplementation(async (payload) => ({
    token: "mock-valid-jwt-token",
    livekit_url: "wss://mock.livekit.cloud",
    room_name: "livekit-m2m-test",
    identity: "alex-morgan-id",
    expires_in_seconds: 3600,
    meeting_id: "mtg-live-1",
    // The server echoes back only consent it actually recorded.
    consented: payload?.metadata?.consented === true,
  }));
  vi.spyOn(api, "endLiveRoom").mockResolvedValue({
    room_name: "livekit-m2m-test",
    status: "ended",
  });
  vi.spyOn(api, "updateLiveConsent").mockResolvedValue({
    status: "updated",
  });
  vi.spyOn(api, "getLiveRecording").mockResolvedValue(recordingState());
  vi.spyOn(api, "startLiveRecording").mockResolvedValue(recordingState({ status: "starting" }));
  vi.spyOn(api, "stopLiveRecording").mockResolvedValue(recordingState({ status: "recorded" }));
  vi.spyOn(api, "processLiveRecording").mockResolvedValue(recordingState({ status: "processing" }));
});

// Shape of GET /api/live/meetings/{id}/recording
function recordingState(overrides = {}) {
  return {
    meeting_id: "mtg-live-1",
    room_name: "livekit-m2m-test",
    meeting_status: "active",
    status: "idle",
    capture_active: false,
    live: null,
    consent: { all_connected_consented: true, blocked: [], consent_blocked: false },
    participants: [],
    last_stop_reason: null,
    segments: [],
    artifact: null,
    processing: null,
    can_start: true,
    can_process: false,
    ...overrides,
  };
}

describe("Phase 4.1: Real LiveKit Meeting UI & WebRTC Lifecycle", () => {
  // Phase 8: the start screen is the Meetings dashboard; both sources are
  // offered there as primary actions.
  it("renders 'Start live meeting' alongside 'Upload recording' on the start screen", async () => {
    vi.spyOn(api, "listMeetings").mockResolvedValue([]);
    vi.spyOn(api, "getLedger").mockResolvedValue({ commitments: [] });
    render(<App />);

    expect(await screen.findByRole("heading", { level: 1, name: "Meetings" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Upload recording" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: "Start live meeting" }).length).toBeGreaterThan(0);
  });

  it("navigates to Live Meeting flow from the start screen", async () => {
    vi.spyOn(api, "listMeetings").mockResolvedValue([]);
    vi.spyOn(api, "getLedger").mockResolvedValue({ commitments: [] });
    render(<App />);

    const [liveBtn] = await screen.findAllByRole("button", { name: "Start live meeting" });
    fireEvent.click(liveBtn);

    // The live room is code-split (LiveKit is loaded on demand).
    expect(await screen.findByText("Create a Live Meeting")).toBeTruthy();
    expect(screen.getByText(/Phase 4.1 · LiveKit Real WebRTC/i)).toBeTruthy();
  });

  it("allows switching to Live Meeting directly from UploadMeeting page", () => {
    render(
      <MeetingFlowProvider>
        <UploadMeeting />
      </MeetingFlowProvider>
    );

    expect(screen.getByText("Upload a meeting recording")).toBeTruthy();
    const liveTabs = screen.getAllByText(/Start Live Meeting/i);
    expect(liveTabs.length).toBeGreaterThan(0);
  });

  it("renders Create Meeting form with room code, topic, host, language, consent toggle and device controls", () => {
    render(
      <MeetingFlowProvider>
        <LiveMeeting />
      </MeetingFlowProvider>
    );

    // Form fields
    const roomCodeInput = screen.getByLabelText(/Room Code \/ Identifier/i);
    expect(roomCodeInput.value).toMatch(/livekit-m2m-\d+/);

    const topicInput = screen.getByLabelText(/Meeting Topic \/ Purpose/i);
    expect(topicInput.value).toBe("Engineering Sync & Sprint Planning");

    const hostInput = screen.getByLabelText(/Your Display Name/i);
    expect(hostInput.value).toBe("Alex Morgan (Host)");

    const langSelect = screen.getByLabelText(/Spoken Language Scope/i);
    expect(langSelect).toBeTruthy();

    // Consent policy checkbox
    const consentCheckbox = screen.getByLabelText(/Explicit Recording & MoM Transcription Consent/i);
    // Phase 4.2: consent is opt-in, never pre-ticked.
    expect(consentCheckbox.checked).toBe(false);

    // Pre-join hardware controls
    const micToggle = screen.getByRole("button", { name: /Mic On/i });
    const camToggle = screen.getByRole("button", { name: /Camera On/i });
    expect(micToggle).toBeTruthy();
    expect(camToggle).toBeTruthy();

    // Toggling pre-join controls
    fireEvent.click(micToggle);
    expect(screen.getByRole("button", { name: /Mic Muted/i })).toBeTruthy();

    // Copy link helper
    expect(screen.getByText(/Room:/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Copy Invite Link/i })).toBeTruthy();
  });

  it("creates and connects to LiveKit Room, rendering participant tiles and controls", async () => {
    render(
      <MeetingFlowProvider>
        <LiveMeeting />
      </MeetingFlowProvider>
    );

    fireEvent.click(screen.getByLabelText(/Explicit Recording & MoM Transcription Consent/i));
    const startBtn = screen.getByRole("button", { name: /Create & Start Live Meeting Room/i });
    fireEvent.click(startBtn);

    // Wait for Room connection. Connecting alone never shows REC.
    await waitFor(() => {
      expect(screen.getByText("Not recording")).toBeTruthy();
    });
    expect(screen.queryByText(/^REC/)).toBeNull();
    expect(api.getLiveToken).toHaveBeenCalledWith(
      expect.objectContaining({ metadata: expect.objectContaining({ consented: true }) })
    );

    // Consent pill & top bar controls
    expect(screen.getByText(/Consent: 3\/3 Consented/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Copy Invite Link/i })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /End Meeting/i }).length).toBeGreaterThan(0);

    // Local participant tile
    expect(screen.getAllByText(/Alex Morgan/i).length).toBeGreaterThanOrEqual(1);

    // Remote participants from mock room
    expect(screen.getAllByText("Priya Sharma").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("David Chen").length).toBeGreaterThanOrEqual(1);

    // Side Drawer Tab
    expect(screen.getByText(/Attendees \(3\)/i)).toBeTruthy();
  });

  it("toggles microphone and camera in the Live Meeting Room", async () => {
    render(
      <MeetingFlowProvider>
        <LiveMeeting />
      </MeetingFlowProvider>
    );

    // Join room
    fireEvent.click(screen.getByRole("button", { name: /Create & Start Live Meeting Room/i }));

    await waitFor(() => {
      expect(screen.getByLabelText("Toggle Microphone")).toBeTruthy();
    });

    const micBtn = screen.getByLabelText("Toggle Microphone");
    const camBtn = screen.getByLabelText("Toggle Camera");

    expect(micBtn.getAttribute("title")).toContain("Mute Microphone");
    fireEvent.click(micBtn);
    await waitFor(() => {
      expect(micBtn.getAttribute("title")).toContain("Unmute Microphone");
    });

    expect(camBtn.getAttribute("title")).toContain("Turn Off Camera");
    fireEvent.click(camBtn);
    await waitFor(() => {
      expect(camBtn.getAttribute("title")).toContain("Turn On Camera");
    });
  });

  it("shows confirmation modal before ending meeting and allows canceling", async () => {
    render(
      <MeetingFlowProvider>
        <LiveMeeting />
      </MeetingFlowProvider>
    );

    fireEvent.click(screen.getByRole("button", { name: /Create & Start Live Meeting Room/i }));

    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: /End Meeting/i }).length).toBeGreaterThan(0);
    });

    const endMeetingBtn = screen.getAllByRole("button", { name: /End Meeting/i })[0];
    fireEvent.click(endMeetingBtn);

    // Modal appears
    expect(screen.getByText("End Live Meeting for All?")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Stay in Meeting/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /End for Everyone/i })).toBeTruthy();

    // Dismiss modal
    fireEvent.click(screen.getByRole("button", { name: /Stay in Meeting/i }));
    expect(screen.queryByText("End Live Meeting for All?")).toBeNull();
  });

  it("transitions to Meeting-Ended state without inventing a recording", async () => {
    render(
      <MeetingFlowProvider>
        <LiveMeeting />
      </MeetingFlowProvider>
    );

    // Start room
    fireEvent.click(screen.getByRole("button", { name: /Create & Start Live Meeting Room/i }));

    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: /End Meeting/i }).length).toBeGreaterThan(0);
    });

    // Open end modal and confirm
    fireEvent.click(screen.getAllByRole("button", { name: /End Meeting/i })[0]);
    fireEvent.click(screen.getByRole("button", { name: /End for Everyone/i }));

    // Meeting-Ended screen reflects the server: nothing was recorded.
    await waitFor(() => {
      expect(screen.getByText("Live Meeting Ended")).toBeTruthy();
    });
    expect(api.endLiveRoom).toHaveBeenCalledWith("livekit-m2m-test", "mock-valid-jwt-token");
    await waitFor(() => {
      expect(screen.getByText(/No consented audio was recorded/i)).toBeTruthy();
    });
    expect(screen.queryByText(/Consent Verified/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /Process Meeting/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Proceed to MoM Review/i })).toBeNull();
  });
});

describe("Phase 4.2: server-side recording UI", () => {
  async function joinRoom() {
    render(
      <MeetingFlowProvider>
        <LiveMeeting />
      </MeetingFlowProvider>
    );
    fireEvent.click(screen.getByLabelText(/Explicit Recording & MoM Transcription Consent/i));
    fireEvent.click(screen.getByRole("button", { name: /Create & Start Live Meeting Room/i }));
    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: /End Meeting/i }).length).toBeGreaterThan(0);
    });
  }

  it("shows REC only after the backend confirms audio capture", async () => {
    api.getLiveRecording.mockResolvedValue(recordingState({ status: "starting", live: { frames_captured: 0 } }));
    await joinRoom();
    await waitFor(() => expect(screen.getByText(/Starting capture/i)).toBeTruthy());
    expect(screen.queryByTestId("rec-badge")).toBeNull();

    api.getLiveRecording.mockResolvedValue(
      recordingState({
        status: "recording",
        capture_active: true,
        live: { frames_captured: 4200, elapsed_seconds: 42, tracks: [] },
      })
    );
    await waitFor(() => expect(screen.getByTestId("rec-badge").textContent).toContain("REC 00:42"), {
      timeout: 4000,
    });
  });

  it("lets the host start and stop recording through the server", async () => {
    await joinRoom();
    const startBtn = await screen.findByRole("button", { name: /Start Recording/i });
    await waitFor(() => expect(startBtn.disabled).toBe(false));

    api.startLiveRecording.mockResolvedValue(
      recordingState({ status: "recording", capture_active: true, live: { frames_captured: 10, elapsed_seconds: 1 } })
    );
    fireEvent.click(startBtn);
    await waitFor(() =>
      expect(api.startLiveRecording).toHaveBeenCalledWith("mtg-live-1", "mock-valid-jwt-token")
    );
    const stopBtn = await screen.findByRole("button", { name: /Stop Recording/i });
    fireEvent.click(stopBtn);
    await waitFor(() =>
      expect(api.stopLiveRecording).toHaveBeenCalledWith("mtg-live-1", "mock-valid-jwt-token")
    );
  });

  it("blocks recording and names participants who have not consented", async () => {
    api.getLiveRecording.mockResolvedValue(
      recordingState({
        can_start: false,
        consent: {
          all_connected_consented: false,
          blocked: [{ identity: "priya-sharma-id", name: "Priya Sharma" }],
          consent_blocked: true,
        },
      })
    );
    await joinRoom();
    await waitFor(() =>
      expect(screen.getByText(/Recording is blocked until these participants consent: Priya Sharma/i)).toBeTruthy()
    );
    expect(screen.getByRole("button", { name: /Start Recording/i }).disabled).toBe(true);
    expect(screen.getByText("Consent required")).toBeTruthy();
  });

  it("explains that a consent revocation stopped capture and will not auto-resume", async () => {
    api.getLiveRecording.mockResolvedValue(
      recordingState({
        status: "recorded",
        last_stop_reason: "consent_revoked",
        segments: [{ segment_id: "s1", seq: 1, state: "finalized", stop_reason: "consent_revoked" }],
        consent: { all_connected_consented: true, blocked: [], consent_blocked: true },
      })
    );
    await joinRoom();
    await waitFor(() => expect(screen.getByText(/will not resume automatically/i)).toBeTruthy());
    expect(screen.queryByTestId("rec-badge")).toBeNull();
    expect(screen.getByRole("button", { name: /Start New Segment/i })).toBeTruthy();
  });

  it("sends consent changes to the server with the participant token", async () => {
    api.updateLiveConsent.mockResolvedValue({
      status: "updated",
      consented: false,
      recording: recordingState({ status: "stopping" }),
    });
    await joinRoom();
    fireEvent.click(screen.getByRole("button", { name: /Revoke Consent/i }));
    await waitFor(() =>
      expect(api.updateLiveConsent).toHaveBeenCalledWith(
        "livekit-m2m-test",
        "alex-morgan-id",
        false,
        "mock-valid-jwt-token"
      )
    );
    await waitFor(() => expect(screen.getByRole("button", { name: /Grant Consent/i })).toBeTruthy());
  });

  it("processes a finalized recording and opens the existing review workflow", async () => {
    const getTranscript = vi.spyOn(api, "getTranscript").mockResolvedValue([]);
    await joinRoom();
    api.getLiveRecording.mockResolvedValue(
      recordingState({
        status: "recorded",
        meeting_status: "ended",
        can_process: true,
        artifact: { frames: 160000, duration_seconds: 10, segments: 1, participants: ["Alex Morgan", "Priya Sharma"] },
        segments: [{ segment_id: "s1", seq: 1, state: "finalized", duration_seconds: 10, stop_reason: "meeting_ended" }],
      })
    );
    fireEvent.click(screen.getAllByRole("button", { name: /End Meeting/i })[0]);
    fireEvent.click(screen.getByRole("button", { name: /End for Everyone/i }));

    const processBtn = await screen.findByRole("button", { name: /Process Meeting/i });
    expect(screen.getByText("Alex Morgan, Priya Sharma")).toBeTruthy();
    fireEvent.click(processBtn);
    await waitFor(() =>
      expect(api.processLiveRecording).toHaveBeenCalledWith("mtg-live-1", "mock-valid-jwt-token", { retry: false })
    );

    api.getLiveRecording.mockResolvedValue(
      recordingState({
        status: "completed",
        meeting_status: "ended",
        processing: { state: "done", stage: "done", progress: 1, error: null },
      })
    );
    await waitFor(() => expect(getTranscript).toHaveBeenCalledWith("mtg-live-1"), { timeout: 4000 });
  });

  it("shows live processing progress, then a failure with a working retry, without reloading", async () => {
    await joinRoom();
    const ended = {
      meeting_status: "ended",
      artifact: { frames: 160000, duration_seconds: 10, segments: 1, participants: ["Alex Morgan"] },
      segments: [{ segment_id: "s1", seq: 1, state: "finalized", duration_seconds: 10 }],
    };
    api.getLiveRecording.mockResolvedValue(
      recordingState({
        ...ended,
        status: "processing",
        processing: { state: "running", stage: "asr", progress: 0.3636, error: null },
      })
    );
    fireEvent.click(screen.getAllByRole("button", { name: /End Meeting/i })[0]);
    fireEvent.click(screen.getByRole("button", { name: /End for Everyone/i }));

    await waitFor(() => expect(screen.getByText(/Processing… \(asr\)/)).toBeTruthy());
    expect(screen.getByText(/36% · running/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Retry Processing/i })).toBeNull();

    // The next poll reports the failure; no reload is needed to see it.
    api.getLiveRecording.mockResolvedValue(
      recordingState({
        ...ended,
        status: "failed",
        processing: { state: "failed", stage: "merge", progress: 0.4545, error: "asr crashed" },
      })
    );
    const retryBtn = await screen.findByRole("button", { name: /Retry Processing/i }, { timeout: 4000 });
    expect(screen.getByText("asr crashed")).toBeTruthy();
    fireEvent.click(retryBtn);
    await waitFor(() =>
      expect(api.processLiveRecording).toHaveBeenCalledWith("mtg-live-1", "mock-valid-jwt-token", { retry: true })
    );
  });
});
