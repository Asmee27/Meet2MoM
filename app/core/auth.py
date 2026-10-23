"""
Accounts, sessions and meeting access (Phase 7).

* Passwords: scrypt (Python standard library) with a random 16-byte salt.
* Sessions: a random token in an HttpOnly cookie; only its SHA-256 is stored.
* Access: every /api meeting route requires a signed-in member of that
  meeting. Non-members get 404, so meeting IDs are not confirmed to exist.
* Invitations: an owner creates a single-meeting token; accepting it while
  signed in grants membership of that meeting only.

AUTH_REQUIRED=false turns enforcement off (local fallback and the legacy test
suite). It is on by default.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

from app.core import config, db

SESSION_COOKIE = "m2m_session"
SESSION_DAYS = 7
INVITATION_DAYS = 2
MIN_PASSWORD_LENGTH = 8
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def auth_required() -> bool:
    return bool(getattr(config, "AUTH_REQUIRED", True))


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt),
                                n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Accounts and sessions
# ---------------------------------------------------------------------------

def public_user(user: Dict[str, Any]) -> Dict[str, Any]:
    return {"user_id": user["user_id"], "email": user["email"], "display_name": user["display_name"]}


def register(email: str, password: str, display_name: str) -> Dict[str, Any]:
    email = (email or "").strip().lower()
    display_name = (display_name or "").strip()
    if not _EMAIL.match(email):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=422, detail=f"Use at least {MIN_PASSWORD_LENGTH} characters for the password.")
    if not display_name or len(display_name) > 80:
        raise HTTPException(status_code=422, detail="Enter your name (up to 80 characters).")
    user_id = "u_" + uuid.uuid4().hex[:16]
    if not db.create_user(user_id, email, display_name, hash_password(password)):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    return db.get_user(user_id)


def authenticate(email: str, password: str) -> Optional[Dict[str, Any]]:
    user = db.get_user_by_email(email or "")
    if user is None:
        hash_password(password or "x")           # similar timing whether or not the email exists
        return None
    return user if verify_password(password or "", user["password_hash"]) else None


def start_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    db.create_session(token_hash(token), user_id, (_now() + timedelta(days=SESSION_DAYS)).isoformat())
    return token


def end_session(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.delete_session(token_hash(token))


def session_user(request: Request) -> Optional[Dict[str, Any]]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return db.get_session_user(token_hash(token), _now().isoformat())


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
        secure=bool(getattr(config, "SESSION_COOKIE_SECURE", False)), path="/",
    )


# ---------------------------------------------------------------------------
# Request dependencies
# ---------------------------------------------------------------------------

def current_user(request: Request) -> Optional[Dict[str, Any]]:
    """The signed-in user; 401 when sign-in is required and missing."""
    user = session_user(request)
    if user is None and auth_required():
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    request.state.user = user
    return user


def require_meeting_access(request: Request) -> None:
    """
    Router-wide dependency for /api: a signed-in user, and membership of the
    meeting named in the path (meeting_id or file_id).
    """
    user = current_user(request)
    if not auth_required():
        return
    meeting_id = request.path_params.get("meeting_id") or request.path_params.get("file_id")
    if meeting_id and not db.get_member_role(meeting_id, user["user_id"]):
        raise HTTPException(status_code=404, detail="Meeting not found.")


def require_owner(request: Request, meeting_id: str) -> Optional[Dict[str, Any]]:
    user = current_user(request)
    if auth_required() and db.get_member_role(meeting_id, user["user_id"]) != "owner":
        raise HTTPException(status_code=403, detail="Only the meeting owner can do this.")
    return user


def reviewer_identity(request: Request, provided: Optional[str]) -> Optional[str]:
    """The signed-in user is the reviewer; the client-supplied name is ignored."""
    user = getattr(request.state, "user", None) or (session_user(request) if auth_required() else None)
    if user:
        return f"{user['display_name']} <{user['email']}>"
    return provided


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------

def create_invitation(meeting_id: str, user_id: str) -> str:
    token = secrets.token_urlsafe(24)
    db.create_invitation(token_hash(token), meeting_id, user_id,
                         (_now() + timedelta(days=INVITATION_DAYS)).isoformat())
    return token


def accept_invitation(token: str, user_id: str) -> str:
    invitation = db.get_invitation(token_hash(token or ""), _now().isoformat())
    if invitation is None:
        raise HTTPException(status_code=404, detail="This invitation is invalid or has expired.")
    db.add_meeting_member(invitation["meeting_id"], user_id, "member")
    return invitation["meeting_id"]
