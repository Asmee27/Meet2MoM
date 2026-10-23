"""Accounts, sessions and meeting invitations (Phase 7)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.core import auth, db

router = APIRouter(prefix="/api/auth", tags=["Auth"])


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: str


class LoginRequest(BaseModel):
    email: str
    password: str


class AcceptInvitationRequest(BaseModel):
    token: str


@router.post("/register")
def register(payload: RegisterRequest, response: Response):
    db.init_db()
    user = auth.register(payload.email, payload.password, payload.display_name)
    auth.set_session_cookie(response, auth.start_session(user["user_id"]))
    return {"user": auth.public_user(user)}


@router.post("/login")
def login(payload: LoginRequest, response: Response):
    db.init_db()
    user = auth.authenticate(payload.email, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    auth.set_session_cookie(response, auth.start_session(user["user_id"]))
    return {"user": auth.public_user(user)}


@router.post("/logout")
def logout(request: Request, response: Response):
    auth.end_session(request)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"signed_out": True}


@router.get("/me")
def me(request: Request):
    """The signed-in user, and whether sign-in is required at all."""
    user = auth.session_user(request)
    if user is None and auth.auth_required():
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    return {"auth_required": auth.auth_required(), "user": auth.public_user(user) if user else None}


@router.post("/meetings/{meeting_id}/invitations")
def create_invitation(meeting_id: str, request: Request):
    """The meeting owner creates a single-meeting invitation (valid 2 days)."""
    user = auth.require_owner(request, meeting_id)
    if not auth.auth_required():
        raise HTTPException(status_code=409, detail="Invitations need sign-in (AUTH_REQUIRED=true).")
    token = auth.create_invitation(meeting_id, user["user_id"])
    live = db.get_live_meeting(meeting_id) or {}
    return {"token": token, "meeting_id": meeting_id, "room_name": live.get("room_name"),
            "expires_in_days": auth.INVITATION_DAYS}


@router.post("/invitations/accept")
def accept_invitation(payload: AcceptInvitationRequest, request: Request):
    """A signed-in user accepts an invitation and becomes a member of that meeting only."""
    user = auth.current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    meeting_id = auth.accept_invitation(payload.token, user["user_id"])
    live = db.get_live_meeting(meeting_id) or {}
    return {"meeting_id": meeting_id, "room_name": live.get("room_name")}
