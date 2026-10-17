import { useMeetingFlow } from "../context/MeetingFlowContext";

export default function Summary() {
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
      {currentStep === 5 && (

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
            STEP 5 · DELIVERY
          </div>

          <h1>
            Final Minutes of Meeting, ready to share
          </h1>

          <p className="page-description">
            Meet2MoM builds the final document from the real transcript
            and extracted meeting outcomes.
          </p>

          {isGeneratingMom && (
            <div className="processing-card">
              <div className="processing-spinner"></div>
              <div>
                <strong>Generating Minutes of Meeting...</strong>
                <p>Building the structured MoM and preparing the PDF.</p>
              </div>
            </div>
          )}

          {momError && (
            <div className="error-message" role="alert">
              {momError}
            </div>
          )}

          {!isGeneratingMom && momData && (
            <>
              {/* REAL MEETING MINUTES */}

              <div className="minutes-card">

                <div className="minutes-header">

                  <div>
                    <span className="minutes-label">
                      MEETING MINUTES
                    </span>

                    <h2>
                      {momData.meeting_title || "Meeting Minutes"}
                    </h2>
                  </div>

                  <span className="minutes-status">
                    ✓ Generated
                  </span>

                </div>

                <div className="minutes-divider"></div>

                <div className="minutes-block">
                  <h3>Meeting Overview</h3>

                  <div className="minutes-table">
                    <div className="table-header">
                      <span>Duration</span>
                      <span>Language</span>
                      <span>Date</span>
                    </div>

                    <div className="table-row">
                      <span>
                        {typeof momData.duration_seconds === "number"
                          ? `${Math.floor(momData.duration_seconds / 60)}m ${Math.round(momData.duration_seconds % 60)}s`
                          : "Not available"}
                      </span>
                      <span>
                        {momData.language
                          ? momData.language.toUpperCase()
                          : "Not specified"}
                      </span>
                      <span>
                        {momData.meeting_date || "Not specified"}
                      </span>
                    </div>
                  </div>
                </div>

                {/* SUMMARY */}

                <div className="minutes-block">
                  <h3>Meeting Summary</h3>
                  <p>
                    {momData.summary ||
                      "No meeting summary was generated."}
                  </p>
                </div>

                {/* DISCUSSION POINTS */}

                <div className="minutes-block">
                  <h3>Key Discussion Points</h3>

                  {Array.isArray(momData.discussion_points) &&
                  momData.discussion_points.length > 0 ? (
                    momData.discussion_points.map((point, index) => (
                      <div className="minutes-decision" key={index}>
                        <span>{index + 1}</span>
                        <p>
                          {typeof point === "string"
                            ? point
                            : point?.text || "Discussion point unavailable"}
                        </p>
                      </div>
                    ))
                  ) : (
                    <p className="empty-state-text">
                      No key discussion points were identified.
                    </p>
                  )}
                </div>

                {/* DECISIONS */}

                <div className="minutes-block">
                  <h3>Decisions</h3>

                  {Array.isArray(momData.decisions) &&
                  momData.decisions.length > 0 ? (
                    momData.decisions.map((decision, index) => (
                      <div className="minutes-decision" key={index}>
                        <span>{index + 1}</span>
                        <p>
                          {typeof decision === "string"
                            ? decision
                            : decision?.text || "Decision text unavailable"}
                          {(decision?.warnings || []).map((warning) => (
                            <span className="minutes-warning" key={warning}>
                              ⚠ {warning}
                            </span>
                          ))}
                        </p>
                      </div>
                    ))
                  ) : (
                    <p className="empty-state-text">
                      No decisions could be confirmed from transcript evidence.
                    </p>
                  )}
                </div>

                {/* ACTION ITEMS */}

                <div className="minutes-block">
                  <h3>Action Items</h3>

                  {Array.isArray(momData.action_items) &&
                  momData.action_items.length > 0 ? (
                    <div className="minutes-table">
                      <div className="table-header">
                        <span>Task</span>
                        <span>Owner</span>
                        <span>Due Date</span>
                      </div>

                      {momData.action_items.map((item, index) => (
                        <div className="table-row" key={index}>
                          <span>
                            {item.task || "Not specified"}
                            {(item.warnings || []).map((warning) => (
                              <span className="minutes-warning" key={warning}>
                                ⚠ {warning}
                              </span>
                            ))}
                          </span>
                          <span>{item.owner || "Not assigned"}</span>
                          <span>{item.dueDate || "Not specified"}</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="empty-state-text">
                      No confirmed action items recorded.
                    </p>
                  )}
                </div>

                {/* HELD FOR REVIEW: shown, never part of the confirmed minutes */}

                {Array.isArray(momData.needs_review) && momData.needs_review.length > 0 && (
                  <div className="minutes-block minutes-needs-review">
                    <h3>Held for Review (not confirmed)</h3>
                    <p className="empty-state-text">
                      These items could not be confirmed from transcript evidence and are
                      not decisions or tasks of record.
                    </p>
                    {momData.needs_review.map((item) => (
                      <div className="minutes-decision" key={item.claim_id}>
                        <span>{item.claim_type === "decision" ? "D" : "A"}</span>
                        <p>
                          {item.text}
                          {(item.reasons || []).map((reason) => (
                            <span className="minutes-warning" key={reason}>
                              {reason}
                            </span>
                          ))}
                        </p>
                      </div>
                    ))}
                  </div>
                )}

                {momData.extraction &&
                  (momData.extraction.fallback_reason ||
                    !["completed", "no_claims"].includes(momData.extraction.status)) && (
                    <p className="empty-state-text minutes-extraction-note">
                      Extraction status: {momData.extraction.status}
                      {momData.extraction.mode_used
                        ? ` (extractor used: ${momData.extraction.mode_used})`
                        : ""}
                      {momData.extraction.fallback_reason
                        ? ` · ${momData.extraction.fallback_reason}`
                        : ""}
                      {momData.extraction.reason ? ` · ${momData.extraction.reason}` : ""}
                    </p>
                  )}

                {/* FOLLOW UPS */}

                <div className="minutes-block">
                  <h3>Follow-ups / Open Items</h3>

                  {Array.isArray(momData.follow_ups) &&
                  momData.follow_ups.length > 0 ? (
                    momData.follow_ups.map((item, index) => (
                      <div className="minutes-decision" key={index}>
                        <span>{index + 1}</span>
                        <p>{item}</p>
                      </div>
                    ))
                  ) : (
                    <p className="empty-state-text">
                      No explicit follow-ups or open items recorded.
                    </p>
                  )}
                </div>

                <div className="minutes-footer">
                  Generated from transcript-grounded Meet2MoM data
                </div>

              </div>

              {/* EXPORT OPTIONS */}

              <div className="export-options">

                <div className="export-options-heading">
                  <h2>Download your meeting minutes</h2>
                  <p>
                    Export the server-generated Minutes of Meeting as a
                    professional PDF.
                  </p>
                </div>

                <div className="export-buttons">

                  <button
                    type="button"
                    className={`export-button ${
                      exportStatus.pdf ? "completed" : "primary"
                    }`}
                    onClick={downloadMeetingPdf}
                    disabled={isDownloadingPdf}
                  >
                    <span className="export-button-icon">
                      {isDownloadingPdf ? "…" : "↓"}
                    </span>

                    <span>
                      <strong>
                        {isDownloadingPdf
                          ? "Preparing PDF..."
                          : exportStatus.pdf
                          ? "PDF ✓ Downloaded"
                          : "Download PDF"}
                      </strong>

                      <small>
                        {exportStatus.pdf
                          ? "Meeting minutes PDF downloaded"
                          : "Download the generated Meet2MoM PDF"}
                      </small>
                    </span>
                  </button>

                </div>

              </div>
            </>
          )}

          {!isGeneratingMom && !momData && !momError && (
            <div className="processing-card">
              <div>
                <strong>No MoM data available yet.</strong>
                <p>Return to verification and generate the final minutes.</p>
              </div>
            </div>
          )}

          {/* START NEW MEETING */}

          <div className="new-meeting-container">

            <button
              className="new-meeting-button"
              onClick={() => {
                setSelectedFile(null);
                setBackendFileId(null);
                setTranscriptSegments([]);
                setTranscriptText("");
                setDetectedLanguage("");
                setTranscriptionError("");
                setError("");
                setCurrentStep(1);
                setTranscriptionProgress(0);
                setTranscriptionComplete(false);
                setIsTranscribing(false);
                setExtractedData({ decisions: [], actionItems: [] });
                setMomData(null);
                setMomError("");
                setIsGeneratingMom(false);
                setIsDownloadingPdf(false);
                setExportStatus({ pdf: false, word: false, email: false });
              }}
            >
              ← Start a new meeting
            </button>

          </div>

        </main>

      )}
    </>
  );
}
