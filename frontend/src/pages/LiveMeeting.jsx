import { useCallback, useEffect, useRef, useState } from "react";
import {
  Room,
  RoomEvent,
  Track,
} from "livekit-client";
import { useMeetingFlow } from "../context/MeetingFlowContext";
import {
  acceptInvitation,
  createInvitation,
  createLiveRoom,
  endLiveRoom,
  getLiveConfig,
  getLiveRecording,
  getLiveToken,
  processLiveRecording,
  startLiveRecording,
  stopLiveRecording,
  updateLiveConsent,
} from "../services/api";
import "./LiveMeeting.css";
import StageTimings from "../components/StageTimings";
import { useAuth } from "../components/AuthGate";

// LiveKit protocol DisconnectReason.DUPLICATE_IDENTITY (enum value 2): another
// connection with the same identity replaced this one.
const DUPLICATE_IDENTITY_REASON = 2;

// ============================================================================
// Server-side recording helpers (Phase 4.2)
// ============================================================================

const RECORDING_POLL_MS = 2000;

const STOP_REASON_LABELS = {
  host_stopped: "stopped by the host",
  consent_revoked: "a participant revoked consent",
  consent_missing: "not every participant had consented",
  unconsented_participant_joined: "a participant without consent joined",
  unconsented_audio: "audio arrived from a participant without consent",
  no_participants: "everyone left the room",
  meeting_ended: "the meeting ended",
  recorder_disconnected: "the recorder lost its connection",
  recorder_error: "the recorder failed",
  server_shutdown: "the server shut down",
  interrupted: "the server restarted while recording",
};

const CONSENT_STOP_REASONS = [
  "consent_revoked",
  "consent_missing",
  "unconsented_participant_joined",
  "unconsented_audio",
];

function stopReasonLabel(reason) {
  return STOP_REASON_LABELS[reason] || reason || "unknown reason";
}

// One stable identity per browser tab and room, so a rejoin keeps the same
// server-side consent record and host binding.
function stableIdentity(room) {
  const key = `m2m-live-identity:${room}`;
  try {
    const existing = window.sessionStorage.getItem(key);
    if (existing) return existing;
    const created = `user_${Math.random().toString(36).slice(2, 10)}`;
    window.sessionStorage.setItem(key, created);
    return created;
  } catch {
    return undefined;
  }
}

