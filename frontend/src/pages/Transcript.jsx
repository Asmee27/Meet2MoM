import { useMeetingFlow } from "../context/MeetingFlowContext";

export default function Transcript() {
  const {
    selectedFile,
    setSelectedFile,
    error,
    setError,
    isDragging,
    setIsDragging,
    currentStep,
    setCurrentStep,
    uploadProgress,
    setUploadProgress,
    isUploading,
    setIsUploading,
    fileInputRef,
    backendFileId,
    setBackendFileId,
    transcriptSegments,
    setTranscriptSegments,
    transcriptText,
    setTranscriptText,
    detectedLanguage,
    setDetectedLanguage,
    transcriptionError,
    setTranscriptionError,
    transcriptionProgress,
    setTranscriptionProgress,
    isTranscribing,
    setIsTranscribing,
    transcriptionComplete,
    setTranscriptionComplete,
    extractedData,
    setExtractedData,
    isExtracting,
    setIsExtracting,
    extractionError,
    setExtractionError,
    editingDecisions,
    setEditingDecisions,
    editingActions,
    setEditingActions,
    visibleDecisions,
    setVisibleDecisions,
    visibleActions,
    setVisibleActions,
    extractAnimationComplete,
    setExtractAnimationComplete,
    verificationStage,
    setVerificationStage,
    verificationProgress,
    setVerificationProgress,
    automaticCheckComplete,
    setAutomaticCheckComplete,
    exportStatus,
    setExportStatus,
    momData,
    setMomData,
    isGeneratingMom,
    setIsGeneratingMom,
    momError,
    setMomError,
    isDownloadingPdf,
    setIsDownloadingPdf,
    validateFile,
    handleFileChange,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    removeFile,
    goBack,
    goToTranscribe,
    goToExtract,
    goToVerify,
    goToExport,
    updateDecision,
    updateActionItem,
    downloadTextFile,
    downloadMeetingPdf,
    exportWordDocument,
    sendMeetingEmail
  } = useMeetingFlow();

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
            STEP 2 · SPEECH-TO-TEXT & DIARIZATION
          </div>

          <h1>Transcribing & identifying speakers</h1>

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
                      Speaker diarization pending
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

            <div className="live-transcript-panel">

              <div className="live-transcript-title">
                Live transcript
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

                {transcriptSegments.map((segment, index) => (
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
                      TRANSCRIPT
                    </span>

                    <span>
                      <small style={{ opacity: 0.6, marginRight: "8px" }}>
                        {segment.start}s–{segment.end}s
                      </small>
                      {segment.text}
                    </span>
                  </div>
                ))}

                {transcriptionComplete && transcriptSegments.length === 0 && (
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
