import { useMeetingFlow } from "../context/MeetingFlowContext";
import {
  ClaimReviewCard,
  MethodComparison,
  ReasonList,
} from "../components/ClaimVerification";
import { STATUS_LABELS } from "../components/verificationFormat";

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
    reviewSuggestion,
    suggestionBusy,
    suggestionError,
    isUploading,
    isTranscribing,
    isExtracting,
    isGeneratingMom,
    isDownloadingPdf,
  } = useMeetingFlow();

  if (currentStep !== 4) return null;

  const claims = Array.isArray(verificationData?.claims) ? verificationData.claims : [];
  const counts = verificationData?.counts || {};
  const groups = groupClaims(claims);
  const timings = verificationData?.verification_run || {};

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
      ) : (
        <>
          <div className="step-label">STEP 4 · REVIEW</div>

          <h1>Review claims and their evidence</h1>

          <p className="page-description">
            Only confirmed claims go into the minutes. Items that need review
            stay out until their evidence is fixed; when the verifier found a
            better evidence span you can accept or reject it here.
          </p>

          <RunNotice verificationData={verificationData} />

          {suggestionError && (
            <div className="verification-run-notice" role="alert">
              <p>{suggestionError}</p>
            </div>
          )}

          <div className="verify-section" data-testid="needs-review-section">
            <div className="verify-section-header">
              <div>
                <h2>Needs review ({groups.review.length})</h2>
                <p>Held back from the minutes. Reasons and evidence are listed for each item.</p>
              </div>
            </div>
            {groups.review.length === 0 ? (
              <div className="verify-decision">
                <p>No items are waiting for review.</p>
              </div>
            ) : (
              groups.review.map((claim) => (
                <ClaimReviewCard
                  key={claim.claim_id}
                  claim={claim}
                  busy={suggestionBusy}
                  onSuggestionDecision={reviewSuggestion}
                />
              ))
            )}
          </div>

          <div className="verify-section" data-testid="confirmed-section">
            <div className="verify-section-header">
              <div>
                <h2>Confirmed ({groups.confirmed.length})</h2>
                <p>These will appear in the minutes; warnings stay visible.</p>
              </div>
            </div>
            {groups.confirmed.length === 0 ? (
              <div className="verify-decision">
                <p>No claims could be confirmed from transcript evidence.</p>
              </div>
            ) : (
              groups.confirmed.map((claim) => (
                <ClaimReviewCard
                  key={claim.claim_id}
                  claim={claim}
                  busy={suggestionBusy}
                  onSuggestionDecision={reviewSuggestion}
                />
              ))
            )}
          </div>

          {groups.rejected.length > 0 && (
            <div className="verify-section" data-testid="rejected-section">
              <div className="verify-section-header">
                <div>
                  <h2>Rejected ({groups.rejected.length})</h2>
                  <p>Excluded from the minutes.</p>
                </div>
              </div>
              {groups.rejected.map((claim) => (
                <ClaimReviewCard
                  key={claim.claim_id}
                  claim={claim}
                  busy={suggestionBusy}
                  onSuggestionDecision={reviewSuggestion}
                />
              ))}
            </div>
          )}

          <div className="verify-footer">
            <div className="verification-complete">
              <span className="check-circle">{groups.review.length ? "?" : "✓"}</span>
              <span>
                {groups.review.length
                  ? `${groups.review.length} item(s) will be listed as "held for review", not as confirmed`
                  : "All remaining claims are confirmed"}
              </span>
            </div>

            <button type="button" className="continue-button" onClick={goToExport}>
              Continue to Export →
            </button>
          </div>
        </>
      )}
    </main>
  );
}
