"""
How an invitation link reaches an invitee (Phase 8.3).

Meet2MoM has no email configuration, so the only channel is manual: the host
copies each invitee's link and shares it themselves. Results always say
whether anything was actually sent; the manual channel never claims it was.
A real email channel would implement the same deliver() and be chosen in
get_delivery() only when it is configured.
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
            message="No email was sent. Copy this invitee's link and share it with them yourself.",
        )


def get_delivery() -> ManualLinkDelivery:
    return ManualLinkDelivery()
