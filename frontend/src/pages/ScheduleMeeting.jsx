// Phase 8.3: schedule a future meeting and invite several people at once.
// Form -> review -> scheduled (one link per invitee).
// Phase 8.4: each invitee is emailed their own link when a provider is
// configured; the result says only "queued" (never "sent"), and copy links
// stay available as a fallback. In development mode nothing is emailed.
import { useMemo, useRef, useState } from "react";
import { PageHeader } from "../components/AppShell";
import { Alert, Badge, Button, Icon, useToast } from "../components/ui";
import { scheduleMeeting } from "../services/api";
import { formatScheduledStart } from "../format";

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function browserZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

// Browsers list some zones only under legacy names (Asia/Calcutta, no UTC),
// so current IANA names people look for are always offered as well. The
// server accepts both spellings.
const COMMON_ZONES = ["UTC", "Asia/Kolkata", "Europe/Kyiv", "America/Nuuk", "Asia/Yangon", "Asia/Kathmandu"];

function zoneList(current) {
  let zones = [];
  try {
    zones = Intl.supportedValuesOf ? Intl.supportedValuesOf("timeZone") : [];
  } catch {
    zones = [];
  }
  return [...new Set([current, ...COMMON_ZONES, ...zones])].sort();
}

export function inviteUrl(linkPath) {
  return `${window.location.origin}${linkPath}`;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

// Splits pasted text ("a@x.com, b@y.com; c@z.com") into entries.
function splitEmails(text) {
  return text.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean);
}

