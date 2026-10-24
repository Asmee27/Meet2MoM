"""
The copyable invitation link (Phase 8.3).

Every invitee also gets a link the host copies and shares by hand. That link
is never emailed by the server; the result says so. Invitation emails are a
separate channel with their own link and status (Phase 8.4,
app/services/email/outbox.py), so copying a link never claims an email.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class DeliveryResult:
    channel: str
    sent: bool
    message: str

    def as_dict(self) -> Dict[str, Any]:
        return {"channel": self.channel, "sent": self.sent, "message": self.message}


class ManualLinkDelivery:
    """The host shares the link; nothing is sent by the server."""

    channel = "manual"

    def deliver(self, email: str, link_path: str, meeting_title: str) -> DeliveryResult:
        return DeliveryResult(
            channel=self.channel,
            sent=False,
            message="Copy link: not emailed by Meet2MoM. Share it with this invitee yourself.",
        )


def get_delivery() -> ManualLinkDelivery:
    return ManualLinkDelivery()
