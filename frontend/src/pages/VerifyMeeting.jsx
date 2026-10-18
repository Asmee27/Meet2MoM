import { useEffect, useState } from "react";
import { useMeetingFlow } from "../context/MeetingFlowContext";
import {
  ClaimReviewCard,
  MethodComparison,
  ReasonList,
} from "../components/ClaimVerification";
import { STATUS_LABELS } from "../components/verificationFormat";
import "../components/ClaimVerification.css";

const CONFIRMED = ["verified", "verified_with_warning"];

function groupClaims(claims) {
  return {
    confirmed: claims.filter((c) => c.exportable),
    review: claims.filter(
      (c) => !c.exportable && c.verification.final.label !== "rejected"
    ),
    rejected: claims.filter((c) => c.verification.final.label === "rejected"),
  };
}

function RunNotice({ verificationData }) {
  const run = verificationData?.verification_run || {};
  const extraction = verificationData?.extraction || {};
  const notices = [];
  if (run.status === "semantic_unavailable") {
    notices.push(
      `Semantic verification was unavailable (${run.reason || "model could not load"}). ` +
        "Claims could not be confirmed and are held for review."
    );
  }
  if (extraction.fallback_reason) {
    notices.push(
      `Extraction used the ${extraction.mode_used} extractor because: ${extraction.fallback_reason}.`
    );
  }
  if (notices.length === 0) return null;
  return (
    <div className="verification-run-notice" data-testid="run-notice">
      {notices.map((notice) => (
        <p key={notice}>⚠ {notice}</p>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Audit log drawer
// ---------------------------------------------------------------------------
function AuditLogSection({ backendFileId, auditLog, isLoadingAudit, loadAuditLog }) {
  const [open, setOpen] = useState(false);

  const toggle = () => {
    if (!open) loadAuditLog(backendFileId);
    setOpen((v) => !v);
  };

  function fmtTime(ts) {
    if (!ts) return "";
    try { return new Date(ts).toLocaleString(); } catch { return ts; }
  }

  return (
    <div data-testid="audit-section">
      <button type="button" className="audit-drawer-toggle" onClick={toggle}>
        {open ? "▾" : "▸"} Audit history {auditLog.length > 0 ? `(${auditLog.length})` : ""}
      </button>
      {open && (
        <div className="audit-log-drawer" data-testid="audit-log">
          {isLoadingAudit && <p className="audit-log-empty">Loading…</p>}
          {!isLoadingAudit && auditLog.length === 0 && (
            <p className="audit-log-empty">No audit events recorded yet.</p>
          )}
          {!isLoadingAudit && auditLog.map((entry, i) => (
            <div className="audit-log-entry" key={i} data-testid="audit-entry">
              <span className="audit-log-action">{entry.action}</span>
              <span className="audit-log-meta">
                {entry.reviewer ? `${entry.reviewer} · ` : ""}{fmtTime(entry.timestamp)}
              </span>
              {entry.reason && <span className="audit-log-detail">Reason: {entry.reason}</span>}
              {entry.item_id && <span className="audit-log-detail">Item: {entry.item_id}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Reopen modal
// ---------------------------------------------------------------------------
function ReopenModal({ onConfirm, onClose, isReopening, reopenError }) {
  const [localReviewer, setLocalReviewer] = useState("");
  const [localReason, setLocalReason] = useState("");
  const canSubmit = localReason.trim().length > 0;

  return (
    <div className="reopen-modal-overlay" data-testid="reopen-modal">
      <div className="reopen-modal" role="dialog" aria-modal="true">
        <h3>Reopen review</h3>
        <p>
          Reopening will allow editing again. The currently approved snapshot will be
          preserved. A reason is required.
        </p>
        <div className="edit-form-field">
          <label className="edit-form-label">Your name</label>
          <input
            className="reviewer-bar-input"
            value={localReviewer}
            onChange={(e) => setLocalReviewer(e.target.value)}
            placeholder="Reviewer name (optional)"
            data-testid="reopen-reviewer"
            disabled={isReopening}
          />
        </div>
        <div className="edit-form-field">
          <label className="edit-form-label">Reason for reopening *</label>
          <input
            className="reviewer-bar-input"
            value={localReason}
            onChange={(e) => setLocalReason(e.target.value)}
            placeholder="e.g. Missed an action item"
            required
            data-testid="reopen-reason"
            disabled={isReopening}
          />
        </div>
        {reopenError && <p className="reopen-error">{reopenError}</p>}
        <div className="reopen-modal-actions">
          <button
            type="button"
            className="reopen-confirm-button"
            onClick={() => onConfirm(localReviewer, localReason)}
            disabled={!canSubmit || isReopening}
            data-testid="reopen-confirm"
          >
            {isReopening ? "Reopening…" : "Reopen review"}
          </button>
          <button
            type="button"
            className="reopen-cancel-button"
            onClick={onClose}
            disabled={isReopening}
            data-testid="reopen-cancel"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Reviewer bar (approval controls)
// ---------------------------------------------------------------------------
function ReviewerBar({
  reviewerName,
  setReviewerName,
  approvalNotes,
  setApprovalNotes,
  onApprove,
  isApproving,
  approveError,
}) {
  return (
    <div className="reviewer-bar" data-testid="reviewer-bar">
      <p className="reviewer-bar-title">Approve &amp; lock minutes</p>
      <div className="reviewer-bar-row">
        <div className="reviewer-bar-field">
          <label>Reviewer name</label>
          <input
            className="reviewer-bar-input"
            value={reviewerName}
            onChange={(e) => setReviewerName(e.target.value)}
            placeholder="Your name"
            disabled={isApproving}
            data-testid="reviewer-name-input"
          />
        </div>
        <div className="reviewer-bar-field">
          <label>Notes (optional)</label>
          <input
            className="reviewer-bar-input"
            value={approvalNotes}
            onChange={(e) => setApprovalNotes(e.target.value)}
            placeholder="Any notes about this approval"
            disabled={isApproving}
            data-testid="approval-notes-input"
          />
        </div>
        <button
          type="button"
          className="approve-button"
          onClick={onApprove}
          disabled={isApproving || !reviewerName.trim()}
          data-testid="approve-button"
        >
          {isApproving ? "Approving…" : "✓ Approve & Lock Minutes"}
        </button>
      </div>
      {approveError && (
        <p className="approve-error" role="alert" data-testid="approve-error">
          {approveError}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Add item form
// ---------------------------------------------------------------------------
function AddItemForm({ onAdd, onCancel }) {
  const [itemType, setItemType] = useState("decision");
  const [text, setText] = useState("");
  const [owner, setOwner] = useState("");
  const [deadline, setDeadline] = useState("");
  const [reason, setReason] = useState("");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState("");

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!text.trim()) return;
    setAdding(true);
    setAddError("");
    try {
      await onAdd({
        item_type: itemType,
        text: text.trim(),
        owner: itemType === "action" ? owner.trim() : null,
        deadline_text: itemType === "action" ? deadline.trim() : null,
        reason: reason.trim() || "Manual item added by reviewer",
      });
      onCancel();
    } catch (err) {
      setAddError(err?.message || "Failed to add item.");
    } finally {
      setAdding(false);
    }
  };

  return (
    <form className="review-edit-form" onSubmit={handleSubmit} data-testid="add-item-form">
      <h4 style={{ margin: "0 0 6px", color: "#0f172a" }}>Add new item</h4>
      <div className="edit-form-field">
        <label className="edit-form-label">Type</label>
        <select
          className="edit-form-input"
          value={itemType}
          onChange={(e) => setItemType(e.target.value)}
          data-testid="add-item-type"
        >
          <option value="decision">Decision</option>
          <option value="action">Action Item</option>
        </select>
      </div>
      <div className="edit-form-field">
        <label className="edit-form-label">Text *</label>
        <textarea
          className="edit-form-textarea"
          value={text}
          onChange={(e) => setText(e.target.value)}
          required
          rows={2}
          placeholder="Enter item description"
          data-testid="add-item-text"
        />
      </div>
      {itemType === "action" && (
        <div className="edit-form-row">
          <div className="edit-form-field">
            <label className="edit-form-label">Owner</label>
            <input
              className="edit-form-input"
              value={owner}
              onChange={(e) => setOwner(e.target.value)}
              placeholder="Assignee name"
              data-testid="add-item-owner"
            />
          </div>
          <div className="edit-form-field">
            <label className="edit-form-label">Deadline</label>
            <input
              className="edit-form-input"
              value={deadline}
              onChange={(e) => setDeadline(e.target.value)}
              placeholder="e.g. Next Friday"
              data-testid="add-item-deadline"
            />
          </div>
        </div>
      )}
      <div className="edit-form-field">
        <label className="edit-form-label">Reason / Notes</label>
        <input
          className="edit-form-input"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. Discussed in meeting but omitted by model"
          data-testid="add-item-reason"
        />
      </div>
      {addError && <p className="edit-form-error">{addError}</p>}
      <div className="edit-form-actions">
        <button type="submit" className="save-button" disabled={adding || !text.trim()} data-testid="add-item-submit">
          {adding ? "Adding…" : "Add to Review"}
        </button>
        <button type="button" className="edit-button" onClick={onCancel} disabled={adding}>
          Cancel
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export default function VerifyMeeting() {
  const {
    currentStep,
    goBack,
    goToExport,
    backendFileId,
    verificationStage,
    setVerificationStage,
    verificationProgress,
    automaticCheckComplete,
    verificationData,
    isVerifying,
    verificationError,
    reviewSuggestion,
    suggestionBusy,
    suggestionError,
    isUploading,
    isTranscribing,
    isExtracting,
    isGeneratingMom,
    isDownloadingPdf,
    // Phase 3
    reviewData,
    isLoadingReview,
    reviewError,
    reviewerName,
    setReviewerName,
    isApproving,
    approveError,
    setApproveError,
    isReopening,
    reopenError,
    setReopenError,
    auditLog,
    isLoadingAudit,
    loadReviewData,
    editReviewItem,
    addNewReviewItem,
    approveMeeting,
    reopenMeetingReview,
    loadAuditLog,
  } = useMeetingFlow();

  const [approvalNotes, setApprovalNotes] = useState("");
  const [showReopenModal, setShowReopenModal] = useState(false);
  const [showAddItem, setShowAddItem] = useState(false);

  // Load review draft when entering the manual review stage.
  useEffect(() => {
    if (verificationStage === "manual" && backendFileId && !reviewData && !isLoadingReview) {
      loadReviewData(backendFileId);
    }
  }, [verificationStage, backendFileId, reviewData, isLoadingReview, loadReviewData]);

  if (currentStep !== 4) return null;

  const claims = Array.isArray(verificationData?.claims) ? verificationData.claims : [];
  const counts = verificationData?.counts || {};
  const groups = groupClaims(claims);
  const timings = verificationData?.verification_run || {};

  // Phase 3: merge review_items into claims for display
  const reviewItemsMap = {};
  if (reviewData?.items) {
    for (const item of reviewData.items) {
      reviewItemsMap[item.item_id] = item;
      if (item.original_claim_id) {
        reviewItemsMap[item.original_claim_id] = item;
      }
    }
  }

  const enrichedClaims = claims.map((c) => {
    const ri = reviewItemsMap[c.claim_id];
    if (!ri) return c;
    return {
      ...c,
      item_id: ri.item_id,
      review_text: ri.text != null ? ri.text : ri.review_text,
      review_owner: ri.owner != null ? ri.owner : ri.review_owner,
      review_deadline: ri.deadline_resolved || ri.deadline_text || ri.review_deadline,
      review_status: ri.review_status === "edited" ? "human_edited" : ri.review_status,
      excluded: Boolean(ri.is_excluded || ri.excluded),
      exportable: !ri.is_excluded && !ri.excluded && c.exportable,
    };
  });

  // Include manually added review items
  if (reviewData?.items) {
    for (const item of reviewData.items) {
      if (!item.original_claim_id && !claims.some((c) => c.claim_id === item.item_id)) {
        enrichedClaims.push({
          claim_id: item.item_id,
          item_id: item.item_id,
          claim_type: item.item_type,
          text: item.text,
          owner: item.owner,
          deadline_resolved: item.deadline_resolved,
          deadline_text: item.deadline_text,
          review_text: item.text,
          review_owner: item.owner,
          review_deadline: item.deadline_resolved || item.deadline_text,
          review_status: "human_edited",
          excluded: Boolean(item.is_excluded),
          exportable: !item.is_excluded,
          evidence: item.evidence || [],
          evidence_ids: item.evidence_ids || [],
          verification: item.verification || {
            deterministic: { label: "human_added" },
            semantic: { label: "human_added" },
            final: {
              label: "verified",
              reasons: [{ code: "human_added", severity: "info", message: "Added by reviewer" }],
            },
          },
          suggestion: null,
          contradiction: null,
          correction: null,
        });
      }
    }
  }

  const enrichedGroups = groupClaims(enrichedClaims);

  const isApproved = reviewData?.draft_status === "approved";
  const snapshot = reviewData?.latest_approved_snapshot || reviewData?.approved_snapshot;

  const handleApprove = async () => {
    setApproveError("");
    try {
      await approveMeeting(reviewerName.trim(), approvalNotes.trim());
    } catch {
      // error is set in context
    }
  };

  const handleReopenConfirm = async (reviewer, reason) => {
    setReopenError("");
    try {
      await reopenMeetingReview(reviewer, reason);
      setShowReopenModal(false);
    } catch {
      // error is set in context
    }
  };

  // -------------------------------------------------------------------------
  // STAGE: automatic verification progress
  // -------------------------------------------------------------------------
  if (verificationStage === "automatic") {
    return (
      <main className="main-content">
        <button
          type="button"
          className="back-button"
          onClick={goBack}
          disabled={
            isUploading || isTranscribing || isExtracting ||
            isVerifying || isGeneratingMom || isDownloadingPdf
          }
        >
          <span aria-hidden="true">←</span> Back
        </button>

        <>
          <div className="step-label">STEP 4 · FAITHFULNESS VERIFICATION</div>
          <h1>Checking every claim against the transcript</h1>
          <p className="page-description">
            Each decision and action item is checked twice: a deterministic
            text match, and an independent semantic check that retrieves
            transcript evidence and tests entailment and contradiction. The two
            results and audio reliability are combined into one status with
            reasons.
          </p>

          <div className="automatic-verification-page">
            <div className="faithfulness-progress-card">
              <div className="faithfulness-progress-header">
                <div>
                  <strong>Evidence Verification</strong>
                  <span>Deterministic + semantic (retrieval + NLI) verification</span>
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
                    ? "Verification complete"
                    : "Checking transcript evidence… (the semantic model can take a while on CPU)"}
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
                <RunNotice verificationData={verificationData} />

                <div className="verification-checks verification-checks-large">
                  {["verified", "verified_with_warning", "review", "rejected"].map((label) => (
                    <div className="verification-check-item verification-reveal" key={label}>
                      <span className="verification-check-icon">
                        {CONFIRMED.includes(label) ? "✓" : label === "review" ? "?" : "✕"}
                      </span>
                      <div>
                        <strong>
                          {counts[label] || 0} {STATUS_LABELS[label].toLowerCase()}
                        </strong>
                        <span>
                          {label === "review"
                            ? "Held back from the minutes until a person checks them."
                            : label === "rejected"
                              ? "Contradicted or without resolvable evidence; excluded."
                              : label === "verified_with_warning"
                                ? "Confirmed, with warnings kept visible."
                                : "Confirmed by cited evidence with no warnings."}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>

                {claims.length === 0 ? (
                  <div className="verify-decision" data-testid="no-claims">
                    <p>
                      {verificationData?.extraction?.status === "no_claims"
                        ? "Extraction ran and found no decisions or action items in this meeting."
                        : "No claims are available to verify."}
                    </p>
                  </div>
                ) : (
                  <div className="verification-claim-table">
                    {claims.map((claim) => (
                      <div className="verification-claim-row" key={claim.claim_id}>
                        <p className="claim-text">
                          <strong>{claim.claim_type === "decision" ? "Decision" : "Action"}:</strong>{" "}
                          {claim.text}
                        </p>
                        <MethodComparison claim={claim} />
                        <ReasonList reasons={claim.verification.final.reasons.slice(0, 2)} />
                      </div>
                    ))}
                  </div>
                )}

                {timings.seconds_total !== undefined && timings.seconds_total !== null && (
                  <p className="verification-timing">
                    Verified {timings.claims_total || 0} claim(s) in {timings.seconds_total}s
                    {timings.semantic_seconds ? ` (semantic: ${timings.semantic_seconds}s)` : ""}.
                  </p>
                )}
              </>
            )}

            <div className="verification-page-footer">
              <button
                type="button"
                className="continue-button"
                onClick={() => setVerificationStage("manual")}
                disabled={!automaticCheckComplete || isVerifying || Boolean(verificationError)}
              >
                Review Evidence →
              </button>
            </div>
          </div>
        </>
      </main>
    );
  }

  // -------------------------------------------------------------------------
  // STAGE: manual review (Phase 1 + Phase 3)
  // -------------------------------------------------------------------------
  return (
    <main className="main-content">
      <button
        type="button"
        className="back-button"
        onClick={goBack}
        disabled={
          isUploading || isTranscribing || isExtracting ||
          isVerifying || isGeneratingMom || isDownloadingPdf
        }
      >
        <span aria-hidden="true">←</span> Back
      </button>

      <div className="step-label">STEP 4 · REVIEW</div>
      <h1>Review claims and their evidence</h1>
      <p className="page-description">
        Only confirmed claims go into the minutes. Items that need review
        stay out until their evidence is fixed; when the verifier found a
        better evidence span you can accept or reject it here.
        {isApproved ? " This review has been approved and is locked." : " Edit items and approve below."}
      </p>

      <RunNotice verificationData={verificationData} />

      {suggestionError && (
        <div className="verification-run-notice" role="alert">
          <p>{suggestionError}</p>
        </div>
      )}

      {reviewError && (
        <div className="verification-run-notice" role="alert" data-testid="review-error">
          <p>{reviewError}</p>
        </div>
      )}

      {isLoadingReview && (
        <p style={{ color: "#64748b", fontSize: "0.9rem" }}>Loading review draft…</p>
      )}

      {/* ---- APPROVAL LOCK BANNER ---- */}
      {isApproved && snapshot && (
        <div className="approval-lock-banner" data-testid="approval-lock-banner">
          <div className="approval-lock-info">
            <span className="approval-lock-title">
              ✓ Approved — {snapshot.version || "v1"}
            </span>
            <span className="approval-lock-meta">
              By {snapshot.approved_by || "reviewer"} ·{" "}
              {snapshot.approved_at
                ? new Date(snapshot.approved_at).toLocaleString()
                : ""}
              {snapshot.notes ? ` · "${snapshot.notes}"` : ""}
            </span>
          </div>
          <button
            type="button"
            className="reopen-button"
            onClick={() => setShowReopenModal(true)}
            data-testid="reopen-trigger"
          >
            Reopen review
          </button>
        </div>
      )}

      {/* ---- REOPEN MODAL ---- */}
      {showReopenModal && (
        <ReopenModal
          onConfirm={handleReopenConfirm}
          onClose={() => { setShowReopenModal(false); setReopenError(""); }}
          isReopening={isReopening}
          reopenError={reopenError}
        />
      )}

      {/* ---- CLAIM SECTIONS ---- */}
      <div className="verify-section" data-testid="needs-review-section">
        <div className="verify-section-header">
          <div>
            <h2>Needs review ({enrichedGroups.review.length})</h2>
            <p>Held back from the minutes. Reasons and evidence are listed for each item.</p>
          </div>
        </div>
        {enrichedGroups.review.length === 0 ? (
          <div className="verify-decision">
            <p>No items are waiting for review.</p>
          </div>
        ) : (
          enrichedGroups.review.map((claim) => (
            <ClaimReviewCard
              key={claim.claim_id}
              claim={claim}
              busy={suggestionBusy}
              onSuggestionDecision={reviewSuggestion}
              isApproved={isApproved}
              onEdit={editReviewItem}
            />
          ))
        )}
      </div>

      <div className="verify-section" data-testid="confirmed-section">
        <div className="verify-section-header">
          <div>
            <h2>Confirmed ({enrichedGroups.confirmed.length})</h2>
            <p>These will appear in the minutes; warnings stay visible.</p>
          </div>
        </div>
        {enrichedGroups.confirmed.length === 0 ? (
          <div className="verify-decision">
            <p>No claims could be confirmed from transcript evidence.</p>
          </div>
        ) : (
          enrichedGroups.confirmed.map((claim) => (
            <ClaimReviewCard
              key={claim.claim_id}
              claim={claim}
              busy={suggestionBusy}
              onSuggestionDecision={reviewSuggestion}
              isApproved={isApproved}
              onEdit={editReviewItem}
            />
          ))
        )}
      </div>

      {enrichedGroups.rejected.length > 0 && (
        <div className="verify-section" data-testid="rejected-section">
          <div className="verify-section-header">
            <div>
              <h2>Rejected ({enrichedGroups.rejected.length})</h2>
              <p>Excluded from the minutes.</p>
            </div>
          </div>
          {enrichedGroups.rejected.map((claim) => (
            <ClaimReviewCard
              key={claim.claim_id}
              claim={claim}
              busy={suggestionBusy}
              onSuggestionDecision={reviewSuggestion}
              isApproved={isApproved}
              onEdit={editReviewItem}
            />
          ))}
        </div>
      )}

      {/* ---- ADD ITEM (when not approved) ---- */}
      {!isApproved && (
        <div className="add-item-container" style={{ margin: "14px 0" }}>
          {!showAddItem ? (
            <button
              type="button"
              className="add-item-button"
              onClick={() => setShowAddItem(true)}
              data-testid="add-item-trigger"
            >
              + Add Decision or Action Item
            </button>
          ) : (
            <AddItemForm
              onAdd={async (itemPayload) => {
                await addNewReviewItem(itemPayload);
                setShowAddItem(false);
              }}
              onCancel={() => setShowAddItem(false)}
            />
          )}
        </div>
      )}

      {/* ---- AUDIT LOG ---- */}
      <AuditLogSection
        backendFileId={backendFileId}
        auditLog={auditLog}
        isLoadingAudit={isLoadingAudit}
        loadAuditLog={loadAuditLog}
      />

      {/* ---- REVIEWER BAR (only when not approved) ---- */}
      {!isApproved && (
        <ReviewerBar
          reviewerName={reviewerName}
          setReviewerName={setReviewerName}
          approvalNotes={approvalNotes}
          setApprovalNotes={setApprovalNotes}
          onApprove={handleApprove}
          isApproving={isApproving}
          approveError={approveError}
        />
      )}

      <div className="verify-footer">
        <div className="verification-complete">
          <span className="check-circle">{enrichedGroups.review.length ? "?" : "✓"}</span>
          <span>
            {enrichedGroups.review.length
              ? `${enrichedGroups.review.length} item(s) will be listed as "held for review", not as confirmed`
              : "All remaining claims are confirmed"}
          </span>
        </div>

        <button type="button" className="continue-button" onClick={goToExport}>
          Continue to Export →
        </button>
      </div>
    </main>
  );
}
