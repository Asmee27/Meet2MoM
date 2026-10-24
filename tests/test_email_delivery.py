"""
Phase 8.4: invitation and approved-minutes email.

Nothing here talks to a real provider: a fake sender records messages, and
the Brevo sender is exercised against a fake urlopen. conftest.py also sets
EMAIL_PROVIDER=console and EMAIL_DISPATCH=off for the whole suite.
"""
from __future__ import annotations

import io
import json
import logging
import urllib.error
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.api import live_routes, meeting_routes
from app.api.main import app
from app.core import config, db
from app.schemas.utterance import Utterance
from app.services.email import outbox, providers
from app.services.email.addresses import InvalidEmail, normalize_email, normalize_many
from tests.scenario_helpers import add_action, add_decision, record_extraction
from tests.semantic_fakes import install_semantic_fakes

ZONE = "Asia/Kolkata"
WEBHOOK_TOKEN = "hook-secret-for-tests-0123456789"


class FakeSender:
    """Records messages; `outcomes` decides what each send returns (default: accepted)."""

    name = "fake"
    live = True

    def __init__(self):
        self.sent = []
        self.outcomes = []

    def send(self, message):
        self.sent.append(message)
        if self.outcomes:
            return self.outcomes.pop(0)
        return providers.SendOutcome("accepted", message_id=f"<msg-{uuid4().hex[:10]}@relay.test>")


