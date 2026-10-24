"""
Email senders (Phase 8.4).

* ConsoleSender (EMAIL_PROVIDER=console, the default): development mode. It
  sends nothing and logs only the masked recipient and the subject (never a
  link), and the message is recorded as "not sent (development mode)".
* BrevoSender (EMAIL_PROVIDER=brevo): POST https://api.brevo.com/v3/smtp/email
  with the `api-key` header. HTTP 201 with a messageId means Brevo accepted
  the message, not that it was delivered; delivery is reported later by
  Brevo's webhooks (app/services/email/outbox.py).

Outcomes:
  accepted      the provider took the message (has a message id)
  not_sent_dev  development mode; nothing was sent
  retry         the provider did not take it and a later try may work
                (connection refused, rate limit, provider error 5xx)
  failed        it will not work without a change (bad address, key, sender,
                no credits, configuration missing)
  unknown       timed out after sending; the provider may or may not have it
"""
from __future__ import annotations

import base64
import json
import logging
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from app.core import config
from app.services.email.addresses import mask_email

logger = logging.getLogger(__name__)


@dataclass
class OutgoingEmail:
    to_email: str
    to_name: Optional[str]
    subject: str
    html: str
    reference: str                                   # outbox id, echoed back in webhooks
    tags: List[str] = field(default_factory=list)
    attachments: List[Tuple[str, bytes]] = field(default_factory=list)


@dataclass
class SendOutcome:
    kind: str
    message_id: Optional[str] = None
    error: Optional[str] = None
    retry_after: Optional[float] = None


class ConsoleSender:
    name = "console"
    live = False

    def send(self, message: OutgoingEmail) -> SendOutcome:
        logger.info("Development mode, email NOT sent: to=%s subject=%r attachments=%d",
                    mask_email(message.to_email), message.subject, len(message.attachments))
        return SendOutcome("not_sent_dev")


class BrevoSender:
    name = "brevo"
    live = True

    def __init__(self, api_key: str, sender_email: str, sender_name: str, url: str, timeout: float):
        self._api_key = api_key
        self._sender = {"email": sender_email, "name": sender_name}
        self._url = url
        self._timeout = timeout

    def send(self, message: OutgoingEmail) -> SendOutcome:
        body = {
            "sender": self._sender,
            "to": [{"email": message.to_email, **({"name": message.to_name} if message.to_name else {})}],
            "subject": message.subject,
            "htmlContent": message.html,
            "headers": {"X-Mailin-custom": message.reference},
            "tags": message.tags,
        }
        if message.attachments:
            body["attachment"] = [
                {"name": name, "content": base64.b64encode(data).decode("ascii")} for name, data in message.attachments
            ]
        request = urllib.request.Request(
            self._url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"api-key": self._api_key, "content-type": "application/json", "accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                data = json.loads(response.read().decode("utf-8") or "{}")
            message_id = data.get("messageId")
            if not message_id:
                return SendOutcome("unknown", error="Brevo answered without a message id.")
            return SendOutcome("accepted", message_id=message_id)
        except urllib.error.HTTPError as exc:
            return _http_error_outcome(exc)
        except (socket.timeout, TimeoutError):
            return SendOutcome("unknown", error="Brevo did not answer in time; the message may or may not have "
                                                "been accepted. Check the Brevo logs before retrying.")
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                return SendOutcome("unknown", error="Brevo did not answer in time; the message may or may not have "
                                                    "been accepted. Check the Brevo logs before retrying.")
            return SendOutcome("retry", error=f"Could not reach Brevo ({type(exc.reason).__name__}).")


def _http_error_outcome(exc: urllib.error.HTTPError) -> SendOutcome:
    try:
        detail = json.loads(exc.read().decode("utf-8") or "{}")
        text = str(detail.get("message") or detail.get("code") or "")[:200]
    except Exception:
        text = ""
    suffix = f": {text}" if text else ""
    if exc.code == 429:
        retry_after = None
        try:
            retry_after = float(exc.headers.get("Retry-After") or 0) or None
        except (TypeError, ValueError):
            retry_after = None
        return SendOutcome("retry", error=f"Brevo rate limit reached (HTTP 429){suffix}", retry_after=retry_after)
    if exc.code >= 500:
        return SendOutcome("retry", error=f"Brevo error (HTTP {exc.code}){suffix}")
    if exc.code in (401, 403):
        return SendOutcome("failed", error=f"Brevo refused the request (HTTP {exc.code}); check the API key "
                                           f"and that the sender is verified{suffix}")
    if exc.code == 402:
        return SendOutcome("failed", error=f"Brevo account has no sending credits (HTTP 402){suffix}")
    return SendOutcome("failed", error=f"Brevo rejected the message (HTTP {exc.code}){suffix}")


class UnconfiguredSender:
    """EMAIL_PROVIDER names a provider whose settings are missing: every message fails honestly."""

    live = True

    def __init__(self, name: str, problem: str):
        self.name = name
        self._problem = problem

    def send(self, message: OutgoingEmail) -> SendOutcome:
        return SendOutcome("failed", error=self._problem)


def provider_status() -> dict:
    provider = getattr(config, "EMAIL_PROVIDER", "console")
    if provider == "brevo":
        missing = [name for name in ("BREVO_API_KEY", "EMAIL_SENDER_ADDRESS") if not getattr(config, name, None)]
        if missing:
            return {"provider": "brevo", "live": True, "configured": False,
                    "message": f"Email is set to Brevo but {', '.join(missing)} is not set, so nothing can be sent."}
        return {"provider": "brevo", "live": True, "configured": True,
                "delivery_events": bool(getattr(config, "BREVO_WEBHOOK_TOKEN", None)),
                "message": "Emails are sent through Brevo. Accepted by Brevo is not the same as delivered."}
    if provider != "console":
        return {"provider": provider, "live": True, "configured": False,
                "message": f"Unknown EMAIL_PROVIDER \"{provider}\"; nothing can be sent."}
    return {"provider": "console", "live": False, "configured": True,
            "message": "Development mode: emails are not sent. Share links yourself."}


def get_sender():
    status = provider_status()
    if not status["live"]:
        return ConsoleSender()
    if not status["configured"]:
        return UnconfiguredSender(status["provider"], status["message"])
    return BrevoSender(
        api_key=config.BREVO_API_KEY,
        sender_email=config.EMAIL_SENDER_ADDRESS,
        sender_name=getattr(config, "EMAIL_SENDER_NAME", "Meet2MoM") or "Meet2MoM",
        url=getattr(config, "BREVO_API_URL", "https://api.brevo.com/v3/smtp/email"),
        timeout=float(getattr(config, "EMAIL_TIMEOUT_SECONDS", 15)),
    )
