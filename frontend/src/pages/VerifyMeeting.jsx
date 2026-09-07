import { useMeetingFlow } from "../context/MeetingFlowContext";

function formatTimestamp(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) {
    return "Timestamp unavailable";
  }

  const total = Math.max(0, Math.floor(Number(seconds)));
  const minutes = Math.floor(total / 60);
  const remaining = total % 60;
  return `${minutes}:${String(remaining).padStart(2, "0")}`;
}

function statusLabel(status) {
  if (status === "verified_with_warning") return "Verified with warning";
  if (status === "verified") return "Verified";
  if (status === "review") return "Needs review";
  if (status === "rejected") return "Rejected";
  return "Checked";
}

export default function VerifyMeeting() {
  const {
    currentStep,
    goBack,
    goToExport,
    verificationStage,
    setVerificationStage,
    verificationProgress,
    automaticCheckComplete,
    verificationData,
    isVerifying,
    verificationError,
    editingDecisions,
    setEditingDecisions,
    editingActions,
    setEditingActions,
    extractedData,
    updateDecision,
    updateActionItem,
    isUploading,
    isTranscribing,
    isExtracting,
    isGeneratingMom,
    isDownloadingPdf,
  } = useMeetingFlow();

  if (currentStep !== 4) return null;

  const decisionStats = verificationData?.decisions || {};
  const actionStats = verificationData?.action_items || {};
  const verifiedDecisions = Array.isArray(verificationData?.verified_decisions)
    ? verificationData.verified_decisions.filter((item) =>
        ["verified", "verified_with_warning"].includes(item?.verification_status)
      )
    : [];
  const verifiedActions = Array.isArray(verificationData?.verified_action_items)
    ? verificationData.verified_action_items.filter((item) =>
        ["verified", "verified_with_warning"].includes(item?.verification_status)
      )
    : [];

  const warnings =
    Number(decisionStats.with_warnings || 0) +
    Number(actionStats.with_warnings || 0);

  return (
    <main className="main-content">
      <button
        type="button"
        className="back-button"
        onClick={goBack}
        disabled={
          isUploading ||
          isTranscribing ||
          isExtracting ||
          isVerifying ||
          isGeneratingMom ||
          isDownloadingPdf
        }
      >
        <span aria-hidden="true">←</span> Back
      </button>

      {verificationStage === "automatic" ? (
        <>
          <div className="step-label">STEP 4 · FAITHFULNESS VERIFICATION</div>

          <h1>Checking output against the transcript</h1>

          <p className="page-description">
            Meet2MoM verifies extracted decisions and action items against the
            original speaker-attributed transcript before final review.
          </p>

          <div className="automatic-verification-page">
            <div className="faithfulness-progress-card">
              <div className="faithfulness-progress-header">
                <div>
                  <strong>Evidence Verification</strong>
                  <span>
                    Matching extracted information to transcript evidence
                  </span>
                </div>
                <strong className="faithfulness-progress-value">
                  {isVerifying ? "Checking…" : automaticCheckComplete ? "Complete" : `${verificationProgress}%`}
                </strong>
              </div>

              <div className="faithfulness-progress-track">
                <div
                  className="faithfulness-progress-fill"
                  style={{ width: `${verificationProgress}%` }}
                />
              </div>

              <div className="faithfulness-progress-status">
                {verificationError
                  ? verificationError
                  : automaticCheckComplete
                    ? "Transcript-grounded verification complete"
                    : "Checking transcript evidence…"}
              </div>
            </div>

            {verificationError && (
              <div className="automatic-complete-card verification-reveal">
                <div className="automatic-complete-icon">!</div>
                <div>
                  <strong>Verification could not be completed</strong>
                  <span>{verificationError}</span>
                </div>
              </div>
            )}

            {automaticCheckComplete && verificationData && (
              <>
                <div className="verification-checks verification-checks-large">
                  <div className="verification-check-item verification-reveal">
                    <span className="verification-check-icon">✓</span>
                    <div>
                      <strong>
                        {decisionStats.evidence_supported || 0} / {decisionStats.total || 0} decisions supported
                      </strong>
                      <span>
                        {decisionStats.verified_clean || 0} verified cleanly ·{" "}
                        {decisionStats.with_warnings || 0} with warning ·{" "}
                        {decisionStats.review || 0} review ·{" "}
                        {decisionStats.rejected || 0} rejected
                      </span>
                    </div>
                  </div>

                  <div className="verification-check-item verification-reveal">
                    <span className="verification-check-icon">✓</span>
                    <div>
                      <strong>
                        {actionStats.evidence_supported || 0} / {actionStats.total || 0} action items supported
                      </strong>
                      <span>
                        Owners, deadlines and source statements are checked when action items exist.
                      </span>
                    </div>
                  </div>

                  <div className="verification-check-item verification-reveal">
                    <span className="verification-check-icon">✓</span>
                    <div>
                      <strong>Speaker and timestamp evidence recovered</strong>
                      <span>
                        Final claims are linked back to speaker-attributed transcript segments.
                      </span>
                    </div>
                  </div>

                  <div className="verification-check-item verification-reveal">
                    <span className="verification-check-icon">
                      {warnings > 0 ? "⚠" : "✓"}
                    </span>
                    <div>
                      <strong>
                        {warnings > 0
                          ? `${warnings} supported claim${warnings === 1 ? "" : "s"} contain reliability warnings`
                          : "No reliability warnings detected"}
                      </strong>
                      <span>
                        Cross-talk, source-review flags and speaker-attribution confidence are considered.
                      </span>
                    </div>
                  </div>
                </div>

                <div className="verification-method-note">
                  <span className="method-icon">↔</span>
                  <div>
                    <strong>Transcript-grounded evidence verification</strong>
                    <p>
                      This stage uses deterministic evidence matching and source
                      reliability checks. It is not a simulated score and does
                      not claim that uncertain evidence is fully reliable.
                    </p>
                  </div>
                </div>

                <div className="automatic-complete-card verification-reveal">
                  <div className="automatic-complete-icon">✓</div>
                  <div>
                    <strong>Automatic check complete</strong>
                    <span>
                      Evidence-supported results are ready for human review.
                    </span>
                  </div>
                </div>
              </>
            )}

            <div className="verification-page-footer">
              <button
                type="button"
                className="continue-button"
                onClick={() => setVerificationStage("manual")}
                disabled={!automaticCheckComplete || isVerifying || Boolean(verificationError)}
              >
                Manual Review Ready →
              </button>
            </div>
          </div>
        </>
      ) : (
        <>
          <div className="step-label">STEP 4 · MANUAL REVIEW</div>

          <h1>Review verified meeting information</h1>

          <p className="page-description">
            Review evidence-supported decisions and action items before creating
            the final Minutes of Meeting.
          </p>

          <div className="manual-review-ready-banner">
            <div className="automatic-complete-icon">✓</div>
            <div>
              <strong>Transcript verification complete</strong>
              <span>
                {decisionStats.evidence_supported || 0} decision(s) and{" "}
                {actionStats.evidence_supported || 0} action item(s) are supported by transcript evidence.
              </span>
            </div>
          </div>

          <div className="verify-section">
            <div className="verify-section-header">
              <div>
                <h2>Decisions</h2>
                <p>Review each decision together with its actual transcript evidence.</p>
              </div>

              <div className="verify-header-actions">
                <span className="verified-badge">Evidence Checked</span>
                {!editingDecisions ? (
                  <button
                    type="button"
                    className="edit-button"
                    onClick={() => setEditingDecisions(true)}
                  >
                    ✎ Edit
                  </button>
                ) : (
                  <button
                    type="button"
                    className="save-button"
                    onClick={() => setEditingDecisions(false)}
                  >
                    ✓ Save
                  </button>
                )}
              </div>
            </div>

            {verifiedDecisions.length === 0 ? (
              <div className="verify-decision">
                <p>No evidence-supported decisions were found.</p>
              </div>
            ) : (
              verifiedDecisions.map((decision, index) => {
                const evidence = Array.isArray(decision.evidence)
                  ? decision.evidence
                  : [];
                const proposalEvidence =
                  evidence.find((item) => item.role === "proposal_or_decision") ||
                  evidence[0];
                const confirmationEvidence =
                  evidence.find((item) => item.role === "confirmation");

                return (
                  <div className="verify-decision" key={`${decision.topic_id || "decision"}-${index}`}>
                    <label>
                      Decision {index + 1} · {statusLabel(decision.verification_status)}
                    </label>

                    <textarea
                      value={extractedData.decisions[index] || decision.text || ""}
                      readOnly={!editingDecisions}
                      onChange={(event) =>
                        updateDecision(index, event.target.value)
                      }
                      rows="3"
                      className={!editingDecisions ? "read-only-field" : ""}
                    />

                    <div className="transcript-evidence">
                      <span className="evidence-label">Transcript evidence</span>
                      <span className="evidence-speaker">
                        {proposalEvidence?.speaker || decision.proposal_speaker || "Speaker unavailable"}
                        {" · "}
                        {formatTimestamp(
                          proposalEvidence?.start ?? decision.proposal_timestamp
                        )}
                      </span>
                      <span>
                        “{proposalEvidence?.text || decision.original_proposal || decision.text}”
                      </span>

                      {confirmationEvidence && (
                        <>
                          <span className="evidence-label">Confirmation</span>
                          <span className="evidence-speaker">
                            {confirmationEvidence.speaker || decision.confirmation_speaker || "Speaker unavailable"}
                            {" · "}
                            {formatTimestamp(
                              confirmationEvidence.start ??
                                decision.verified_confirmation_timestamp
                            )}
                          </span>
                          <span>“{confirmationEvidence.text}”</span>
                        </>
                      )}

                      <span>
                        Evidence confidence:{" "}
                        {Math.round(Number(decision.evidence_confidence || 0) * 100)}%
                      </span>

                      {decision.verification_status === "verified_with_warning" && (
                        <span>
                          ⚠{" "}
                          {(decision.verification_review_reasons || [])
                            .join(", ")
                            .replaceAll("_", " ")}
                        </span>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>

          <div className="verify-section">
            <div className="verify-section-header">
              <div>
                <h2>Action Items</h2>
                <p>Review supported tasks, owners, due dates and source evidence.</p>
              </div>

              <div className="verify-header-actions">
                <span className="verified-badge">Evidence Checked</span>
                {verifiedActions.length > 0 &&
                  (!editingActions ? (
                    <button
                      type="button"
                      className="edit-button"
                      onClick={() => setEditingActions(true)}
                    >
                      ✎ Edit
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="save-button"
                      onClick={() => setEditingActions(false)}
                    >
                      ✓ Save
                    </button>
                  ))}
              </div>
            </div>

            {verifiedActions.length === 0 ? (
              <div className="verify-action">
                <p>No reliable action items were detected in this meeting.</p>
              </div>
            ) : (
              verifiedActions.map((item, index) => {
                const evidence = Array.isArray(item.evidence) ? item.evidence : [];
                const sourceEvidence = evidence[0];

                return (
                  <div className="verify-action" key={`${item.topic_id || "action"}-${index}`}>
                    <div className="verify-field">
                      <label>Task</label>
                      <input
                        type="text"
                        value={extractedData.actionItems[index]?.task || item.task || ""}
                        readOnly={!editingActions}
                        className={!editingActions ? "read-only-field" : ""}
                        onChange={(event) =>
                          updateActionItem(index, "task", event.target.value)
                        }
                      />
                    </div>

                    <div className="verify-field">
                      <label>Owner</label>
                      <input
                        type="text"
                        value={extractedData.actionItems[index]?.owner || item.owner || "Not specified"}
                        readOnly={!editingActions}
                        className={!editingActions ? "read-only-field" : ""}
                        onChange={(event) =>
                          updateActionItem(index, "owner", event.target.value)
                        }
                      />
                    </div>

                    <div className="verify-field">
                      <label>Due Date</label>
                      <input
                        type="text"
                        value={
                          extractedData.actionItems[index]?.dueDate ||
                          item.dueDate ||
                          item.due_date ||
                          item.deadline_text ||
                          "Not specified"
                        }
                        readOnly={!editingActions}
                        className={!editingActions ? "read-only-field" : ""}
                        onChange={(event) =>
                          updateActionItem(index, "dueDate", event.target.value)
                        }
                      />
                    </div>

                    <div className="transcript-evidence action-evidence">
                      <span className="evidence-label">Transcript evidence</span>
                      <span className="evidence-speaker">
                        {sourceEvidence?.speaker || item.owner || "Speaker unavailable"}
                        {sourceEvidence?.start !== undefined
                          ? ` · ${formatTimestamp(sourceEvidence.start)}`
                          : ""}
                      </span>
                      <span>
                        “{sourceEvidence?.text || item.source_text || item.task || ""}”
                      </span>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          <div className="verify-footer">
            <div className="verification-complete">
              <span className="check-circle">✓</span>
              <span>Manual review complete · Ready to export</span>
            </div>

            <button
              type="button"
              className="continue-button"
              onClick={goToExport}
              disabled={editingDecisions || editingActions}
            >
              Confirm &amp; Continue to Export →
            </button>
          </div>
        </>
      )}
    </main>
  );
}