function formatClock(totalSeconds) {
  const seconds = Math.max(0, Math.floor(totalSeconds || 0));
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

// REC is shown only when the backend reports that audio frames are being written.
function RecordingBadge({ recState }) {
  const status = recState?.status;
  if (status === "recording") {
    const frames = recState?.live?.frames_captured ?? 0;
    return (
      <div
        className="lm-rec-indicator"
        title={`Server is capturing audio (${frames} frames written)`}
        data-testid="rec-badge"
      >
        <span className="lm-rec-dot" />
        <span>REC {formatClock(recState?.live?.elapsed_seconds)}</span>
      </div>
    );
  }
  let label = "Not recording";
  if (status === "starting") label = "Starting capture…";
  else if (status === "stopping") label = "Finalizing audio…";
  else if (status === "processing") label = "Processing";
  else if (status === "completed") label = "Processed";
  else if (status === "failed") label = "Recording failed";
  else if (recState?.consent?.consent_blocked) label = "Consent required";
  else if (status === "recorded") label = "Recorded · paused";
  return (
    <div className="lm-rec-indicator lm-rec-idle" data-testid="rec-status">
      <span>{label}</span>
    </div>
  );
}

// ============================================================================
// Remote Participant Video & Audio Tile Component
// ============================================================================

function RemoteParticipantTile({ participant, isSpeaking }) {
  const videoRef = useRef(null);
  const audioRef = useRef(null);
  const [hasVideo, setHasVideo] = useState(false);
  const [hasAudio, setHasAudio] = useState(false);
  const [, setRerender] = useState(0);

  useEffect(() => {
    if (!participant) return;

    const attachExisting = () => {
      participant.trackPublications.forEach((pub) => {
        if (pub.track) {
          if (pub.kind === Track.Kind.Video && videoRef.current) {
            pub.track.attach(videoRef.current);
            setHasVideo(!pub.isMuted);
          }
          if (pub.kind === Track.Kind.Audio && audioRef.current) {
            pub.track.attach(audioRef.current);
            setHasAudio(!pub.isMuted);
          }
        }
      });
    };

    attachExisting();

    const handleTrackSubscribed = (track, publication) => {
      if (track.kind === Track.Kind.Video && videoRef.current) {
        track.attach(videoRef.current);
        setHasVideo(!publication.isMuted);
      }
      if (track.kind === Track.Kind.Audio && audioRef.current) {
        track.attach(audioRef.current);
        setHasAudio(!publication.isMuted);
      }
    };

    const handleTrackUnsubscribed = (track) => {
      if (track.kind === Track.Kind.Video && videoRef.current) {
        track.detach(videoRef.current);
        setHasVideo(false);
      }
      if (track.kind === Track.Kind.Audio && audioRef.current) {
        track.detach(audioRef.current);
        setHasAudio(false);
      }
    };

    const handleTrackMuted = (pub) => {
      if (pub.kind === Track.Kind.Video) setHasVideo(false);
      if (pub.kind === Track.Kind.Audio) setHasAudio(false);
    };

    const handleTrackUnmuted = (pub) => {
      if (pub.kind === Track.Kind.Video && pub.track && videoRef.current) {
        pub.track.attach(videoRef.current);
        setHasVideo(true);
      }
      if (pub.kind === Track.Kind.Audio && pub.track && audioRef.current) {
        pub.track.attach(audioRef.current);
        setHasAudio(true);
      }
    };

    const handleMetaChanged = () => {
      setRerender((v) => v + 1);
    };

    participant.on(RoomEvent.TrackSubscribed, handleTrackSubscribed);
    participant.on(RoomEvent.TrackUnsubscribed, handleTrackUnsubscribed);
    participant.on(RoomEvent.TrackMuted, handleTrackMuted);
    participant.on(RoomEvent.TrackUnmuted, handleTrackUnmuted);
    participant.on(RoomEvent.ParticipantMetadataChanged, handleMetaChanged);

    return () => {
      participant.off(RoomEvent.TrackSubscribed, handleTrackSubscribed);
      participant.off(RoomEvent.TrackUnsubscribed, handleTrackUnsubscribed);
      participant.off(RoomEvent.TrackMuted, handleTrackMuted);
      participant.off(RoomEvent.TrackUnmuted, handleTrackUnmuted);
      participant.off(RoomEvent.ParticipantMetadataChanged, handleMetaChanged);
      if (videoRef.current) {
        participant.trackPublications.forEach((pub) => pub.track?.detach(videoRef.current));
      }
      if (audioRef.current) {
        participant.trackPublications.forEach((pub) => pub.track?.detach(audioRef.current));
      }
    };
  }, [participant]);

  let meta = {};
  try {
    meta = participant.metadata ? JSON.parse(participant.metadata) : {};
  } catch {}

  const displayName = participant.name || participant.identity || "Remote Participant";
  const initials = displayName
    .split(" ")
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  const isHost = meta.is_host === true;
  const isConsented = meta.consented === true;

  return (
    <div className={`lm-tile ${isSpeaking ? "speaking" : ""}`}>
      {/* Top Overlay */}
      <div className="lm-tile-overlay-top">
        <span
          className={`lm-badge ${isConsented ? "lm-badge-consent" : "lm-badge-demo"}`}
          style={{ fontSize: "10px", padding: "2px 8px" }}
        >
          {isConsented ? "✓ Consented" : "Pending Consent"}
        </span>

        {isSpeaking && (
          <div className="lm-speaking-waveform" title="Speaking">
            <span className="lm-waveform-bar" />
            <span className="lm-waveform-bar" />
            <span className="lm-waveform-bar" />
            <span className="lm-waveform-bar" />
          </div>
        )}
      </div>

      {/* Media elements */}
      <video
        ref={videoRef}
        autoPlay
        playsInline
        className="lm-tile-video"
        style={{ display: hasVideo ? "block" : "none" }}
      />
      <audio ref={audioRef} autoPlay />

      {/* Fallback avatar view when video is muted or off */}
      {!hasVideo && (
        <div className="lm-tile-avatar-view">
          <div
            className={`lm-avatar-circle ${isSpeaking ? "speaking-pulse" : ""}`}
            style={{ background: "linear-gradient(135deg, #10b981 0%, #047857 100%)" }}
          >
            {initials}
          </div>
          <span style={{ fontSize: "11px", color: "#94a3b8" }}>
            Camera is turned off
          </span>
        </div>
      )}

      {/* Bottom Overlay Name Tag & Status Icons */}
      <div className="lm-tile-overlay-bottom">
        <div className="lm-tile-name-tag">
          <span>{displayName}</span>
          <span className="lm-role-badge">
            {isHost ? "Host" : "Participant"}
          </span>
        </div>

        <div className="lm-tile-status-icons">
          <span
            className={`lm-status-icon-badge ${!hasAudio ? "off" : ""}`}
            title={hasAudio ? "Microphone Active" : "Microphone Muted"}
          >
            {hasAudio ? "🎙️" : "🔇"}
          </span>
          <span
            className={`lm-status-icon-badge ${!hasVideo ? "off" : ""}`}
            title={hasVideo ? "Camera Active" : "Camera Off"}
          >
            {hasVideo ? "📹" : "🚫"}
          </span>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Local Participant Video Tile Component
// ============================================================================

function LocalParticipantTile({ room, name, isHost, consented, isSpeaking, micEnabled, camEnabled }) {
  const videoRef = useRef(null);

  useEffect(() => {
    if (!room?.localParticipant) return;
    const localPart = room.localParticipant;

    const attachLocalVideo = () => {
      localPart.trackPublications.forEach((pub) => {
        if (pub.kind === Track.Kind.Video && pub.track && videoRef.current && camEnabled) {
          pub.track.attach(videoRef.current);
        }
      });
    };

    attachLocalVideo();

    const handleLocalPublished = (pub) => {
      if (pub.kind === Track.Kind.Video && pub.track && videoRef.current && camEnabled) {
        pub.track.attach(videoRef.current);
      }
    };

    const handleLocalUnpublished = (pub) => {
      if (pub.kind === Track.Kind.Video && videoRef.current) {
        pub.track?.detach(videoRef.current);
      }
    };

    localPart.on(RoomEvent.LocalTrackPublished, handleLocalPublished);
    localPart.on(RoomEvent.LocalTrackUnpublished, handleLocalUnpublished);

    return () => {
      localPart.off(RoomEvent.LocalTrackPublished, handleLocalPublished);
      localPart.off(RoomEvent.LocalTrackUnpublished, handleLocalUnpublished);
      if (videoRef.current) {
        localPart.trackPublications.forEach((pub) => pub.track?.detach(videoRef.current));
      }
    };
  }, [room, camEnabled]);

  const initials = (name || "You")
    .split(" ")
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className={`lm-tile ${isSpeaking ? "speaking" : ""}`}>
      {/* Top Overlay */}
      <div className="lm-tile-overlay-top">
        <span
          className={`lm-badge ${consented ? "lm-badge-consent" : "lm-badge-demo"}`}
          style={{ fontSize: "10px", padding: "2px 8px" }}
        >
          {consented ? "✓ Consented" : "Pending Consent"}
        </span>

        {isSpeaking && (
          <div className="lm-speaking-waveform" title="Speaking">
            <span className="lm-waveform-bar" />
            <span className="lm-waveform-bar" />
            <span className="lm-waveform-bar" />
            <span className="lm-waveform-bar" />
          </div>
        )}
      </div>

      {/* Local Video Stream (Mirrored) */}
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        className="lm-tile-video"
        style={{
          display: camEnabled ? "block" : "none",
          transform: "scaleX(-1)",
        }}
      />

      {/* Fallback avatar view when video is off */}
      {!camEnabled && (
        <div className="lm-tile-avatar-view">
          <div
            className={`lm-avatar-circle ${isSpeaking ? "speaking-pulse" : ""}`}
            style={{ background: "linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%)" }}
          >
            {initials}
          </div>
          <span style={{ fontSize: "11px", color: "#94a3b8" }}>
            Camera is turned off
          </span>
        </div>
      )}

      {/* Bottom Overlay Name Tag & Status Icons */}
      <div className="lm-tile-overlay-bottom">
        <div className="lm-tile-name-tag">
          <span>{name} (You)</span>
          <span className="lm-role-badge">
            {isHost ? "Host" : "Participant"}
          </span>
        </div>

        <div className="lm-tile-status-icons">
          <span
            className={`lm-status-icon-badge ${!micEnabled ? "off" : ""}`}
            title={micEnabled ? "Microphone Active" : "Microphone Muted"}
          >
            {micEnabled ? "🎙️" : "🔇"}
          </span>
          <span
            className={`lm-status-icon-badge ${!camEnabled ? "off" : ""}`}
            title={camEnabled ? "Camera Active" : "Camera Off"}
          >
            {camEnabled ? "📹" : "🚫"}
          </span>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Main LiveMeeting Component with Real LiveKit WebRTC Integration
// ============================================================================

export default function LiveMeeting() {
  const {
    setMeetingMode,
    loadExistingMeeting,
  } = useMeetingFlow();

  const { user: signedInUser, authRequired } = useAuth();

  // Invite links: ?room=<code>&invite=<token>. Read once at mount so later
  // URL updates can never drop the invitation before it is accepted.
  const [{ initialRoomParam, initialInvite }] = useState(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      return { initialRoomParam: params.get("room") || "", initialInvite: params.get("invite") || "" };
    } catch {
      return { initialRoomParam: "", initialInvite: "" };
    }
  });
  const inviteTokenRef = useRef(initialInvite);

  // View States: "create" | "room" | "ended"
  const [viewState, setViewState] = useState("create");

  // Create / Join Configuration
  const [isJoinMode, setIsJoinMode] = useState(Boolean(initialRoomParam || initialInvite));
  const [roomCode, setRoomCode] = useState(
    initialRoomParam || `livekit-m2m-${Math.floor(1000 + Math.random() * 9000)}`
  );
  const [topic, setTopic] = useState(
    initialRoomParam ? `Live Meeting: ${initialRoomParam}` : "Engineering Sync & Sprint Planning"
  );
  const [displayName, setDisplayName] = useState(
    initialRoomParam ? "Guest Participant" : "Alex Morgan (Host)"
  );
  // The role requested follows the form (create = host, join = participant);
  // once connected, the server's answer in the token response is used.
  const [isHost, setIsHost] = useState(!(initialRoomParam || initialInvite));
  const [language, setLanguage] = useState("en");
  // Consent is opt-in: nothing is pre-ticked on the participant's behalf.
  const [hasConsented, setHasConsented] = useState(false);
  const [previewMic, setPreviewMic] = useState(true);
  const [previewCam, setPreviewCam] = useState(true);

  // LiveKit Connection State
  const roomRef = useRef(null);
  const [connectionState, setConnectionState] = useState("disconnected"); // "disconnected" | "connecting" | "connected" | "reconnecting"
  const [remoteParticipants, setRemoteParticipants] = useState([]);
  const [activeSpeakerIdentities, setActiveSpeakerIdentities] = useState([]);
  const [micEnabled, setMicEnabled] = useState(true);
  const [camEnabled, setCamEnabled] = useState(true);
  const [connectionError, setConnectionError] = useState("");
  const [permissionError, setPermissionError] = useState("");
  const [isConnecting, setIsConnecting] = useState(false);
  const [liveConfigNotice, setLiveConfigNotice] = useState("");

  // Server-side recording state (Phase 4.2). The backend is the only source
  // of truth for consent and capture; the UI never infers recording.
  const tokenRef = useRef("");
  const liveRoomRef = useRef("");
  const processRequestedRef = useRef(false);
  const [meetingId, setMeetingId] = useState("");
  const [recState, setRecState] = useState(null);
  const [recError, setRecError] = useState("");
  const [recBusy, setRecBusy] = useState(false);

  const refreshRecording = useCallback(async () => {
    if (!meetingId || !tokenRef.current) return;
    try {
      const state = await getLiveRecording(meetingId, tokenRef.current);
      setRecState(state);
      const selfIdentity = roomRef.current?.localParticipant?.identity;
      const self = state?.participants?.find((p) => p.identity === selfIdentity);
      if (self) setHasConsented(self.consented === true);
    } catch (err) {
      setRecError(err?.message || "Unable to read recording state from the server.");
    }
  }, [meetingId]);

  // Probe LiveKit backend configuration on component mount
  useEffect(() => {
    let active = true;
    getLiveConfig()
      .then((cfg) => {
        if (!active) return;
        if (!cfg?.configured) {
          setLiveConfigNotice(cfg?.message || "LiveKit credentials not configured.");
        }
      })
      .catch((err) => {
        if (!active) return;
        setLiveConfigNotice(
          err?.message || "Cannot connect to backend server. Please verify FastAPI is running."
        );
      });
    return () => {
      active = false;
    };
  }, []);

  // Room UI State
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [isDrawerOpen, setIsDrawerOpen] = useState(true);
  const [drawerTab, setDrawerTab] = useState("participants"); // "participants" | "info"
  const [showEndModal, setShowEndModal] = useState(false);
  const [copyFeedback, setCopyFeedback] = useState(false);
  const timerRef = useRef(null);

  // --------------------------------------------------------------------------
  // Timer Management in Room
  // --------------------------------------------------------------------------
  useEffect(() => {
    if (viewState === "room" && connectionState === "connected") {
      timerRef.current = setInterval(() => {
        setElapsedSeconds((prev) => prev + 1);
      }, 1000);
    } else {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [viewState, connectionState]);

  const formatTime = formatClock;

  // Poll the authoritative recording state while in the room and after it ends.
  useEffect(() => {
    if (!meetingId || viewState === "create") return undefined;
    refreshRecording();
    const timer = setInterval(refreshRecording, RECORDING_POLL_MS);
    return () => clearInterval(timer);
  }, [meetingId, viewState, refreshRecording]);

  // Hand off to the existing review workflow once processing has completed.
  useEffect(() => {
    if (
      viewState === "ended" &&
      processRequestedRef.current &&
      recState?.status === "completed" &&
      meetingId
    ) {
      processRequestedRef.current = false;
      loadExistingMeeting(meetingId);
    }
  }, [viewState, recState?.status, meetingId, loadExistingMeeting]);

  const handleCopyInvite = async () => {
    let inviteUrl = `${window.location.origin}/?room=${encodeURIComponent(liveRoomRef.current || roomCode)}`;
    // Phase 7: with sign-in on, the link carries a single-meeting invitation.
    // Without sign-in the plain room link is used.
    if (meetingId) {
      try {
        const invitation = await createInvitation(meetingId);
        if (invitation?.token) {
          inviteUrl += `&invite=${encodeURIComponent(invitation.token)}`;
        }
      } catch (err) {
        if (authRequired) {
          // A plain link would not admit anyone; say so instead of copying it.
          setRecError(err?.message || "Only the meeting host can create an invite link.");
          return;
        }
      }
    }
    if (navigator.clipboard) {
      navigator.clipboard.writeText(inviteUrl).catch(() => {});
    }
    setCopyFeedback(true);
    setTimeout(() => setCopyFeedback(false), 2200);
  };

  // --------------------------------------------------------------------------
  // Connect to LiveKit Room
  // --------------------------------------------------------------------------
  const handleStartOrJoinRoom = async (e) => {
    if (e) e.preventDefault();
    // A second click while a join is in flight, or while connected, must not
    // create another connection with the same identity.
    if (isConnecting || (roomRef.current && roomRef.current.state !== "disconnected")) return;
    setConnectionError("");
    setPermissionError("");
    setIsConnecting(true);

    try {
      let roomToJoin = roomCode.trim();

      // 1. Creating a meeting: register the room. A room code owned by another
      // account cannot be taken over; everything else continues to the token.
      if (isHost) {
        try {
          await createLiveRoom({
            room_name: roomToJoin,
            title: topic,
            host_name: displayName,
          });
        } catch (err) {
          if (err?.status === 409) {
            throw new Error(
              `Room code “${roomToJoin}” already belongs to another host's meeting. Choose a different code, or use “Have a Room Code? Join” with that host's invite link.`
            );
          }
          console.warn("createLiveRoom warning (continuing with join token):", err);
        }
      }

      // 1b. Phase 7: a guest with an invitation becomes a member of that
      // meeting first, and joins the room the invitation belongs to.
      if (!isHost && inviteTokenRef.current) {
        let accepted;
        try {
          accepted = await acceptInvitation(inviteTokenRef.current);
        } catch (err) {
          throw new Error(
            err?.status === 404
              ? "This invitation link is invalid or has expired. Ask the host to copy a new invite link."
              : err?.status === 401
                ? "Sign in first, then open the invite link again."
                : err?.message || "The invitation could not be accepted."
          );
        }
        if (accepted?.room_name && accepted.room_name !== roomToJoin) {
          roomToJoin = accepted.room_name;
          setRoomCode(accepted.room_name);
        }
      }

      // 2. Request authenticated JWT access token from FastAPI backend.
      // With sign-in on, the server chooses identity, name and host role.
      // The join-form consent tick is recorded server-side as an explicit grant.
      let tokenResponse;
      try {
        tokenResponse = await getLiveToken({
          room_name: roomToJoin,
          participant_name: displayName,
          participant_identity: stableIdentity(roomToJoin),
          is_host: isHost,
          metadata: {
            consented: hasConsented,
            topic: topic,
            role: isHost ? "host" : "participant",
          },
        });
      } catch (err) {
        if (err?.status === 403 && !isHost) {
          throw new Error(
            "You are not a member of this meeting yet. Open the invite link the host copied from the meeting room; the room code alone does not grant access."
          );
        }
        if (err?.status === 404) {
          throw new Error(
            `No meeting is active with room code “${roomToJoin}”. Check the code, or ask the host to start the meeting first.`
          );
        }
        throw err;
      }

      const { token, livekit_url: serverUrl } = tokenResponse;

      if (!token || !serverUrl) {
        throw new Error("Backend did not return a valid LiveKit token or server URL.");
      }
      tokenRef.current = token;
      liveRoomRef.current = tokenResponse.room_name || roomToJoin;
      setMeetingId(tokenResponse.meeting_id || "");
      // The server decides who hosts (the meeting owner) and the shown name.
      if (typeof tokenResponse.is_host === "boolean") setIsHost(tokenResponse.is_host);
      if (tokenResponse.participant_name) setDisplayName(tokenResponse.participant_name);
      if (typeof tokenResponse.consented === "boolean") {
        setHasConsented(tokenResponse.consented);
      }

      // 3. Instantiate LiveKit Room client
      const room = new Room({
        adaptiveStream: true,
        dynacast: true,
      });
      roomRef.current = room;

      // Event: Connection State
      room.on(RoomEvent.Connected, () => {
        setConnectionState("connected");
        setElapsedSeconds(0);
        setViewState("room");
        setIsConnecting(false);
      });

      room.on(RoomEvent.Reconnecting, () => {
        setConnectionState("reconnecting");
      });

      room.on(RoomEvent.Reconnected, () => {
        setConnectionState("connected");
      });

      room.on(RoomEvent.Disconnected, (reason) => {
        setConnectionState("disconnected");
        console.log("LiveKit disconnected:", reason);
        if (reason === DUPLICATE_IDENTITY_REASON) {
          // LiveKit allows one connection per identity: the same account
          // joined from another tab or device and this tab was replaced.
          roomRef.current = null;
          setConnectionError(
            "This account joined the meeting from another tab or device, so this tab was disconnected. To join as a second participant, sign in with a different account in a separate browser or private window."
          );
          setViewState("create");
          return;
        }
        setViewState("ended");
      });

      // Event: Remote Participants
      const updateRemotes = () => {
        const remotes = Array.from(room.remoteParticipants.values());
        setRemoteParticipants([...remotes]);
      };

      room.on(RoomEvent.ParticipantConnected, updateRemotes);
      room.on(RoomEvent.ParticipantDisconnected, updateRemotes);
      // Consent badges come from server-written metadata.
      room.on(RoomEvent.ParticipantMetadataChanged, updateRemotes);

      // Event: Active Speakers
      room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        const ids = speakers.map((s) => s.identity);
        setActiveSpeakerIdentities(ids);
      });

      // 4. Connect to LiveKit WebRTC Signaling Server
      setConnectionState("connecting");
      await room.connect(serverUrl, token);

      // 5. Publish Media Tracks based on pre-join selections
      let micWorked = false;
      let camWorked = false;

      if (previewMic) {
        try {
          await room.localParticipant.setMicrophoneEnabled(true);
          micWorked = true;
        } catch (micErr) {
          console.warn("Could not enable microphone:", micErr);
          setPermissionError("Microphone permission denied or device not found.");
        }
      }

      if (previewCam) {
        try {
          await room.localParticipant.setCameraEnabled(true);
          camWorked = true;
        } catch (camErr) {
          console.warn("Could not enable camera:", camErr);
          setPermissionError((prev) =>
            prev ? `${prev} Camera permission denied.` : "Camera permission denied or device not found."
          );
        }
      }

      setMicEnabled(micWorked);
      setCamEnabled(camWorked);
      updateRemotes();
    } catch (err) {
      console.error("LiveKit connection error:", err);
      setIsConnecting(false);
      setConnectionState("disconnected");
      const rawMsg = err?.message || "";
      const displayMsg =
        rawMsg.includes("Failed to fetch") || rawMsg.includes("Cannot connect")
          ? "Could not reach backend API server at http://127.0.0.1:8000. Please ensure the FastAPI backend is running."
          : rawMsg || "Failed to connect to LiveKit room. Please check your credentials and internet connection.";
      setConnectionError(displayMsg);
    }
  };

  // --------------------------------------------------------------------------
  // Media Toggles
  // --------------------------------------------------------------------------
  const toggleMic = async () => {
    if (!roomRef.current?.localParticipant) return;
    try {
      const next = !micEnabled;
      await roomRef.current.localParticipant.setMicrophoneEnabled(next);
      setMicEnabled(next);
    } catch (err) {
      console.error("Failed to toggle microphone:", err);
      setPermissionError("Unable to access microphone. Please check browser permissions.");
    }
  };

  const toggleCam = async () => {
    if (!roomRef.current?.localParticipant) return;
    try {
      const next = !camEnabled;
      await roomRef.current.localParticipant.setCameraEnabled(next);
      setCamEnabled(next);
    } catch (err) {
      console.error("Failed to toggle camera:", err);
      setPermissionError("Unable to access camera. Please check browser permissions.");
    }
  };

  // --------------------------------------------------------------------------
  // Update Participant Consent
  // --------------------------------------------------------------------------
  // The server records the decision (and halts capture on revocation); the
  // toggle only reflects what the server confirmed.
  const toggleConsentInRoom = async () => {
    const identity = roomRef.current?.localParticipant?.identity;
    if (!identity) return;
    const next = !hasConsented;
    setRecError("");
    try {
      const res = await updateLiveConsent(
        liveRoomRef.current || roomCode,
        identity,
        next,
        tokenRef.current
      );
      setHasConsented(res?.consented === true);
      if (res?.recording) setRecState(res.recording);
    } catch (err) {
      setRecError(err?.message || "Could not update your consent on the server.");
    }
  };

  // --------------------------------------------------------------------------
  // Server-side Recording Controls (host)
  // --------------------------------------------------------------------------
  const runRecordingAction = async (action) => {
    if (!meetingId) return;
    setRecBusy(true);
    setRecError("");
    try {
      const state = await action(meetingId, tokenRef.current);
      setRecState(state);
    } catch (err) {
      const blocked = err?.detail?.blocked;
      const names = Array.isArray(blocked) ? blocked.map((p) => p.name || p.identity).join(", ") : "";
      setRecError(names ? `${err.message} Waiting for: ${names}.` : err?.message || "Recording request failed.");
      refreshRecording();
    } finally {
      setRecBusy(false);
    }
  };

  const handleStartRecording = () => runRecordingAction(startLiveRecording);
  const handleStopRecording = () => runRecordingAction(stopLiveRecording);
  const handleProcessRecording = (retry = false) => {
    processRequestedRef.current = true;
    return runRecordingAction((id, token) => processLiveRecording(id, token, { retry }));
  };

  // --------------------------------------------------------------------------
  // End or Leave Meeting
  // --------------------------------------------------------------------------
  const handleConfirmEnd = async () => {
    setShowEndModal(false);
    if (roomRef.current) {
      if (isHost) {
        // The server stops and finalizes any recording before closing the room.
        try {
          await endLiveRoom(liveRoomRef.current || roomCode, tokenRef.current);
        } catch (err) {
          console.warn("endLiveRoom backend warning:", err);
          setRecError(err?.message || "The server could not end the meeting.");
        }
      }
      roomRef.current.disconnect();
    }
    setViewState("ended");
  };

  // Consent statistics: prefer the server's roster, fall back to metadata
  // (which only the server writes) before the first state poll arrives.
  const serverRoster = (recState?.participants || []).filter((p) => p.connected);
  const totalCount = serverRoster.length || 1 + remoteParticipants.length;
  const consentedCount = serverRoster.length
    ? serverRoster.filter((p) => p.consented).length
    : (hasConsented ? 1 : 0) +
      remoteParticipants.filter((p) => {
        try {
          const m = p.metadata ? JSON.parse(p.metadata) : {};
          return m.consented === true;
        } catch {
          return false;
        }
      }).length;
  const consentPercentage = Math.round((consentedCount / totalCount) * 100);

  const recStatus = recState?.status || "idle";
  const captureLive = recStatus === "recording" || recStatus === "starting";
  const lastStopReason = recState?.last_stop_reason;
  const consentStopNotice =
    !captureLive && CONSENT_STOP_REASONS.includes(lastStopReason)
      ? `Recording stopped because ${stopReasonLabel(lastStopReason)}. It will not resume automatically — the host must start a new segment once everyone has consented.`
      : "";
  const blockedNames = (recState?.consent?.blocked || []).map((p) => p.name || p.identity);

  // ==========================================================================
  // VIEW 1: CREATE OR JOIN LIVE MEETING
  // ==========================================================================
  if (viewState === "create") {
    return (
      <div className="lm-create-view">
        <button
          type="button"
          className="lm-back-link"
          onClick={() => setMeetingMode(null)}
        >
          ← Back to Source Selection
        </button>

        <div className="lm-card">
          <div className="lm-card-header">
            <div>
              <span className="lm-badge lm-badge-phase">Phase 4.1 · LiveKit Real WebRTC</span>
              <h2 className="lm-card-title" style={{ marginTop: "8px" }}>
                {isJoinMode ? "Join Live Meeting" : "Create a Live Meeting"}
              </h2>
              <p className="lm-card-subtitle">
                {isJoinMode
                  ? "Enter your display name and confirm recording consent to join this LiveKit conference."
                  : "Configure your live conference room. LiveKit will connect multiple attendees with real WebRTC audio & video."}
              </p>
            </div>
            <div style={{ display: "flex", gap: "8px" }}>
              <button
                type="button"
                className="lm-topbar-btn"
                style={{ fontSize: "11px", padding: "4px 8px" }}
                onClick={() => {
                  const joining = !isJoinMode;
                  setIsJoinMode(joining);
                  // Joining never requests the host role.
                  setIsHost(!joining);
                  setConnectionError("");
                }}
              >
                {isJoinMode ? "Switch to Create Room" : "Have a Room Code? Join"}
              </button>
            </div>
          </div>

          {/* Who you will be in this meeting, before connecting. */}
          <div className={`lm-role-notice ${isJoinMode ? "is-guest" : "is-host"}`} data-testid="role-notice">
            <strong>{isJoinMode ? "You are joining as a participant." : "You will host this meeting."}</strong>{" "}
            {isJoinMode
              ? inviteTokenRef.current
                ? "Your invitation is applied when you join. The meeting's host is the account that created it; if the host is already in the room, you join alongside them."
                : authRequired
                  ? "With sign-in on, open the invite link the host copied from the meeting room. A room code alone does not grant access."
                  : "Enter the room code the host shared."
              : "You can copy an invite link for guests once the meeting has started."}
            {authRequired && signedInUser && (
              <span className="lm-role-account"> Signed in as {signedInUser.display_name} ({signedInUser.email}).</span>
            )}
          </div>

          {/* LiveKit Cloud Setup Notice */}
          {liveConfigNotice && (
            <div
              className="lm-banner-alert"
              style={{ background: "#fffbeb", borderColor: "#fde68a", color: "#92400e" }}
            >
              <div>
                <strong>Configuration Notice:</strong> {liveConfigNotice}
              </div>
            </div>
          )}

          {/* Connection Error Banner */}
          {connectionError && (
            <div className="lm-banner-alert">
              <div>
                <strong>Connection Error:</strong> {connectionError}
              </div>
              <button
                type="button"
                className="lm-copy-btn"
                style={{ color: "#991b1b" }}
                onClick={() => setConnectionError("")}
              >
                ✕
              </button>
            </div>
          )}

          {/* Permission Denied Warning */}
          {permissionError && (
            <div className="lm-banner-alert" style={{ background: "#fffbeb", borderColor: "#fde68a", color: "#92400e" }}>
              <div>
                <strong>Device Notice:</strong> {permissionError} You can still join in listen-only mode.
              </div>
            </div>
          )}

          <form onSubmit={handleStartOrJoinRoom}>
            <div className="lm-form-grid">
              <div className="lm-form-group">
                <label className="lm-label" htmlFor="lm-room-code">
                  Room Code / Identifier
                </label>
                <input
                  id="lm-room-code"
                  type="text"
                  className="lm-input"
                  value={roomCode}
                  onChange={(e) => setRoomCode(e.target.value)}
                  placeholder="e.g. sprint-planning-7489"
                  required
                />
              </div>

              <div className="lm-form-group">
                <label className="lm-label" htmlFor="lm-display-name">
                  Your Display Name
                </label>
                <input
                  id="lm-display-name"
                  type="text"
                  className="lm-input"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  required
                />
              </div>

              {!isJoinMode && (
                <div className="lm-form-group lm-field-full">
                  <label className="lm-label" htmlFor="lm-topic">
                    Meeting Topic / Purpose
                  </label>
                  <input
                    id="lm-topic"
                    type="text"
                    className="lm-input"
                    value={topic}
                    onChange={(e) => setTopic(e.target.value)}
                    required
                  />
                </div>
              )}

              <div className="lm-form-group lm-field-full">
                <label className="lm-label" htmlFor="lm-language">
                  Spoken Language Scope
                </label>
                <select
                  id="lm-language"
                  className="lm-select"
                  value={language}
                  onChange={(e) => setLanguage(e.target.value)}
                >
                  <option value="en">English (Primary)</option>
                  <option value="hi">Hindi (हिंदी)</option>
                  <option value="mr">Marathi (मराठी)</option>
                  <option value="code-switch">Hinglish / Code-Switching</option>
                </select>
              </div>
            </div>

            {/* Explicit Recording Consent Policy */}
            <div className="lm-consent-card">
              <input
                id="lm-consent-toggle"
                type="checkbox"
                className="lm-consent-checkbox"
                checked={hasConsented}
                onChange={(e) => setHasConsented(e.target.checked)}
              />
              <div className="lm-consent-text">
                <h4>
                  <label htmlFor="lm-consent-toggle" style={{ cursor: "pointer" }}>
                    Explicit Recording &amp; MoM Transcription Consent
                  </label>
                </h4>
                <p>
                  I consent to having my voice captured and transcribed for Minutes of Meeting generation.
                  Your consent status will be displayed to all participants in the room.
                </p>
              </div>
            </div>

            {/* Pre-join Media Device Controls */}
            <div className="lm-device-preview-bar">
              <div>
                <strong style={{ fontSize: "13px", color: "#1e293b", display: "block" }}>
                  Hardware Setup
                </strong>
                <span style={{ fontSize: "12px", color: "#64748b" }}>
                  Select initial microphone and camera state before joining
                </span>
              </div>

              <div className="lm-device-toggles">
                <button
                  type="button"
                  className={`lm-device-btn ${previewMic ? "active" : ""}`}
                  onClick={() => setPreviewMic(!previewMic)}
                >
                  {previewMic ? "🎙️ Mic On" : "🔇 Mic Muted"}
                </button>

                <button
                  type="button"
                  className={`lm-device-btn ${previewCam ? "active" : ""}`}
                  onClick={() => setPreviewCam(!previewCam)}
                >
                  {previewCam ? "📹 Camera On" : "🚫 Camera Off"}
                </button>
              </div>
            </div>

            {/* Shareable Invite Preview */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "24px" }}>
              <div className="lm-invite-preview">
                <span>Room: <strong>{roomCode}</strong></span>
                <span>·</span>
                <span style={{ fontFamily: "monospace", fontSize: "11px" }}>
                  {window.location.origin}/?room={encodeURIComponent(roomCode)}
                </span>
              </div>

              <button
                type="button"
                className="lm-copy-btn"
                onClick={handleCopyInvite}
                // With sign-in on, a working link carries an invitation, which
                // exists only once the meeting has been created.
                disabled={authRequired && !meetingId}
                title={authRequired && !meetingId ? "Start the meeting first; then copy the invite link from the room." : undefined}
              >
                {copyFeedback ? "✓ Link Copied!" : "Copy Invite Link"}
              </button>
            </div>

            <button
              type="submit"
              className="lm-start-btn"
              disabled={isConnecting}
              style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "10px" }}
            >
              {isConnecting ? (
                <>
                  <span className="lm-loading-spinner" />
                  Connecting to LiveKit...
                </>
              ) : (
                isJoinMode ? "🎙️ Join Live Meeting Room" : "🎙️ Create & Start Live Meeting Room"
              )}
            </button>
          </form>
        </div>
      </div>
    );
  }

  // ==========================================================================
  // VIEW 2: REAL LIVEKIT MEETING ROOM
  // ==========================================================================
  if (viewState === "room") {
    const isLocalSpeaking = activeSpeakerIdentities.includes(
      roomRef.current?.localParticipant?.identity
    );

    return (
      <div className="lm-room-view">
        {/* Reconnecting banner if WebRTC connection drops */}
        {connectionState === "reconnecting" && (
          <div className="lm-reconnecting-bar">
            <span className="lm-loading-spinner" style={{ borderTopColor: "#92400e" }} />
            Connection lost. Reconnecting to LiveKit Cloud...
          </div>
        )}

        {/* Top Header Bar */}
        <header className="lm-room-topbar">
          <div className="lm-topbar-left">
            <RecordingBadge recState={recState} />

            <div className="lm-topbar-title-block">
              <h3>{topic}</h3>
              <span className="lm-topbar-code">
                Room: {roomCode} · ⏱ {formatTime(elapsedSeconds)}
              </span>
            </div>
          </div>

          <div className="lm-topbar-center">
            {/* Real Consent Status Pill */}
            <div
              className="lm-consent-pill"
              title={`${consentedCount} of ${totalCount} attendees have consented to recording`}
              style={{
                background: consentPercentage === 100 ? "rgba(34, 197, 94, 0.15)" : "rgba(234, 179, 8, 0.15)",
                borderColor: consentPercentage === 100 ? "rgba(34, 197, 94, 0.35)" : "rgba(234, 179, 8, 0.35)",
                color: consentPercentage === 100 ? "#4ade80" : "#facc15",
              }}
            >
              <span>🛡️ Consent: {consentedCount}/{totalCount} Consented ({consentPercentage}%)</span>
            </div>
          </div>

          <div className="lm-topbar-right">
            {isHost && meetingId && (
              captureLive ? (
                <button
                  type="button"
                  className="lm-topbar-btn lm-rec-stop-btn"
                  onClick={handleStopRecording}
                  disabled={recBusy}
                >
                  {recBusy ? "Stopping…" : "■ Stop Recording"}
                </button>
              ) : (
                <button
                  type="button"
                  className="lm-topbar-btn lm-rec-start-btn"
                  onClick={handleStartRecording}
                  disabled={recBusy || !recState?.can_start}
                  title={
                    recState?.can_start
                      ? "Start a server-side recording segment"
                      : "Every connected participant must consent before recording"
                  }
                >
                  {recBusy
                    ? "Starting…"
                    : (recState?.segments?.length || 0) > 0
                      ? "● Start New Segment"
                      : "● Start Recording"}
                </button>
              )
            )}

            {(isHost || !authRequired) && (
              <button
                type="button"
                className="lm-topbar-btn"
                onClick={handleCopyInvite}
              >
                {copyFeedback ? "✓ Copied!" : "📋 Copy Invite Link"}
              </button>
            )}

            <span
              className="lm-badge"
              style={{
                background: connectionState === "connected" ? "rgba(34, 197, 94, 0.2)" : "rgba(234, 179, 8, 0.2)",
                color: connectionState === "connected" ? "#4ade80" : "#facc15",
                fontSize: "11px",
              }}
            >
              {connectionState === "connected" ? "● Connected" : connectionState}
            </span>

            <button
              type="button"
              className="lm-end-btn"
              onClick={() => setShowEndModal(true)}
            >
              {isHost ? "⏹ End Meeting" : "Leave Meeting"}
            </button>
          </div>
        </header>

        {/* Authoritative recording / consent notices from the server */}
        {(recError || consentStopNotice || (!captureLive && blockedNames.length > 0)) && (
          <div className="lm-rec-notices" role="status" aria-live="polite">
            {consentStopNotice && <div className="lm-rec-notice warn">{consentStopNotice}</div>}
            {!captureLive && blockedNames.length > 0 && (
              <div className="lm-rec-notice warn">
                Recording is blocked until these participants consent: {blockedNames.join(", ")}.
              </div>
            )}
            {recError && <div className="lm-rec-notice error">{recError}</div>}
          </div>
        )}

        {/* Room Body: Real WebRTC Participant Tiles Grid + Drawer */}
        <div className="lm-room-body">
          <main className="lm-grid-area" aria-label="Meeting Participants Grid">
            {/* 1. Local Participant Tile */}
            <LocalParticipantTile
              room={roomRef.current}
              name={displayName}
              isHost={isHost}
              consented={hasConsented}
              isSpeaking={isLocalSpeaking}
              micEnabled={micEnabled}
              camEnabled={camEnabled}
            />

            {/* 2. Remote Participants Tiles */}
            {remoteParticipants.map((p) => {
              const isRemoteSpeaking = activeSpeakerIdentities.includes(p.identity);
              return (
                <RemoteParticipantTile
                  key={p.identity}
                  participant={p}
                  isSpeaking={isRemoteSpeaking}
                />
              );
            })}

            {/* Empty state hint if only local user is in room */}
            {remoteParticipants.length === 0 && (
              <div
                className="lm-tile"
                style={{
                  border: "1.5px dashed rgba(255, 255, 255, 0.2)",
                  background: "rgba(15, 23, 42, 0.4)",
                  flexDirection: "column",
                  gap: "12px",
                  padding: "24px",
                  textAlign: "center",
                }}
              >
                <div style={{ fontSize: "32px" }}>👥</div>
                <div style={{ color: "#e2e8f0", fontWeight: 600, fontSize: "15px" }}>
                  Waiting for participants to join...
                </div>
                <div style={{ color: "#94a3b8", fontSize: "13px", maxWidth: "300px" }}>
                  {isHost || !authRequired
                    ? "Share the invite link. Each guest opens it in their own browser (or a private window) and signs in with their own account."
                    : "You are in the room. Others appear here when they join."}
                </div>
                {(isHost || !authRequired) && (
                  <button
                    type="button"
                    className="lm-topbar-btn"
                    onClick={handleCopyInvite}
                    style={{ marginTop: "4px" }}
                  >
                    {copyFeedback ? "✓ Copied!" : "📋 Copy Invite Link"}
                  </button>
                )}
              </div>
            )}
          </main>

          {/* Side Drawer */}
          {isDrawerOpen && (
            <aside className="lm-side-drawer">
              <div className="lm-drawer-tabs">
                <button
                  type="button"
                  className={`lm-drawer-tab ${drawerTab === "participants" ? "active" : ""}`}
                  onClick={() => setDrawerTab("participants")}
                >
                  👥 Attendees ({1 + remoteParticipants.length})
                </button>
                <button
                  type="button"
                  className={`lm-drawer-tab ${drawerTab === "info" ? "active" : ""}`}
                  onClick={() => setDrawerTab("info")}
                >
                  ℹ️ Room Details
                </button>
              </div>

              <div className="lm-drawer-content">
                {drawerTab === "participants" && (
                  <div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
                      <span style={{ fontSize: "11px", color: "#94a3b8" }}>
                        Consent Roster ({consentedCount}/{totalCount} Consented)
                      </span>
                      <button
                        type="button"
                        onClick={toggleConsentInRoom}
                        style={{
                          background: "none",
                          border: "none",
                          color: hasConsented ? "#4ade80" : "#f87171",
                          fontSize: "11px",
                          fontWeight: 600,
                          cursor: "pointer",
                        }}
                      >
                        {hasConsented ? "Revoke Consent" : "Grant Consent"}
                      </button>
                    </div>

                    {/* Local Participant Row */}
                    <div className="lm-attendee-row">
                      <div className="lm-attendee-info">
                        <div className="lm-attendee-avatar" style={{ background: "#2563eb" }}>
                          {displayName.slice(0, 2).toUpperCase()}
                        </div>
                        <div>
                          <div style={{ fontSize: "13px", fontWeight: 600 }}>{displayName} (You)</div>
                          <div style={{ fontSize: "11px", color: hasConsented ? "#4ade80" : "#f87171" }}>
                            {hasConsented ? "✓ Consented" : "Pending Consent"}
                          </div>
                        </div>
                      </div>
                      <div style={{ display: "flex", gap: "6px", fontSize: "13px" }}>
                        <span>{micEnabled ? "🎙️" : "🔇"}</span>
                        <span>{camEnabled ? "📹" : "🚫"}</span>
                      </div>
                    </div>

                    {/* Remote Participants Rows */}
                    {remoteParticipants.map((p) => {
                      let meta = {};
                      try {
                        meta = p.metadata ? JSON.parse(p.metadata) : {};
                      } catch {}
                      const isCons = meta.consented === true;
                      const pubs = Array.from(p.trackPublications?.values?.() || []);
                      const hasAud = pubs.some((pub) => pub.kind === Track.Kind.Audio && !pub.isMuted);
                      const hasVid = pubs.some((pub) => pub.kind === Track.Kind.Video && !pub.isMuted);

                      return (
                        <div key={p.identity} className="lm-attendee-row">
                          <div className="lm-attendee-info">
                            <div className="lm-attendee-avatar" style={{ background: "#059669" }}>
                              {(p.name || p.identity).slice(0, 2).toUpperCase()}
                            </div>
                            <div>
                              <div style={{ fontSize: "13px", fontWeight: 600 }}>
                                {p.name || p.identity}
                              </div>
                              <div style={{ fontSize: "11px", color: isCons ? "#4ade80" : "#facc15" }}>
                                {isCons ? "✓ Consented" : "Pending Consent"}
                              </div>
                            </div>
                          </div>
                          <div style={{ display: "flex", gap: "6px", fontSize: "13px" }}>
                            <span>{hasAud ? "🎙️" : "🔇"}</span>
                            <span>{hasVid ? "📹" : "🚫"}</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}

                {drawerTab === "info" && (
                  <div style={{ fontSize: "13px", lineHeight: 1.6, color: "#cbd5e1" }}>
                    <h4 style={{ margin: "0 0 8px", color: "#ffffff" }}>LiveKit Room Info</h4>
                    <p><strong>Room Code:</strong> {roomCode}</p>
                    <p><strong>Topic:</strong> {topic}</p>
                    <p><strong>Role:</strong> {isHost ? "Host (Room Admin)" : "Participant"}</p>
                    <p><strong>Signaling Status:</strong> {connectionState.toUpperCase()}</p>
                    <p><strong>Active Participants:</strong> {1 + remoteParticipants.length}</p>
                    <p><strong>Invite URL:</strong></p>
                    <code style={{ display: "block", background: "#0f172a", padding: "8px", borderRadius: "6px", wordBreak: "break-all", fontSize: "11px" }}>
                      {window.location.origin}/?room={encodeURIComponent(roomCode)}
                    </code>
                  </div>
                )}
              </div>
            </aside>
          )}
        </div>

        {/* Floating Bottom Control Dock */}
        <footer className="lm-dock">
          <button
            type="button"
            className={`lm-dock-btn ${!micEnabled ? "off" : ""}`}
            onClick={toggleMic}
            title={micEnabled ? "Mute Microphone" : "Unmute Microphone"}
            aria-label="Toggle Microphone"
          >
            {micEnabled ? "🎙️" : "🔇"}
          </button>

          <button
            type="button"
            className={`lm-dock-btn ${!camEnabled ? "off" : ""}`}
            onClick={toggleCam}
            title={camEnabled ? "Turn Off Camera" : "Turn On Camera"}
            aria-label="Toggle Camera"
          >
            {camEnabled ? "📹" : "🚫"}
          </button>

          <button
            type="button"
            className="lm-dock-btn"
            disabled
            title="Screen Sharing (Milestone 4.2)"
            aria-label="Screen Share"
          >
            🖥️
          </button>

          <button
            type="button"
            className={`lm-dock-btn ${isDrawerOpen ? "active" : ""}`}
            onClick={() => setIsDrawerOpen(!isDrawerOpen)}
            title="Toggle Attendees Drawer"
            aria-label="Toggle Side Drawer"
          >
            👥
          </button>

          <button
            type="button"
            className="lm-end-btn"
            onClick={() => setShowEndModal(true)}
            style={{ marginLeft: "12px", height: "46px" }}
          >
            {isHost ? "⏹ End Meeting" : "Leave Meeting"}
          </button>
        </footer>

        {/* End Meeting Confirmation Modal */}
        {showEndModal && (
          <div className="lm-modal-overlay" role="dialog" aria-modal="true" aria-labelledby="lm-modal-title">
            <div className="lm-modal">
              <div className="lm-modal-icon">⏹</div>
              <h3 id="lm-modal-title">
                {isHost ? "End Live Meeting for All?" : "Leave Meeting?"}
              </h3>
              <p>
                {isHost
                  ? "Ending the meeting will disconnect all participants and finalize the live session for MoM review."
                  : "Are you sure you want to leave this LiveKit meeting?"}
              </p>
              <div className="lm-modal-actions">
                <button
                  type="button"
                  className="lm-modal-btn cancel"
                  onClick={() => setShowEndModal(false)}
                >
                  Stay in Meeting
                </button>
                <button
                  type="button"
                  className="lm-modal-btn confirm-end"
                  onClick={handleConfirmEnd}
                >
                  {isHost ? "End for Everyone" : "Leave Room"}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    );
  }

  // ==========================================================================
  // VIEW 3: MEETING-ENDED, RECORDING FINALIZATION & PROCESSING HAND-OFF
  // ==========================================================================
  const artifact = recState?.artifact;
  const processing = recState?.processing;
  const segments = recState?.segments || [];
  const statusLabels = {
    idle: "No recording",
    starting: "Starting",
    recording: "Recording",
    stopping: "Finalizing audio…",
    recorded: "Recorded",
    processing: "Processing",
    completed: "Processed",
    failed: "Failed",
  };
  const endedSubtitle = (() => {
    if (!recState) return "Checking the recording status on the server…";
    if (recStatus === "stopping") return "The server is finalizing the recorded audio.";
    if (recStatus === "processing") return "The recording is being transcribed, analysed and verified.";
    if (recStatus === "completed") return "Processing finished. The meeting is ready for review.";
    if (recStatus === "failed") return "Processing or recording failed. See the details below.";
    if (artifact) return "The server-side recording is finalized and ready to process.";
    return "No consented audio was recorded in this meeting, so there is nothing to process.";
  })();

  return (
    <div className="lm-ended-view">
      <div className="lm-ended-header">
        <div className="lm-ended-icon">⏹</div>
        <span className="lm-badge lm-badge-phase" style={{ marginBottom: "10px" }}>
          Phase 4.2 · LiveKit Session Concluded
        </span>
        <h2 className="lm-ended-title">Live Meeting Ended</h2>
        <p className="lm-ended-subtitle">{endedSubtitle}</p>
      </div>

      {/* Session Metadata Card (server-reported) */}
      <div className="lm-session-card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <h3 style={{ margin: "0 0 6px", color: "#202858", fontSize: "18px" }}>{topic}</h3>
            <span style={{ fontSize: "13px", color: "#64748b" }}>
              Room ID: <code style={{ background: "#f1f5f9", padding: "2px 6px", borderRadius: "4px" }}>{roomCode}</code>
            </span>
          </div>
          <span className="lm-badge lm-badge-consent">
            {segments.length} recording segment{segments.length === 1 ? "" : "s"}
          </span>
        </div>

        <div className="lm-session-metrics">
          <div className="lm-metric-box">
            <div className="lm-metric-label">Meeting Duration</div>
            <div className="lm-metric-value">{formatTime(elapsedSeconds)}</div>
          </div>
          <div className="lm-metric-box">
            <div className="lm-metric-label">Recorded Audio</div>
            <div className="lm-metric-value">
              {artifact ? formatTime(artifact.duration_seconds) : "None"}
            </div>
          </div>
          <div className="lm-metric-box">
            <div className="lm-metric-label">Captured Speakers</div>
            <div className="lm-metric-value" style={{ fontSize: "13px" }}>
              {artifact?.participants?.length ? artifact.participants.join(", ") : "—"}
            </div>
          </div>
          <div className="lm-metric-box">
            <div className="lm-metric-label">Recording Status</div>
            <div className="lm-metric-value" data-testid="ended-rec-status">
              {statusLabels[recStatus] || recStatus}
            </div>
          </div>
        </div>

        {segments.length > 0 && (
          <ul className="lm-segment-list">
            {segments.map((seg) => (
              <li key={seg.segment_id}>
                <strong>Segment {seg.seq}</strong> · {seg.state}
                {seg.duration_seconds ? ` · ${formatTime(seg.duration_seconds)}` : ""}
                {seg.stop_reason ? ` · ${stopReasonLabel(seg.stop_reason)}` : ""}
                {seg.error ? ` · ${seg.error}` : ""}
              </li>
            ))}
          </ul>
        )}
      </div>

      {processing && (
        <div className="lm-stepper-card">
          <h3>Pipeline Processing</h3>
          <p style={{ margin: "0 0 8px", color: "#475569", fontSize: "14px" }}>
            Stage: <strong>{processing.stage}</strong> · {Math.round((processing.progress || 0) * 100)}% · {processing.state}
          </p>
          <div className="lm-progress-track">
            <div className="lm-progress-fill" style={{ width: `${Math.round((processing.progress || 0) * 100)}%` }} />
          </div>
          {processing.error && (
            <div className="lm-rec-notice error" style={{ marginTop: "10px" }}>{processing.error}</div>
          )}
          <StageTimings timings={processing.stage_timings} />
        </div>
      )}

      {recError && <div className="lm-rec-notice error">{recError}</div>}

      <div className="lm-ended-actions">
        {isHost && recState?.can_process && (
          <button
            type="button"
            className="lm-proceed-btn"
            onClick={() => handleProcessRecording(false)}
            disabled={recBusy}
          >
            {recBusy ? "Submitting…" : "⚙️ Process Meeting"}
          </button>
        )}

        {recStatus === "processing" && (
          <button type="button" className="lm-proceed-btn" disabled>
            Processing… ({processing?.stage || "queued"})
          </button>
        )}

        {recStatus === "completed" && (
          <button
            type="button"
            className="lm-proceed-btn"
            onClick={() => loadExistingMeeting(meetingId)}
          >
            🔍 Open Meeting Review →
          </button>
        )}

        {isHost && processing?.state === "failed" && (
          <button
            type="button"
            className="lm-proceed-btn"
            onClick={() => handleProcessRecording(true)}
            disabled={recBusy}
          >
            ↻ Retry Processing
          </button>
        )}

        {!isHost && recState?.can_process && (
          <p className="lm-ended-subtitle">The host can now process the recording.</p>
        )}

        <div className="lm-secondary-actions">
          <button
            type="button"
            className="lm-secondary-btn"
            onClick={() => {
              setViewState("create");
              setMeetingMode(null);
            }}
          >
            ← Back to Source Selection
          </button>
        </div>
      </div>
    </div>
  );
}