export function InviteeInput({ emails, onChange, hostEmail = "", id = "invitee-input" }) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");

  const add = () => {
    const entries = splitEmails(draft);
    if (!entries.length) return;
    const next = [...emails];
    const problems = [];
    entries.forEach((entry) => {
      const email = entry.toLowerCase();
      if (!EMAIL.test(email)) problems.push(`“${entry}” is not a valid email address.`);
      else if (hostEmail && email === hostEmail.toLowerCase()) problems.push("You are the host; you do not need to invite yourself.");
      else if (next.includes(email)) problems.push(`${email} is already in the list.`);
      else next.push(email);
    });
    onChange(next);
    setError(problems.join(" "));
    setDraft(problems.length ? entries.filter((e) => !next.includes(e.toLowerCase())).join(", ") : "");
  };

  return (
    <div className="ui-field">
      <label className="ui-label" htmlFor={id}>Invitees</label>
      <div className="invitee-input">
        <input
          id={id}
          className="ui-input"
          type="text"
          inputMode="email"
          placeholder="name@example.com (separate several with commas)"
          value={draft}
          aria-invalid={Boolean(error)}
          aria-describedby={`${id}-help`}
          onChange={(e) => { setDraft(e.target.value); setError(""); }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              add();
            }
          }}
        />
        <Button icon="plus" onClick={add} disabled={!draft.trim()}>Add</Button>
      </div>
      {error ? (
        <p className="field-error" role="alert" id={`${id}-help`}>{error}</p>
      ) : (
        <p className="ui-help" id={`${id}-help`}>
          Each person gets their own link. The email is how you keep track of them; it does not prove who opens the link.
        </p>
      )}
      {emails.length > 0 && (
        <ul className="invitee-chips" aria-label="Invitees added">
          {emails.map((email) => (
            <li key={email} className="invitee-chip">
              <span>{email}</span>
              <button
                type="button"
                className="invitee-chip__remove"
                aria-label={`Remove ${email}`}
                onClick={() => onChange(emails.filter((e) => e !== email))}
              >
                <Icon name="close" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function InviteLinks({ links }) {
  const { notify } = useToast();
  if (!links?.length) return null;
  return (
    <ul className="invite-links" aria-label="Invitation links">
      {links.map((link) => {
        const url = inviteUrl(link.link_path);
        return (
          <li key={link.invitee_id} className="invite-link">
            <div className="invite-link__who">
              <strong>{link.email}</strong>
              <span className="ui-help">Link expires {new Date(link.expires_at).toLocaleString()}</span>
            </div>
            <input className="ui-input ui-mono invite-link__url" readOnly value={url} aria-label={`Invitation link for ${link.email}`}
              onFocus={(e) => e.target.select()} />
            <Button
              size="sm"
              icon="file"
              onClick={async () => {
                const copied = await copyText(url);
                notify(copied ? `Link for ${link.email} copied.` : "Copy failed; select the link and copy it.",
                  copied ? "success" : "warning");
              }}
            >
              Copy link
            </Button>
          </li>
        );
      })}
    </ul>
  );
}

// What happened to the invitation emails, in the provider's own terms.
function EmailOutcome({ email, invited }) {
  if (!invited) return null;
  if (email?.live && email?.configured) {
    return (
      <Alert tone="info" title={`Invitation emails queued for ${email.queued || invited} people`} data-testid="email-queued-notice">
        <p>
          Each person gets their own link by email. Being accepted by the email provider is not the same as delivery;
          check each person's status on the meeting page. The links below are a fallback you can share yourself.
        </p>
      </Alert>
    );
  }
  return (
    <Alert tone={email?.live ? "warning" : "info"} title="No email was sent" data-testid="no-email-notice">
      <p>
        {email?.live
          ? `${email.message} `
          : "Email is in development mode, so nothing was sent. "}
        Copy each person's link below and share it with them yourself. Each link works only for this meeting and
        only until it expires or you revoke it.
      </p>
    </Alert>
  );
}

function localToday() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export default function ScheduleMeeting({ hostEmail = "", onOpenScheduled, onCancel }) {
  const [form, setForm] = useState(() => ({
    title: "",
    agenda: "",
    date: "",
    time: "",
    timezone: browserZone(),
    duration: "",
    invitees: [],
  }));
  const [stage, setStage] = useState("form"); // form | review | done
  const [errors, setErrors] = useState({});
  const [submit, setSubmit] = useState({ busy: false, error: "" });
  const [result, setResult] = useState(null);
  // One id per form: a double click or retry creates one meeting, not two.
  const requestId = useRef(
    typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `req-${Date.now()}-${Math.random()}`
  );
  const zones = useMemo(() => zoneList(form.timezone), [form.timezone]);
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  const validate = () => {
    const next = {};
    if (!form.title.trim()) next.title = "Enter a title.";
    if (!form.date || !form.time) next.when = "Choose a date and a start time.";
    if (form.duration && (Number(form.duration) < 5 || Number(form.duration) > 480)) {
      next.duration = "Duration must be between 5 and 480 minutes.";
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const doSubmit = async () => {
    setSubmit({ busy: true, error: "" });
    try {
      const data = await scheduleMeeting({
        title: form.title.trim(),
        agenda: form.agenda.trim() || null,
        start_local: `${form.date}T${form.time}`,
        timezone: form.timezone,
        duration_minutes: form.duration ? Number(form.duration) : null,
        invitees: form.invitees,
        request_id: requestId.current,
      });
      setResult(data);
      setStage("done");
      setSubmit({ busy: false, error: "" });
    } catch (err) {
      setSubmit({ busy: false, error: err?.message || "The meeting could not be scheduled." });
      setStage("form");
    }
  };

  if (stage === "done" && result) {
    const meeting = result.meeting;
    return (
      <main className="page ui-enter" aria-labelledby="schedule-title">
        <PageHeader
          eyebrow="Meeting scheduled"
          title={<span id="schedule-title">{meeting.title}</span>}
          description={formatScheduledStart(meeting)}
          actions={<Button variant="primary" onClick={() => onOpenScheduled(meeting.meeting_id)} iconRight="chevronRight">Open meeting page</Button>}
        />
        <EmailOutcome email={result.email} invited={result.links.length} />
        {result.duplicate_request && (
          <Alert tone="warning" title="Already scheduled">
            <p>This form was already submitted, so no second meeting was created. Copy links from the meeting page.</p>
          </Alert>
        )}
        <section className="ui-card ui-card--pad">
          <h2 className="ui-card__title">Invitation links</h2>
          {result.links.length ? <InviteLinks links={result.links} /> : <p className="ui-help">No one was invited yet. You can add people on the meeting page.</p>}
        </section>
      </main>
    );
  }

  return (
    <main className="page ui-enter" aria-labelledby="schedule-title">
      <PageHeader
        title={<span id="schedule-title">Schedule meeting</span>}
        description="Pick a time and invite people. Nothing is recorded until you start the meeting and participants consent."
      />
      {submit.error && (
        <Alert tone="danger" title="The meeting was not scheduled"><p>{submit.error}</p></Alert>
      )}

      {stage === "form" && (
        <form
          className="ui-card ui-card--pad schedule-form"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            if (validate()) setStage("review");
          }}
        >
          <div className="ui-field">
            <label className="ui-label" htmlFor="schedule-title-input">Title</label>
            <input id="schedule-title-input" className="ui-input" maxLength={120} value={form.title} onChange={set("title")}
              aria-invalid={Boolean(errors.title)} />
            {errors.title && <p className="field-error" role="alert">{errors.title}</p>}
          </div>
          <div className="ui-field">
            <label className="ui-label" htmlFor="schedule-agenda">Agenda <span className="ui-help">(optional)</span></label>
            <textarea id="schedule-agenda" className="ui-textarea" maxLength={2000} value={form.agenda} onChange={set("agenda")} />
          </div>
          <div className="schedule-form__row">
            <div className="ui-field">
              <label className="ui-label" htmlFor="schedule-date">Date</label>
              <input id="schedule-date" className="ui-input" type="date" min={localToday()} value={form.date} onChange={set("date")}
                aria-invalid={Boolean(errors.when)} />
            </div>
            <div className="ui-field">
              <label className="ui-label" htmlFor="schedule-time">Start time</label>
              <input id="schedule-time" className="ui-input" type="time" value={form.time} onChange={set("time")}
                aria-invalid={Boolean(errors.when)} />
            </div>
            <div className="ui-field">
              <label className="ui-label" htmlFor="schedule-zone">Timezone</label>
              <select id="schedule-zone" className="ui-select" value={form.timezone} onChange={set("timezone")}>
                {zones.map((z) => <option key={z} value={z}>{z}</option>)}
              </select>
            </div>
            <div className="ui-field">
              <label className="ui-label" htmlFor="schedule-duration">Duration <span className="ui-help">(minutes, optional)</span></label>
              <input id="schedule-duration" className="ui-input" type="number" min={5} max={480} step={5} value={form.duration}
                onChange={set("duration")} aria-invalid={Boolean(errors.duration)} />
            </div>
          </div>
          {errors.when && <p className="field-error" role="alert">{errors.when}</p>}
          {errors.duration && <p className="field-error" role="alert">{errors.duration}</p>}

          <InviteeInput emails={form.invitees} onChange={(invitees) => setForm((f) => ({ ...f, invitees }))} hostEmail={hostEmail} />

          <div className="schedule-form__actions">
            <Button variant="ghost" onClick={onCancel}>Cancel</Button>
            <Button variant="primary" type="submit" iconRight="chevronRight">Review</Button>
          </div>
        </form>
      )}

      {stage === "review" && (
        <section className="ui-card ui-card--pad schedule-review" aria-labelledby="review-heading">
          <h2 className="ui-card__title" id="review-heading">Check the details</h2>
          <dl className="schedule-review__list">
            <dt>Title</dt><dd>{form.title.trim()}</dd>
            <dt>When</dt>
            <dd>
              {form.date} at {form.time} <Badge tone="neutral" icon="clock">{form.timezone}</Badge>
              {form.duration && <span className="ui-help"> · {form.duration} minutes</span>}
            </dd>
            {form.agenda.trim() && (<><dt>Agenda</dt><dd className="schedule-review__agenda">{form.agenda.trim()}</dd></>)}
            <dt>Invitees</dt>
            <dd>{form.invitees.length ? form.invitees.join(", ") : "No one yet (you can add people later)"}</dd>
          </dl>
          <p className="ui-help">
            Scheduling does not send email. After scheduling you get one link per invitee to share yourself.
            The server checks that the time exists in {form.timezone} and is in the future.
          </p>
          <div className="schedule-form__actions">
            <Button onClick={() => setStage("form")} icon="arrowLeft">Edit</Button>
            <Button variant="primary" icon="check" loading={submit.busy} disabled={submit.busy} onClick={doSubmit}>
              Schedule meeting
            </Button>
          </div>
        </section>
      )}
    </main>
  );
}
