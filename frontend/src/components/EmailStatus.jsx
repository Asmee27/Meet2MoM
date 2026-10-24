// Phase 8.4: one honest label per email status. "Sent to provider" means the
// email service accepted the message; only "Delivered" (reported by the
// provider afterwards) is shown in green.
import { Alert, Badge } from "./ui";

export const EMAIL_STATUS = {
  queued: { tone: "info", label: "Queued", hint: "Waiting to be handed to the email provider." },
  sending: { tone: "info", label: "Sending", pulse: true, hint: "Being handed to the email provider now." },
  not_sent_dev: { tone: "neutral", label: "Not sent (development mode)",
    hint: "Email is in development mode, so nothing was sent. Share the link yourself." },
  provider_accepted: { tone: "info", label: "Sent to provider",
    hint: "The email provider accepted it. Delivery to the recipient is not confirmed yet." },
  deferred: { tone: "warning", label: "Delayed", hint: "The recipient's server deferred it; the provider is retrying." },
  soft_bounced: { tone: "warning", label: "Soft bounce", hint: "Temporarily refused; the provider may retry." },
  delivered: { tone: "success", label: "Delivered", hint: "The provider reported delivery to the recipient's mail server." },
  hard_bounced: { tone: "danger", label: "Bounced", hint: "The address does not accept mail." },
  invalid: { tone: "danger", label: "Invalid address", hint: "The provider rejected the address." },
  blocked: { tone: "danger", label: "Blocked", hint: "The provider blocked delivery to this address." },
  spam: { tone: "danger", label: "Marked as spam", hint: "The recipient reported it as spam." },
  failed: { tone: "danger", label: "Failed", hint: "Not sent." },
  cancelled: { tone: "neutral", label: "Cancelled", hint: "Not sent." },
};

export function EmailStatusBadge({ delivery }) {
  if (!delivery) return <span className="ui-help">No email</span>;
  const status = EMAIL_STATUS[delivery.status] || { tone: "neutral", label: delivery.status };
  const detail = delivery.last_error || status.hint;
  return (
    <span className="email-status">
      <Badge tone={status.tone} pulse={status.pulse} data-testid="email-status">{status.label}</Badge>
      {detail && <span className="ui-help email-status__detail">{detail}</span>}
    </span>
  );
}

// What the configured provider really does, shown wherever email is offered.
export function ProviderNotice({ provider }) {
  if (!provider) return null;
  if (!provider.live) {
    return (
      <Alert tone="info" title="Development mode: emails are not sent" data-testid="email-dev-mode">
        <p>Nothing leaves this computer. Copy links and share them yourself.</p>
      </Alert>
    );
  }
  if (!provider.configured) {
    return (
      <Alert tone="warning" title="Email delivery is unavailable" data-testid="email-unconfigured">
        <p>{provider.message}</p>
      </Alert>
    );
  }
  return null;
}
