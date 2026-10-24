// Presentational components for persisted claim verification (Phase 1).
// They only render what the backend persisted; no scores are computed here.
import { useState } from "react";
import "./ClaimVerification.css";
import { STATUS_LABELS, formatTimestamp } from "./verificationFormat";
import { Icon } from "./ui";

// Phase 8: each status has its own icon so it never relies on colour alone.
const STATUS_ICONS = {
  verified: "checkCircle",
  verified_with_warning: "warning",
  review: "clock",
  rejected: "error",
  unverified: "skip",
};

const SEMANTIC_LABELS = {
  supported: "Supported by cited evidence",
  supported_with_context: "Citation incomplete",
  supported_elsewhere: "Supported elsewhere (possible mis-citation)",
  unsupported: "Not supported",
  contradicted: "Contradicted",
  unavailable: "Verifier unavailable",
  error: "Verifier error",
  no_cited_evidence: "No cited evidence",
};

const DETERMINISTIC_LABELS = {
  verified: "Text match",
  verified_with_warning: "Text match (warning)",
  review: "Partial text match",
  rejected: "No text match",
  error: "Check failed",
};

function formatScore(value) {
  return value === null || value === undefined ? "n/a" : Number(value).toFixed(2);
}

export function StatusBadge({ label }) {
  return (
    <span className={`claim-status claim-status-${label}`} data-testid="claim-status">
      {STATUS_ICONS[label] && <Icon name={STATUS_ICONS[label]} />}
      {STATUS_LABELS[label] || label}
    </span>
  );
}

// Phase 3 review-status badge (human_edited, reviewer_approved, excluded).
export function ReviewBadge({ reviewStatus }) {
  if (!reviewStatus || reviewStatus === "original") return null;
  const labels = {
    human_edited: "Human Edited",
    reviewer_approved: "Reviewer Approved",
    excluded: "Excluded",
  };
  return (
    <span
      className={`claim-status claim-status-review-${reviewStatus}`}
      data-testid="review-badge"
    >
      {labels[reviewStatus] || reviewStatus}
    </span>
  );
}

export function EvidenceList({ title, utterances, onSeek, onFocusEvidence, testId }) {
  if (!utterances || utterances.length === 0) return null;
  return (
    <div className="transcript-evidence" data-testid={testId}>
      <span className="evidence-label">{title}</span>
      {utterances.map((u) => (
        <div className="evidence-utterance" key={u.utt_id}>
          <span className="evidence-speaker">
            {u.speaker || "Unknown speaker"} ·{" "}
            {onSeek && u.start != null ? (
              <button
                type="button"
                className="evidence-seek-btn"
                onClick={() => onSeek(u.start)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSeek(u.start);
                  }
                }}
                aria-label={`Play from ${formatTimestamp(u.start)}`}
                title={`Seek to ${formatTimestamp(u.start)}`}
              >
                ▶ {formatTimestamp(u.start)}
              </button>
            ) : (
              <span className="evidence-plain-time">{formatTimestamp(u.start)}</span>
            )}{" "}
            ·{" "}
            {onFocusEvidence ? (
              <button
                type="button"
                className="evidence-utt-link"
                onClick={() => onFocusEvidence(u.utt_id)}
                aria-label={`Show ${u.utt_id} in transcript`}
                title="Show in transcript"
              >
                {u.utt_id}
              </button>
            ) : (
              u.utt_id
            )}
          </span>
          <span>“{u.text}”</span>
        </div>
      ))}
    </div>
  );
}

export function ReasonList({ reasons }) {
  if (!reasons || reasons.length === 0) return null;
  return (
    <ul className="claim-reasons" data-testid="claim-reasons">
      {reasons.map((reason) => (
        <li key={reason.code} className={`claim-reason claim-reason-${reason.severity}`}>
          {reason.message}
        </li>
      ))}
    </ul>
  );
}

// Side-by-side summary of the two methods and the fused status (Step 4).
export function MethodComparison({ claim }) {
  const { deterministic, semantic, final } = claim.verification;
  return (
    <div className="method-comparison" data-testid="method-comparison">
      <div className="method-cell">
        <span className="method-name">Deterministic check</span>
        <strong>{DETERMINISTIC_LABELS[deterministic?.label] || deterministic?.label || "not run"}</strong>
      </div>
      <div className="method-cell">
        <span className="method-name">Semantic check (NLI)</span>
        <strong>{SEMANTIC_LABELS[semantic?.label] || semantic?.label || "not run"}</strong>
        {semantic && semantic.cited_entailment !== null && semantic.cited_entailment !== undefined && (
          <span className="method-scores">
            cited {formatScore(semantic.cited_entailment)} · best {formatScore(semantic.best_support)} ·
            contradiction {formatScore(semantic.contradiction)}
          </span>
        )}
      </div>
      <div className="method-cell">
        <span className="method-name">Final status</span>
        <StatusBadge label={final.label} />
      </div>
    </div>
  );
}

