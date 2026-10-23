import "./App.css";

import { useState } from "react";

import Header from "./components/Header";
import AuthGate from "./components/AuthGate";
import {
  MeetingFlowProvider,
  useMeetingFlow,
} from "./context/MeetingFlowContext";

import UploadMeeting from "./pages/UploadMeeting";
import LiveMeeting from "./pages/LiveMeeting";
import Transcript from "./pages/Transcript";
import Decisions from "./pages/Decisions";
import VerifyMeeting from "./pages/VerifyMeeting";
import Summary from "./pages/Summary";

function MeetingFlow() {
  const { currentStep, meetingMode, setMeetingMode } = useMeetingFlow();

  // If opening a shared invite link (e.g. ?room=xyz), auto-switch to Live Meeting mode
  useState(() => {
    try {
      const params = new URLSearchParams(window.location.search);
      if (params.get("room")) {
        setMeetingMode("live");
      }
    } catch {}
  });

  if (currentStep === 1 && !meetingMode) {
    return (
      <div className="app">
        <Header />

        <div className="meeting-mode-container">
          <h2>Choose Meeting Source</h2>
          <p>Select how you want to generate the Minutes of Meeting.</p>

          <div className="meeting-mode-grid">
            <button
              className="meeting-mode-card"
              onClick={() => setMeetingMode("recorded")}
            >
              <div className="meeting-mode-icon">📁</div>
              <h3>Upload Recorded Meeting</h3>
              <p>Upload MP4, WAV or MP3 meeting recordings.</p>
            </button>

            <button
              className="meeting-mode-card live-card"
              onClick={() => setMeetingMode("live")}
            >
              <div className="meeting-mode-icon">🎙️</div>
              <h3>Start Live Meeting</h3>
              <p>Capture meeting audio live and generate MoM.</p>
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <Header />

      {currentStep === 1 && meetingMode === "recorded" && (
        <>
          <button
            className="back-mode-btn"
            onClick={() => setMeetingMode(null)}
          >
            ← Back
          </button>

          <UploadMeeting />
        </>
      )}

      {currentStep === 1 && meetingMode === "live" && (
        <>
          <button
            className="back-mode-btn"
            onClick={() => setMeetingMode(null)}
          >
            ← Back
          </button>

          <LiveMeeting />
        </>
      )}

      {currentStep === 2 && <Transcript />}
      {currentStep === 3 && <Decisions />}
      {currentStep === 4 && <VerifyMeeting />}
      {currentStep === 5 && <Summary />}
    </div>
  );
}

export default function App() {
  return (
    <AuthGate>
      <MeetingFlowProvider>
        <MeetingFlow />
      </MeetingFlowProvider>
    </AuthGate>
  );
}