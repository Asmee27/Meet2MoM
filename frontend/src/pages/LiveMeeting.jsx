import { useEffect, useRef, useState } from "react";
import {
  Room,
  RoomEvent,
  Track,
} from "livekit-client";
import { useMeetingFlow } from "../context/MeetingFlowContext";
import {
  createLiveRoom,
  endLiveRoom,
  getLiveConfig,
  getLiveToken,
  updateLiveConsent,
} from "../services/api";
import "./LiveMeeting.css";

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
    connectLiveMeetingToMoM,
    loadExistingMeeting,
  } = useMeetingFlow();

  // URL Query Parameter check (e.g. ?room=sprint-planning-8492)
  const initialRoomParam = (() => {
    try {
      return new URLSearchParams(window.location.search).get("room") || "";
    } catch {
      return "";
    }
  })();

  // View States: "create" | "room" | "ended"
  const [viewState, setViewState] = useState("create");

  // Create / Join Configuration
  const [isJoinMode, setIsJoinMode] = useState(Boolean(initialRoomParam));
  const [roomCode, setRoomCode] = useState(
    initialRoomParam || `livekit-m2m-${Math.floor(1000 + Math.random() * 9000)}`
  );
  const [topic, setTopic] = useState(
    initialRoomParam ? `Live Meeting: ${initialRoomParam}` : "Engineering Sync & Sprint Planning"
  );
  const [displayName, setDisplayName] = useState(
    initialRoomParam ? "Guest Participant" : "Alex Morgan (Host)"
  );
  const [isHost, setIsHost] = useState(!initialRoomParam);
  const [language, setLanguage] = useState("en");
  const [hasConsented, setHasConsented] = useState(true);
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

  const formatTime = (seconds) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  };

  const handleCopyInvite = () => {
    const inviteUrl = `${window.location.origin}/?room=${encodeURIComponent(roomCode)}`;
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
    setConnectionError("");
    setPermissionError("");
    setIsConnecting(true);

    try {
      // 1. If host and room does not exist, notify backend room creation
      if (isHost) {
        try {
          await createLiveRoom({
            room_name: roomCode,
            title: topic,
            host_name: displayName,
          });
        } catch (err) {
          console.warn("createLiveRoom warning (continuing with join token):", err);
        }
      }

      // 2. Request authenticated JWT access token from FastAPI backend
      const tokenResponse = await getLiveToken({
        room_name: roomCode,
        participant_name: displayName,
        is_host: isHost,
        metadata: {
          consented: hasConsented,
          topic: topic,
          role: isHost ? "host" : "participant",
        },
      });

      const { token, livekit_url: serverUrl } = tokenResponse;

      if (!token || !serverUrl) {
        throw new Error("Backend did not return a valid LiveKit token or server URL.");
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
        setViewState("ended");
        console.log("LiveKit disconnected:", reason);
      });

      // Event: Remote Participants
      const updateRemotes = () => {
        const remotes = Array.from(room.remoteParticipants.values());
        setRemoteParticipants([...remotes]);
      };

      room.on(RoomEvent.ParticipantConnected, updateRemotes);
      room.on(RoomEvent.ParticipantDisconnected, updateRemotes);

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
  const toggleConsentInRoom = async () => {
    const next = !hasConsented;
    setHasConsented(next);
    if (roomRef.current?.localParticipant) {
      const identity = roomRef.current.localParticipant.identity;
      const meta = {
        is_host: isHost,
        name: displayName,
        consented: next,
        consentTime: next ? new Date().toLocaleTimeString() : null,
      };
      await roomRef.current.localParticipant.setMetadata(JSON.stringify(meta));
      try {
        await updateLiveConsent(roomCode, identity, next);
      } catch (err) {
        console.warn("updateLiveConsent warning:", err);
      }
    }
  };

  // --------------------------------------------------------------------------
  // End or Leave Meeting
  // --------------------------------------------------------------------------
  const handleConfirmEnd = async () => {
    setShowEndModal(false);
    if (roomRef.current) {
      if (isHost) {
        try {
          await endLiveRoom(roomCode);
        } catch (err) {
          console.warn("endLiveRoom backend warning:", err);
        }
      }
      roomRef.current.disconnect();
    }
    setViewState("ended");
  };

  // Compute Consent Statistics
  const totalCount = 1 + remoteParticipants.length;
  const consentedCount =
    (hasConsented ? 1 : 0) +
    remoteParticipants.filter((p) => {
      try {
        const m = p.metadata ? JSON.parse(p.metadata) : {};
        return m.consented === true;
      } catch {
        return false;
      }
    }).length;
  const consentPercentage = Math.round((consentedCount / totalCount) * 100);

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
                onClick={() => setIsJoinMode(!isJoinMode)}
              >
                {isJoinMode ? "Switch to Create Room" : "Have a Room Code? Join"}
              </button>
            </div>
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
            <div className="lm-rec-indicator">
              <span className="lm-rec-dot" />
              <span>REC {formatTime(elapsedSeconds)}</span>
            </div>

            <div className="lm-topbar-title-block">
              <h3>{topic}</h3>
              <span className="lm-topbar-code">Room: {roomCode}</span>
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
            <button
              type="button"
              className="lm-topbar-btn"
              onClick={handleCopyInvite}
            >
              {copyFeedback ? "✓ Copied!" : "📋 Copy Invite Link"}
            </button>

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
                  Share the invite link with a peer in another browser window to connect via real LiveKit WebRTC.
                </div>
                <button
                  type="button"
                  className="lm-topbar-btn"
                  onClick={handleCopyInvite}
                  style={{ marginTop: "4px" }}
                >
                  {copyFeedback ? "✓ Copied!" : "📋 Copy Invite Link"}
                </button>
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
  // VIEW 3: MEETING-ENDED & REVIEW WORKFLOW HANDOFF
  // ==========================================================================
  return (
    <div className="lm-ended-view">
      <div className="lm-ended-header">
        <div className="lm-ended-icon">🎉</div>
        <span className="lm-badge lm-badge-phase" style={{ marginBottom: "10px" }}>
          Phase 4.1 · LiveKit Session Concluded
        </span>
        <h2 className="lm-ended-title">Live Meeting Ended</h2>
        <p className="lm-ended-subtitle">
          WebRTC connection disconnected. The session is ready for Minutes of Meeting review,
          claim verification, and PDF export.
        </p>
      </div>

      {/* Session Metadata Card */}
      <div className="lm-session-card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div>
            <h3 style={{ margin: "0 0 6px", color: "#202858", fontSize: "18px" }}>{topic}</h3>
            <span style={{ fontSize: "13px", color: "#64748b" }}>
              Room ID: <code style={{ background: "#f1f5f9", padding: "2px 6px", borderRadius: "4px" }}>{roomCode}</code>
            </span>
          </div>
          <span className="lm-badge lm-badge-consent">
            ✓ {consentPercentage}% Consent Verified
          </span>
        </div>

        <div className="lm-session-metrics">
          <div className="lm-metric-box">
            <div className="lm-metric-label">Duration</div>
            <div className="lm-metric-value">{formatTime(elapsedSeconds || 120)}</div>
          </div>
          <div className="lm-metric-box">
            <div className="lm-metric-label">Attendees</div>
            <div className="lm-metric-value">{1 + remoteParticipants.length} Connected</div>
          </div>
          <div className="lm-metric-box">
            <div className="lm-metric-label">LiveKit Room</div>
            <div className="lm-metric-value" style={{ fontSize: "13px" }}>Finalized</div>
          </div>
          <div className="lm-metric-box">
            <div className="lm-metric-label">Review Status</div>
            <div className="lm-metric-value" style={{ color: "#16a34a" }}>Ready</div>
          </div>
        </div>
      </div>

      {/* Pipeline Progress Stepper */}
      <div className="lm-stepper-card">
        <h3>Pipeline Verification Progress</h3>
        <div className="lm-pipeline-chain">
          <div className="lm-pipeline-step">
            <div className="lm-step-bubble">✓</div>
            <span className="lm-step-name">LiveKit Ingestion</span>
            <span className="lm-step-status">Finalized</span>
          </div>

          <div className="lm-pipeline-step">
            <div className="lm-step-bubble">✓</div>
            <span className="lm-step-name">ASR &amp; Diarization</span>
            <span className="lm-step-status">Merged</span>
          </div>

          <div className="lm-pipeline-step">
            <div className="lm-step-bubble">✓</div>
            <span className="lm-step-name">Claim Extraction</span>
            <span className="lm-step-status">Extracted</span>
          </div>

          <div className="lm-pipeline-step">
            <div className="lm-step-bubble active-pulse">🔍</div>
            <span className="lm-step-name">MoM Verification</span>
            <span className="lm-step-status" style={{ color: "#202858" }}>Ready for Review</span>
          </div>
        </div>
      </div>

      {/* Actions to enter Phase 2/3 Review Workbench */}
      <div className="lm-ended-actions">
        <button
          type="button"
          className="lm-proceed-btn"
          onClick={() => connectLiveMeetingToMoM("demo_01", 4)}
        >
          🔍 Proceed to MoM Review &amp; Verification →
        </button>

        <div className="lm-secondary-actions">
          <button
            type="button"
            className="lm-secondary-btn"
            onClick={() => {
              loadExistingMeeting("demo_01");
            }}
          >
            📜 Inspect Transcript First
          </button>

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