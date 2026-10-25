// Phase 9: evidence-linked commitment ledger for one meeting.
// Commitments are created when minutes are approved. A follow-up meeting gets
// *proposed* statuses with cited utterances; nothing changes until a reviewer
// confirms or corrects the proposal.
// Phase 9.1: the current confirmed status, the AI proposal (with its reason,
// or "needs review" for conflicting evidence) and the reviewer's decision are
// shown separately, and every citation names its meeting as well as its
// utterance, because utterance IDs (u00000…) restart in every meeting.
import { useCallback, useEffect, useState } from "react";
import {
  getMeetingCommitments,
  proposeCommitmentUpdates,
  reviewCommitmentUpdate,
} from "../services/api";
import { Alert, Badge, Button, Icon } from "./ui";

const STATUS_LABEL = { done: "Done", delayed: "Delayed", no_update: "No update", open: "Open" };
const STATUS_TONE = { done: "success", delayed: "warning", no_update: "neutral", open: "info" };
const STATUS_ICON = { done: "checkCircle", delayed: "warning", no_update: "skip", open: "clock" };

function formatTime(seconds) {
  if (typeof seconds !== "number") return "";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

const shortId = (id) => String(id || "").slice(0, 8);

// Who was assigned the task in the transcript. A spoken name is never treated
// as a registered account.
export function AssigneeText({ assignee, assigneeUserId }) {
  if (!assignee) return <span>No assignee stated</span>;
  return (
    <span>
      Assigned in the transcript: {assignee}
      {!assigneeUserId && <span className="ui-help"> (identity not linked to an account)</span>}
    </span>
  );
}

function Citations({ items, meetingLabel }) {
  return (
    <ul className="commitment-evidence">
      {items.map((e) => (
        <li key={e.ref || e.utt_id} data-ref={e.ref}>
          <span className="ui-help">{meetingLabel} · </span>
          <code>{e.utt_id}</code> {e.speaker} ({formatTime(e.start)}): “{e.text}”
        </li>
      ))}
    </ul>
  );
}

function ProposalBadge({ followUp }) {
  if (followUp.needs_review) {
    return <Badge tone="warning" icon="warning">AI proposal: needs review</Badge>;
  }
  return <Badge tone="accent" icon="sparkle">AI proposal: {STATUS_LABEL[followUp.proposed_status]}</Badge>;
}

function ReviewerDecision({ followUp }) {
  if (followUp.review_status === "proposed") {
    return <span className="ui-help">Awaiting reviewer decision</span>;
  }
  const corrected = followUp.review_status === "corrected";
  return (
    <Badge tone="success" icon="shield">
      Reviewer {corrected ? "set" : "confirmed"}: {STATUS_LABEL[followUp.final_status]}
      {followUp.reviewer ? ` · ${followUp.reviewer}` : ""}
      {corrected ? " (changed the AI proposal)" : ""}
    </Badge>
  );
}

export default function CommitmentPanel({ meetingId, reviewerName = "" }) {
  const [data, setData] = useState({ created_here: [], follow_ups: [] });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notes, setNotes] = useState({});

  const load = useCallback(() => {
    if (!meetingId) return;
    getMeetingCommitments(meetingId)
      .then((result) => setData(result))
      .catch((err) => setError(err.message || "Could not load commitments."));
  }, [meetingId]);

  useEffect(() => {
    load();
  }, [load]);

  const propose = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await proposeCommitmentUpdates(meetingId);
      setData((prev) => ({ ...prev, follow_ups: result.follow_ups }));
    } catch (err) {
      setError(err.message || "Could not check commitments.");
    } finally {
      setBusy(false);
    }
  };

  const review = async (commitmentId, status) => {
    setBusy(true);
    setError("");
    try {
      const result = await reviewCommitmentUpdate(meetingId, commitmentId, status, notes[commitmentId] || "", reviewerName);
      setData((prev) => ({ ...prev, follow_ups: result.follow_ups }));
    } catch (err) {
      setError(err.message || "Could not save the review.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="ui-card commitment-panel" aria-labelledby="commitments-title">
      <div className="ui-card__header">
        <h3 className="ui-card__title" id="commitments-title">
          <Icon name="ledger" size={18} /> Commitment ledger
        </h3>
      </div>

      <div className="ui-card__body commitment-panel__body">
        {data.created_here.length > 0 && (
          <div>
            <h4 className="ui-section-title">Commitments from this meeting</h4>
            <ul className="commitment-list">
              {data.created_here.map((c) => (
                <li key={c.commitment_id} data-testid={`commitment-${c.commitment_id}`}>
                  <div className="commitment-list__head">
                    <strong>{c.text}</strong>
                    <Badge tone={STATUS_TONE[c.status] || "neutral"} icon={STATUS_ICON[c.status]}>
                      {STATUS_LABEL[c.status] || c.status}
                    </Badge>
                  </div>
                  <div className="ui-meta">
                    <AssigneeText assignee={c.assignee} assigneeUserId={c.assignee_user_id} />
                    {c.deadline && <span>Due: {c.deadline}</span>}
                    <span>
                      Evidence (this meeting): <span className="ui-mono">{(c.evidence_ids || []).join(", ") || "none"}</span>
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div>
          <h4 className="ui-section-title">Follow-ups of earlier commitments</h4>
          <p className="ui-help commitment-panel__intro">
            Check outstanding commitments against this meeting&apos;s transcript. Proposals cite the utterances they
            rely on and change nothing until you confirm or correct them.
          </p>
          <Button icon="refresh" size="sm" onClick={propose} disabled={busy || !meetingId} loading={busy}>
            {busy ? "Checking…" : "Check open commitments"}
          </Button>
          {error && <Alert tone="danger" className="commitment-panel__error">{error}</Alert>}
        </div>

        {data.follow_ups.length === 0 ? (
          <p className="ui-help">No follow-ups for this meeting yet.</p>
        ) : (
          <ul className="commitment-list">
            {data.follow_ups.map((f) => {
              const unchangedNote = f.review_status === "proposed" && f.proposed_status === "no_update"
                && f.commitment_status && f.commitment_status !== "open";
              return (
                <li key={f.commitment_id} data-testid={`follow-up-${f.commitment_id}`}>
                  <strong>{f.text}</strong>
                  <div className="ui-meta">
                    <AssigneeText assignee={f.assignee} assigneeUserId={f.assignee_user_id} />
                    <span>
                      From meeting <span className="ui-mono">{shortId(f.source_meeting_id)}</span>
                      {(f.source_evidence || []).length > 0 && (
                        <> · evidence <span className="ui-mono">{f.source_evidence.map((e) => e.utt_id).join(", ")}</span></>
                      )}
                    </span>
                  </div>
                  <div className="commitment-list__badges">
                    <span className="commitment-status" data-testid="current-status">
                      Current status:{" "}
                      <Badge tone={STATUS_TONE[f.commitment_status] || "neutral"} icon={STATUS_ICON[f.commitment_status]}>
                        {STATUS_LABEL[f.commitment_status] || f.commitment_status}
                      </Badge>
                    </span>
                    <ProposalBadge followUp={f} />
                    <ReviewerDecision followUp={f} />
                  </div>
                  {f.reason && <p className="ui-help" data-testid="proposal-reason">{f.reason}</p>}
                  {unchangedNote && (
                    <p className="ui-help" data-testid="status-unchanged">
                      This meeting gave no new status evidence. The confirmed status stays{" "}
                      {STATUS_LABEL[f.commitment_status]}.
                    </p>
                  )}
                  {f.evidence && f.evidence.length > 0 ? (
                    <Citations items={f.evidence} meetingLabel="This meeting" />
                  ) : (
                    <p className="ui-help">No utterance in this meeting supports a status change.</p>
                  )}
                  {f.context && f.context.length > 0 && (
                    <details className="commitment-context">
                      <summary>Mentioned here, not evidence of a change ({f.context.length})</summary>
                      <Citations items={f.context} meetingLabel="This meeting" />
                    </details>
                  )}
                  {f.review_status === "proposed" && (
                    <div className="commitment-actions">
                      <input
                        className="ui-input"
                        aria-label={`Note for ${f.text}`}
                        placeholder="Optional note"
                        value={notes[f.commitment_id] || ""}
                        onChange={(e) => setNotes({ ...notes, [f.commitment_id]: e.target.value })}
                      />
                      {["done", "delayed", "no_update"].map((status) => {
                        const isProposal = !f.needs_review && status === f.proposed_status;
                        return (
                          <Button
                            key={status}
                            size="sm"
                            variant={isProposal ? "primary" : "secondary"}
                            disabled={busy}
                            onClick={() => review(f.commitment_id, status)}
                          >
                            {isProposal ? `Confirm: ${STATUS_LABEL[status]}` : `Mark ${STATUS_LABEL[status]}`}
                          </Button>
                        );
                      })}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}
