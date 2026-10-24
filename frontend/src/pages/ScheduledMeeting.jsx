// Phase 8.3: a scheduled meeting. The owner sees invitees and their status,
// copies (re-issues) links, revokes, invites more, cancels or starts it.
// Other members see the time and agenda only.
// Phase 8.4: each invitee's invitation email status (one per person), send or
// retry, and the host's approval when a different account opened a link.
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../components/AppShell";
import { Alert, Badge, Button, ConfirmDialog, SkeletonRows, useToast } from "../components/ui";
import {
  addScheduleInvitees,
  approveInviteeAccount,
  cancelScheduledMeeting,
  copyInviteeLink,
  getEmailStatus,
  getSchedule,
  revokeInvitee,
  sendInvitationEmail,
} from "../services/api";
import { EmailStatusBadge, ProviderNotice } from "../components/EmailStatus";
import { formatScheduledStart } from "../format";
import { InviteeInput, InviteLinks, copyText, inviteUrl } from "./ScheduleMeeting";

const STATUS = {
  scheduled: { tone: "accent", label: "Upcoming" },
  started: { tone: "live", label: "Started" },
  cancelled: { tone: "neutral", label: "Cancelled" },
};

const INVITEE_STATUS = {
  pending: { tone: "warning", label: "Not joined yet" },
  accepted: { tone: "success", label: "Accepted" },
  revoked: { tone: "neutral", label: "Revoked" },
};

