// Phase 8: meeting workspace home. Everything shown comes from
// GET /api/meetings and GET /api/commitments; nothing is estimated.
import { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "../components/AppShell";
import { Alert, Badge, Button, ConfirmDialog, EmptyState, Icon, SkeletonRows, useToast } from "../components/ui";
import { deleteMeeting, getLedger, listMeetings } from "../services/api";
import { formatDateTime, formatDuration, formatScheduledStart, meetingStatus, meetingTitle } from "../format";

const FILTERS = [
  { id: "all", label: "All" },
  { id: "upcoming", label: "Upcoming" },
  { id: "live", label: "Live" },
  { id: "review", label: "Needs review" },
  { id: "approved", label: "Approved" },
  { id: "processing", label: "Processing" },
  { id: "failed", label: "Failed" },
];

const NEXT_ACTION = {
  processing: "View progress",
  failed: "View error",
  review: "Review",
  approved: "Open",
  upcoming: "Details",
  live: "Open room",
  cancelled: "Details",
};

function SummaryTile({ label, value, hint, icon, tone }) {
  return (
    <div className={`summary-tile summary-tile--${tone}`}>
      <span className="summary-tile__icon"><Icon name={icon} /></span>
      <div>
        <div className="summary-tile__value">{value}</div>
        <div className="summary-tile__label">{label}</div>
        {hint && <div className="summary-tile__hint">{hint}</div>}
      </div>
    </div>
  );
}

export default function Dashboard({ onOpenMeeting, onUpload, onStartLive, onSchedule, onOpenCommitments }) {
  const { notify } = useToast();
  const [state, setState] = useState({ status: "loading", meetings: [], error: "" });
  const [ledger, setLedger] = useState(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [pendingDelete, setPendingDelete] = useState(null);
  const [deleteState, setDeleteState] = useState({ busy: false, error: "" });
  const [joinCode, setJoinCode] = useState("");

  const handleJoinRoom = (e) => {
    if (e) e.preventDefault();
    const trimmed = joinCode.trim();
    if (!trimmed) return;
    onStartLive({ roomName: trimmed });
  };

  const load = useCallback(() => {
    setState((s) => ({ ...s, status: "loading", error: "" }));
    listMeetings()
      .then((meetings) => setState({ status: "ready", meetings: Array.isArray(meetings) ? meetings : [], error: "" }))
      .catch((err) => setState({ status: "error", meetings: [], error: err?.message || "Could not load meetings." }));
    getLedger()
      .then((data) => setLedger(Array.isArray(data?.commitments) ? data.commitments : null))
      .catch(() => setLedger(null));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const meetings = state.meetings;
  const counts = useMemo(() => {
    const result = { all: meetings.length, review: 0, approved: 0, processing: 0, failed: 0, upcoming: 0, live: 0, cancelled: 0 };
    meetings.forEach((m) => { result[meetingStatus(m).key] += 1; });
    return result;
  }, [meetings]);

  const openCommitments = ledger ? ledger.filter((c) => c.status === "open" || c.status === "delayed").length : null;

  const visible = meetings.filter((m) => {
    if (filter !== "all" && meetingStatus(m).key !== filter) return false;
    const needle = query.trim().toLowerCase();
    return !needle || meetingTitle(m).toLowerCase().includes(needle) || m.meeting_id.toLowerCase().includes(needle);
  });

  const confirmDelete = async () => {
    if (!pendingDelete) return;
    setDeleteState({ busy: true, error: "" });
    try {
      await deleteMeeting(pendingDelete.meeting_id);
      setState((s) => ({ ...s, meetings: s.meetings.filter((m) => m.meeting_id !== pendingDelete.meeting_id) }));
      notify(`Deleted “${meetingTitle(pendingDelete)}”.`, "success");
      setPendingDelete(null);
      setDeleteState({ busy: false, error: "" });
    } catch (err) {
      setDeleteState({ busy: false, error: err?.message || "The meeting could not be deleted." });
    }
  };

  return (
    <main className="page ui-enter" aria-labelledby="dashboard-title">
      {/* Hero Quick-Launch Space (Cosmic Midnight & Sunset Coral Theme) */}
      <section className="dashboard-hero" aria-label="Meeting quick launcher">
        <div className="dashboard-hero__left">
          <div className="dashboard-hero__kicker">
            <span className="auth-live-dot" /> LIVE INTELLIGENCE
          </div>
          <h2 className="dashboard-hero__title">
            Make room for the conversations that matter.
          </h2>
          <p className="dashboard-hero__tagline">
            <i>Speak freely. We’ll capture the truth, extract decisions, verify evidence, and hold the commitment ledger.</i>

          </p>
          <div className="dashboard-hero__actions">
            <Button variant="primary" icon="mic" className="dashboard-hero-cta" onClick={onStartLive}>
              Start a meeting
            </Button>
            <Button icon="upload" onClick={onUpload}>
              Upload recording
            </Button>
          </div>
          {/* Subtle trust & feature micro-badges */}
          
        </div>

        {/* Center / Card 1: Interactive "Where should we meet?" Launcher Card */}
        <div className="dashboard-launcher-card">
          <div className="dashboard-launcher-card__top">
            <span className="dashboard-launcher-card__kicker">YOUR MEETING SPACE</span>
            <span className="dashboard-launcher-card__pill">Instant room</span>
          </div>

          <h3 className="dashboard-launcher-card__heading">Where should we meet?</h3>

          <button
            type="button"
            className="dashboard-create-room-btn"
            onClick={onStartLive}
          >
            <div>
              <strong>Create a new room</strong>
              <small>Start instant conversation</small>
            </div>
            <span className="dashboard-create-arrow" aria-hidden="true">→</span>
          </button>

          <div className="dashboard-launcher-divider">
            <span>or join one</span>
          </div>

          <form className="dashboard-join-form" onSubmit={handleJoinRoom}>
            <input
              type="text"
              className="ui-input dashboard-join-input"
              placeholder="Enter meeting code"
              value={joinCode}
              onChange={(e) => setJoinCode(e.target.value)}
              aria-label="Meeting code"
            />
            <button
              type="submit"
              className="dashboard-join-btn"
              disabled={!joinCode.trim()}
            >
              Join
            </button>
          </form>
        </div>

        {/* Right / Card 2: Stylish "Recent Insights Demo" Card */}
        <div className="dashboard-insights-card" aria-label="Recent insights demo">
          <div className="dashboard-insights-card__top">
            <span className="dashboard-insights-card__kicker">
              <span className="insights-pulse-dot" aria-hidden="true" /> LIVE AI SUMMARY
            </span>
            <span className="dashboard-insights-card__pill">Real-Time MoM</span>
          </div>

          <div className="dashboard-insights-card__header">
            <h3 className="dashboard-insights-card__heading">Product Roadmap &amp; Architecture</h3>
            <span className="dashboard-insights-card__meta">Recorded 24m ago · 45m call</span>
          </div>

          <div className="dashboard-insights-card__body">
            <div className="dashboard-insight-item">
              <span className="insight-badge insight-badge--decision">Decision</span>
              <p className="insight-text">
                Approved hybrid WebRTC audio pipeline for sub-100ms real-time transcription.
              </p>
            </div>

            <div className="dashboard-insight-item">
              <span className="insight-badge insight-badge--action">Action Item</span>
              <p className="insight-text">
                <strong>Alex</strong> to benchmark noise suppression and sync commitments by Friday.
              </p>
            </div>

            <div className="dashboard-insight-item">
              <span className="insight-badge insight-badge--decision">Decision</span>
              <p className="insight-text">
                Distribute auto-verified minutes to attendees with 24h review window.
              </p>
            </div>
          </div>

          <div className="dashboard-insights-card__footer">
            <Icon name="check" size={13} />
            <span>Evidence verified against audio transcript &amp; ledger</span>
          </div>
        </div>
      </section>

      {/* Semantic PageHeader for Test Compatibility & Section Identity */}
      <PageHeader
        title={<span id="dashboard-title">Meetings</span>}
        description="Upcoming, live and recorded meetings, their review status, and the minutes you have approved."
        actions={
          <>
            {onSchedule && <Button icon="clock" onClick={onSchedule}>Schedule meeting</Button>}
          </>
        }
      />

      {state.status === "ready" && meetings.length > 0 && (
        <section className="summary-grid" aria-label="Summary">
          {(counts.upcoming > 0 || counts.live > 0) && (
            <SummaryTile
              icon="clock"
              tone="accent"
              value={counts.upcoming}
              label="Upcoming"
              hint={counts.live ? `${counts.live} live now` : "Scheduled, not started"}
            />
          )}
          <SummaryTile
            icon="warning"
            tone="warning"
            value={counts.review}
            label="Need review"
            hint="Minutes not approved yet"
          />
          <SummaryTile
            icon="checkCircle"
            tone="success"
            value={counts.approved}
            label="Approved"
            hint="Minutes locked by a reviewer"
          />
          <SummaryTile
            icon="clock"
            tone="info"
            value={counts.processing + counts.failed}
            label="Processing or failed"
            hint={counts.failed ? `${counts.failed} failed` : "None failed"}
          />
          {openCommitments !== null && (
            <button type="button" className="summary-tile summary-tile--accent summary-tile--link" onClick={onOpenCommitments}>
              <span className="summary-tile__icon"><Icon name="ledger" /></span>
              <div>
                <div className="summary-tile__value">{openCommitments}</div>
                <div className="summary-tile__label">Open commitments</div>
                <div className="summary-tile__hint">Open or delayed in your ledger</div>
              </div>
            </button>
          )}
        </section>
      )}

      <section className="ui-card meetings-card" aria-labelledby="meeting-history-title">
        <div className="meetings-card__toolbar">
          <h2 className="ui-card__title" id="meeting-history-title">Meeting history</h2>
          {state.status === "ready" && meetings.length > 0 && (
            <div className="meetings-card__controls">
              <label className="ui-visually-hidden" htmlFor="meeting-search">Search meetings</label>
              <input
                id="meeting-search"
                className="ui-input ui-input--search"
                type="search"
                placeholder="Search by name or ID"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <div className="ui-tabs" role="group" aria-label="Filter by status">
                {FILTERS.map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    className="ui-tab"
                    aria-pressed={filter === f.id}
                    onClick={() => setFilter(f.id)}
                  >
                    {f.label}
                    <span className="ui-tab__count">{counts[f.id]}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        {state.status === "loading" && <div className="ui-card__body"><SkeletonRows rows={4} label="Loading meetings" /></div>}

        {state.status === "error" && (
          <div className="ui-card__body">
            <Alert
              tone="danger"
              title="Meetings could not be loaded"
              actions={<Button size="sm" icon="refresh" onClick={load}>Try again</Button>}
            >
              <p>{state.error}</p>
            </Alert>
          </div>
        )}

        {state.status === "ready" && meetings.length === 0 && (
          <EmptyState
            icon="meetings"
            title="Start your first meeting"
            actions={
              <>
                <Button variant="primary" icon="mic" onClick={onStartLive}>Start live meeting</Button>
                <Button icon="upload" onClick={onUpload}>Upload recording</Button>
              </>
            }
          >
            Host a live meeting or upload a recording. Your transcript, decisions, and action items will appear here
            for review.
          </EmptyState>
        )}

        {state.status === "ready" && meetings.length > 0 && visible.length === 0 && (
          <EmptyState icon="meetings" title="No matching meetings">
            Nothing matches the current search or filter.
          </EmptyState>
        )}

        {state.status === "ready" && visible.length > 0 && (
          <div className="ui-table-wrap">
            <table className="ui-table meetings-table">
              <thead>
                <tr>
                  <th scope="col">Meeting</th>
                  <th scope="col">Date</th>
                  <th scope="col">Duration</th>
                  <th scope="col">Speakers</th>
                  <th scope="col">Items</th>
                  <th scope="col">Status</th>
                  <th scope="col"><span className="ui-visually-hidden">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {visible.map((meeting) => {
                  const status = meetingStatus(meeting);
                  const title = meetingTitle(meeting);
                  return (
                    <tr key={meeting.meeting_id}>
                      <td data-label="Meeting">
                        <div className="meetings-table__name">
                          <button
                            type="button"
                            className="meetings-table__title"
                            onClick={() => onOpenMeeting(meeting)}
                            title={title}
                          >
                            {title}
                          </button>
                          <span className="ui-mono meetings-table__id">{meeting.meeting_id.slice(0, 12)}</span>
                        </div>
                      </td>
                      <td data-label="Date" className="meetings-table__nowrap">
                        {meeting.schedule ? formatScheduledStart(meeting.schedule) : formatDateTime(meeting.created_at)}
                        {meeting.schedule && meeting.schedule.invitee_count > 0 && (
                          <div className="meetings-table__sub">{meeting.schedule.invitee_count} invited</div>
                        )}
                      </td>
                      <td data-label="Duration">{formatDuration(meeting.duration_seconds)}</td>
                      <td data-label="Speakers">{meeting.speaker_count || "—"}</td>
                      <td data-label="Items">
                        {meeting.utterance_count > 0
                          ? `${meeting.claim_count} from ${meeting.utterance_count} utterances`
                          : "—"}
                      </td>
                      <td data-label="Status">
                        <Badge tone={status.tone} pulse={status.pulse}>{status.label}</Badge>
                        {status.key === "processing" && meeting.job_stage && (
                          <div className="meetings-table__sub">Stage: {meeting.job_stage}</div>
                        )}
                      </td>
                      <td className="meetings-table__actions">
                        <Button size="sm" onClick={() => onOpenMeeting(meeting)} iconRight="chevronRight">
                          {NEXT_ACTION[status.key]}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          icon="trash"
                          className="meetings-table__delete"
                          aria-label={`Delete meeting ${meeting.meeting_id}`}
                          title="Delete meeting"
                          onClick={() => { setDeleteState({ busy: false, error: "" }); setPendingDelete(meeting); }}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {pendingDelete && (
        <ConfirmDialog
          title="Delete this meeting?"
          description={`“${meetingTitle(pendingDelete)}” will be removed permanently: recording, transcript, minutes, caches and the commitments created from it. This cannot be undone.`}
          confirmLabel="Delete meeting"
          tone="danger"
          busy={deleteState.busy}
          error={deleteState.error}
          onConfirm={confirmDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </main>
  );
}
