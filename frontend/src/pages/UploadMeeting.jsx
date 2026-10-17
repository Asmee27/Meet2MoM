import { useEffect, useState } from "react";
import { useMeetingFlow } from "../context/MeetingFlowContext";
import { listMeetings } from "../services/api";

// Ordered pipeline stages matching app/core/jobs.py
const PIPELINE_STAGES = [
  "uploaded",
  "audio",
  "quality",
  "asr",
  "diarization",
  "merge",
  "index",
  "topics",
  "extraction",
  "verification",
  "mom",
  "done",
];

const STAGE_LABELS = {
  uploaded:     "Recording received",
  audio:        "Extracting audio",
  quality:      "Checking audio quality",
  asr:          "Speech-to-text (ASR)",
  diarization:  "Speaker diarization",
  merge:        "Merging transcript",
  index:        "Building utterance index",
  topics:       "Segmenting topics",
  extraction:   "Extracting decisions & actions",
  verification: "Verifying against transcript",
  mom:          "Generating minutes",
  done:         "Processing complete",
};

export default function UploadMeeting() {
  const [previousMeetings, setPreviousMeetings] = useState([]);

  const {
    selectedFile,
    error,
    isDragging,
    uploadProgress,
    isUploading,
    fileInputRef,
    backendFileId,
    jobStage,
    jobError,
    jobStatus,
    transcriptionComplete,
    validateFile,
    handleFileChange,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    removeFile,
    goToTranscribe,
    loadExistingMeeting,
    currentStep,
  } = useMeetingFlow();

  useEffect(() => {
    let active = true;
    listMeetings()
      .then((meetings) => {
        if (active) setPreviousMeetings(Array.isArray(meetings) ? meetings : []);
      })
      .catch(() => {
        if (active) setPreviousMeetings([]);
      });
    return () => {
      active = false;
    };
  }, []);

  const currentStageIndex = PIPELINE_STAGES.indexOf(jobStage);
  const isPipelineRunning =
    backendFileId &&
    !isUploading &&
    jobStage !== "done" &&
    !jobError;

  return (
    <>
      {currentStep === 1 && (

        <main className="main-content">

          <div className="step-label">
            STEP 1 · MEETING ACQUISITION
          </div>

          <h1>
            Upload a meeting recording
          </h1>

          <div
            className={`upload-card ${
              isDragging ? "dragging" : ""
            }`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            role="button"
            tabIndex="0"
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                fileInputRef.current?.click();
              }
            }}
          >
            <div className="upload-cloud-icon" aria-hidden="true">
              <svg viewBox="0 0 64 64" fill="none">
                <path
                  d="M20 48h27a11 11 0 0 0 1.8-21.85A16.5 16.5 0 0 0 17.5 24.8 12 12 0 0 0 20 48Z"
                  stroke="currentColor"
                  strokeWidth="4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <path
                  d="M32 40V25m0 0-7 7m7-7 7 7"
                  stroke="currentColor"
                  strokeWidth="4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>

            <div className="upload-main-text">
              <strong>Drag &amp; drop, or browse a file</strong>
            </div>

            <div className="upload-support-text">
              Supports .mp4 · .wav · .mp3 · Zoom · Teams · Google Meet
            </div>

            <input
              ref={fileInputRef}
              type="file"
              id="file-upload"
              accept=".mp4,.wav,.mp3"
              onChange={handleFileChange}
              hidden
            />
          </div>

          {previousMeetings.length > 0 && (
            <section className="previous-meetings" aria-labelledby="previous-meetings-title">
              <h2 id="previous-meetings-title">Or open a previous meeting</h2>
              <div className="previous-meeting-list">
                {previousMeetings.map((meeting) => (
                  <button
                    type="button"
                    className="previous-meeting-row"
                    key={meeting.meeting_id}
                    onClick={() => loadExistingMeeting(meeting.meeting_id)}
                  >
                    <span>
                      <strong>{meeting.meeting_id}</strong>
                      {meeting.filename && <small>{meeting.filename}</small>}
                    </span>
                    <span className="previous-meeting-counts">
                      {meeting.utterance_count} utterances · {meeting.claim_count} claims
                    </span>
                  </button>
                ))}
              </div>
            </section>
          )}

          {error && (
            <div className="error-message">
              {error}
            </div>
          )}

          {selectedFile && (
            <div className="selected-file">
              <div className="file-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none">
                  <path
                    d="M6 3.5h7.2L18.5 8v12.5H6V3.5Z"
                    stroke="currentColor"
                    strokeWidth="1.7"
                    strokeLinejoin="round"
                  />
                  <path
                    d="M13 3.5V8h5.5"
                    stroke="currentColor"
                    strokeWidth="1.7"
                    strokeLinejoin="round"
                  />
                </svg>
              </div>

              <div className="file-info">
                <strong>{selectedFile.name}</strong>
                <span>Meeting recording · {(selectedFile.size / (1024 * 1024)).toFixed(2)} MB</span>
                <div className="upload-progress-track" aria-label={`Upload progress ${uploadProgress}%`}>
                  <div
                    className="upload-progress-fill"
                    style={{ width: `${uploadProgress}%` }}
                  ></div>
                </div>
              </div>

              <div className={`upload-status ${isUploading ? "uploading" : "uploaded"}`}>
                {isUploading ? "Uploading..." : "Uploaded"}
              </div>

              {!isUploading && (
                <button
                  type="button"
                  className="remove-button"
                  onClick={removeFile}
                  aria-label="Remove selected file"
                >
                  Remove
                </button>
              )}
            </div>
          )}

          {/* ── Pipeline stage progress ── */}
          {isPipelineRunning && (
            <div className="pipeline-progress" style={{ marginTop: "1.5rem" }}>
              <div style={{ fontWeight: 600, marginBottom: "0.75rem", fontSize: "0.9rem", opacity: 0.8 }}>
                Processing recording…
              </div>
              <ol style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {PIPELINE_STAGES.filter(s => s !== "done").map((stage, idx) => {
                  const isDone = idx < currentStageIndex;
                  const isActive = idx === currentStageIndex;
                  return (
                    <li
                      key={stage}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "0.6rem",
                        padding: "0.25rem 0",
                        opacity: isDone || isActive ? 1 : 0.35,
                        fontSize: "0.85rem",
                      }}
                    >
                      <span style={{
                        width: "1.1rem",
                        height: "1.1rem",
                        borderRadius: "50%",
                        border: `2px solid ${isDone ? "#22c55e" : isActive ? "#6366f1" : "#94a3b8"}`,
                        background: isDone ? "#22c55e" : "transparent",
                        flexShrink: 0,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        color: "white",
                        fontSize: "0.65rem",
                      }}>
                        {isDone ? "✓" : isActive ? "●" : ""}
                      </span>
                      <span style={{ color: isActive ? "#6366f1" : undefined, fontWeight: isActive ? 600 : undefined }}>
                        {STAGE_LABELS[stage] || stage}
                      </span>
                    </li>
                  );
                })}
              </ol>
            </div>
          )}

          {/* ── Job error with retry ── */}
          {jobError && (
            <div className="upload-error" role="alert" style={{ marginTop: "1rem" }}>
              <strong>Processing failed at stage "{jobStage}":</strong>
              <div style={{ marginTop: "0.35rem", fontSize: "0.85rem" }}>{jobError}</div>
              <button
                type="button"
                className="remove-button"
                style={{ marginTop: "0.5rem" }}
                onClick={removeFile}
              >
                Retry with a new file
              </button>
            </div>
          )}

          {selectedFile && !isUploading && (

            <div className="continue-container">

              {/* Wait for the background job: continuing earlier would start a
                  second, concurrent pipeline run for the same recording. */}
              <button
                type="button"
                className="continue-button"
                onClick={goToTranscribe}
                disabled={isPipelineRunning}
                title={isPipelineRunning ? "Processing is still running" : undefined}
              >
                {isPipelineRunning ? "Processing…" : "Continue to Transcribe →"}
              </button>

            </div>

          )}

        </main>

      )}
    </>
  );
}
