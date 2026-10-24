// Phase 8.4: send approved minutes to this meeting's own participants.
// Recipients come from the server (members and invitees of this meeting);
// nobody can be typed in. Choose -> confirm (version, recipients, optional
// PDF) -> result, plus a delivery history with retries for failures.
import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Button, Dialog } from "./ui";
import { EmailStatusBadge, ProviderNotice } from "./EmailStatus";
import { getMinutesEmail, retryEmail, sendMinutesEmail } from "../services/api";
import { formatDateTime } from "../format";

const ACTIVE = new Set(["queued", "sending"]);
const KIND_LABEL = { owner: "Host", member: "Participant", invitee: "Invited, not joined" };

function RecipientChooser({ recipients, selected, onToggle }) {
  return (
    <fieldset className="recipient-list">
      <legend className="ui-label">Recipients</legend>
      {recipients.map((r) => (
        <label key={r.key} className={`recipient-option${r.eligible ? "" : " recipient-option--disabled"}`}>
          <input
            type="checkbox"
            checked={selected.includes(r.key)}
            disabled={!r.eligible}
            onChange={() => onToggle(r.key)}
          />
          <span className="recipient-option__text">
            <strong>{r.name || r.email}</strong>
            {r.name && <span className="ui-help"> {r.email}</span>}
            <Badge tone="neutral" icon={null}>{KIND_LABEL[r.kind] || r.kind}</Badge>
            {r.reason && <span className="ui-help recipient-option__reason">{r.reason}</span>}
          </span>
        </label>
      ))}
    </fieldset>
  );
}

