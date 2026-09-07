import { useMeetingFlow } from "../context/MeetingFlowContext";

export default function Decisions() {
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
      {currentStep === 3 && (

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
            STEP 3 · UNDERSTANDING
          </div>

          {isExtracting && (
            <div className="page-description">
              Analyzing the real transcript to identify decisions and action items...
            </div>
          )}

          {extractionError && (
            <div className="upload-error" role="alert">
              {extractionError}
            </div>
          )}

          <h1>
            Extracting decisions &amp; action items
          </h1>

          <div className="extract-section extract-decisions-section">

            <div className="section-heading">
              <div className="section-icon decision-section-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none">
                  <path d="M5 12.5 9.2 17 19 7" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </div>

              <div>
                <h2>Decisions</h2>
                <p>Key decisions made during the meeting</p>
              </div>
            </div>

            <div className="decision-list">
              {extractedData.decisions.slice(0, visibleDecisions).map((decision, index) => (
                <div className="decision-card decision-card-demo" key={index}>
                  <div className="decision-icon-card" aria-hidden="true">
                    <svg viewBox="0 0 24 24" fill="none">
                      <path d="M5 12.5 9.2 17 19 7" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                  </div>
                  <div className="decision-content">
                    <span className="item-type decision-type">DECISION</span>
                    <div className="decision-text">{decision}</div>
                  </div>
                </div>
              ))}
            </div>

          </div>

          <div className="extract-section extract-actions-section">

            <div className="section-heading">
              <div className="section-icon action-section-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none">
                  <rect x="5" y="6.5" width="14" height="13" rx="2" stroke="currentColor" strokeWidth="1.8"/>
                  <path d="M8 4.5v4M16 4.5v4M5 10.5h14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
                </svg>
              </div>

              <div>
                <h2>Action Items</h2>
                <p>Tasks identified from the conversation</p>
              </div>
            </div>

            <div className="action-list">
              {extractedData.actionItems.slice(0, visibleActions).map((item, index) => (
                <div className="action-card action-card-demo" key={index}>
                  <div className="action-icon-card" aria-hidden="true">
                    <svg viewBox="0 0 24 24" fill="none">
                      <rect x="5" y="6.5" width="14" height="13" rx="2" stroke="currentColor" strokeWidth="1.8"/>
                      <path d="M8 4.5v4M16 4.5v4M5 10.5h14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
                    </svg>
                  </div>

                  <div className="action-details">
                    <span className="item-type action-type">ACTION ITEM</span>
                    <strong>{item.task}</strong>
                    <div className="action-meta demo-action-meta">
                      <span className="owner-pill">Owner: {item.owner}</span>
                      <span className="due-pill">Due: {item.dueDate}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>

          </div>

          <div className="continue-container">
            <button
              type="button"
              className="continue-button"
              onClick={goToVerify}
              disabled={isExtracting || !extractAnimationComplete}>
              Continue to Verify →
            </button>
          </div>

        </main>

      )}
    </>
  );
}