export function SuggestedEvidenceCard({ suggestion, onDecision, busy, onFocusEvidence }) {
  if (!suggestion) return null;
  return (
    <div className="suggested-evidence-card" data-testid="suggested-evidence">
      <strong>Suggested evidence</strong>
      <p>
        The semantic verifier found utterances that support this claim better than the
        current citation (support {formatScore(suggestion.support)} vs{" "}
        {formatScore(suggestion.cited_support)}).
      </p>
      <EvidenceList title="Suggested span" utterances={suggestion.evidence} onFocusEvidence={onFocusEvidence} testId="suggested-utterances" />
      <div className="suggestion-actions">
        <button type="button" className="save-button" disabled={busy} onClick={() => onDecision("accept")}>
          Accept suggestion
        </button>
        <button type="button" className="edit-button" disabled={busy} onClick={() => onDecision("reject")}>
          Reject
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Phase 3 inline edit form
// ---------------------------------------------------------------------------

function InlineEditForm({ claim, onSave, onCancel, saving, saveError }) {
  const [text, setText] = useState(claim.text || "");
  const [owner, setOwner] = useState(claim.owner || "");
  const [deadline, setDeadline] = useState(
    claim.deadline_resolved || claim.deadline_text || ""
  );
  const [excluded, setExcluded] = useState(Boolean(claim.excluded));
  const [reason, setReason] = useState("");

  const handleSubmit = (e) => {
    e.preventDefault();
    onSave({ text, owner, deadline, excluded, reason });
  };

  return (
    <form className="review-edit-form" onSubmit={handleSubmit} data-testid="edit-form">
      <div className="edit-form-field">
        <label className="edit-form-label">Text</label>
        <textarea
          className="edit-form-textarea"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          required
          disabled={saving}
          data-testid="edit-text"
        />
      </div>

      {claim.claim_type === "action" && (
        <>
          <div className="edit-form-row">
            <div className="edit-form-field">
              <label className="edit-form-label">Owner</label>
              <input
                className="edit-form-input"
                value={owner}
                onChange={(e) => setOwner(e.target.value)}
                disabled={saving}
                data-testid="edit-owner"
              />
            </div>
            <div className="edit-form-field">
              <label className="edit-form-label">Deadline</label>
              <input
                className="edit-form-input"
                value={deadline}
                onChange={(e) => setDeadline(e.target.value)}
                disabled={saving}
                data-testid="edit-deadline"
              />
            </div>
          </div>
        </>
      )}

      <div className="edit-form-field edit-form-checkbox-row">
        <label className="edit-form-label">
          <input
            type="checkbox"
            checked={excluded}
            onChange={(e) => setExcluded(e.target.checked)}
            disabled={saving}
            data-testid="edit-excluded"
          />{" "}
          Exclude from minutes
        </label>
      </div>

      <div className="edit-form-field">
        <label className="edit-form-label">Reason for change (optional)</label>
        <input
          className="edit-form-input"
          placeholder="e.g. Corrected spelling / updated owner"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          disabled={saving}
          data-testid="edit-reason"
        />
      </div>

      {saveError && (
        <p className="edit-form-error" role="alert" data-testid="edit-error">
          {saveError}
        </p>
      )}

      <div className="edit-form-actions">
        <button type="submit" className="save-button" disabled={saving} data-testid="edit-save">
          {saving ? "Saving…" : "Save changes"}
        </button>
        <button
          type="button"
          className="edit-button"
          onClick={onCancel}
          disabled={saving}
          data-testid="edit-cancel"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Phase 3 ClaimReviewCard — read mode + inline edit toggle
// ---------------------------------------------------------------------------

export function ClaimReviewCard({
  claim,
  onSuggestionDecision,
  busy,
  // Phase 3 props (all optional — card degrades gracefully without them)
  isApproved = false,
  onEdit,         // async (itemId, updates, reason) => void
  onSeek,         // (seconds) => void
  onFocusEvidence, // (uttId) => void — Phase 8: jump to the transcript line
}) {
  const [editMode, setEditMode] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");

  const final = claim.verification.final;
  const semantic = claim.verification.semantic;
  const alternatives =
    semantic && !claim.suggestion
      ? (semantic.best_evidence || []).filter((u) => !claim.evidence_ids.includes(u.utt_id))
      : [];

  // Determine the displayed text / meta — prefer review_item fields when available.
  const displayText = claim.review_text != null ? claim.review_text : claim.text;
  const displayOwner = claim.review_owner != null ? claim.review_owner : claim.owner;
  const displayDeadline =
    claim.review_deadline != null
      ? claim.review_deadline
      : claim.deadline_resolved || claim.deadline_text;
  const reviewStatus = claim.review_status; // "original"|"human_edited"|"reviewer_approved"|"excluded"

  const handleSave = async (updates) => {
    setSaving(true);
    setSaveError("");
    try {
      await onEdit(claim.item_id || claim.claim_id, updates, updates.reason);
      setEditMode(false);
    } catch (err) {
      setSaveError(err?.message || "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className={`claim-card claim-card-${final.label}${reviewStatus === "excluded" ? " claim-card-excluded" : ""}`}
      data-testid={`claim-${claim.claim_id}`}
    >
      {/* Header */}
      <div className="claim-card-header">
        <label>
          {claim.claim_type === "decision" ? "Decision" : "Action item"}
          {claim.state && claim.state !== "accepted" ? ` · ${claim.state}` : ""}
        </label>
        <div className="claim-card-badges">
          <StatusBadge label={final.label} />
          <ReviewBadge reviewStatus={reviewStatus} />
        </div>
      </div>

      {/* Read mode content */}
      {!editMode && (
        <>
          <p className="claim-text">{displayText}</p>

          {claim.claim_type === "action" && (
            <p className="claim-meta">
              Owner: {displayOwner || "Not assigned"} · Due:{" "}
              {displayDeadline || "Not specified"}
            </p>
          )}

          <EvidenceList
            title="Cited evidence"
            utterances={claim.evidence}
            onSeek={onSeek}
            onFocusEvidence={onFocusEvidence}
            testId="cited-evidence"
          />

          {claim.contradiction && (
            <EvidenceList
              title="Possibly contradicting evidence"
              utterances={claim.contradiction.evidence}
              onSeek={onSeek}
              onFocusEvidence={onFocusEvidence}
              testId="contradicting-evidence"
            />
          )}

          {alternatives.length > 0 && (
            <EvidenceList
              title="Independently retrieved evidence"
              utterances={alternatives}
              onSeek={onSeek}
              onFocusEvidence={onFocusEvidence}
              testId="retrieved-evidence"
            />
          )}

          {final.label === "review" && final.reasons?.length > 0 && (
            <span className="claim-reasons-title">Why it is held for review</span>
          )}
          <ReasonList reasons={final.reasons} />

          {claim.correction && (
            <p className="claim-correction" data-testid="claim-correction">
              Reviewer {claim.correction.action} the suggested evidence (
              {claim.correction.proposed_utt_ids.join(", ")}).
            </p>
          )}

          <SuggestedEvidenceCard
            suggestion={claim.suggestion}
            busy={busy}
            onFocusEvidence={onFocusEvidence}
            onDecision={(action) => onSuggestionDecision(claim.claim_id, action)}
          />

          {/* Phase 3 edit button — only when an edit handler is provided and draft is open */}
          {onEdit && !isApproved && (
            <div className="claim-card-edit-row">
              <button
                type="button"
                className="edit-button edit-button-sm"
                onClick={() => { setEditMode(true); setSaveError(""); }}
                data-testid="open-edit"
              >
                <Icon name="pencil" /> Edit item
              </button>
            </div>
          )}

          {isApproved && (
            <p className="claim-approved-note" data-testid="approved-note">
              ✓ Approved — reopen the review to make changes.
            </p>
          )}
        </>
      )}

      {/* Edit mode */}
      {editMode && (
        <InlineEditForm
          claim={{
            ...claim,
            text: displayText,
            owner: displayOwner,
            deadline_text: displayDeadline,
          }}
          onSave={handleSave}
          onCancel={() => { setEditMode(false); setSaveError(""); }}
          saving={saving}
          saveError={saveError}
        />
      )}
    </div>
  );
}