export default function MinutesEmailPanel({ meetingId, shownVersion }) {
  const [state, setState] = useState({ status: "loading", data: null, error: "" });
  const [dialog, setDialog] = useState(null); // null | "choose" | "confirm" | "result"
  const [selected, setSelected] = useState([]);
  const [attachPdf, setAttachPdf] = useState(false);
  const [attachConfirmed, setAttachConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const load = useCallback(() => {
    getMinutesEmail(meetingId)
      .then((data) => setState({ status: "ready", data, error: "" }))
      .catch((err) => setState({ status: "error", data: null, error: err?.message || "" }));
  }, [meetingId]);

  useEffect(() => { load(); }, [load]);

  const history = state.data?.history || [];
  const active = history.some((h) => ACTIVE.has(h.status));
  useEffect(() => {
    if (!active) return undefined;
    const timer = setTimeout(load, 4000);
    return () => clearTimeout(timer);
  }, [active, load, history]);

  if (state.status !== "ready") return null;
  const data = state.data;
  if (!data.approved || !data.can_send) return null;

  const recipients = data.recipients || [];
  const chosen = recipients.filter((r) => selected.includes(r.key));
  const stale = shownVersion && data.version !== shownVersion;

  const open = () => {
    setSelected(recipients.filter((r) => r.eligible && r.kind !== "owner").map((r) => r.key));
    setAttachPdf(false);
    setAttachConfirmed(false);
    setError("");
    setDialog("choose");
  };

  const send = async () => {
    setBusy(true);
    setError("");
    try {
      const res = await sendMinutesEmail(meetingId, {
        recipients: selected, version: data.version, attachPdf, confirmAttachment: attachPdf && attachConfirmed,
      });
      setResult(res);
      setDialog("result");
      setState({ status: "ready", data: { ...data, history: res.history }, error: "" });
    } catch (err) {
      setError(err?.message || "The minutes could not be queued.");
    } finally {
      setBusy(false);
    }
  };

  const retry = async (outboxId) => {
    setError("");
    try {
      await retryEmail(meetingId, outboxId);
      load();
    } catch (err) {
      setError(err?.message || "Retry failed.");
    }
  };

  const devMode = !data.provider?.live;

  return (
    <section className="ui-card ui-card--pad minutes-email" aria-labelledby="minutes-email-title">
      <div className="minutes-email__head">
        <div>
          <h3 className="ui-card__title" id="minutes-email-title">Email approved minutes</h3>
          <p className="ui-help">
            Sends version {data.version} (approved {formatDateTime(data.approved_at)}) to people in this meeting.
            “Sent to provider” is not the same as delivered.
          </p>
        </div>
        <Button variant="primary" icon="arrowRight" onClick={open} disabled={stale}>Send approved minutes</Button>
      </div>
      {stale && (
        <Alert tone="warning" title="Newer approval">
          <p>The approved version is now v{data.version}. Reload the minutes before sending.</p>
        </Alert>
      )}
      <ProviderNotice provider={data.provider} />
      {error && !dialog && <Alert tone="danger" title="Action failed"><p>{error}</p></Alert>}

      {history.length > 0 && (
        <div className="ui-table-wrap">
          <table className="ui-table" data-testid="minutes-email-history">
            <thead>
              <tr>
                <th scope="col">Recipient</th><th scope="col">Version</th><th scope="col">Status</th>
                <th scope="col">Queued</th><th scope="col"><span className="ui-visually-hidden">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.outbox_id}>
                  <td data-label="Recipient">{h.recipient_name || h.recipient_email}{h.attach_pdf && <span className="ui-help"> · PDF</span>}</td>
                  <td data-label="Version">v{h.minutes_version}</td>
                  <td data-label="Status"><EmailStatusBadge delivery={h} /></td>
                  <td data-label="Queued" className="meetings-table__nowrap">{formatDateTime(h.created_at)}</td>
                  <td className="meetings-table__actions">
                    {h.can_retry && <Button size="sm" icon="refresh" onClick={() => retry(h.outbox_id)}>Retry</Button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {dialog === "choose" && (
        <Dialog
          title="Send approved minutes"
          description={`Version ${data.version}. Only people in this meeting can be chosen.`}
          onClose={() => setDialog(null)}
          testId="minutes-email-dialog"
          actions={(
            <>
              <Button variant="ghost" onClick={() => setDialog(null)}>Cancel</Button>
              <Button variant="primary" disabled={!selected.length} onClick={() => setDialog("confirm")}>
                Review {selected.length} recipient{selected.length === 1 ? "" : "s"}
              </Button>
            </>
          )}
        >
          <RecipientChooser
            recipients={recipients}
            selected={selected}
            onToggle={(key) => setSelected((s) => (s.includes(key) ? s.filter((k) => k !== key) : [...s, key]))}
          />
          <label className="recipient-option">
            <input type="checkbox" checked={attachPdf} onChange={(e) => { setAttachPdf(e.target.checked); setAttachConfirmed(false); }} />
            <span className="recipient-option__text">
              <strong>Attach the approved PDF</strong>
              <span className="ui-help recipient-option__reason">
                Off by default. The PDF contains the full minutes and evidence quotes; it cannot be recalled once delivered.
              </span>
            </span>
          </label>
        </Dialog>
      )}

      {dialog === "confirm" && (
        <Dialog
          title="Confirm sending"
          description={`Approved minutes, version ${data.version}${attachPdf ? ", with the PDF attached" : ", link only"}.`}
          onClose={() => setDialog(null)}
          busy={busy}
          testId="minutes-email-confirm"
          actions={(
            <>
              <Button onClick={() => setDialog("choose")} icon="arrowLeft">Back</Button>
              <Button variant="primary" loading={busy} disabled={busy || (attachPdf && !attachConfirmed)} onClick={send}>
                {devMode ? "Queue (development mode)" : `Send to ${chosen.length}`}
              </Button>
            </>
          )}
        >
          <ul className="confirm-recipients" aria-label="Selected recipients">
            {chosen.map((r) => <li key={r.key}>{r.name ? `${r.name} (${r.email})` : r.email}</li>)}
          </ul>
          {attachPdf && (
            <label className="recipient-option">
              <input type="checkbox" checked={attachConfirmed} onChange={(e) => setAttachConfirmed(e.target.checked)} />
              <span className="recipient-option__text">
                <strong>I confirm these recipients may receive the full minutes PDF.</strong>
              </span>
            </label>
          )}
          {devMode && <p className="ui-help">Development mode: nothing will actually be sent.</p>}
          {error && <p className="field-error" role="alert">{error}</p>}
        </Dialog>
      )}

      {dialog === "result" && result && (
        <Dialog
          title={result.queued.length ? "Minutes queued" : "Nothing new to send"}
          onClose={() => setDialog(null)}
          testId="minutes-email-result"
          actions={<Button variant="primary" onClick={() => setDialog(null)}>Done</Button>}
        >
          <p data-testid="minutes-email-summary">
            {result.queued.length > 0 && `${result.queued.length} queued. `}
            {result.already.length > 0 && `${result.already.length} already sent or in progress for version ${result.version}, so not sent again. `}
            {devMode
              ? "Development mode: no email will be sent."
              : "Statuses update below as the provider reports them; “Sent to provider” is not yet delivery."}
          </p>
        </Dialog>
      )}
    </section>
  );
}
