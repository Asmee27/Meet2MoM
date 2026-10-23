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

const STATUS_LABEL = { done: "Done", delayed: "Delayed", no_update: "No update", open: "Open" };

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
    <section className="minutes-block commitment-panel" aria-labelledby="commitments-title">
      <h3 id="commitments-title">Commitment ledger</h3>

      {data.created_here.length > 0 && (
        <>
          <h4>Commitments from this meeting</h4>
          <ul className="commitment-list">
            {data.created_here.map((c) => (
              <li key={c.commitment_id} data-testid={`commitment-${c.commitment_id}`}>
                <strong>{c.text}</strong>
                <span className={`commitment-status status-${c.status}`}>{STATUS_LABEL[c.status] || c.status}</span>
                <small>
                  {c.assignee ? `Assignee: ${c.assignee}` : "No assignee"}
                  {!c.assignee_user_id && c.assignee ? " (not linked to an account)" : ""}
                  {c.deadline ? ` · Due: ${c.deadline}` : ""}
                  {` · Evidence: ${(c.evidence_ids || []).join(", ") || "none"}`}
                </small>
              </li>
            ))}
          </ul>
        </>
      )}

      <h4>Follow-ups of earlier commitments</h4>
      <p className="empty-state-text">
        Check outstanding commitments against this meeting&apos;s transcript. Proposals cite the utterances they
        rely on and change nothing until you confirm or correct them.
      </p>
      <button type="button" className="commitment-check" onClick={propose} disabled={busy || !meetingId}>
        {busy ? "Checking…" : "Check open commitments"}
      </button>
      {error && <div className="minutes-warning" role="alert">{error}</div>}

      {data.follow_ups.length === 0 ? (
        <p className="empty-state-text">No follow-ups for this meeting yet.</p>
      ) : (
        <ul className="commitment-list">
          {data.follow_ups.map((f) => (
            <li key={f.commitment_id} data-testid={`follow-up-${f.commitment_id}`}>
              <strong>{f.text}</strong>
              <div>
                Proposed: <span className={`commitment-status status-${f.proposed_status}`}>{STATUS_LABEL[f.proposed_status]}</span>
                {f.review_status !== "proposed" && (
                  <span>
                    {" "}· {f.review_status === "confirmed" ? "Confirmed" : "Corrected to"} {STATUS_LABEL[f.final_status]}
                    {f.reviewer ? ` by ${f.reviewer}` : ""}
                  </span>
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
                <small>No utterance in this meeting supports a status change.</small>
              )}
              {f.review_status === "proposed" && (
                <div className="commitment-actions">
                  <input
                    aria-label={`Note for ${f.text}`}
                    placeholder="Optional note"
                    value={notes[f.commitment_id] || ""}
                    onChange={(e) => setNotes({ ...notes, [f.commitment_id]: e.target.value })}
                  />
                  {["done", "delayed", "no_update"].map((status) => (
                    <button key={status} type="button" disabled={busy} onClick={() => review(f.commitment_id, status)}>
                      {status === f.proposed_status ? `Confirm: ${STATUS_LABEL[status]}` : `Mark ${STATUS_LABEL[status]}`}
                    </button>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
