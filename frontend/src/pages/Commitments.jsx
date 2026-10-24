// Phase 8: the signed-in user's commitment ledger (GET /api/commitments).
// A commitment's status changes only when a reviewer confirms or corrects a
// proposal; AI proposals are labelled as proposals until then.
import { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "../components/AppShell";
import { Alert, Badge, Button, EmptyState, Icon, SkeletonRows } from "../components/ui";
import { getLedger, listMeetings } from "../services/api";
import { formatDateTime, meetingTitle } from "../format";

const STATUS = {
  open: { tone: "info", label: "Open", icon: "clock" },
  done: { tone: "success", label: "Done", icon: "checkCircle" },
  delayed: { tone: "warning", label: "Delayed", icon: "warning" },
  no_update: { tone: "neutral", label: "No update", icon: "skip" },
};

const FILTERS = [
  { id: "outstanding", label: "Outstanding" },
  { id: "done", label: "Done" },
  { id: "all", label: "All" },
];

function StatusBadge({ status }) {
  const s = STATUS[status] || { tone: "neutral", label: status, icon: null };
  return <Badge tone={s.tone} icon={s.icon}>{s.label}</Badge>;
}

// The latest proposal or review for a commitment, labelled by who decided it.
function LatestUpdate({ history }) {
  const latest = (history || [])[history.length - 1];
  if (!latest) return <span className="ledger-muted">No follow-up checked yet</span>;
  if (latest.review_status === "proposed") {
    return (
      <div className="ledger-update">
        <Badge tone="accent" icon="sparkle">AI proposal: {STATUS[latest.proposed_status]?.label || latest.proposed_status}</Badge>
        <span className="ledger-muted">Awaiting reviewer confirmation</span>
      </div>
    );
  }
  return (
    <div className="ledger-update">
      <Badge tone="success" icon="shield">
        {latest.review_status === "confirmed" ? "Confirmed" : "Corrected"}: {STATUS[latest.final_status]?.label || latest.final_status}
      </Badge>
      <span className="ledger-muted">
        {latest.reviewer ? `by ${latest.reviewer}` : "by a reviewer"}
        {latest.reviewed_at ? ` · ${formatDateTime(latest.reviewed_at)}` : ""}
      </span>
      {latest.note && <span className="ledger-note">“{latest.note}”</span>}
    </div>
  );
}

export default function Commitments({ onOpenMeeting }) {
  const [state, setState] = useState({ status: "loading", items: [], error: "" });
  const [titles, setTitles] = useState({});
  const [filter, setFilter] = useState("outstanding");

  const load = useCallback(() => {
    setState((s) => ({ ...s, status: "loading", error: "" }));
    getLedger()
      .then((data) => setState({ status: "ready", items: data?.commitments || [], error: "" }))
      .catch((err) => setState({ status: "error", items: [], error: err?.message || "Could not load the ledger." }));
    listMeetings()
      .then((meetings) => setTitles(Object.fromEntries((meetings || []).map((m) => [m.meeting_id, m]))))
      .catch(() => setTitles({}));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const counts = useMemo(() => {
    const items = state.items;
    return {
      outstanding: items.filter((c) => c.status === "open" || c.status === "delayed").length,
      done: items.filter((c) => c.status === "done").length,
      all: items.length,
      pending: items.filter((c) => c.history?.length && c.history[c.history.length - 1].review_status === "proposed").length,
    };
  }, [state.items]);

  const visible = state.items.filter((c) =>
    filter === "all" ? true : filter === "done" ? c.status === "done" : c.status === "open" || c.status === "delayed"
  );

  return (
    <main className="page ui-enter" aria-labelledby="commitments-title">
      <PageHeader
        title={<span id="commitments-title">Commitments</span>}
        description="Action items from approved minutes. Follow-up meetings propose updates with cited evidence; a status changes only when a reviewer confirms it."
      />

      {state.status === "ready" && counts.pending > 0 && (
        <Alert tone="info" title={`${counts.pending} AI proposal${counts.pending === 1 ? "" : "s"} awaiting review`}>
          <p>Open the follow-up meeting&apos;s minutes to confirm or correct each proposal.</p>
        </Alert>
      )}

      <section className="ui-card meetings-card" aria-label="Commitment ledger">
        <div className="meetings-card__toolbar">
          <h2 className="ui-card__title">Ledger</h2>
          {state.status === "ready" && state.items.length > 0 && (
            <div className="ui-tabs" role="group" aria-label="Filter by status">
              {FILTERS.map((f) => (
                <button key={f.id} type="button" className="ui-tab" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>
                  {f.label}
                  <span className="ui-tab__count">{counts[f.id]}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {state.status === "loading" && <div className="ui-card__body"><SkeletonRows rows={3} label="Loading commitments" /></div>}

        {state.status === "error" && (
          <div className="ui-card__body">
            <Alert tone="danger" title="The ledger could not be loaded" actions={<Button size="sm" icon="refresh" onClick={load}>Try again</Button>}>
              <p>{state.error}</p>
            </Alert>
          </div>
        )}

        {state.status === "ready" && state.items.length === 0 && (
          <EmptyState icon="ledger" title="No commitments yet">
            Commitments are created when you approve minutes that contain action items.
          </EmptyState>
        )}

        {state.status === "ready" && state.items.length > 0 && visible.length === 0 && (
          <EmptyState icon="ledger" title="Nothing in this view">No commitments have this status.</EmptyState>
        )}

        {state.status === "ready" && visible.length > 0 && (
          <div className="ui-table-wrap">
            <table className="ui-table meetings-table ledger-table">
              <thead>
                <tr>
                  <th scope="col">Commitment</th>
                  <th scope="col">Status</th>
                  <th scope="col">Latest follow-up</th>
                  <th scope="col">From meeting</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((c) => {
                  const source = titles[c.source_meeting_id];
                  return (
                    <tr key={c.commitment_id}>
                      <td data-label="Commitment">
                        <div className="ledger-task">{c.text}</div>
                        <div className="ui-meta">
                          <span>{c.assignee ? `Assignee: ${c.assignee}` : "No assignee"}</span>
                          <span>{c.deadline ? `Due: ${c.deadline}` : "No deadline"}</span>
                          <span className="ui-mono" title="Cited utterances">
                            <Icon name="quote" size={12} /> {(c.evidence_ids || []).join(", ") || "no evidence"}
                          </span>
                        </div>
                      </td>
                      <td data-label="Status"><StatusBadge status={c.status} /></td>
                      <td data-label="Latest follow-up"><LatestUpdate history={c.history || []} /></td>
                      <td data-label="From meeting">
                        <button type="button" className="meetings-table__title" onClick={() => onOpenMeeting(source || { meeting_id: c.source_meeting_id })}>
                          {source ? meetingTitle(source) : c.source_meeting_id.slice(0, 12)}
                        </button>
                        <div className="meetings-table__sub">{formatDateTime(c.created_at)}</div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
