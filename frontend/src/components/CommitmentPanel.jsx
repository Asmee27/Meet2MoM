// Phase 9: evidence-linked commitment ledger for one meeting.
// Commitments are created when minutes are approved. A follow-up meeting gets
// *proposed* statuses with cited utterances; nothing changes until a reviewer
// confirms or corrects the proposal.
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
                    <span>
                      {c.assignee ? `Assignee: ${c.assignee}` : "No assignee"}
                      {!c.assignee_user_id && c.assignee ? " (not linked to an account)" : ""}
                    </span>
                    {c.deadline && <span>Due: {c.deadline}</span>}
                    <span>Evidence: <span className="ui-mono">{(c.evidence_ids || []).join(", ") || "none"}</span></span>
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
            {data.follow_ups.map((f) => (
              <li key={f.commitment_id} data-testid={`follow-up-${f.commitment_id}`}>
                <strong>{f.text}</strong>
                <div className="commitment-list__badges">
                  <Badge tone="accent" icon="sparkle">AI proposal: {STATUS_LABEL[f.proposed_status]}</Badge>
                  {f.review_status === "proposed" ? (
                    <span className="ui-help">Awaiting reviewer confirmation</span>
                  ) : (
                    <Badge tone="success" icon="shield">
                      {f.review_status === "confirmed" ? "Confirmed" : "Corrected to"} {STATUS_LABEL[f.final_status]}
                      {f.reviewer ? ` by ${f.reviewer}` : ""}
                    </Badge>
                  )}
                </div>
                {f.evidence && f.evidence.length > 0 ? (
                  <ul className="commitment-evidence">
                    {f.evidence.map((e) => (
                      <li key={e.utt_id}>
                        <code>{e.utt_id}</code> {e.speaker} ({formatTime(e.start)}): “{e.text}”
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="ui-help">No utterance in this meeting supports a status change.</p>
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
                    {["done", "delayed", "no_update"].map((status) => (
                      <Button
                        key={status}
                        size="sm"
                        variant={status === f.proposed_status ? "primary" : "secondary"}
                        disabled={busy}
                        onClick={() => review(f.commitment_id, status)}
                      >
                        {status === f.proposed_status ? `Confirm: ${STATUS_LABEL[status]}` : `Mark ${STATUS_LABEL[status]}`}
                      </Button>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