export default function ScheduledMeeting({ meetingId, hostEmail = "", onStart }) {
  const { notify } = useToast();
  const [state, setState] = useState({ status: "loading", data: null, error: "" });
  const [adding, setAdding] = useState([]);
  const [newLinks, setNewLinks] = useState([]);
  const [busy, setBusy] = useState("");
  const [actionError, setActionError] = useState("");
  const [confirm, setConfirm] = useState(null); // { kind: "cancel" } | { kind: "revoke", invitee }
  const [provider, setProvider] = useState(null);

  useEffect(() => {
    getEmailStatus().then(setProvider).catch(() => setProvider(null));
  }, []);

  const load = useCallback(() => {
    getSchedule(meetingId)
      .then((data) => setState({ status: "ready", data, error: "" }))
      .catch((err) => setState({ status: "error", data: null, error: err?.message || "The meeting could not be loaded." }));
  }, [meetingId]);

  useEffect(() => { load(); }, [load]);

  // Refresh while any invitation email is still queued or being sent.
  const pendingEmail = (state.data?.meeting?.invitees || [])
    .some((i) => ["queued", "sending"].includes(i.email_delivery?.status));
  useEffect(() => {
    if (!pendingEmail) return undefined;
    const timer = setTimeout(load, 4000);
    return () => clearTimeout(timer);
  }, [pendingEmail, load, state.data]);

  if (state.status === "loading") return <main className="page"><SkeletonRows rows={4} label="Loading meeting" /></main>;
  if (state.status === "error") {
    return (
      <main className="page">
        <Alert tone="danger" title="The meeting could not be opened"><p>{state.error}</p></Alert>
      </main>
    );
  }

  const { meeting, is_owner: isOwner } = state.data;
  const status = STATUS[meeting.status] || STATUS.scheduled;
  const open = meeting.status === "scheduled";
  const update = (next) => setState({ status: "ready", data: { ...state.data, meeting: next }, error: "" });

  const run = async (key, action) => {
    setBusy(key);
    setActionError("");
    try {
      await action();
    } catch (err) {
      setActionError(err?.message || "That did not work.");
    } finally {
      setBusy("");
    }
  };

  const copyLink = (invitee) => run(`copy-${invitee.invitee_id}`, async () => {
    const link = await copyInviteeLink(meetingId, invitee.invitee_id);
    const url = inviteUrl(link.link_path);
    const copied = await copyText(url);
    setNewLinks((links) => [link, ...links.filter((l) => l.invitee_id !== link.invitee_id)]);
    notify(
      copied
        ? `New link for ${invitee.email} copied.${link.previous_link_revoked ? " Their previous link no longer works." : ""}`
        : "Copy failed; the new link is shown below.",
      copied ? "success" : "warning",
    );
  });

  const confirmAction = () => run("confirm", async () => {
    if (confirm.kind === "cancel") {
      update((await cancelScheduledMeeting(meetingId)).meeting);
      notify("Meeting cancelled. Every invitation link has stopped working.", "success");
    } else {
      update((await revokeInvitee(meetingId, confirm.invitee.invitee_id)).meeting);
      setNewLinks((links) => links.filter((l) => l.invitee_id !== confirm.invitee.invitee_id));
      notify(`Invitation for ${confirm.invitee.email} revoked.`, "success");
    }
    setConfirm(null);
  });

  const emailInvite = (invitee) => run(`email-${invitee.invitee_id}`, async () => {
    await sendInvitationEmail(meetingId, invitee.invitee_id);
    load();
  });

  const decideAccount = (invitee, approve) => run(`approve-${invitee.invitee_id}`, async () => {
    update((await approveInviteeAccount(meetingId, invitee.invitee_id, approve)).meeting);
    notify(approve ? "Account admitted as a participant." : "Account turned away.", "success");
  });

  const invite = () => run("invite", async () => {
    const res = await addScheduleInvitees(meetingId, adding);
    update(res.meeting);
    setNewLinks((links) => [...res.links, ...links]);
    setAdding([]);
    if (res.already_invited.length) notify(`Already invited: ${res.already_invited.join(", ")}`, "warning");
  });

  return (
    <main className="page ui-enter" aria-labelledby="scheduled-title">
      <PageHeader
        eyebrow={<Badge tone={status.tone} data-testid="schedule-status">{status.label}</Badge>}
        title={<span id="scheduled-title">{meeting.title}</span>}
        description={formatScheduledStart(meeting) + (meeting.duration_minutes ? ` · ${meeting.duration_minutes} minutes` : "")}
        actions={isOwner && meeting.status !== "cancelled" && (
          <>
            {open && (
              <Button variant="danger" icon="close" onClick={() => setConfirm({ kind: "cancel" })}>Cancel meeting</Button>
            )}
            <Button variant="primary" icon="mic" onClick={() => onStart(meeting)}>
              {open ? "Start meeting" : "Open live room"}
            </Button>
          </>
        )}
      />

      {actionError && <Alert tone="danger" title="Action failed"><p>{actionError}</p></Alert>}
      {meeting.status === "cancelled" && (
        <Alert tone="warning" title="This meeting was cancelled">
          <p>Its invitation links no longer work. Nothing was recorded.</p>
        </Alert>
      )}
      {!isOwner && open && (
        <Alert tone="info" title="Waiting for the host">
          <p>You can join once the host starts the meeting. Use the invitation link you received.</p>
        </Alert>
      )}

      <section className="ui-card ui-card--pad">
        <h2 className="ui-card__title">Details</h2>
        <dl className="schedule-review__list">
          <dt>Room code</dt><dd className="ui-mono">{meeting.room_name}</dd>
          <dt>Agenda</dt><dd className="schedule-review__agenda">{meeting.agenda || "No agenda"}</dd>
          <dt>Invitees</dt><dd>{meeting.invitee_count}</dd>
        </dl>
        <p className="ui-help">The room code alone does not let anyone join; each person needs their own invitation link.</p>
      </section>

      {isOwner && (
        <section className="ui-card ui-card--pad" aria-labelledby="invitees-heading">
          <h2 className="ui-card__title" id="invitees-heading">Invitees</h2>
          <ProviderNotice provider={provider} />
          <p className="ui-help">
            Each invitee is emailed their own link. Copy link is a fallback: it issues a separate link for you to
            share, and replaces only your previously copied link, not the emailed one.
          </p>
          {meeting.invitees.length === 0 ? (
            <p className="ui-help">No one is invited yet.</p>
          ) : (
            <div className="ui-table-wrap">
              <table className="ui-table" data-testid="invitee-table">
                <thead>
                  <tr><th scope="col">Email</th><th scope="col">Status</th><th scope="col">Invitation email</th><th scope="col">Joined as</th><th scope="col"><span className="ui-visually-hidden">Actions</span></th></tr>
                </thead>
                <tbody>
                  {meeting.invitees.map((inv) => {
                    const st = INVITEE_STATUS[inv.status] || INVITEE_STATUS.pending;
                    return (
                      <tr key={inv.invitee_id}>
                        <td data-label="Email">{inv.email}</td>
                        <td data-label="Status"><Badge tone={st.tone}>{st.label}</Badge></td>
                        <td data-label="Invitation email"><EmailStatusBadge delivery={inv.email_delivery} /></td>
                        <td data-label="Joined as">
                          {inv.accepted_by ? (
                            <>
                              {inv.accepted_by.display_name} <span className="ui-help">{inv.accepted_by.email}</span>
                              {!inv.accepted_by.email_matches_invite && (
                                <div className="ui-help" data-testid="email-mismatch">
                                  Signed in with a different email than the one invited.
                                </div>
                              )}
                            </>
                          ) : inv.approval_request ? (
                            <div className="approval-request" data-testid="approval-request">
                              <span>
                                {inv.approval_request.display_name} <span className="ui-help">{inv.approval_request.email}</span>
                              </span>
                              <span className="ui-help">Opened this invite with a different email. Admit them?</span>
                              <span className="approval-request__actions">
                                <Button size="sm" variant="primary" loading={busy === `approve-${inv.invitee_id}`}
                                  onClick={() => decideAccount(inv, true)}>Admit</Button>
                                <Button size="sm" variant="ghost" onClick={() => decideAccount(inv, false)}>Turn away</Button>
                              </span>
                            </div>
                          ) : "—"}
                        </td>
                        <td className="meetings-table__actions">
                          {inv.status !== "revoked" && meeting.status !== "cancelled" && (
                            <>
                              {open && inv.status === "pending" && provider?.live && provider?.configured
                                && !["queued", "sending"].includes(inv.email_delivery?.status) && (
                                <Button size="sm" icon={inv.email_delivery?.can_retry ? "refresh" : "arrowRight"}
                                  loading={busy === `email-${inv.invitee_id}`} onClick={() => emailInvite(inv)}>
                                  {inv.email_delivery?.can_retry ? "Retry email" : inv.email_delivery ? "Email again" : "Send invitation"}
                                </Button>
                              )}
                              <Button size="sm" icon="file" loading={busy === `copy-${inv.invitee_id}`}
                                onClick={() => copyLink(inv)}>Copy link</Button>
                              <Button size="sm" variant="ghost" onClick={() => setConfirm({ kind: "revoke", invitee: inv })}>
                                Revoke
                              </Button>
                            </>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {newLinks.length > 0 && (
            <>
              <h3 className="ui-section-title">New links</h3>
              <InviteLinks links={newLinks} />
            </>
          )}

          {open && (
            <div className="schedule-add">
              <InviteeInput emails={adding} onChange={setAdding} hostEmail={hostEmail} id="add-invitee-input" />
              <Button variant="primary" icon="plus" disabled={!adding.length} loading={busy === "invite"} onClick={invite}>
                Invite {adding.length || ""}
              </Button>
            </div>
          )}
        </section>
      )}

      {confirm && (
        <ConfirmDialog
          title={confirm.kind === "cancel" ? "Cancel this meeting?" : `Revoke ${confirm.invitee.email}?`}
          description={confirm.kind === "cancel"
            ? "Every invitation link stops working. Invitees are not notified; tell them yourself."
            : "Their link stops working. If they already joined with it, they lose access to this meeting."}
          confirmLabel={confirm.kind === "cancel" ? "Cancel meeting" : "Revoke"}
          tone="danger"
          busy={busy === "confirm"}
          error={actionError}
          onConfirm={confirmAction}
          onCancel={() => setConfirm(null)}
        />
      )}
    </main>
  );
}
