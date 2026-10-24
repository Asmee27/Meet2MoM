"""
Email address checks (Phase 8.4).

Syntax is checked with the email-validator library. No DNS or mailbox check is
made, so a well-formed address may still not exist; only the provider's
delivery events (delivered, bounced, invalid) say anything about that.
Addresses are lower-cased as a whole, the same way accounts store them, so an
invitation and an account compare consistently.
"""
from __future__ import annotations

from typing import Dict, Iterable, List

from email_validator import EmailNotValidError, validate_email


class InvalidEmail(ValueError):
    pass


def normalize_email(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise InvalidEmail("Enter an email address.")
    if len(text) > 254:
        raise InvalidEmail(f"“{text[:40]}…” is too long to be an email address.")
    try:
        result = validate_email(text, check_deliverability=False)
    except EmailNotValidError as exc:
        raise InvalidEmail(f"“{text}” is not a valid email address ({exc}).") from None
    return result.normalized.lower()


def normalize_many(raw: Iterable[str]) -> Dict[str, List[str]]:
    """Unique normalised emails in input order, duplicates removed, and per-entry errors."""
    emails: List[str] = []
    duplicates: List[str] = []
    errors: List[str] = []
    for item in raw or []:
        if not (item or "").strip():
            continue
        try:
            email = normalize_email(item)
        except InvalidEmail as exc:
            errors.append(str(exc))
            continue
        if email in emails:
            duplicates.append(email)
        else:
            emails.append(email)
    return {"emails": emails, "duplicates": duplicates, "errors": errors}


def mask_email(email: str) -> str:
    """For logs: 'a***@example.com'. Never log full recipient addresses."""
    local, _, domain = (email or "").partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"
