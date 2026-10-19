import { useEffect, useState } from "react";
import { useMeetingFlow } from "../context/MeetingFlowContext";
import { getTranscript } from "../services/api";

export default function Transcript() {
  const {
    currentStep,
    backendFileId,
    transcriptSegments,
    transcriptText,
    detectedLanguage,
    transcriptionError,
    transcriptionProgress,
    isTranscribing,
    transcriptionComplete,
    isUploading,
    isExtracting,
    isGeneratingMom,
    isDownloadingPdf,
    jobStage,
    goBack,
    goToExtract,
  } = useMeetingFlow();

  const [utterances, setUtterances] = useState([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [fetchError, setFetchError] = useState("");
  const [isFetching, setIsFetching] = useState(false);

  // Fetch utterances from DB once the index stage is reached
  useEffect(() => {
    if (currentStep !== 2 || !backendFileId) return;

    const INDEXED_STAGES = [
      "index", "topics", "extraction", "verification", "mom", "done",
    ];
    const indexReached = INDEXED_STAGES.includes(jobStage);

    if (!indexReached) return;

    setIsFetching(true);
    setFetchError("");

    getTranscript(backendFileId)
      .then((data) => {
        if (Array.isArray(data)) {
          setUtterances(data);
        }
      })
      .catch((err) => {
        setFetchError(err?.message || "Could not load utterances.");
      })
      .finally(() => setIsFetching(false));
  }, [currentStep, backendFileId, jobStage]);

  // Determine what to render in the transcript panel
  const displayUtterances = utterances.length > 0 ? utterances : null;
  const displaySegments = displayUtterances ? null : transcriptSegments;

  const filteredUtterances = displayUtterances
    ? displayUtterances.filter((u) =>
        (u.text || "").toLowerCase().includes(searchQuery.toLowerCase()) ||
        (u.speaker_id || "").toLowerCase().includes(searchQuery.toLowerCase()) ||
        (u.speaker_name || "").toLowerCase().includes(searchQuery.toLowerCase())
      )
    : null;

  const filteredSegments = displaySegments
    ? displaySegments.filter((s) =>
        (s.text || "").toLowerCase().includes(searchQuery.toLowerCase())
      )
    : null;

  const formatTime = (seconds) => {
    if (seconds == null) return "";
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  };

  return (
    <>
      {currentStep === 2 && (
        <main className="main-content">

          <button
            type="button"
            className="back-button"
            onClick={goBack}
            disabled={
              isUploading ||
              isTranscribing ||
              isExtracting ||
              isGeneratingMom ||
              isDownloadingPdf
            }
          >
            <span aria-hidden="true">←</span>
            Back
          </button>

          <div className="step-label">
            STEP 2 · SPEECH-TO-TEXT &amp; DIARIZATION
          </div>

          <h1>Transcribing &amp; identifying speakers</h1>

          <div className="diarization-layout">

            <div className="processing-panel">
              <div className="processing-title">
                <span className="processing-dot"></span>
                {isTranscribing ? "Processing audio" : "Audio processed"}
              </div>

              <div
                className={`waveform ${
                  transcriptionProgress >= 80 ? "waveform-complete" : ""
                }`}
                aria-label={`Transcription progress ${transcriptionProgress}%`}
              >
                {Array.from({ length: 32 }).map((_, index) => {
                  const heights = [
                    20, 27, 34, 42, 55, 64, 72, 82,
                    68, 76, 88, 78, 66, 55, 43, 35,
                    42, 52, 63, 73, 82, 72, 60, 48,
                    39, 47, 58, 68, 77, 68, 55, 40
                  ];
                  return (
                    <span
                      key={index}
                      className="wave-bar"
                      style={{ height: `${heights[index]}px` }}
                    ></span>
                  );
                })}
              </div>

              <div className="speaker-identification">
                {isTranscribing && (
                  <div className="identified-speaker speaker-one">
                    <span></span>
                    Transcribing audio...
                  </div>
                )}
                {transcriptionComplete && (
                  <>
                    <div className="identified-speaker speaker-one">
                      <span></span>
                      Speech-to-text complete
                    </div>
                    <div className="identified-speaker speaker-two">
                      <span></span>
                      {utterances.length > 0
                        ? `${utterances.length} utterances indexed`
                        : "Speaker diarization pending"}
                    </div>
                  </>
                )}
              </div>

              {detectedLanguage && (
                <div className="detected-language">
                  Detected language: {detectedLanguage.toUpperCase()}
                </div>
              )}
            </div>

            {/* ── Live / indexed transcript panel ── */}
            <div className="live-transcript-panel">

              <div className="live-transcript-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span>{utterances.length > 0 ? "Indexed transcript" : "Live transcript"}</span>
                {(utterances.length > 0 || transcriptSegments.length > 0) && (
                  <input
                    type="search"
                    placeholder="Search…"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    style={{
                      fontSize: "0.78rem",
                      padding: "0.25rem 0.5rem",
                      borderRadius: "4px",
                      border: "1px solid #e2e8f0",
                      background: "transparent",
                      color: "inherit",
                      outline: "none",
                      width: "140px",
                    }}
                  />
                )}
              </div>

              <div className="live-transcript-content">

                {isTranscribing && transcriptSegments.length === 0 && (
                  <div className="live-transcript-line reveal-line">
                    <span className="speaker-pill speaker-pill-one">
                      PROCESSING
                    </span>
                    <span>
                      Transcribing the meeting audio with the speech-to-text engine...
                    </span>
                  </div>
                )}

                {isFetching && (
                  <div className="live-transcript-line reveal-line">
                    <span className="speaker-pill speaker-pill-one">INFO</span>
                    <span>Loading indexed utterances…</span>
                  </div>
                )}

                {fetchError && (
                  <div className="live-transcript-line reveal-line">
                    <span className="speaker-pill speaker-pill-three">WARN</span>
                    <span>{fetchError}</span>
                  </div>
                )}

                {/* DB utterances */}
                {filteredUtterances && filteredUtterances.map((u, index) => (
                  <div
                    className="live-transcript-line reveal-line"
                    key={u.utt_id || index}
                  >
                    <span
                      className={`speaker-pill ${
                        index % 3 === 0
                          ? "speaker-pill-one"
                          : index % 3 === 1
                            ? "speaker-pill-two"
                            : "speaker-pill-three"
                      }`}
                      title={u.speaker_id}
                    >
                      {u.speaker_name || u.speaker_id || "SPK"}
                    </span>
                    <span>
                      <small style={{ opacity: 0.55, marginRight: "6px" }}>
                        [{u.utt_id}] {formatTime(u.start)}–{formatTime(u.end)}
                      </small>
                      {u.text}
                    </span>
                  </div>
                ))}

                {/* Legacy segments (while DB not yet ready) */}
                {filteredSegments && filteredSegments.map((segment, index) => (
                  <div
                    className="live-transcript-line reveal-line"
                    key={`${segment.start}-${segment.end}-${index}`}
                  >
                    <span
                      className={`speaker-pill ${
                        index % 3 === 0
                          ? "speaker-pill-one"
                          : index % 3 === 1
                            ? "speaker-pill-two"
                            : "speaker-pill-three"
                      }`}
                    >
                      {segment.speaker || "TRANSCRIPT"}
                    </span>
                    <span>
                      <small style={{ opacity: 0.6, marginRight: "8px" }}>
                        {formatTime(segment.start)}–{formatTime(segment.end)}
                      </small>
                      {segment.text}
                    </span>
                  </div>
                ))}

                {transcriptionComplete && transcriptSegments.length === 0 && utterances.length === 0 && (
                  <div className="live-transcript-line reveal-line">
                    <span className="speaker-pill speaker-pill-one">
                      INFO
                    </span>
                    <span>
                      Transcription completed, but no speech segments were returned.
                    </span>
                  </div>
                )}

              </div>
            </div>

          </div>

          {transcriptionError && (
            <div className="upload-error" role="alert">
              {transcriptionError}
            </div>
          )}

          {transcriptionComplete && (
            <div className="continue-container">
              <button
                type="button"
                className="continue-button"
                onClick={goToExtract}
              >
                Continue to Extract →
              </button>
            </div>
          )}

        </main>
      )}
    </>
  );
}

