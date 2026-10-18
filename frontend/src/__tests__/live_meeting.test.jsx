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
  vi.spyOn(api, "getLiveToken").mockResolvedValue({
    token: "mock-valid-jwt-token",
    livekit_url: "wss://mock.livekit.cloud",
    room_name: "livekit-m2m-test",
    identity: "alex-morgan-id",
    expires_in_seconds: 3600,
  });
  vi.spyOn(api, "endLiveRoom").mockResolvedValue({
    room_name: "livekit-m2m-test",
    status: "ended",
  });
  vi.spyOn(api, "updateLiveConsent").mockResolvedValue({
    status: "updated",
  });
});

describe("Phase 4.1: Real LiveKit Meeting UI & WebRTC Lifecycle", () => {
  it("renders 'Start Live Meeting' alongside 'Upload Recorded Meeting' on initial source selection", () => {
    render(<App />);

    expect(screen.getByText("Choose Meeting Source")).toBeTruthy();
    expect(screen.getByText("Upload Recorded Meeting")).toBeTruthy();
    expect(screen.getByText("Start Live Meeting")).toBeTruthy();
  });

  it("navigates to Live Meeting flow from mode selection", () => {
    render(<App />);

    const liveBtn = screen.getByText("Start Live Meeting").closest("button");
    fireEvent.click(liveBtn);

    expect(screen.getByText("Create a Live Meeting")).toBeTruthy();
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
    expect(consentCheckbox.checked).toBe(true);

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

    const startBtn = screen.getByRole("button", { name: /Create & Start Live Meeting Room/i });
    fireEvent.click(startBtn);

    // Wait for Room connection
    await waitFor(() => {
      expect(screen.getByText(/REC 00:/i)).toBeTruthy();
    });

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

  it("transitions to Meeting-Ended state and connects to MoM Review workflow", async () => {
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

    // Meeting-Ended screen
    await waitFor(() => {
      expect(screen.getByText("Live Meeting Ended")).toBeTruthy();
    });
    expect(screen.getByText(/100% Consent Verified/i)).toBeTruthy();
    expect(screen.getByText("Pipeline Verification Progress")).toBeTruthy();

    // Visual connection button to MoM review workflow
    const proceedBtn = screen.getByRole("button", { name: /Proceed to MoM Review & Verification/i });
    expect(proceedBtn).toBeTruthy();

    // Inspect transcript button
    expect(screen.getByRole("button", { name: /Inspect Transcript First/i })).toBeTruthy();
  });
});