@pytest.fixture(autouse=True)
def auth_on(monkeypatch):
    monkeypatch.setattr(config, "AUTH_REQUIRED", True)
    monkeypatch.setattr(config, "LIVEKIT_URL", "wss://unit-test.livekit.invalid")
    monkeypatch.setattr(config, "LIVEKIT_API_KEY", "APIunittest")
    monkeypatch.setattr(config, "LIVEKIT_API_SECRET", "unit_test_secret_0123456789abcdefghij")

    async def fake_room(**kwargs):
        return {"status": "ok", "room_name": kwargs.get("room_name"), "sid": "RM_test"}

    monkeypatch.setattr(live_routes, "create_live_room", fake_room)
    monkeypatch.setattr(meeting_routes, "run_pipeline", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def only_this_tests_email():
    """Other test files queue invitations in the shared test database; never send those here."""
    with db.get_conn() as conn:
        conn.execute("UPDATE email_outbox SET status = 'cancelled' WHERE status IN ('queued', 'sending')")


@pytest.fixture
def sender(monkeypatch):
    fake = FakeSender()
    monkeypatch.setattr(providers, "get_sender", lambda: fake)
    monkeypatch.setattr(providers, "provider_status",
                        lambda: {"provider": "fake", "live": True, "configured": True, "message": "test"})
    return fake


def _addr(name):
    return f"{name.lower()}-{uuid4().hex[:8]}@example.com"


def _account(name, email=None):
    client = TestClient(app)
    res = client.post("/api/auth/register", json={"email": email or _addr(name), "password": "correct horse 1",
                                                  "display_name": name})
    assert res.status_code == 200, res.text
    return client, res.json()["user"]


def _future(days=3):
    local = (datetime.now(ZoneInfo(ZONE)) + timedelta(days=days)).replace(hour=15, minute=0)
    return local.strftime("%Y-%m-%dT%H:%M")


def _schedule(client, invitees):
    res = client.post("/api/schedule", json={"title": "Login portal release", "agenda": "Backend API",
                                             "start_local": _future(), "timezone": ZONE, "duration_minutes": 45,
                                             "invitees": invitees})
    assert res.status_code == 200, res.text
    return res.json()


def _link_token(message):
    marker = "invite="
    start = message.html.index(marker) + len(marker)
    return message.html[start:message.html.index('"', start)].split("&")[0]


def _rows(mid, kind=None):
    return db.list_emails(mid, kind)


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------

def test_addresses_are_checked_by_the_library_and_normalised():
    assert normalize_email("  Asha.K@Example.COM ") == "asha.k@example.com"
    for bad in ("not-an-email", "a@localhost", "x@@example.com", "asha@example.test", ""):
        with pytest.raises(InvalidEmail):
            normalize_email(bad)
    result = normalize_many(["a@example.com", "A@EXAMPLE.com", "nope"])
    assert result["emails"] == ["a@example.com"] and result["duplicates"] == ["a@example.com"]
    assert len(result["errors"]) == 1


# ---------------------------------------------------------------------------
# Invitation emails
# ---------------------------------------------------------------------------

def test_three_invitees_each_get_their_own_meeting_scoped_email(sender, caplog):
    host_client, host = _account("Samarth")
    emails = [_addr("asmee"), _addr("ravi"), _addr("priya")]
    body = _schedule(host_client, emails + ["not-valid"][:0])
    mid, room = body["meeting"]["meeting_id"], body["meeting"]["room_name"]
    assert body["email_sent"] is False and body["email"]["queued"] == 3      # queued, not sent
    assert [r["status"] for r in _rows(mid)] == ["queued"] * 3

    with caplog.at_level(logging.INFO):
        assert outbox.dispatch_pending() == 3
    assert sorted(m.to_email for m in sender.sent) == sorted(emails)
    tokens = []
    for message in sender.sent:
        assert message.subject == "Invitation: Login portal release"
        assert "Samarth" in message.html and "Asia/Kolkata" in message.html and "15:00" in message.html
        assert f"http://app.test/?room={room}&amp;invite=" in message.html
        tokens.append(_link_token(message))
    assert len(set(tokens)) == 3
    assert [r["status"] for r in _rows(mid)] == ["provider_accepted"] * 3

    # Raw tokens exist only in the emails: not in the outbox, the invitation
    # table, or the logs.
    with db.get_conn() as conn:
        stored = json.dumps([dict(r) for r in conn.execute("SELECT * FROM email_outbox WHERE meeting_id = ?", (mid,))]
                            + [dict(r) for r in conn.execute("SELECT * FROM meeting_invitations WHERE meeting_id = ?",
                                                              (mid,))])
    assert not any(t in stored for t in tokens)
    assert not any(t in caplog.text for t in tokens) and emails[0] not in caplog.text

    # Each emailed link admits its own invitee, as a participant.
    by_email = {m.to_email: _link_token(m) for m in sender.sent}
    guest_client, guest = _account("Asmee", emails[0])
    accepted = guest_client.post("/api/auth/invitations/accept", json={"token": by_email[emails[0]]})
    assert accepted.status_code == 200 and accepted.json()["meeting_id"] == mid
    assert db.get_member_role(mid, guest["user_id"]) == "member"
    assert db.get_meeting_owner_ids(mid) == [host["user_id"]]
    # Ravi's link used by Asmee's account is not silently accepted.
    assert guest_client.post("/api/auth/invitations/accept", json={"token": by_email[emails[1]]}).status_code == 409


def test_development_mode_never_claims_an_email_was_sent():
    host_client, _ = _account("Samarth")
    body = _schedule(host_client, [_addr("asmee")])
    assert body["email"]["live"] is False and body["email"]["queued"] == 0
    outbox.dispatch_pending()
    row = _rows(body["meeting"]["meeting_id"])[0]
    assert row["status"] == "not_sent_dev" and row["provider_message_id"] is None
    with db.get_conn() as conn:                     # the never-sent email link was discarded
        channels = [r[0] for r in conn.execute("SELECT channel FROM meeting_invitations WHERE meeting_id = ?",
                                               (body["meeting"]["meeting_id"],))]
    assert channels == ["copy"]


def test_rate_limits_retry_with_backoff_then_fail_and_a_retry_reuses_the_record(sender, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_MAX_ATTEMPTS", 2)
    host_client, _ = _account("Samarth")
    body = _schedule(host_client, [_addr("asmee")])
    mid = body["meeting"]["meeting_id"]
    invitee_id = body["links"][0]["invitee_id"]
    sender.outcomes = [providers.SendOutcome("retry", error="Brevo rate limit reached (HTTP 429)", retry_after=5)]

    outbox.dispatch_pending()
    row = _rows(mid)[0]
    assert row["status"] == "queued" and row["attempts"] == 1 and "429" in row["last_error"]
    assert "Next attempt at" in row["last_error"]

    db.update_email(row["outbox_id"], next_attempt_at="2000-01-01T00:00:00")     # make it due now
    sender.outcomes = [providers.SendOutcome("retry", error="Brevo error (HTTP 503)")]
    outbox.dispatch_pending()
    row = _rows(mid)[0]
    assert row["status"] == "failed" and "Gave up after 2 attempts" in row["last_error"]
    assert row["provider_message_id"] is None and row["delivered_at"] is None

    view = host_client.get(f"/api/meetings/{mid}/schedule").json()["meeting"]["invitees"][0]["email_delivery"]
    assert view["status"] == "failed" and view["can_retry"] is True

    resent = host_client.post(f"/api/meetings/{mid}/schedule/invitees/{invitee_id}/email")
    assert resent.status_code == 200 and resent.json()["email_delivery"]["outbox_id"] == row["outbox_id"]
    assert host_client.post(f"/api/meetings/{mid}/schedule/invitees/{invitee_id}/email").status_code == 409
    outbox.dispatch_pending()
    assert [r["status"] for r in _rows(mid)] == ["provider_accepted"]           # one record, no duplicate
    assert len(db.list_invitees(mid)) == 1


@pytest.mark.parametrize("outcome,expected", [
    (providers.SendOutcome("failed", error="Brevo refused the request (HTTP 401)"), "HTTP 401"),
    (providers.SendOutcome("unknown", error="Brevo did not answer in time; the message may or may not have been "
                                            "accepted."), "may or may not"),
])
def test_permanent_errors_and_timeouts_fail_visibly(sender, outcome, expected):
    host_client, _ = _account("Samarth")
    mid = _schedule(host_client, [_addr("asmee")])["meeting"]["meeting_id"]
    sender.outcomes = [outcome]
    outbox.dispatch_pending()
    row = _rows(mid)[0]
    assert row["status"] == "failed" and expected in row["last_error"] and row["attempts"] == 1


def test_revoking_or_cancelling_stops_queued_invitations(sender):
    host_client, _ = _account("Samarth")
    body = _schedule(host_client, [_addr("asmee"), _addr("ravi")])
    mid = body["meeting"]["meeting_id"]
    host_client.delete(f"/api/meetings/{mid}/schedule/invitees/{body['links'][0]['invitee_id']}")
    host_client.post(f"/api/meetings/{mid}/schedule/cancel")
    outbox.dispatch_pending()
    assert sender.sent == []
    rows = _rows(mid)
    assert [r["status"] for r in rows] == ["cancelled", "cancelled"]
    assert "revoked" in rows[0]["last_error"] and "cancelled" in rows[1]["last_error"]


def test_delivery_webhooks_update_status_without_downgrading(sender, monkeypatch):
    host_client, _ = _account("Samarth")
    mid = _schedule(host_client, [_addr("asmee"), _addr("ravi")])["meeting"]["meeting_id"]
    outbox.dispatch_pending()
    first, second = _rows(mid)
    hook = TestClient(app)

    event = {"event": "delivered", "message-id": first["provider_message_id"], "email": first["recipient_email"],
             "ts_event": 1761300000}
    assert hook.post("/api/email/webhooks/brevo", json=event).status_code == 503        # not configured
    monkeypatch.setattr(config, "BREVO_WEBHOOK_TOKEN", WEBHOOK_TOKEN)
    assert hook.post("/api/email/webhooks/brevo", json=event).status_code == 401
    assert hook.post("/api/email/webhooks/brevo", json=event,
                     headers={"X-Meet2MoM-Webhook-Token": "wrong"}).status_code == 401
    ok = hook.post("/api/email/webhooks/brevo", json=event, headers={"X-Meet2MoM-Webhook-Token": WEBHOOK_TOKEN})
    assert ok.json() == {"received": 1, "applied": 1}
    # A late "request" (sent) or an "opened" never moves it back from delivered.
    for name in ("request", "opened"):
        hook.post("/api/email/webhooks/brevo", json={**event, "event": name},
                  headers={"Authorization": f"Bearer {WEBHOOK_TOKEN}"})
    hook.post("/api/email/webhooks/brevo", headers={"Authorization": f"Bearer {WEBHOOK_TOKEN}"},
              json={"event": "hard_bounce", "message-id": second["provider_message_id"], "reason": "mailbox unknown"})

    first, second = _rows(mid)
    assert first["status"] == "delivered" and first["delivered_at"]
    assert second["status"] == "hard_bounced" and second["last_error"] == "mailbox unknown"
    history = host_client.get(f"/api/meetings/{mid}/emails?kind=invitation").json()["emails"]
    events = {e["recipient_email"]: [x["event"] for x in e["events"]] for e in history}
    assert events[first["recipient_email"]] == ["delivered", "request"]          # opens are not stored
    assert not any(e["can_retry"] for e in history)                              # bounced: retry cannot help


# ---------------------------------------------------------------------------
# Brevo sender (fake urlopen; never the network)
# ---------------------------------------------------------------------------

class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_brevo_request_uses_the_documented_api(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"], seen["headers"], seen["body"] = request.full_url, dict(request.header_items()), json.loads(request.data)
        return _Response(b'{"messageId": "<201798300811.5787683@relay.domain.com>"}')

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    sender = providers.BrevoSender("xkeysib-test", "meet@example.com", "Meet2MoM", config.BREVO_API_URL, 5)
    outcome = sender.send(providers.OutgoingEmail("asmee@example.com", "Asmee", "Subject", "<p>Hi</p>",
                                                  reference="m2m-outbox-7", attachments=[("m.pdf", b"%PDF-1")]))
    assert outcome.kind == "accepted" and outcome.message_id.startswith("<2017")
    assert seen["url"] == "https://api.brevo.com/v3/smtp/email"
    assert seen["headers"]["Api-key"] == "xkeysib-test"
    assert seen["body"]["sender"] == {"email": "meet@example.com", "name": "Meet2MoM"}
    assert seen["body"]["to"] == [{"email": "asmee@example.com", "name": "Asmee"}]
    assert seen["body"]["headers"] == {"X-Mailin-custom": "m2m-outbox-7"}
    assert seen["body"]["attachment"] == [{"name": "m.pdf", "content": "JVBERi0x"}]


@pytest.mark.parametrize("code,kind", [(429, "retry"), (503, "retry"), (401, "failed"), (400, "failed"), (402, "failed")])
def test_brevo_errors_are_classified(monkeypatch, code, kind):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, code, "x", {"Retry-After": "7"},
                                     io.BytesIO(b'{"code": "bad", "message": "nope"}'))

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    sender = providers.BrevoSender("xkeysib-test", "meet@example.com", "Meet2MoM", config.BREVO_API_URL, 5)
    outcome = sender.send(providers.OutgoingEmail("a@example.com", None, "S", "<p>x</p>", reference="r"))
    assert outcome.kind == kind and "xkeysib-test" not in (outcome.error or "")
    if code == 429:
        assert outcome.retry_after == 7


def test_missing_brevo_settings_fail_honestly(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "brevo")
    status = providers.provider_status()
    assert status["configured"] is False and "BREVO_API_KEY" in status["message"]
    outcome = providers.get_sender().send(providers.OutgoingEmail("a@example.com", None, "S", "x", reference="r"))
    assert outcome.kind == "failed"


# ---------------------------------------------------------------------------
# Approved minutes
# ---------------------------------------------------------------------------

HELD_TEXT = "Send the signed contract to the client"


def _approved_meeting(monkeypatch, host_client, host, mid=None, approve=True):
    """A meeting the host owns with a confirmed decision, a confirmed action and a held action."""
    install_semantic_fakes(monkeypatch)
    mid = mid or f"mail-{uuid4().hex[:10]}"
    if db.get_meeting(mid) is None:
        db.insert_meeting(mid, filename="planning.wav")
        db.add_meeting_member(mid, host["user_id"], "owner")
    lines = [
        ("u_host", "Samarth", "We decided to launch the new login portal next Monday.", 1.0),
        ("u_host", "Samarth", "I will finalize the backend API by Friday.", 1.0),
        ("SPEAKER_01", None, "I will send the signed contract to the client tomorrow.", 0.2),
    ]
    db.bulk_insert_utterances(mid, [
        Utterance(meeting_id=mid, utt_id=f"u{i:05d}", start=i * 5.0, end=i * 5.0 + 4, speaker_id=spk,
                  speaker_name=name, text=text, asr_conf=0.9, spk_conf=conf, overlap=False, conf_source="numeric")
        for i, (spk, name, text, conf) in enumerate(lines)
    ])
    add_decision(mid, 0, "Launch the new login portal next Monday", ["u00000"])
    add_action(mid, 0, "Finalize the backend API", ["u00001"], owner="Samarth", owner_type="explicit_first_person",
               deadline_text="by Friday", deadline_resolved="2026-10-30")
    add_action(mid, 1, HELD_TEXT, ["u00002"], owner="SPEAKER_01", owner_type="explicit_first_person",
               deadline_text="tomorrow", deadline_resolved="2026-10-25")
    record_extraction(mid)
    host_client.post(f"/api/verify/{mid}")
    if approve:
        res = host_client.post(f"/api/meetings/{mid}/approve", json={"reviewer": "ignored"})
        assert res.status_code == 200, res.text
        assert {x["text"] for x in res.json()["mom"]["needs_review"]} == {HELD_TEXT}
    return mid


def _send(client, mid, keys, version=1, **extra):
    return client.post(f"/api/meetings/{mid}/minutes-email", json={"recipients": keys, "version": version, **extra})


def test_only_approved_minutes_can_be_sent(monkeypatch, sender):
    host_client, host = _account("Samarth")
    mid = _approved_meeting(monkeypatch, host_client, host, approve=False)
    options = host_client.get(f"/api/meetings/{mid}/minutes-email").json()
    assert options["approved"] is False and options["can_send"] is False
    assert _send(host_client, mid, [f"user:{host['user_id']}"]).status_code == 409


def test_approved_minutes_go_to_entitled_recipients_with_the_exact_version(monkeypatch, sender):
    host_client, host = _account("Samarth")
    mid = _approved_meeting(monkeypatch, host_client, host)
    member_client, member = _account("Asmee")
    db.add_meeting_member(mid, member["user_id"], "member")

    options = host_client.get(f"/api/meetings/{mid}/minutes-email").json()
    assert options["approved"] is True and options["version"] == 1 and options["can_send"] is True
    keys = {r["key"] for r in options["recipients"]}
    assert keys == {f"user:{host['user_id']}", f"user:{member['user_id']}"}

    # Never an arbitrary address, never another meeting's people.
    refused = _send(host_client, mid, ["user:u_someone_else", "evil@example.com"])
    assert refused.status_code == 422

    res = _send(host_client, mid, [f"user:{member['user_id']}"])
    assert res.status_code == 200 and len(res.json()["queued"]) == 1
    outbox.dispatch_pending()
    message = sender.sent[-1]
    assert message.to_email == member["email"]
    assert message.subject == "Approved minutes: Meeting Minutes (v1)"
    assert "Approved minutes, version 1" in message.html
    assert "Launch the new login portal next Monday" in message.html
    assert "Finalize the backend API" in message.html
    # The held item is never presented as a confirmed commitment.
    assert HELD_TEXT not in message.html and "1 item was held for review" in message.html
    assert f"http://app.test/?meeting={mid}&amp;step=minutes" in message.html and "invite=" not in message.html
    assert not message.attachments

    # Repeating the request does not email the person again.
    again = _send(host_client, mid, [f"user:{member['user_id']}"]).json()
    assert again["queued"] == [] and len(again["already"]) == 1
    outbox.dispatch_pending()
    assert len(sender.sent) == 1 and len(_rows(mid, "minutes")) == 1

    audit = host_client.get(f"/api/meetings/{mid}/audit-log").json()["audit_log"]
    requested = [json.loads(e["new_value"]) for e in audit if e["action"] == "minutes_email_requested"]
    assert requested[0]["version"] == 1 and requested[0]["recipients"] == [member["email"]]
    assert any(e["action"] == "minutes_email_status" and '"provider_accepted"' in e["new_value"] for e in audit)
    assert all("invite=" not in (e["new_value"] or "") for e in audit)


def test_permissions_versions_and_attachment_confirmation(monkeypatch, sender):
    host_client, host = _account("Samarth")
    mid = _approved_meeting(monkeypatch, host_client, host)
    member_client, member = _account("Asmee")
    db.add_meeting_member(mid, member["user_id"], "member")
    outsider, _ = _account("Olga")
    key = [f"user:{member['user_id']}"]

    assert _send(member_client, mid, key).status_code == 403          # a member who did not approve
    assert _send(outsider, mid, key).status_code == 404
    assert _send(host_client, mid, key, version=2).status_code == 409
    assert _send(host_client, mid, key, attach_pdf=True).status_code == 422
    res = _send(host_client, mid, key, attach_pdf=True, confirm_attachment=True)
    assert res.status_code == 200 and res.json()["queued"][0]["attach_pdf"] is True
    outbox.dispatch_pending()
    name, data = sender.sent[-1].attachments[0]
    assert name == "minutes-v1.pdf" and data[:4] == b"%PDF"


def test_provider_failure_keeps_approval_and_is_never_marked_delivered(monkeypatch, sender):
    host_client, host = _account("Samarth")
    mid = _approved_meeting(monkeypatch, host_client, host)
    sender.outcomes = [providers.SendOutcome("failed", error="Brevo refused the request (HTTP 401)")]
    _send(host_client, mid, [f"user:{host['user_id']}"])
    outbox.dispatch_pending()
    row = _rows(mid, "minutes")[0]
    assert row["status"] == "failed" and row["delivered_at"] is None and row["sent_at"] is None
    assert db.get_review_draft(mid)["draft_status"] == "approved"
    assert db.get_latest_approved_snapshot(mid)["version"] == 1

    retried = host_client.post(f"/api/meetings/{mid}/emails/{row['outbox_id']}/retry")
    assert retried.status_code == 200 and retried.json()["email"]["status"] == "queued"
    outbox.dispatch_pending()
    assert [r["status"] for r in _rows(mid, "minutes")] == ["provider_accepted"]
    assert host_client.post(f"/api/meetings/{mid}/emails/{row['outbox_id']}/retry").status_code == 409


def test_reopened_minutes_are_not_sent(monkeypatch, sender):
    host_client, host = _account("Samarth")
    mid = _approved_meeting(monkeypatch, host_client, host)
    _send(host_client, mid, [f"user:{host['user_id']}"])
    assert host_client.post(f"/api/meetings/{mid}/reopen", json={"reviewer": "x", "reason": "Fix owner"}).status_code == 200
    outbox.dispatch_pending()
    assert sender.sent == []
    assert _rows(mid, "minutes")[0]["status"] == "cancelled"


def test_recipients_cannot_open_another_meetings_minutes(monkeypatch, sender):
    host_client, host = _account("Samarth")
    mine = _approved_meeting(monkeypatch, host_client, host)
    other_host, other = _account("Hana")
    theirs = _approved_meeting(monkeypatch, other_host, other)
    member_client, member = _account("Asmee")
    db.add_meeting_member(mine, member["user_id"], "member")
    assert member_client.get(f"/api/meetings/{mine}/export/json").status_code == 200
    assert member_client.get(f"/api/meetings/{theirs}/export/json").status_code == 404
    assert member_client.get(f"/api/download-mom/{theirs}").status_code == 404


def test_a_pending_invitee_gets_minutes_through_sign_in_and_acceptance(monkeypatch, sender):
    host_client, host = _account("Samarth")
    invited = _addr("ravi")
    mid = _schedule(host_client, [invited])["meeting"]["meeting_id"]
    outbox.dispatch_pending()                                         # the invitation itself
    _approved_meeting(monkeypatch, host_client, host, mid=mid)

    options = host_client.get(f"/api/meetings/{mid}/minutes-email").json()
    invitee = next(r for r in options["recipients"] if r["kind"] == "invitee")
    assert invitee["email"] == invited and invitee["eligible"] is True
    _send(host_client, mid, [invitee["key"]])
    outbox.dispatch_pending()
    message = sender.sent[-1]
    assert message.to_email == invited and f"?meeting={mid}&amp;step=minutes&amp;invite=" in message.html

    ravi_client, ravi = _account("Ravi", invited)
    assert ravi_client.get(f"/api/meetings/{mid}/export/json").status_code == 404       # not yet a member
    token = _link_token(message)
    assert ravi_client.post("/api/auth/invitations/accept", json={"token": token}).status_code == 200
    minutes = ravi_client.get(f"/api/meetings/{mid}/export/json")
    assert minutes.status_code == 200 and minutes.json()["status"] == "approved"
    assert db.get_member_role(mid, ravi["user_id"]) == "member"
