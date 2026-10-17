// Presentational components for persisted claim verification (Phase 1).
// They only render what the backend persisted; no scores are computed here.
import "./ClaimVerification.css";
import { STATUS_LABELS, formatTimestamp } from "./verificationFormat";

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
      {STATUS_LABELS[label] || label}
    </span>
  );
}

export function EvidenceList({ title, utterances, testId }) {
  if (!utterances || utterances.length === 0) return null;
  return (
    <div className="transcript-evidence" data-testid={testId}>
      <span className="evidence-label">{title}</span>
      {utterances.map((u) => (
        <div className="evidence-utterance" key={u.utt_id}>
          <span className="evidence-speaker">
            {u.speaker || "Unknown speaker"} · {formatTimestamp(u.start)} · {u.utt_id}
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

export function SuggestedEvidenceCard({ suggestion, onDecision, busy }) {
  if (!suggestion) return null;
  return (
    <div className="suggested-evidence-card" data-testid="suggested-evidence">
      <strong>Suggested evidence</strong>
      <p>
        The semantic verifier found utterances that support this claim better than the
        current citation (support {formatScore(suggestion.support)} vs{" "}
        {formatScore(suggestion.cited_support)}).
      </p>
      <EvidenceList title="Suggested span" utterances={suggestion.evidence} testId="suggested-utterances" />
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

export function ClaimReviewCard({ claim, onSuggestionDecision, busy }) {
  const final = claim.verification.final;
  const semantic = claim.verification.semantic;
  const alternatives =
    semantic && !claim.suggestion
      ? (semantic.best_evidence || []).filter((u) => !claim.evidence_ids.includes(u.utt_id))
      : [];
  return (
    <div className={`claim-card claim-card-${final.label}`} data-testid={`claim-${claim.claim_id}`}>
      <div className="claim-card-header">
        <label>
          {claim.claim_type === "decision" ? "Decision" : "Action item"}
          {claim.state && claim.state !== "accepted" ? ` · ${claim.state}` : ""}
        </label>
        <StatusBadge label={final.label} />
      </div>

      <p className="claim-text">{claim.text}</p>

      {claim.claim_type === "action" && (
        <p className="claim-meta">
          Owner: {claim.owner || "Not assigned"} · Due:{" "}
          {claim.deadline_resolved || claim.deadline_text || "Not specified"}
        </p>
      )}

      <EvidenceList title="Cited evidence" utterances={claim.evidence} testId="cited-evidence" />

      {claim.contradiction && (
        <EvidenceList
          title="Possibly contradicting evidence"
          utterances={claim.contradiction.evidence}
          testId="contradicting-evidence"
        />
      )}

      {alternatives.length > 0 && (
        <EvidenceList
          title="Independently retrieved evidence"
          utterances={alternatives}
          testId="retrieved-evidence"
        />
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
        onDecision={(action) => onSuggestionDecision(claim.claim_id, action)}
      />
    </div>
  );
}
