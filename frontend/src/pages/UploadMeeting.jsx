import { useMeetingFlow } from "../context/MeetingFlowContext";

export default function UploadMeeting() {
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

          {selectedFile && !isUploading && (

            <div className="continue-container">

              <button
                type="button"
                className="continue-button"
                onClick={goToTranscribe}
              >
                Continue to Transcribe →
              </button>

            </div>

          )}

        </main>

      )}
    </>
  );
}
