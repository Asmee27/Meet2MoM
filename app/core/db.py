from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional
from uuid import uuid4

from app.core.config import DB_PATH
from app.schemas.claim import ActionItem, ClaimBase, Decision
from app.schemas.job import JobStatus
from app.schemas.utterance import Utterance


@contextmanager
def get_conn(db_path: Optional[Path | str] = None) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager yielding an SQLite connection with foreign keys strictly enabled.
    Commits on clean exit, rolls back completely on any error.
    """
    path = Path(db_path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Optional[Path | str] = None) -> None:
    """Initialize database tables, foreign keys, and indexes."""
    with get_conn(db_path) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS meetings (
                meeting_id TEXT PRIMARY KEY,
                filename TEXT,
                duration REAL,
                created_at TEXT,
                status TEXT
            );

            CREATE TABLE IF NOT EXISTS utterances (
                meeting_id TEXT,
                utt_id TEXT,
                start REAL,
                end REAL,
                speaker_id TEXT,
                speaker_name TEXT,
                text TEXT,
                lang_tags TEXT,
                script TEXT,
                asr_conf REAL,
                spk_conf REAL,
                no_speech_prob REAL,
                conf_source TEXT,
                overlap INTEGER,
                snr REAL,
                topic_id TEXT,
                PRIMARY KEY (meeting_id, utt_id),
                FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
            );

            CREATE INDEX IF NOT EXISTS idx_utterances_meeting_start
                ON utterances(meeting_id, start);
            """
        )
        try:
            conn.execute("ALTER TABLE utterances ADD COLUMN conf_source TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE utterances ADD COLUMN no_speech_prob REAL")
        except sqlite3.OperationalError:
            pass

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS claims (
                claim_id TEXT PRIMARY KEY,
                meeting_id TEXT,
                claim_type TEXT,
                text TEXT,
                payload TEXT,
                created_at TEXT,
                FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
            );

            CREATE INDEX IF NOT EXISTS idx_claims_meeting_type
                ON claims(meeting_id, claim_type);

            CREATE TABLE IF NOT EXISTS evidence_links (
                claim_id TEXT NOT NULL,
                meeting_id TEXT NOT NULL,
                utt_id TEXT NOT NULL,
                PRIMARY KEY (claim_id, utt_id),
                FOREIGN KEY (claim_id) REFERENCES claims(claim_id) ON DELETE CASCADE,
                FOREIGN KEY (meeting_id, utt_id)
                    REFERENCES utterances(meeting_id, utt_id) ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS dropped_claims (
                drop_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id  TEXT NOT NULL,
                window_id   TEXT,
                claim_type  TEXT,
                text        TEXT,
                cited_evidence TEXT,
                reason      TEXT NOT NULL,
                detail      TEXT,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
            );

            CREATE INDEX IF NOT EXISTS idx_dropped_meeting
                ON dropped_claims(meeting_id);

            CREATE TABLE IF NOT EXISTS jobs (
                meeting_id TEXT PRIMARY KEY,
                stage TEXT,
                progress REAL,
                state TEXT,
                error TEXT,
                updated_at TEXT,
                heartbeat_at TEXT,
                retry_count INTEGER DEFAULT 0,
                file_path TEXT,
                claim_token TEXT
            );
            """
        )
        for col_def in (
            "heartbeat_at TEXT",
            "retry_count INTEGER DEFAULT 0",
            "file_path TEXT",
            "claim_token TEXT",
        ):
            try:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col_def}")
            except sqlite3.OperationalError:
                pass

        _migrate(conn)



# ---------------------------------------------------------------------------
# Versioned schema migrations (PRAGMA user_version)
# ---------------------------------------------------------------------------
# Migrations only ADD tables/indexes; they never rewrite or drop existing
# data. Each step is idempotent (IF NOT EXISTS) and bumps user_version.
# One exception: v10 fills in a missing commitment owner (NULL only), see
# _MIGRATION_V10_BACKFILL.

SCHEMA_VERSION = 10

_MIGRATION_V1 = """
CREATE TABLE IF NOT EXISTS extraction_runs (
    meeting_id        TEXT PRIMARY KEY,
    mode_requested    TEXT,
    mode_used         TEXT,
    status            TEXT NOT NULL,
    reason            TEXT,
    fallback_reason   TEXT,
    accepted          INTEGER,
    dropped           INTEGER,
    extractor_version TEXT,
    started_at        TEXT,
    finished_at       TEXT,
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
);

CREATE TABLE IF NOT EXISTS verification_results (
    claim_id              TEXT NOT NULL,
    meeting_id            TEXT NOT NULL,
    method                TEXT NOT NULL,
    label                 TEXT NOT NULL,
    cited_utt_ids         TEXT,
    cited_entailment      REAL,
    best_utt_ids          TEXT,
    best_support          REAL,
    contradiction         REAL,
    contradiction_utt_ids TEXT,
    reasons               TEXT,
    details               TEXT,
    model_version         TEXT,
    config_version        TEXT,
    latency_ms            REAL,
    created_at            TEXT NOT NULL,
    PRIMARY KEY (claim_id, method),
    FOREIGN KEY (claim_id) REFERENCES claims(claim_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_verification_results_meeting
    ON verification_results(meeting_id);

CREATE TABLE IF NOT EXISTS verification_runs (
    meeting_id       TEXT PRIMARY KEY,
    status           TEXT NOT NULL,
    reason           TEXT,
    claims_total     INTEGER,
    seconds_total    REAL,
    semantic_seconds REAL,
    model_version    TEXT,
    config_version   TEXT,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
);

CREATE TABLE IF NOT EXISTS evidence_corrections (
    claim_id         TEXT PRIMARY KEY,
    meeting_id       TEXT NOT NULL,
    action           TEXT NOT NULL CHECK (action IN ('accepted', 'rejected')),
    original_utt_ids TEXT NOT NULL,
    proposed_utt_ids TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (claim_id) REFERENCES claims(claim_id) ON DELETE CASCADE
);
"""

_MIGRATION_V2 = """
CREATE TABLE IF NOT EXISTS review_drafts (
    meeting_id      TEXT PRIMARY KEY,
    draft_status    TEXT NOT NULL DEFAULT 'draft' CHECK (draft_status IN ('draft', 'approved', 'reopened')),
    meeting_title   TEXT,
    summary         TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS review_items (
    item_id             TEXT NOT NULL,
    meeting_id          TEXT NOT NULL,
    original_claim_id   TEXT,
    item_type           TEXT NOT NULL CHECK (item_type IN ('decision', 'action')),
    text                TEXT NOT NULL,
    state               TEXT,
    owner               TEXT,
    owner_type          TEXT,
    deadline_text       TEXT,
    deadline_resolved   TEXT,
    evidence_ids        TEXT NOT NULL DEFAULT '[]',
    review_status       TEXT NOT NULL CHECK (review_status IN ('original', 'edited', 'added', 'rejected', 'accepted_from_held')),
    is_excluded         INTEGER NOT NULL DEFAULT 0,
    order_index         INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL,
    PRIMARY KEY (meeting_id, item_id),
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_review_items_meeting ON review_items(meeting_id);

CREATE TABLE IF NOT EXISTS review_audit_log (
    audit_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id      TEXT NOT NULL,
    item_id         TEXT,
    action          TEXT NOT NULL,
    field_name      TEXT,
    old_value       TEXT,
    new_value       TEXT,
    reason          TEXT,
    reviewer_id     TEXT,
    timestamp       TEXT NOT NULL,
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_review_audit_meeting ON review_audit_log(meeting_id);

CREATE TABLE IF NOT EXISTS approved_snapshots (
    snapshot_id     TEXT PRIMARY KEY,
    meeting_id      TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    approved_by     TEXT,
    approved_at     TEXT NOT NULL,
    mom_payload     TEXT NOT NULL,
    reopened_at     TEXT,
    reopened_by     TEXT,
    reopen_reason   TEXT,
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_approved_snapshots_meeting ON approved_snapshots(meeting_id, version);
"""

# Phase 4.2: live meetings, server-side consent and LiveKit audio recording.
_MIGRATION_V3 = """
CREATE TABLE IF NOT EXISTS live_meetings (
    meeting_id      TEXT PRIMARY KEY,
    room_name       TEXT NOT NULL,
    room_sid        TEXT,
    title           TEXT,
    host_identity   TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    created_at      TEXT NOT NULL,
    ended_at        TEXT,
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
);

CREATE INDEX IF NOT EXISTS idx_live_meetings_room ON live_meetings(room_name, created_at);

CREATE TABLE IF NOT EXISTS live_participants (
    meeting_id          TEXT NOT NULL,
    identity            TEXT NOT NULL,
    name                TEXT,
    is_host             INTEGER NOT NULL DEFAULT 0,
    consented           INTEGER NOT NULL DEFAULT 0,
    consent_updated_at  TEXT,
    connected           INTEGER NOT NULL DEFAULT 0,
    first_seen_at       TEXT,
    last_seen_at        TEXT,
    PRIMARY KEY (meeting_id, identity),
    FOREIGN KEY (meeting_id) REFERENCES live_meetings(meeting_id)
);

CREATE TABLE IF NOT EXISTS live_events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    identity    TEXT,
    segment_id  TEXT,
    detail      TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_live_events_meeting ON live_events(meeting_id, event_id);

CREATE TABLE IF NOT EXISTS recording_segments (
    segment_id          TEXT PRIMARY KEY,
    meeting_id          TEXT NOT NULL,
    seq                 INTEGER NOT NULL,
    state               TEXT NOT NULL,
    started_by          TEXT,
    started_at          TEXT NOT NULL,
    capture_started_at  TEXT,
    stopped_at          TEXT,
    stop_reason         TEXT,
    participants        TEXT,
    tracks              TEXT,
    artifact_path       TEXT,
    duration_seconds    REAL,
    error               TEXT,
    updated_at          TEXT NOT NULL,
    UNIQUE (meeting_id, seq),
    FOREIGN KEY (meeting_id) REFERENCES live_meetings(meeting_id)
);

-- At most one non-terminal segment per meeting, enforced by the database.
CREATE UNIQUE INDEX IF NOT EXISTS idx_recording_one_active
    ON recording_segments(meeting_id)
    WHERE state IN ('starting', 'recording', 'stopping');
"""


# Phase 5: per-stage processing timings, one row per stage of each run.
# status: running | completed | failed | skipped | cached | fallback.
# duration_seconds is NULL unless the stage actually ran (never a fake 0).
_MIGRATION_V4 = """
CREATE TABLE IF NOT EXISTS processing_stage_timings (
    meeting_id        TEXT NOT NULL,
    run_id            TEXT NOT NULL,
    stage             TEXT NOT NULL,
    parent_stage      TEXT,
    seq               INTEGER NOT NULL,
    status            TEXT NOT NULL CHECK (status IN
                      ('running', 'completed', 'failed', 'skipped', 'cached', 'fallback')),
    started_at        TEXT,
    finished_at       TEXT,
    duration_seconds  REAL,
    detail            TEXT,
    PRIMARY KEY (meeting_id, run_id, stage),
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
);

CREATE INDEX IF NOT EXISTS idx_stage_timings_meeting
    ON processing_stage_timings(meeting_id, run_id, seq);
"""


# Phase 7: accounts, sessions, meeting membership and invitations.
# Session and invitation tokens are stored only as SHA-256 hashes.
_MIGRATION_V5 = """
CREATE TABLE IF NOT EXISTS users (
    user_id        TEXT PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name   TEXT NOT NULL,
    password_hash  TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS meeting_members (
    meeting_id  TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('owner', 'member')),
    added_at    TEXT NOT NULL,
    PRIMARY KEY (meeting_id, user_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_meeting_members_user ON meeting_members(user_id);

CREATE TABLE IF NOT EXISTS meeting_invitations (
    token_hash  TEXT PRIMARY KEY,
    meeting_id  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    FOREIGN KEY (created_by) REFERENCES users(user_id) ON DELETE CASCADE
);
"""


# Phase 9: evidence-linked commitment ledger. A commitment is created only
# from a human-approved action item; its status changes only when a reviewer
# confirms or corrects a proposal (commitment_updates keeps the history).
_MIGRATION_V6 = """
CREATE TABLE IF NOT EXISTS commitments (
    commitment_id      TEXT PRIMARY KEY,
    ledger_owner_id    TEXT,
    source_meeting_id  TEXT NOT NULL,
    source_item_id     TEXT NOT NULL,
    text               TEXT NOT NULL,
    assignee           TEXT,
    assignee_user_id   TEXT,
    deadline           TEXT,
    evidence_ids       TEXT NOT NULL DEFAULT '[]',
    status             TEXT NOT NULL CHECK (status IN ('open', 'done', 'delayed')),
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE (source_meeting_id, source_item_id)
);

CREATE INDEX IF NOT EXISTS idx_commitments_owner ON commitments(ledger_owner_id, status);

CREATE TABLE IF NOT EXISTS commitment_updates (
    update_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    commitment_id     TEXT NOT NULL,
    meeting_id        TEXT NOT NULL,
    proposed_status   TEXT NOT NULL CHECK (proposed_status IN ('done', 'delayed', 'no_update')),
    evidence_ids      TEXT NOT NULL DEFAULT '[]',
    scores            TEXT,
    method            TEXT,
    review_status     TEXT NOT NULL DEFAULT 'proposed'
                      CHECK (review_status IN ('proposed', 'confirmed', 'corrected')),
    final_status      TEXT CHECK (final_status IN ('done', 'delayed', 'no_update')),
    reviewer          TEXT,
    note              TEXT,
    created_at        TEXT NOT NULL,
    reviewed_at       TEXT,
    UNIQUE (commitment_id, meeting_id),
    FOREIGN KEY (commitment_id) REFERENCES commitments(commitment_id) ON DELETE CASCADE
);
"""


# Phase 8: reviewer corrections of transcript text. Append-only history; the
# ASR text in `utterances` is never overwritten. asr_text records the text the
# correction was made against, so a reprocessed transcript (same utt_id, new
# text) is never shown with a correction meant for different words.
_MIGRATION_V7 = """
CREATE TABLE IF NOT EXISTS transcript_corrections (
    correction_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id      TEXT NOT NULL,
    utt_id          TEXT NOT NULL,
    asr_text        TEXT NOT NULL,
    previous_text   TEXT NOT NULL,
    corrected_text  TEXT NOT NULL,
    reviewer        TEXT,
    reason          TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transcript_corrections_meeting
    ON transcript_corrections(meeting_id, utt_id, correction_id);
"""



# Phase 8.3: scheduled meetings and their invitees. A scheduled meeting is a
# normal meetings row (status 'scheduled') owned through meeting_members; the
# schedule row holds the intended time and the reserved room code. Invitees
# are tracked per email; their tokens live in meeting_invitations (hash only),
# so revoking a link deletes its row and the link stops working.
_MIGRATION_V8 = """
CREATE TABLE IF NOT EXISTS scheduled_meetings (
    meeting_id           TEXT PRIMARY KEY,
    title                TEXT NOT NULL,
    agenda               TEXT,
    scheduled_start_utc  TEXT NOT NULL,
    timezone             TEXT NOT NULL,
    duration_minutes     INTEGER,
    room_name            TEXT NOT NULL UNIQUE,
    status               TEXT NOT NULL CHECK (status IN ('scheduled', 'started', 'cancelled')),
    created_by           TEXT NOT NULL,
    request_id           TEXT,
    created_at           TEXT NOT NULL,
    cancelled_at         TEXT,
    UNIQUE (created_by, request_id),
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
);

CREATE TABLE IF NOT EXISTS meeting_invitees (
    invitee_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id        TEXT NOT NULL,
    email             TEXT NOT NULL COLLATE NOCASE,
    status            TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'revoked')),
    token_hash        TEXT,
    delivery          TEXT NOT NULL DEFAULT 'manual',
    accepted_user_id  TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (meeting_id, email),
    FOREIGN KEY (meeting_id) REFERENCES meetings(meeting_id)
);

CREATE INDEX IF NOT EXISTS idx_meeting_invitees_token ON meeting_invitees(token_hash);
"""


# Phase 8.4: transactional email. email_outbox is the queue and the delivery
# record: one row per message, unique per idempotency key, so a repeated
# request never queues the same email twice. Rows hold no link tokens; links
# are issued when the message is sent and only their hashes are stored.
# email_events keeps the provider's delivery events per message.
_MIGRATION_V9 = """
CREATE TABLE IF NOT EXISTS email_outbox (
    outbox_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key      TEXT NOT NULL UNIQUE,
    kind                 TEXT NOT NULL CHECK (kind IN ('invitation', 'minutes')),
    meeting_id           TEXT NOT NULL,
    recipient_email      TEXT NOT NULL,
    recipient_name       TEXT,
    recipient_user_id    TEXT,
    invitee_id           INTEGER,
    minutes_version      INTEGER,
    attach_pdf           INTEGER NOT NULL DEFAULT 0,
    status               TEXT NOT NULL,
    provider             TEXT,
    provider_message_id  TEXT,
    attempts             INTEGER NOT NULL DEFAULT 0,
    next_attempt_at      TEXT,
    last_error           TEXT,
    requested_by         TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    claimed_at           TEXT,
    sent_at              TEXT,
    delivered_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_email_outbox_due ON email_outbox(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_email_outbox_meeting ON email_outbox(meeting_id, kind);
CREATE INDEX IF NOT EXISTS idx_email_outbox_message ON email_outbox(provider_message_id);

CREATE TABLE IF NOT EXISTS email_events (
    event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    outbox_id     INTEGER NOT NULL,
    event         TEXT NOT NULL,
    reason        TEXT,
    event_ts      TEXT,
    received_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_email_events_outbox ON email_events(outbox_id);
"""


_MIGRATION_V10_BACKFILL = """
UPDATE commitments SET ledger_owner_id = COALESCE(
    (SELECT m.owner_user_id FROM meetings m WHERE m.meeting_id = commitments.source_meeting_id),
    (SELECT mm.user_id FROM meeting_members mm
      WHERE mm.meeting_id = commitments.source_meeting_id AND mm.role = 'owner'
      ORDER BY mm.added_at LIMIT 1)
)
WHERE ledger_owner_id IS NULL
"""


def _add_column(conn: sqlite3.Connection, table: str, column_def: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
    except sqlite3.OperationalError:
        pass


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current < 1:
        conn.executescript(_MIGRATION_V1)
        conn.execute("PRAGMA user_version = 1")
        current = 1
    if current < 2:
        conn.executescript(_MIGRATION_V2)
        conn.execute("PRAGMA user_version = 2")
        current = 2
    if current < 3:
        conn.executescript(_MIGRATION_V3)
        conn.execute("PRAGMA user_version = 3")
        current = 3
    if current < 4:
        conn.executescript(_MIGRATION_V4)
        conn.execute("PRAGMA user_version = 4")
        current = 4
    if current < 5:
        conn.executescript(_MIGRATION_V5)
        try:
            conn.execute("ALTER TABLE meetings ADD COLUMN owner_user_id TEXT")
        except sqlite3.OperationalError:
            pass
        conn.execute("PRAGMA user_version = 5")
        current = 5
    if current < 6:
        conn.executescript(_MIGRATION_V6)
        conn.execute("PRAGMA user_version = 6")
        current = 6
    if current < 7:
        conn.executescript(_MIGRATION_V7)
        conn.execute("PRAGMA user_version = 7")
        current = 7
    if current < 8:
        conn.executescript(_MIGRATION_V8)
        conn.execute("PRAGMA user_version = 8")
        current = 8
    if current < 9:
        conn.executescript(_MIGRATION_V9)
        # An invitation token can belong to one invitee and one channel
        # ("copy" link or "email"), so re-issuing one never breaks the other.
        _add_column(conn, "meeting_invitations", "invitee_id INTEGER")
        _add_column(conn, "meeting_invitations", "channel TEXT")
        # An account whose email differs from the invited address waits here
        # until the host approves it (never treated as the same person).
        _add_column(conn, "meeting_invitees", "approval_user_id TEXT")
        _add_column(conn, "meeting_invitees", "approval_requested_at TEXT")
        conn.execute("PRAGMA user_version = 9")
        current = 9
    if current < 10:
        # Phase 11: commitments were stored without their ledger owner (see
        # commitment_ledger.ledger_owner). Give each one the owner of the
        # meeting it came from; nothing else about a commitment changes.
        conn.execute(_MIGRATION_V10_BACKFILL)
        conn.execute("PRAGMA user_version = 10")


def get_schema_version(db_path: Optional[Path | str] = None) -> int:
    with get_conn(db_path) as conn:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])


def insert_meeting(
    meeting_id: str,
    filename: Optional[str] = None,
    duration: Optional[float] = None,
    status: str = "created",
    created_at: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> None:
    """Insert or update a meeting record."""
    timestamp = created_at or datetime.utcnow().isoformat()
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO meetings (meeting_id, filename, duration, created_at, status)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(meeting_id) DO UPDATE SET
                status = excluded.status
            """,
            (meeting_id, filename, duration, timestamp, status),
        )


def ensure_meeting(
    meeting_id: str,
    filename: Optional[str] = None,
    duration: Optional[float] = None,
    status: str = "created",
    created_at: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> None:
    """Create a meeting parent row if it is missing, without changing an existing row."""
    timestamp = created_at or datetime.utcnow().isoformat()
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO meetings (meeting_id, filename, duration, created_at, status)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(meeting_id) DO NOTHING
            """,
            (meeting_id, filename, duration, timestamp, status),
        )


def get_meeting(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve meeting record by meeting_id."""
    with get_conn(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT meeting_id, filename, duration, created_at, status
            FROM meetings
            WHERE meeting_id = ?
            """,
            (meeting_id,),
        )
        row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def bulk_insert_utterances(
    meeting_id: str,
    utterances: List[Utterance],
    db_path: Optional[Path | str] = None,
) -> None:
    """Bulk insert validated Utterance models for a meeting."""
    if not utterances:
        return

    # Cached ASR/diarization runs can reach this path without the API's
    # meeting-row setup. Ensure the FK parent exists before any insert.
    init_db(db_path)
    ensure_meeting(meeting_id, db_path=db_path)

    rows = []
    for u in utterances:
        rows.append(
            (
                meeting_id,
                u.utt_id,
                u.start,
                u.end,
                u.speaker_id,
                u.speaker_name,
                u.text,
                json.dumps(u.lang_tags),
                u.script,
                u.asr_conf,
                u.spk_conf,
                u.no_speech_prob,
                u.conf_source,
                1 if u.overlap else 0,
                u.snr,
                u.topic_id,
            )
        )

    with get_conn(db_path) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO utterances (
                meeting_id, utt_id, start, end, speaker_id, speaker_name,
                text, lang_tags, script, asr_conf, spk_conf, no_speech_prob,
                conf_source, overlap, snr, topic_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def insert_claim_with_evidence(
    claim: ClaimBase | Decision | ActionItem,
    evidence_utt_ids: Optional[List[str]] = None,
    db_path: Optional[Path | str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """
    Insert a claim and its evidence links within a transaction.
    If ANY evidence utt_id is missing from utterances, SQLite foreign key
    constraint raises IntegrityError and the entire claim insert rolls back.
    """
    target_evidence = evidence_utt_ids if evidence_utt_ids is not None else claim.evidence
    if not target_evidence:
        raise ValueError("Evidence list cannot be empty.")

    claim_type = getattr(claim, "claim_type", "claim")
    payload = claim.model_dump_json()
    created_at = datetime.utcnow().isoformat()

    def _execute(c: sqlite3.Connection):
        # 1. Insert claim
        c.execute(
            """
            INSERT INTO claims (claim_id, meeting_id, claim_type, text, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (claim.claim_id, claim.meeting_id, claim_type, claim.text, payload, created_at),
        )

        # 2. Insert evidence links - will trigger FOREIGN KEY check on utterances(meeting_id, utt_id)
        evidence_rows = [
            (claim.claim_id, claim.meeting_id, utt_id) for utt_id in target_evidence
        ]
        c.executemany(
            """
            INSERT INTO evidence_links (claim_id, meeting_id, utt_id)
            VALUES (?, ?, ?)
            """,
            evidence_rows,
        )

    if conn is not None:
        _execute(conn)
    else:
        with get_conn(db_path) as c:
            _execute(c)


def get_utterances(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> List[Utterance]:
    """Retrieve all utterances for a meeting ordered by start time."""
    with get_conn(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT meeting_id, utt_id, start, end, speaker_id, speaker_name,
                   text, lang_tags, script, asr_conf, spk_conf, no_speech_prob,
                   conf_source, overlap, snr, topic_id
            FROM utterances
            WHERE meeting_id = ?
            ORDER BY start ASC, end ASC, speaker_id ASC
            """,
            (meeting_id,),
        )
        rows = cursor.fetchall()
        corrected = _active_correction_texts(conn, meeting_id)

    result = []
    for r in rows:
        lang_tags = json.loads(r["lang_tags"]) if r["lang_tags"] else []
        text = r["text"]
        latest = corrected.get(r["utt_id"])
        if latest and latest["asr_text"] == text:
            text = latest["corrected_text"]
        conf_src = r["conf_source"] if "conf_source" in r.keys() and r["conf_source"] is not None else "missing"
        result.append(
            Utterance(
                meeting_id=r["meeting_id"],
                utt_id=r["utt_id"],
                start=r["start"],
                end=r["end"],
                speaker_id=r["speaker_id"],
                speaker_name=r["speaker_name"],
                text=text,
                lang_tags=lang_tags,
                script=r["script"],
                asr_conf=r["asr_conf"],
                spk_conf=r["spk_conf"],
                no_speech_prob=r["no_speech_prob"],
                conf_source=conf_src,
                overlap=bool(r["overlap"]),
                snr=r["snr"],
                topic_id=r["topic_id"],
            )
        )
    return result


# ---------------------------------------------------------------------------
# Transcript corrections (Phase 8)
# ---------------------------------------------------------------------------

def _active_correction_texts(conn: sqlite3.Connection, meeting_id: str) -> Dict[str, Dict[str, Any]]:
    """Latest correction per utterance (applied only while asr_text still matches)."""
    try:
        rows = conn.execute(
            """
            SELECT * FROM transcript_corrections
            WHERE meeting_id = ?
            ORDER BY correction_id ASC
            """,
            (meeting_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        # Database not migrated to v7 yet: there are no corrections.
        return {}
    return {r["utt_id"]: dict(r) for r in rows}


def get_transcript_corrections(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Full correction history of a meeting, oldest first."""
    init_db(db_path)
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM transcript_corrections WHERE meeting_id = ? ORDER BY correction_id ASC",
            (meeting_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_active_transcript_corrections(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    utt_id -> latest correction for utterances whose shown text differs from
    the ASR text. A correction back to the ASR wording is not active.
    """
    init_db(db_path)
    with get_conn(db_path) as conn:
        latest = _active_correction_texts(conn, meeting_id)
        asr = {
            r["utt_id"]: r["text"]
            for r in conn.execute(
                "SELECT utt_id, text FROM utterances WHERE meeting_id = ?", (meeting_id,)
            )
        }
    return {
        utt_id: row
        for utt_id, row in latest.items()
        if asr.get(utt_id) == row["asr_text"] and row["corrected_text"] != row["asr_text"]
    }


def latest_transcript_correction_at(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> Optional[str]:
    """
    Time of the most recent correction made against the current ASR text, or
    None. A correction back to the ASR wording counts too: it also changed the
    text the verifier last saw.
    """
    init_db(db_path)
    with get_conn(db_path) as conn:
        try:
            row = conn.execute(
                """
                SELECT MAX(c.created_at)
                FROM transcript_corrections c
                JOIN utterances u ON u.meeting_id = c.meeting_id AND u.utt_id = c.utt_id
                WHERE c.meeting_id = ? AND u.text = c.asr_text
                """,
                (meeting_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    return row[0] if row else None


def record_transcript_correction(
    meeting_id: str,
    utt_id: str,
    corrected_text: str,
    reviewer: Optional[str] = None,
    reason: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """
    Record a reviewer's correction of one utterance and an audit event.
    Raises LookupError for an unknown utterance and ValueError for empty or
    unchanged text.
    """
    init_db(db_path)
    new_text = (corrected_text or "").strip()
    if not new_text:
        raise ValueError("Corrected text cannot be empty.")
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT text FROM utterances WHERE meeting_id = ? AND utt_id = ?",
            (meeting_id, utt_id),
        ).fetchone()
        if row is None:
            raise LookupError("Utterance not found.")
        asr_text = row["text"]
        latest = _active_correction_texts(conn, meeting_id).get(utt_id)
        previous = latest["corrected_text"] if latest and latest["asr_text"] == asr_text else asr_text
        if new_text == previous:
            raise ValueError("The corrected text is the same as the current text.")
        created_at = _now_iso()
        cursor = conn.execute(
            """
            INSERT INTO transcript_corrections (
                meeting_id, utt_id, asr_text, previous_text, corrected_text,
                reviewer, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (meeting_id, utt_id, asr_text, previous, new_text, reviewer, reason, created_at),
        )
        correction_id = cursor.lastrowid
    record_audit_event(
        meeting_id,
        "transcript_corrected",
        item_id=utt_id,
        field_name="text",
        old_value=previous,
        new_value=new_text,
        reason=reason,
        reviewer_id=reviewer,
        timestamp=created_at,
        db_path=db_path,
    )
    return {
        "correction_id": correction_id,
        "meeting_id": meeting_id,
        "utt_id": utt_id,
        "asr_text": asr_text,
        "previous_text": previous,
        "corrected_text": new_text,
        "reviewer": reviewer,
        "reason": reason,
        "created_at": created_at,
    }


def get_claims_with_evidence(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """
    Retrieve all claims for a meeting, each with its list of evidence utt_ids
    joined from evidence_links.
    """
    with get_conn(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT c.claim_id, c.meeting_id, c.claim_type, c.text, c.payload, c.created_at,
                   e.utt_id
            FROM claims c
            LEFT JOIN evidence_links e ON c.claim_id = e.claim_id
            WHERE c.meeting_id = ?
            ORDER BY c.rowid ASC, e.rowid ASC
            """,
            (meeting_id,),
        )
        rows = cursor.fetchall()

    claims_map: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        cid = r["claim_id"]
        if cid not in claims_map:
            claims_map[cid] = {
                "claim_id": cid,
                "meeting_id": r["meeting_id"],
                "claim_type": r["claim_type"],
                "text": r["text"],
                "payload": json.loads(r["payload"]) if r["payload"] else {},
                "created_at": r["created_at"],
                "evidence": [],
            }
        if r["utt_id"] is not None:
            claims_map[cid]["evidence"].append(r["utt_id"])

    return list(claims_map.values())


def get_claims(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve all claims and linked evidence utt_ids for a meeting."""
    return get_claims_with_evidence(meeting_id, db_path=db_path)


def insert_dropped_claims(
    meeting_id: str,
    dropped: list,
    db_path: Optional[Path | str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    """Insert dropped claims into the dropped_claims table."""
    if not dropped:
        return 0

    rows = []
    for d in dropped:
        if hasattr(d, "meeting_id"):
            mid = d.meeting_id
            wid = d.window_id
            ctype = d.claim_type
            txt = d.text
            ev = d.cited_evidence
            evidence = json.dumps(ev) if isinstance(ev, list) else str(ev)
            reason = d.reason.value if hasattr(d.reason, "value") else str(d.reason)
            detail = d.detail
        elif isinstance(d, dict):
            mid = d.get("meeting_id") or meeting_id
            wid = d.get("window_id")
            ctype = d.get("claim_type")
            txt = d.get("text")
            ev = d.get("cited_evidence") or d.get("evidence") or []
            evidence = json.dumps(ev) if isinstance(ev, list) else str(ev)
            r = d.get("reason")
            reason = r.value if hasattr(r, "value") else str(r)
            detail = d.get("detail", "")
        else:
            continue

        created_at = datetime.utcnow().isoformat()
        rows.append((mid, wid, ctype, txt, evidence, reason, detail, created_at))

    if not rows:
        return 0

    def _execute(c: sqlite3.Connection):
        c.executemany(
            """
            INSERT INTO dropped_claims (meeting_id, window_id, claim_type, text, cited_evidence, reason, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    if conn is not None:
        _execute(conn)
    else:
        with get_conn(db_path) as c:
            _execute(c)

    return len(rows)


def get_dropped_claims(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve all dropped claims for a meeting."""
    with get_conn(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT drop_id, meeting_id, window_id, claim_type, text,
                   cited_evidence, reason, detail, created_at
            FROM dropped_claims
            WHERE meeting_id = ?
            ORDER BY drop_id ASC
            """,
            (meeting_id,),
        )
        rows = cursor.fetchall()

    result = []
    for r in rows:
        ev = json.loads(r["cited_evidence"]) if r["cited_evidence"] else []
        result.append(
            {
                "drop_id": r["drop_id"],
                "meeting_id": r["meeting_id"],
                "window_id": r["window_id"],
                "claim_type": r["claim_type"],
                "text": r["text"],
                "cited_evidence": ev,
                "reason": r["reason"],
                "detail": r["detail"],
                "created_at": r["created_at"],
            }
        )
    return result


def delete_claims_for_meeting(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """
    Delete all evidence links, claims, and dropped claims for a meeting in one transaction.
    Deletes evidence_links first to respect RESTRICT foreign key, then claims, then dropped_claims.
    """
    def _execute(c: sqlite3.Connection):
        c.execute("DELETE FROM evidence_links WHERE meeting_id = ?", (meeting_id,))
        c.execute("DELETE FROM claims WHERE meeting_id = ?", (meeting_id,))
        c.execute("DELETE FROM dropped_claims WHERE meeting_id = ?", (meeting_id,))

    if conn is not None:
        _execute(conn)
    else:
        with get_conn(db_path) as c:
            _execute(c)


def delete_utterances_for_meeting(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """Delete a meeting's extraction state and utterances atomically."""
    def _execute(c: sqlite3.Connection):
        delete_claims_for_meeting(meeting_id, conn=c)
        c.execute("DELETE FROM utterances WHERE meeting_id = ?", (meeting_id,))

    if conn is not None:
        _execute(conn)
    else:
        with get_conn(db_path) as c:
            _execute(c)


def _row_to_job(row: sqlite3.Row) -> JobStatus:
    keys = row.keys() if hasattr(row, "keys") else []
    hb = row["heartbeat_at"] if "heartbeat_at" in keys and row["heartbeat_at"] else None
    retries = row["retry_count"] if "retry_count" in keys and row["retry_count"] is not None else 0
    fp = row["file_path"] if "file_path" in keys else None
    token = row["claim_token"] if "claim_token" in keys else None

    return JobStatus(
        meeting_id=row["meeting_id"],
        stage=row["stage"],
        progress=row["progress"],
        state=row["state"],
        error=row["error"],
        updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else datetime.now(timezone.utc),
        heartbeat_at=datetime.fromisoformat(hb) if hb else None,
        retry_count=retries,
        file_path=fp,
        claim_token=token,
    )


def upsert_job_status(
    job: JobStatus,
    db_path: Optional[Path | str] = None,
) -> None:
    """Upsert a JobStatus record, preserving prior metadata when omitted."""
    with get_conn(db_path) as conn:
        cursor = conn.execute(
            "SELECT retry_count, file_path, claim_token, heartbeat_at FROM jobs WHERE meeting_id = ?",
            (job.meeting_id,),
        )
        existing = cursor.fetchone()
        file_path = job.file_path or (existing["file_path"] if existing else None)
        retry_count = max(job.retry_count, (existing["retry_count"] if existing and existing["retry_count"] is not None else 0))
        claim_token = job.claim_token or (existing["claim_token"] if existing else None)
        heartbeat_at = (job.heartbeat_at.isoformat() if job.heartbeat_at else None) or (existing["heartbeat_at"] if existing else None)

        conn.execute(
            """
            INSERT OR REPLACE INTO jobs (
                meeting_id, stage, progress, state, error, updated_at,
                heartbeat_at, retry_count, file_path, claim_token
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.meeting_id,
                job.stage,
                job.progress,
                job.state,
                job.error,
                job.updated_at.isoformat(),
                heartbeat_at,
                retry_count,
                file_path,
                claim_token,
            ),
        )


def get_job_status(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> Optional[JobStatus]:
    """Retrieve JobStatus for a meeting if available."""
    with get_conn(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT meeting_id, stage, progress, state, error, updated_at,
                   heartbeat_at, retry_count, file_path, claim_token
            FROM jobs
            WHERE meeting_id = ?
            """,
            (meeting_id,),
        )
        row = cursor.fetchone()

    if not row:
        return None

    return _row_to_job(row)


def claim_next_job(
    worker_id: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Optional[JobStatus]:
    """
    Atomically claim the next queued job for execution.
    Two workers calling this in sequence (or concurrently) will never claim the same job.
    Uses an atomic UPDATE with rowcount verification and a unique claim token.
    """
    token = f"{worker_id or 'worker'}_{uuid4().hex}"
    now_iso = datetime.now(timezone.utc).isoformat()

    with get_conn(db_path) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        cursor = conn.execute(
            """
            UPDATE jobs
            SET state = 'running',
                stage = 'uploaded',
                claim_token = ?,
                heartbeat_at = ?,
                updated_at = ?
            WHERE meeting_id = (
                SELECT meeting_id
                FROM jobs
                WHERE state = 'queued'
                ORDER BY updated_at ASC
                LIMIT 1
            ) AND state = 'queued'
            """,
            (token, now_iso, now_iso),
        )
        if cursor.rowcount == 0:
            return None

        row = conn.execute(
            """
            SELECT meeting_id, stage, progress, state, error, updated_at,
                   heartbeat_at, retry_count, file_path, claim_token
            FROM jobs
            WHERE claim_token = ?
            """,
            (token,),
        ).fetchone()

        if not row:
            return None
        return _row_to_job(row)


def update_job_heartbeat(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> None:
    """Update heartbeat timestamp for a currently running job."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE jobs
            SET heartbeat_at = ?, updated_at = ?
            WHERE meeting_id = ?
            """,
            (now_iso, now_iso, meeting_id),
        )


def recover_stale_jobs(
    stale_timeout_seconds: float = 300.0,
    max_retries: int = 3,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """
    On worker startup, scan for jobs stuck in 'running' state whose heartbeat is
    older than stale_timeout_seconds (or missing).
    - If retry_count >= max_retries: mark state='failed' with error explanation.
    - Otherwise: increment retry_count, reset state='queued', and clear claim_token/heartbeat.
    Returns list of recovered job summaries.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    recovered = []

    with get_conn(db_path) as conn:
        conn.execute("PRAGMA busy_timeout = 5000")
        rows = conn.execute(
            """
            SELECT meeting_id, stage, progress, state, error, updated_at,
                   heartbeat_at, retry_count, file_path, claim_token
            FROM jobs
            WHERE state = 'running'
            """
        ).fetchall()

        for row in rows:
            mid = row["meeting_id"]
            keys = row.keys() if hasattr(row, "keys") else []
            hb_str = row["heartbeat_at"] if "heartbeat_at" in keys and row["heartbeat_at"] else None
            up_str = row["updated_at"]

            last_active = None
            if hb_str:
                try:
                    last_active = datetime.fromisoformat(hb_str)
                except ValueError:
                    pass
            if last_active is None and up_str:
                try:
                    last_active = datetime.fromisoformat(up_str)
                except ValueError:
                    pass

            if last_active is None:
                is_stale = True
            else:
                if last_active.tzinfo is None:
                    last_active = last_active.replace(tzinfo=timezone.utc)
                is_stale = (now - last_active).total_seconds() > stale_timeout_seconds

            if not is_stale:
                continue

            current_retries = (row["retry_count"] if "retry_count" in keys and row["retry_count"] is not None else 0)
            next_retries = current_retries + 1

            if next_retries > max_retries:
                err_msg = f"Job failed: exceeded maximum retries ({max_retries}) after worker timeout/crash."
                conn.execute(
                    """
                    UPDATE jobs
                    SET state = 'failed',
                        error = ?,
                        retry_count = ?,
                        claim_token = NULL,
                        updated_at = ?
                    WHERE meeting_id = ?
                    """,
                    (err_msg, next_retries, now_iso, mid),
                )
                recovered.append({
                    "meeting_id": mid,
                    "action": "failed",
                    "retries": next_retries,
                    "error": err_msg,
                })
            else:
                conn.execute(
                    """
                    UPDATE jobs
                    SET state = 'queued',
                        stage = 'uploaded',
                        progress = 0.0,
                        heartbeat_at = NULL,
                        claim_token = NULL,
                        retry_count = ?,
                        error = ?,
                        updated_at = ?
                    WHERE meeting_id = ?
                    """,
                    (next_retries, f"Recovered from stale running state (retry {next_retries}/{max_retries})", now_iso, mid),
                )
                recovered.append({
                    "meeting_id": mid,
                    "action": "requeued",
                    "retries": next_retries,
                })

    return recovered


def release_job(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> None:
    """Release a running job back to 'queued' state (e.g. on SIGINT/SIGTERM)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE jobs
            SET state = 'queued',
                heartbeat_at = NULL,
                claim_token = NULL,
                updated_at = ?
            WHERE meeting_id = ? AND state = 'running'
            """,
            (now_iso, meeting_id),
        )



# ---------------------------------------------------------------------------
# Extraction runs (one row per meeting: the latest extraction attempt)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def upsert_extraction_run(
    meeting_id: str,
    *,
    status: str,
    mode_requested: Optional[str] = None,
    mode_used: Optional[str] = None,
    reason: Optional[str] = None,
    fallback_reason: Optional[str] = None,
    accepted: Optional[int] = None,
    dropped: Optional[int] = None,
    extractor_version: Optional[str] = None,
    started_at: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> None:
    init_db(db_path)
    ensure_meeting(meeting_id, db_path=db_path)
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO extraction_runs (
                meeting_id, mode_requested, mode_used, status, reason, fallback_reason,
                accepted, dropped, extractor_version, started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                meeting_id, mode_requested, mode_used, status, reason, fallback_reason,
                accepted, dropped, extractor_version, started_at or _now_iso(), _now_iso(),
            ),
        )


def get_extraction_run(
    meeting_id: str, db_path: Optional[Path | str] = None
) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM extraction_runs WHERE meeting_id = ?", (meeting_id,)
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Verification results (one row per claim and method; re-runs replace rows)
# ---------------------------------------------------------------------------

_JSON_LIST_COLUMNS = ("cited_utt_ids", "best_utt_ids", "contradiction_utt_ids", "reasons")


def replace_verification_results(
    meeting_id: str,
    rows: List[Dict[str, Any]],
    db_path: Optional[Path | str] = None,
) -> None:
    """Atomically replace all verification rows for a meeting (idempotent re-runs)."""
    created_at = _now_iso()
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM verification_results WHERE meeting_id = ?", (meeting_id,))
        conn.executemany(
            """
            INSERT INTO verification_results (
                claim_id, meeting_id, method, label, cited_utt_ids, cited_entailment,
                best_utt_ids, best_support, contradiction, contradiction_utt_ids,
                reasons, details, model_version, config_version, latency_ms, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r["claim_id"], meeting_id, r["method"], r["label"],
                    json.dumps(r.get("cited_utt_ids") or []),
                    r.get("cited_entailment"),
                    json.dumps(r.get("best_utt_ids") or []),
                    r.get("best_support"),
                    r.get("contradiction"),
                    json.dumps(r.get("contradiction_utt_ids") or []),
                    json.dumps(r.get("reasons") or [], ensure_ascii=False),
                    json.dumps(r.get("details") or {}, ensure_ascii=False),
                    r.get("model_version"),
                    r.get("config_version"),
                    r.get("latency_ms"),
                    created_at,
                )
                for r in rows
            ],
        )


def get_verification_results(
    meeting_id: str, db_path: Optional[Path | str] = None
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Return {claim_id: {method: row}} for a meeting."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM verification_results WHERE meeting_id = ? ORDER BY claim_id, method",
            (meeting_id,),
        ).fetchall()
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        for column in _JSON_LIST_COLUMNS:
            item[column] = json.loads(item[column]) if item[column] else []
        item["details"] = json.loads(item["details"]) if item["details"] else {}
        out.setdefault(item["claim_id"], {})[item["method"]] = item
    return out


def upsert_verification_run(
    meeting_id: str,
    *,
    status: str,
    reason: Optional[str] = None,
    claims_total: Optional[int] = None,
    seconds_total: Optional[float] = None,
    semantic_seconds: Optional[float] = None,
    model_version: Optional[str] = None,
    config_version: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> None:
    init_db(db_path)
    ensure_meeting(meeting_id, db_path=db_path)
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO verification_runs (
                meeting_id, status, reason, claims_total, seconds_total, semantic_seconds,
                model_version, config_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                meeting_id, status, reason, claims_total, seconds_total, semantic_seconds,
                model_version, config_version, _now_iso(),
            ),
        )


def get_verification_run(
    meeting_id: str, db_path: Optional[Path | str] = None
) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM verification_runs WHERE meeting_id = ?", (meeting_id,)
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Evidence corrections (reviewer accept/reject of a suggested evidence span)
# ---------------------------------------------------------------------------

def get_evidence_corrections(
    meeting_id: str, db_path: Optional[Path | str] = None
) -> Dict[str, Dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM evidence_corrections WHERE meeting_id = ?", (meeting_id,)
        ).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        item["original_utt_ids"] = json.loads(item["original_utt_ids"])
        item["proposed_utt_ids"] = json.loads(item["proposed_utt_ids"])
        out[item["claim_id"]] = item
    return out


def record_evidence_correction(
    claim_id: str,
    meeting_id: str,
    action: str,
    original_utt_ids: List[str],
    proposed_utt_ids: List[str],
    db_path: Optional[Path | str] = None,
) -> None:
    """
    Persist a reviewer decision on a suggested evidence span.

    "accepted" replaces the claim's evidence links with the proposed utterances
    in the same transaction. The utterance foreign key still applies, so an
    unknown utterance id rolls the whole change back.
    """
    if action not in ("accepted", "rejected"):
        raise ValueError(f"Invalid correction action: {action}")
    if not proposed_utt_ids:
        raise ValueError("Proposed evidence cannot be empty.")
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT payload FROM claims WHERE claim_id = ? AND meeting_id = ?",
            (claim_id, meeting_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown claim {claim_id} for meeting {meeting_id}")
        if action == "accepted":
            conn.execute("DELETE FROM evidence_links WHERE claim_id = ?", (claim_id,))
            conn.executemany(
                "INSERT INTO evidence_links (claim_id, meeting_id, utt_id) VALUES (?, ?, ?)",
                [(claim_id, meeting_id, utt_id) for utt_id in proposed_utt_ids],
            )
            payload = json.loads(row["payload"]) if row["payload"] else {}
            payload["evidence"] = list(proposed_utt_ids)
            conn.execute(
                "UPDATE claims SET payload = ? WHERE claim_id = ?",
                (json.dumps(payload, ensure_ascii=False), claim_id),
            )
        conn.execute(
            """
            INSERT OR REPLACE INTO evidence_corrections (
                claim_id, meeting_id, action, original_utt_ids, proposed_utt_ids, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                claim_id, meeting_id, action,
                json.dumps(list(original_utt_ids)), json.dumps(list(proposed_utt_ids)),
                _now_iso(),
            ),
        )


# ---------------------------------------------------------------------------
# Phase 3: Review Drafts, Review Items, Audit Logging, and Approved Snapshots
# ---------------------------------------------------------------------------

def init_review_draft(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """
    Initialize or retrieve the review draft for a meeting.
    Populates review_items from claims if review_items is currently empty.
    Preserves original AI claims unchanged.
    """
    init_db(db_path)
    ensure_meeting(meeting_id, db_path=db_path)
    now = _now_iso()

    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO review_drafts (meeting_id, draft_status, meeting_title, summary, created_at, updated_at)
            VALUES (?, 'draft', 'Meeting Minutes', NULL, ?, ?)
            ON CONFLICT(meeting_id) DO NOTHING
            """,
            (meeting_id, now, now),
        )

        item_count = conn.execute(
            "SELECT COUNT(*) FROM review_items WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()[0]

        if item_count == 0:
            claims = get_claims_with_evidence(meeting_id, db_path=db_path)
            results = get_verification_results(meeting_id, db_path=db_path)

            rows = []
            for idx, c in enumerate(claims):
                cid = c["claim_id"]
                payload = c.get("payload") or {}
                evidence_list = c.get("evidence") or []
                method_res = results.get(cid, {})
                fusion_label = (method_res.get("fusion") or {}).get("label", "unverified")

                review_status = "rejected" if fusion_label == "rejected" else "original"
                is_excluded = 1 if fusion_label == "rejected" else 0

                rows.append((
                    cid,
                    meeting_id,
                    cid,
                    c["claim_type"],
                    c["text"],
                    payload.get("state"),
                    payload.get("owner"),
                    payload.get("owner_type"),
                    payload.get("deadline_text"),
                    payload.get("deadline_resolved"),
                    json.dumps(evidence_list),
                    review_status,
                    is_excluded,
                    idx,
                    now,
                ))

            if rows:
                conn.executemany(
                    """
                    INSERT INTO review_items (
                        item_id, meeting_id, original_claim_id, item_type, text,
                        state, owner, owner_type, deadline_text, deadline_resolved,
                        evidence_ids, review_status, is_excluded, order_index, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )

    return get_review_draft(meeting_id, db_path=db_path)  # type: ignore[return-value]


def get_review_draft(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve full review draft with its ordered review items."""
    init_db(db_path)
    with get_conn(db_path) as conn:
        draft_row = conn.execute(
            "SELECT * FROM review_drafts WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()

        if not draft_row:
            return None

        draft = dict(draft_row)

        item_rows = conn.execute(
            """
            SELECT * FROM review_items
            WHERE meeting_id = ?
            ORDER BY order_index ASC, rowid ASC
            """,
            (meeting_id,),
        ).fetchall()

        items = []
        for r in item_rows:
            item = dict(r)
            item["evidence_ids"] = json.loads(item["evidence_ids"]) if item["evidence_ids"] else []
            item["is_excluded"] = bool(item["is_excluded"])
            items.append(item)

        draft["items"] = items
        return draft


def update_review_item(
    meeting_id: str,
    item_id: str,
    updates: Dict[str, Any],
    reviewer: Optional[str] = None,
    reason: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """
    Update a review item in the draft and record an audit entry.
    Rejects modifications if the draft is currently approved.
    Marks item review_status as 'edited' if text, owner, deadline, or evidence changed.
    """
    now = _now_iso()
    with get_conn(db_path) as conn:
        draft = conn.execute(
            "SELECT draft_status FROM review_drafts WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()

        if not draft:
            raise ValueError(f"No review draft found for meeting {meeting_id}.")
        if draft["draft_status"] == "approved":
            raise ValueError("Cannot edit an approved MoM. Please reopen review first.")

        cur = conn.execute(
            "SELECT * FROM review_items WHERE meeting_id = ? AND item_id = ?",
            (meeting_id, item_id),
        ).fetchone()

        if not cur:
            raise ValueError(f"Review item {item_id} not found for meeting {meeting_id}.")

        current_item = dict(cur)
        current_evidence = json.loads(current_item["evidence_ids"]) if current_item["evidence_ids"] else []

        # Audit meaningful changes
        fields_to_track = [
            ("text", updates.get("text")),
            ("owner", updates.get("owner")),
            ("deadline_text", updates.get("deadline_text")),
            ("deadline_resolved", updates.get("deadline_resolved")),
            ("state", updates.get("state")),
            ("is_excluded", int(updates["is_excluded"]) if "is_excluded" in updates else None),
            ("evidence_ids", json.dumps(updates["evidence_ids"]) if "evidence_ids" in updates else None),
        ]

        text_or_meta_changed = False
        for field, new_val in fields_to_track:
            if new_val is not None:
                old_val = current_item.get(field)
                if field == "is_excluded":
                    old_val = int(old_val) if old_val is not None else 0
                if str(old_val) != str(new_val):
                    if field in ("text", "owner", "deadline_text", "evidence_ids"):
                        text_or_meta_changed = True
                    conn.execute(
                        """
                        INSERT INTO review_audit_log (
                            meeting_id, item_id, action, field_name,
                            old_value, new_value, reason, reviewer_id, timestamp
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            meeting_id,
                            item_id,
                            f"edit_{field}",
                            field,
                            str(old_val),
                            str(new_val),
                            reason,
                            reviewer,
                            now,
                        ),
                    )

        new_status = current_item["review_status"]
        if text_or_meta_changed and new_status == "original":
            new_status = "edited"

        new_text = updates.get("text", current_item["text"])
        new_state = updates.get("state", current_item["state"])
        new_owner = updates.get("owner", current_item["owner"])
        new_owner_type = updates.get("owner_type", current_item["owner_type"])
        new_deadline_text = updates.get("deadline_text", current_item["deadline_text"])
        new_deadline_resolved = updates.get("deadline_resolved", current_item["deadline_resolved"])
        new_evidence_ids = (
            json.dumps(updates["evidence_ids"]) if "evidence_ids" in updates else current_item["evidence_ids"]
        )
        new_is_excluded = (
            int(updates["is_excluded"]) if "is_excluded" in updates else current_item["is_excluded"]
        )

        conn.execute(
            """
            UPDATE review_items SET
                text = ?, state = ?, owner = ?, owner_type = ?,
                deadline_text = ?, deadline_resolved = ?, evidence_ids = ?,
                review_status = ?, is_excluded = ?, updated_at = ?
            WHERE meeting_id = ? AND item_id = ?
            """,
            (
                new_text, new_state, new_owner, new_owner_type,
                new_deadline_text, new_deadline_resolved, new_evidence_ids,
                new_status, new_is_excluded, now,
                meeting_id, item_id,
            ),
        )

        conn.execute(
            "UPDATE review_drafts SET updated_at = ? WHERE meeting_id = ?",
            (now, meeting_id),
        )

    return get_review_item(meeting_id, item_id, db_path=db_path)  # type: ignore[return-value]


def get_review_item(
    meeting_id: str,
    item_id: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve a single review item."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM review_items WHERE meeting_id = ? AND item_id = ?",
            (meeting_id, item_id),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["evidence_ids"] = json.loads(item["evidence_ids"]) if item["evidence_ids"] else []
        item["is_excluded"] = bool(item["is_excluded"])
        return item


def add_review_item(
    meeting_id: str,
    item_data: Dict[str, Any],
    reviewer: Optional[str] = None,
    reason: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Add a new decision or action item to the review draft."""
    init_db(db_path)
    now = _now_iso()

    with get_conn(db_path) as conn:
        draft = conn.execute(
            "SELECT draft_status FROM review_drafts WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()

        if not draft:
            raise ValueError(f"No review draft found for meeting {meeting_id}.")
        if draft["draft_status"] == "approved":
            raise ValueError("Cannot add items to an approved MoM. Please reopen review first.")

        item_id = item_data.get("item_id") or f"manual_{uuid4().hex[:10]}"
        item_type = item_data.get("item_type", "decision")
        if item_type not in ("decision", "action"):
            raise ValueError(f"Invalid item_type: {item_type}")

        text = item_data.get("text", "").strip()
        if not text:
            raise ValueError("Item text cannot be empty.")

        evidence_ids = item_data.get("evidence_ids") or []
        order_row = conn.execute(
            "SELECT COALESCE(MAX(order_index), 0) + 1 FROM review_items WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()
        order_index = order_row[0]

        conn.execute(
            """
            INSERT INTO review_items (
                item_id, meeting_id, original_claim_id, item_type, text,
                state, owner, owner_type, deadline_text, deadline_resolved,
                evidence_ids, review_status, is_excluded, order_index, updated_at
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 'added', ?, ?, ?)
            """,
            (
                item_id, meeting_id, item_type, text,
                item_data.get("state", "accepted"),
                item_data.get("owner"),
                item_data.get("owner_type", "unassigned" if item_type == "action" else None),
                item_data.get("deadline_text"),
                item_data.get("deadline_resolved"),
                json.dumps(evidence_ids),
                1 if item_data.get("is_excluded") else 0,
                order_index,
                now,
            ),
        )

        conn.execute(
            """
            INSERT INTO review_audit_log (
                meeting_id, item_id, action, field_name,
                old_value, new_value, reason, reviewer_id, timestamp
            ) VALUES (?, ?, 'add_item', 'text', NULL, ?, ?, ?, ?)
            """,
            (meeting_id, item_id, text, reason, reviewer, now),
        )

        conn.execute(
            "UPDATE review_drafts SET updated_at = ? WHERE meeting_id = ?",
            (now, meeting_id),
        )

    return get_review_item(meeting_id, item_id, db_path=db_path)  # type: ignore[return-value]


def record_audit_event(
    meeting_id: str,
    action: str,
    item_id: Optional[str] = None,
    field_name: Optional[str] = None,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
    reason: Optional[str] = None,
    reviewer_id: Optional[str] = None,
    timestamp: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> None:
    """Record an audit event into review_audit_log."""
    init_db(db_path)
    ts = timestamp or _now_iso()
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO review_audit_log (
                meeting_id, item_id, action, field_name,
                old_value, new_value, reason, reviewer_id, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (meeting_id, item_id, action, field_name, old_value, new_value, reason, reviewer_id, ts),
        )


def get_review_audit_log(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve full audit history for a meeting ordered chronologically."""
    init_db(db_path)
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM review_audit_log WHERE meeting_id = ? ORDER BY audit_id ASC",
            (meeting_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def approve_review_draft(
    meeting_id: str,
    reviewer: str,
    notes: Optional[str] = None,
    mom_payload: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """
    Explicitly approve and lock the current review draft.
    Persists an immutable approved snapshot, updates draft status, and writes audit event.
    Idempotent: if already approved and not reopened, returns existing latest snapshot.
    """
    init_db(db_path)
    if not reviewer or not reviewer.strip():
        raise ValueError("A reviewer identity must be provided to approve minutes.")

    now = _now_iso()
    with get_conn(db_path) as conn:
        draft = conn.execute(
            "SELECT draft_status FROM review_drafts WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()

        if draft and draft["draft_status"] == "approved":
            latest = conn.execute(
                "SELECT * FROM approved_snapshots WHERE meeting_id = ? ORDER BY version DESC LIMIT 1",
                (meeting_id,),
            ).fetchone()
            if latest:
                res = dict(latest)
                res["mom_payload"] = json.loads(res["mom_payload"])
                return res

        v_row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM approved_snapshots WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()
        version = int(v_row[0])

        snapshot_id = f"{meeting_id}_v{version}_{uuid4().hex[:6]}"
        payload_dict = mom_payload or {}
        payload_dict["approved_version"] = version
        payload_dict["approved_by"] = reviewer
        payload_dict["approved_at"] = now
        payload_str = json.dumps(payload_dict, ensure_ascii=False)

        conn.execute(
            """
            INSERT INTO approved_snapshots (
                snapshot_id, meeting_id, version, approved_by, approved_at,
                mom_payload, reopened_at, reopened_by, reopen_reason
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
            """,
            (snapshot_id, meeting_id, version, reviewer, now, payload_str),
        )

        conn.execute(
            "UPDATE review_drafts SET draft_status = 'approved', updated_at = ? WHERE meeting_id = ?",
            (now, meeting_id),
        )

        conn.execute(
            "UPDATE meetings SET status = 'approved' WHERE meeting_id = ?",
            (meeting_id,),
        )

        conn.execute(
            """
            INSERT INTO review_audit_log (
                meeting_id, item_id, action, field_name,
                old_value, new_value, reason, reviewer_id, timestamp
            ) VALUES (?, NULL, 'approve_mom', 'status', 'draft', ?, ?, ?, ?)
            """,
            (meeting_id, f"v{version}", notes, reviewer, now),
        )

    return {
        "snapshot_id": snapshot_id,
        "meeting_id": meeting_id,
        "version": version,
        "approved_by": reviewer,
        "approved_at": now,
        "mom_payload": payload_dict,
    }


def reopen_review_draft(
    meeting_id: str,
    reviewer: str,
    reason: str,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """
    Reopen an approved review draft for further human editing.
    Requires an explicit reason. Preserves the prior approved snapshot intact.
    """
    init_db(db_path)
    if not reviewer or not reviewer.strip():
        raise ValueError("A reviewer identity must be provided to reopen review.")
    if not reason or not reason.strip():
        raise ValueError("A reason must be provided to reopen approved minutes.")

    now = _now_iso()
    with get_conn(db_path) as conn:
        draft = conn.execute(
            "SELECT draft_status FROM review_drafts WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()

        if not draft or draft["draft_status"] != "approved":
            raise ValueError("Review draft is not approved; cannot reopen.")

        # Annotate latest snapshot with reopening info without deleting it
        conn.execute(
            """
            UPDATE approved_snapshots SET
                reopened_at = ?, reopened_by = ?, reopen_reason = ?
            WHERE meeting_id = ? AND version = (
                SELECT MAX(version) FROM approved_snapshots WHERE meeting_id = ?
            )
            """,
            (now, reviewer, reason, meeting_id, meeting_id),
        )

        conn.execute(
            "UPDATE review_drafts SET draft_status = 'reopened', updated_at = ? WHERE meeting_id = ?",
            (now, meeting_id),
        )

        conn.execute(
            "UPDATE meetings SET status = 'review_reopened' WHERE meeting_id = ?",
            (meeting_id,),
        )

        conn.execute(
            """
            INSERT INTO review_audit_log (
                meeting_id, item_id, action, field_name,
                old_value, new_value, reason, reviewer_id, timestamp
            ) VALUES (?, NULL, 'reopen_review', 'status', 'approved', 'reopened', ?, ?, ?)
            """,
            (meeting_id, reason, reviewer, now),
        )

    return get_review_draft(meeting_id, db_path=db_path)  # type: ignore[return-value]


def get_latest_approved_snapshot(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve latest approved snapshot for a meeting."""
    init_db(db_path)
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM approved_snapshots WHERE meeting_id = ? ORDER BY version DESC LIMIT 1",
            (meeting_id,),
        ).fetchone()
        if not row:
            return None
        res = dict(row)
        res["mom_payload"] = json.loads(res["mom_payload"]) if res["mom_payload"] else {}
        return res


def get_all_approved_snapshots(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve version history of all approved snapshots."""
    init_db(db_path)
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT snapshot_id, meeting_id, version, approved_by, approved_at,
                   reopened_at, reopened_by, reopen_reason
            FROM approved_snapshots
            WHERE meeting_id = ?
            ORDER BY version ASC
            """,
            (meeting_id,),
        ).fetchall()
        return [dict(r) for r in rows]




# ---------------------------------------------------------------------------
# Phase 4.2: live meetings, server-side consent and recording segments
# ---------------------------------------------------------------------------
# Consent is only ever granted by an explicit, recorded action. A missing
# participant row, a NULL or a 0 all mean "not consented".

ACTIVE_SEGMENT_STATES = ("starting", "recording", "stopping")
_SEGMENT_JSON_COLUMNS = ("participants", "tracks")


def _segment_row(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    for col in _SEGMENT_JSON_COLUMNS:
        raw = data.get(col)
        data[col] = json.loads(raw) if raw else []
    return data


def record_live_event(
    meeting_id: str,
    event_type: str,
    identity: Optional[str] = None,
    segment_id: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> None:
    """Append an audit event (consent, roster or recording change)."""
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO live_events (meeting_id, event_type, identity, segment_id, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                meeting_id,
                event_type,
                identity,
                segment_id,
                json.dumps(detail) if detail else None,
                _now_iso(),
            ),
        )


def get_live_events(
    meeting_id: str,
    limit: int = 200,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT event_id, meeting_id, event_type, identity, segment_id, detail, created_at
            FROM live_events
            WHERE meeting_id = ?
            ORDER BY event_id ASC
            LIMIT ?
            """,
            (meeting_id, limit),
        ).fetchall()
    events = []
    for row in rows:
        item = dict(row)
        item["detail"] = json.loads(item["detail"]) if item["detail"] else None
        events.append(item)
    return events


def create_live_meeting(
    room_name: str,
    title: Optional[str] = None,
    room_sid: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Create a new live meeting (and its parent meetings row) for a room."""
    meeting_id = uuid4().hex
    now = _now_iso()
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO meetings (meeting_id, filename, duration, created_at, status)
            VALUES (?, ?, NULL, ?, 'live')
            ON CONFLICT(meeting_id) DO NOTHING
            """,
            (meeting_id, f"live-{room_name}.wav", now),
        )
        conn.execute(
            """
            INSERT INTO live_meetings (meeting_id, room_name, room_sid, title, status, created_at)
            VALUES (?, ?, ?, ?, 'active', ?)
            """,
            (meeting_id, room_name, room_sid, title, now),
        )
    record_live_event(meeting_id, "meeting_created", detail={"room_name": room_name}, db_path=db_path)
    return get_live_meeting(meeting_id, db_path=db_path)


def get_live_meeting(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM live_meetings WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()
    return dict(row) if row else None


def get_active_live_meeting(
    room_name: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM live_meetings
            WHERE room_name = ? AND status = 'active'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (room_name,),
        ).fetchone()
    return dict(row) if row else None


def get_or_create_live_meeting(
    room_name: str,
    title: Optional[str] = None,
    room_sid: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    existing = get_active_live_meeting(room_name, db_path=db_path)
    if existing:
        return existing
    return create_live_meeting(room_name, title=title, room_sid=room_sid, db_path=db_path)


def claim_live_meeting_host(
    meeting_id: str,
    identity: str,
    db_path: Optional[Path | str] = None,
) -> bool:
    """Bind the host identity once. Returns True if identity is (now) the host."""
    with get_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE live_meetings SET host_identity = ?
            WHERE meeting_id = ? AND host_identity IS NULL
            """,
            (identity, meeting_id),
        )
        row = conn.execute(
            "SELECT host_identity FROM live_meetings WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()
    return bool(row) and row["host_identity"] == identity


def end_live_meeting(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> None:
    now = _now_iso()
    with get_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE live_meetings SET status = 'ended', ended_at = COALESCE(ended_at, ?)
            WHERE meeting_id = ?
            """,
            (now, meeting_id),
        )
        conn.execute(
            "UPDATE live_participants SET connected = 0 WHERE meeting_id = ?",
            (meeting_id,),
        )
    record_live_event(meeting_id, "meeting_ended", db_path=db_path)


def upsert_live_participant(
    meeting_id: str,
    identity: str,
    name: Optional[str] = None,
    is_host: Optional[bool] = None,
    db_path: Optional[Path | str] = None,
) -> None:
    """Ensure a participant row exists. Never touches consent."""
    now = _now_iso()
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO live_participants (meeting_id, identity, name, is_host, consented, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(meeting_id, identity) DO UPDATE SET
                name = COALESCE(excluded.name, live_participants.name),
                is_host = CASE WHEN ? IS NULL THEN live_participants.is_host ELSE excluded.is_host END
            """,
            (meeting_id, identity, name, 1 if is_host else 0, now, now, is_host),
        )


def set_participant_consent(
    meeting_id: str,
    identity: str,
    consented: bool,
    source: str,
    name: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Persist an explicit consent decision and its audit event."""
    upsert_live_participant(meeting_id, identity, name=name, db_path=db_path)
    now = _now_iso()
    with get_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE live_participants SET consented = ?, consent_updated_at = ?
            WHERE meeting_id = ? AND identity = ?
            """,
            (1 if consented else 0, now, meeting_id, identity),
        )
    record_live_event(
        meeting_id,
        "consent_granted" if consented else "consent_revoked",
        identity=identity,
        detail={"source": source},
        db_path=db_path,
    )
    return {"meeting_id": meeting_id, "identity": identity, "consented": consented, "updated_at": now}


def is_participant_consented(
    meeting_id: str,
    identity: str,
    db_path: Optional[Path | str] = None,
) -> bool:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT consented FROM live_participants WHERE meeting_id = ? AND identity = ?",
            (meeting_id, identity),
        ).fetchone()
    return bool(row) and row["consented"] == 1


def get_live_participants(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT identity, name, is_host, consented, consent_updated_at, connected,
                   first_seen_at, last_seen_at
            FROM live_participants
            WHERE meeting_id = ?
            ORDER BY first_seen_at ASC
            """,
            (meeting_id,),
        ).fetchall()
    return [
        {
            **dict(r),
            "is_host": bool(r["is_host"]),
            "consented": r["consented"] == 1,
            "connected": bool(r["connected"]),
        }
        for r in rows
    ]


def sync_live_roster(
    meeting_id: str,
    present: List[Dict[str, Any]],
    db_path: Optional[Path | str] = None,
) -> Dict[str, List[str]]:
    """
    Reconcile the connected flags with the roster LiveKit reports, recording
    participant_joined / participant_left audit events for every change.
    """
    present_ids = {p["identity"]: p for p in present}
    for identity, p in present_ids.items():
        upsert_live_participant(meeting_id, identity, name=p.get("name"), db_path=db_path)

    now = _now_iso()
    joined: List[str] = []
    left: List[str] = []
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT identity, connected FROM live_participants WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchall()
        for row in rows:
            identity = row["identity"]
            is_present = identity in present_ids
            if is_present and not row["connected"]:
                joined.append(identity)
                conn.execute(
                    "UPDATE live_participants SET connected = 1, last_seen_at = ? WHERE meeting_id = ? AND identity = ?",
                    (now, meeting_id, identity),
                )
            elif is_present:
                conn.execute(
                    "UPDATE live_participants SET last_seen_at = ? WHERE meeting_id = ? AND identity = ?",
                    (now, meeting_id, identity),
                )
            elif row["connected"]:
                left.append(identity)
                conn.execute(
                    "UPDATE live_participants SET connected = 0 WHERE meeting_id = ? AND identity = ?",
                    (meeting_id, identity),
                )
    for identity in joined:
        record_live_event(meeting_id, "participant_joined", identity=identity, db_path=db_path)
    for identity in left:
        record_live_event(meeting_id, "participant_left", identity=identity, db_path=db_path)
    return {"joined": joined, "left": left}


def set_participant_connected(
    meeting_id: str,
    identity: str,
    connected: bool,
    name: Optional[str] = None,
    segment_id: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> None:
    """Record a single roster change observed by the recorder."""
    upsert_live_participant(meeting_id, identity, name=name, db_path=db_path)
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE live_participants SET connected = ?, last_seen_at = ? WHERE meeting_id = ? AND identity = ?",
            (1 if connected else 0, _now_iso(), meeting_id, identity),
        )
    record_live_event(
        meeting_id,
        "participant_joined" if connected else "participant_left",
        identity=identity,
        segment_id=segment_id,
        detail={"observed_by": "recorder"},
        db_path=db_path,
    )


class ActiveSegmentExists(Exception):
    """Raised when a meeting already has a non-terminal recording segment."""


def create_recording_segment(
    meeting_id: str,
    started_by: str,
    participants: List[Dict[str, Any]],
    db_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    segment_id = uuid4().hex
    now = _now_iso()
    try:
        with get_conn(db_path) as conn:
            seq_row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM recording_segments WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO recording_segments (
                    segment_id, meeting_id, seq, state, started_by, started_at,
                    participants, tracks, updated_at
                )
                VALUES (?, ?, ?, 'starting', ?, ?, ?, '[]', ?)
                """,
                (
                    segment_id,
                    meeting_id,
                    seq_row["next_seq"],
                    started_by,
                    now,
                    json.dumps(participants),
                    now,
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ActiveSegmentExists(meeting_id) from exc
    record_live_event(
        meeting_id,
        "recording_requested",
        identity=started_by,
        segment_id=segment_id,
        detail={"participants": [p.get("identity") for p in participants]},
        db_path=db_path,
    )
    return get_recording_segment(segment_id, db_path=db_path)


def update_recording_segment(
    segment_id: str,
    db_path: Optional[Path | str] = None,
    **fields: Any,
) -> None:
    allowed = {
        "state", "capture_started_at", "stopped_at", "stop_reason", "participants",
        "tracks", "artifact_path", "duration_seconds", "error",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown recording segment fields: {sorted(unknown)}")
    if not fields:
        return
    values = []
    for key, value in fields.items():
        values.append(json.dumps(value) if key in _SEGMENT_JSON_COLUMNS else value)
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with get_conn(db_path) as conn:
        conn.execute(
            f"UPDATE recording_segments SET {assignments}, updated_at = ? WHERE segment_id = ?",
            (*values, _now_iso(), segment_id),
        )


def get_recording_segment(
    segment_id: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM recording_segments WHERE segment_id = ?",
            (segment_id,),
        ).fetchone()
    return _segment_row(row) if row else None


def get_recording_segments(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM recording_segments WHERE meeting_id = ? ORDER BY seq ASC",
            (meeting_id,),
        ).fetchall()
    return [_segment_row(r) for r in rows]


def get_unfinished_recording_segments(
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Segments left non-terminal by a crashed or killed server process."""
    placeholders = ", ".join("?" for _ in ACTIVE_SEGMENT_STATES)
    with get_conn(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM recording_segments WHERE state IN ({placeholders}) ORDER BY started_at ASC",
            ACTIVE_SEGMENT_STATES,
        ).fetchall()
    return [_segment_row(r) for r in rows]


def enqueue_job_once(
    meeting_id: str,
    file_path: str,
    db_path: Optional[Path | str] = None,
) -> bool:
    """
    Queue a pipeline job only if this meeting has never had one.
    Returns True when this call created the job, False if one already existed,
    so concurrent or repeated requests can never process a recording twice.
    """
    now = _now_iso()
    with get_conn(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO jobs (meeting_id, stage, progress, state, error, updated_at,
                              heartbeat_at, retry_count, file_path, claim_token)
            VALUES (?, 'uploaded', 0.0, 'queued', NULL, ?, NULL, 0, ?, NULL)
            ON CONFLICT(meeting_id) DO NOTHING
            """,
            (meeting_id, now, file_path),
        )
        return cursor.rowcount == 1


def requeue_failed_job(
    meeting_id: str,
    db_path: Optional[Path | str] = None,
) -> bool:
    """Move a failed job back to queued. Returns False unless it was failed."""
    with get_conn(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE jobs
            SET state = 'queued', stage = 'uploaded', progress = 0.0, error = NULL,
                heartbeat_at = NULL, claim_token = NULL, updated_at = ?
            WHERE meeting_id = ? AND state = 'failed'
            """,
            (_now_iso(), meeting_id),
        )
        return cursor.rowcount == 1


def set_meeting_status(
    meeting_id: str,
    status: str,
    db_path: Optional[Path | str] = None,
) -> None:
    """Update only the status of an existing meetings row."""
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE meetings SET status = ? WHERE meeting_id = ?",
            (status, meeting_id),
        )


INTERRUPTED_JOB_ERROR = (
    "Processing was interrupted by a server restart. Retry to process the recording again."
)


def fail_interrupted_inline_jobs(
    db_path: Optional[Path | str] = None,
) -> List[str]:
    """
    Inline jobs (JOB_RUNNER=inline) run inside the API process, so any job
    still queued or running when that process starts can never finish. Mark
    them failed so their status is truthful and they can be retried. Never
    call this in worker mode: there the worker owns and recovers its jobs.
    """
    with get_conn(db_path) as conn:
        ids = [
            r["meeting_id"]
            for r in conn.execute(
                "SELECT meeting_id FROM jobs WHERE state IN ('queued', 'running')"
            ).fetchall()
        ]
        if ids:
            conn.execute(
                """
                UPDATE jobs
                SET state = 'failed', error = ?, claim_token = NULL, updated_at = ?
                WHERE state IN ('queued', 'running')
                """,
                (INTERRUPTED_JOB_ERROR, _now_iso()),
            )
            conn.executemany(
                "UPDATE meetings SET status = 'failed' WHERE meeting_id = ?",
                [(mid,) for mid in ids],
            )
    return ids


# ---------------------------------------------------------------------------
# Phase 5: processing stage timings
# ---------------------------------------------------------------------------

STAGE_STATUSES = ("running", "completed", "failed", "skipped", "cached", "fallback")


def upsert_stage_timing(
    meeting_id: str,
    run_id: str,
    stage: str,
    status: str,
    *,
    seq: int,
    parent_stage: Optional[str] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    duration_seconds: Optional[float] = None,
    detail: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path | str] = None,
) -> None:
    """Insert or update one stage of one processing run."""
    if status not in STAGE_STATUSES:
        raise ValueError(f"Unknown stage status: {status}")
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO processing_stage_timings (
                meeting_id, run_id, stage, parent_stage, seq, status,
                started_at, finished_at, duration_seconds, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(meeting_id, run_id, stage) DO UPDATE SET
                parent_stage = excluded.parent_stage,
                status = excluded.status,
                started_at = COALESCE(excluded.started_at, processing_stage_timings.started_at),
                finished_at = excluded.finished_at,
                duration_seconds = excluded.duration_seconds,
                detail = excluded.detail
            """,
            (
                meeting_id, run_id, stage, parent_stage, seq, status,
                started_at, finished_at,
                None if duration_seconds is None else round(float(duration_seconds), 3),
                json.dumps(detail, ensure_ascii=False) if detail else None,
            ),
        )


def latest_stage_run_id(meeting_id: str, db_path: Optional[Path | str] = None) -> Optional[str]:
    """The run id of the most recent processing run (run ids sort by start time)."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(run_id) AS run_id FROM processing_stage_timings WHERE meeting_id = ?",
            (meeting_id,),
        ).fetchone()
    return row["run_id"] if row and row["run_id"] else None


def get_stage_timings(
    meeting_id: str,
    run_id: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> List[Dict[str, Any]]:
    """Stages of one run (the latest by default), in execution order."""
    run_id = run_id or latest_stage_run_id(meeting_id, db_path=db_path)
    if not run_id:
        return []
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM processing_stage_timings
            WHERE meeting_id = ? AND run_id = ?
            ORDER BY seq, started_at
            """,
            (meeting_id, run_id),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["detail"] = json.loads(item["detail"]) if item["detail"] else {}
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Phase 7: users, sessions, membership, invitations
# ---------------------------------------------------------------------------

def create_user(user_id: str, email: str, display_name: str, password_hash: str,
                db_path: Optional[Path | str] = None) -> bool:
    """False when the email is already registered."""
    try:
        with get_conn(db_path) as conn:
            conn.execute(
                "INSERT INTO users (user_id, email, display_name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, email.strip().lower(), display_name.strip(), password_hash, _now_iso()),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def get_user_by_email(email: str, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    return dict(row) if row else None


def get_user(user_id: str, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def create_session(token_hash: str, user_id: str, expires_at: str,
                   db_path: Optional[Path | str] = None) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash, user_id, _now_iso(), expires_at),
        )


def get_session_user(token_hash: str, now_iso: str,
                     db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            """
            SELECT u.user_id, u.email, u.display_name FROM sessions s
            JOIN users u ON u.user_id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > ?
            """,
            (token_hash, now_iso),
        ).fetchone()
    return dict(row) if row else None


def delete_session(token_hash: str, db_path: Optional[Path | str] = None) -> None:
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def add_meeting_member(meeting_id: str, user_id: str, role: str,
                       db_path: Optional[Path | str] = None) -> None:
    """Idempotent; never downgrades an owner."""
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO meeting_members (meeting_id, user_id, role, added_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(meeting_id, user_id) DO UPDATE SET
                role = CASE WHEN meeting_members.role = 'owner' THEN 'owner' ELSE excluded.role END
            """,
            (meeting_id, user_id, role, _now_iso()),
        )
        if role == "owner":
            conn.execute(
                "UPDATE meetings SET owner_user_id = ? WHERE meeting_id = ? AND owner_user_id IS NULL",
                (user_id, meeting_id),
            )


def get_meeting_owner_ids(meeting_id: str, db_path: Optional[Path | str] = None) -> List[str]:
    """Owners of a meeting: meetings.owner_user_id and any 'owner' membership."""
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT owner_user_id FROM meetings WHERE meeting_id = ?", (meeting_id,)).fetchone()
        owners = [r["user_id"] for r in conn.execute(
            "SELECT user_id FROM meeting_members WHERE meeting_id = ? AND role = 'owner' ORDER BY added_at",
            (meeting_id,),
        )]
    if row and row["owner_user_id"] and row["owner_user_id"] not in owners:
        owners.insert(0, row["owner_user_id"])
    return owners


def get_member_role(meeting_id: str, user_id: str, db_path: Optional[Path | str] = None) -> Optional[str]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT role FROM meeting_members WHERE meeting_id = ? AND user_id = ?", (meeting_id, user_id)
        ).fetchone()
    return row["role"] if row else None


def get_member_meeting_ids(user_id: str, db_path: Optional[Path | str] = None) -> List[str]:
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT meeting_id FROM meeting_members WHERE user_id = ?", (user_id,)).fetchall()
    return [r["meeting_id"] for r in rows]


def create_invitation(token_hash: str, meeting_id: str, created_by: str, expires_at: str,
                      invitee_id: Optional[int] = None, channel: Optional[str] = None,
                      db_path: Optional[Path | str] = None) -> None:
    with get_conn(db_path) as conn:
        if invitee_id is not None and channel:
            # One live token per invitee and channel: a new one replaces the old.
            conn.execute("DELETE FROM meeting_invitations WHERE invitee_id = ? AND channel = ?", (invitee_id, channel))
        conn.execute(
            "INSERT INTO meeting_invitations (token_hash, meeting_id, created_by, created_at, expires_at, "
            "invitee_id, channel) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (token_hash, meeting_id, created_by, _now_iso(), expires_at, invitee_id, channel),
        )


def get_invitation(token_hash: str, now_iso: str,
                   db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM meeting_invitations WHERE token_hash = ? AND expires_at > ?", (token_hash, now_iso)
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Scheduled meetings (Phase 8.3)
# ---------------------------------------------------------------------------

def create_scheduled_meeting(meeting_id: str, *, title: str, agenda: Optional[str], scheduled_start_utc: str,
                             timezone_name: str, duration_minutes: Optional[int], room_name: str,
                             owner_id: str, request_id: Optional[str],
                             db_path: Optional[Path | str] = None) -> None:
    """The meetings row (status 'scheduled'), the schedule and the owner, in one transaction."""
    now = _now_iso()
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO meetings (meeting_id, filename, duration, created_at, status, owner_user_id) "
            "VALUES (?, NULL, NULL, ?, 'scheduled', ?)",
            (meeting_id, now, owner_id),
        )
        conn.execute(
            """
            INSERT INTO scheduled_meetings (meeting_id, title, agenda, scheduled_start_utc, timezone,
                duration_minutes, room_name, status, created_by, request_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'scheduled', ?, ?, ?)
            """,
            (meeting_id, title, agenda, scheduled_start_utc, timezone_name, duration_minutes, room_name,
             owner_id, request_id, now),
        )
        conn.execute(
            "INSERT INTO meeting_members (meeting_id, user_id, role, added_at) VALUES (?, ?, 'owner', ?)",
            (meeting_id, owner_id, now),
        )


def get_scheduled_meeting(meeting_id: str, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM scheduled_meetings WHERE meeting_id = ?", (meeting_id,)).fetchone()
    return dict(row) if row else None


def get_scheduled_meetings(meeting_ids: List[str], db_path: Optional[Path | str] = None) -> Dict[str, Dict[str, Any]]:
    if not meeting_ids:
        return {}
    marks = ",".join("?" * len(meeting_ids))
    with get_conn(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT s.*, (SELECT COUNT(*) FROM meeting_invitees i
                         WHERE i.meeting_id = s.meeting_id AND i.status != 'revoked') AS invitee_count
            FROM scheduled_meetings s WHERE s.meeting_id IN ({marks})
            """,
            meeting_ids,
        ).fetchall()
    return {r["meeting_id"]: dict(r) for r in rows}


def find_scheduled_by_request(owner_id: str, request_id: str,
                              db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_meetings WHERE created_by = ? AND request_id = ?", (owner_id, request_id),
        ).fetchone()
    return dict(row) if row else None


def get_scheduled_by_room(room_name: str, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM scheduled_meetings WHERE room_name = ?", (room_name,)).fetchone()
    return dict(row) if row else None


def cancel_scheduled_meeting(meeting_id: str, db_path: Optional[Path | str] = None) -> bool:
    """Cancel a meeting that has not started; revokes every invitation link. False if it was not 'scheduled'."""
    now = _now_iso()
    with get_conn(db_path) as conn:
        cursor = conn.execute(
            "UPDATE scheduled_meetings SET status = 'cancelled', cancelled_at = ? "
            "WHERE meeting_id = ? AND status = 'scheduled'",
            (now, meeting_id),
        )
        if not cursor.rowcount:
            return False
        conn.execute("UPDATE meetings SET status = 'cancelled' WHERE meeting_id = ?", (meeting_id,))
        conn.execute("DELETE FROM meeting_invitations WHERE meeting_id = ?", (meeting_id,))
        conn.execute(
            "UPDATE meeting_invitees SET status = 'revoked', token_hash = NULL, updated_at = ? "
            "WHERE meeting_id = ? AND status = 'pending'",
            (now, meeting_id),
        )
    return True


def add_invitee(meeting_id: str, email: str, db_path: Optional[Path | str] = None) -> Optional[int]:
    """Insert a pending invitee; None if this email is already invited (a revoked one is re-opened)."""
    now = _now_iso()
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT invitee_id, status FROM meeting_invitees WHERE meeting_id = ? AND email = ?", (meeting_id, email),
        ).fetchone()
        if row:
            if row["status"] != "revoked":
                return None
            conn.execute("UPDATE meeting_invitees SET status = 'pending', accepted_user_id = NULL, updated_at = ? "
                         "WHERE invitee_id = ?", (now, row["invitee_id"]))
            return int(row["invitee_id"])
        cursor = conn.execute(
            "INSERT INTO meeting_invitees (meeting_id, email, status, created_at, updated_at) "
            "VALUES (?, ?, 'pending', ?, ?)",
            (meeting_id, email, now, now),
        )
        return int(cursor.lastrowid)


def list_invitees(meeting_id: str, db_path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM meeting_invitees WHERE meeting_id = ? ORDER BY invitee_id", (meeting_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_invitee(meeting_id: str, invitee_id: int, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM meeting_invitees WHERE meeting_id = ? AND invitee_id = ?", (meeting_id, invitee_id),
        ).fetchone()
    return dict(row) if row else None


def set_invitee_token(invitee_id: int, new_hash: str, db_path: Optional[Path | str] = None) -> None:
    """Point an invitee at a new link; the previous link is revoked."""
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT token_hash FROM meeting_invitees WHERE invitee_id = ?", (invitee_id,)).fetchone()
        if row and row["token_hash"]:
            conn.execute("DELETE FROM meeting_invitations WHERE token_hash = ?", (row["token_hash"],))
        conn.execute("UPDATE meeting_invitees SET token_hash = ?, updated_at = ? WHERE invitee_id = ?",
                     (new_hash, _now_iso(), invitee_id))


def revoke_invitee(invitee_id: int, db_path: Optional[Path | str] = None) -> None:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT token_hash FROM meeting_invitees WHERE invitee_id = ?", (invitee_id,)).fetchone()
        if row and row["token_hash"]:
            conn.execute("DELETE FROM meeting_invitations WHERE token_hash = ?", (row["token_hash"],))
        conn.execute("DELETE FROM meeting_invitations WHERE invitee_id = ?", (invitee_id,))
        conn.execute("UPDATE meeting_invitees SET status = 'revoked', token_hash = NULL, approval_user_id = NULL, "
                     "approval_requested_at = NULL, updated_at = ? WHERE invitee_id = ?", (_now_iso(), invitee_id))


def get_invitee_for_token(token_hash: str, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    """The scheduled-meeting invitee a token belongs to (copy or email link), or None for plain invitations."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            """
            SELECT i.* FROM meeting_invitees i
            WHERE i.token_hash = ?
               OR i.invitee_id = (SELECT invitee_id FROM meeting_invitations WHERE token_hash = ?)
            """,
            (token_hash, token_hash),
        ).fetchone()
    return dict(row) if row else None


def mark_invitee_accepted(invitee_id: int, user_id: str, db_path: Optional[Path | str] = None) -> None:
    """Record which account accepted a tracked invitation."""
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE meeting_invitees SET status = 'accepted', accepted_user_id = ?, approval_user_id = NULL, "
            "approval_requested_at = NULL, updated_at = ? WHERE invitee_id = ? AND status = 'pending'",
            (user_id, _now_iso(), invitee_id),
        )


def request_invitee_approval(invitee_id: int, user_id: str, db_path: Optional[Path | str] = None) -> None:
    """An account with a different email opened the link; it waits for the host."""
    now = _now_iso()
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE meeting_invitees SET approval_user_id = ?, approval_requested_at = ?, updated_at = ? "
            "WHERE invitee_id = ? AND status = 'pending'",
            (user_id, now, now, invitee_id),
        )


def clear_invitee_approval(invitee_id: int, db_path: Optional[Path | str] = None) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE meeting_invitees SET approval_user_id = NULL, approval_requested_at = NULL, updated_at = ? "
            "WHERE invitee_id = ?",
            (_now_iso(), invitee_id),
        )


# ---------------------------------------------------------------------------
# Email outbox (Phase 8.4)
# ---------------------------------------------------------------------------

EMAIL_ACTIVE_STATUSES = ("queued", "sending")


def enqueue_email(*, idempotency_key: str, kind: str, meeting_id: str, recipient_email: str,
                  recipient_name: Optional[str] = None, recipient_user_id: Optional[str] = None,
                  invitee_id: Optional[int] = None, minutes_version: Optional[int] = None,
                  attach_pdf: bool = False, requested_by: Optional[str] = None,
                  db_path: Optional[Path | str] = None) -> tuple[Dict[str, Any], bool]:
    """Queue one message. Returns (row, created); an existing key returns its row unchanged."""
    now = _now_iso()
    with get_conn(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO email_outbox (idempotency_key, kind, meeting_id, recipient_email, recipient_name,
                recipient_user_id, invitee_id, minutes_version, attach_pdf, status, next_attempt_at,
                requested_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (idempotency_key, kind, meeting_id, recipient_email, recipient_name, recipient_user_id, invitee_id,
             minutes_version, int(bool(attach_pdf)), now, requested_by, now, now),
        )
        created = bool(cursor.rowcount)
        row = conn.execute("SELECT * FROM email_outbox WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
    return dict(row), created


def get_email(outbox_id: int, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM email_outbox WHERE outbox_id = ?", (outbox_id,)).fetchone()
    return dict(row) if row else None


def list_emails(meeting_id: str, kind: Optional[str] = None,
                db_path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    query = "SELECT * FROM email_outbox WHERE meeting_id = ?"
    params: list = [meeting_id]
    if kind:
        query += " AND kind = ?"
        params.append(kind)
    with get_conn(db_path) as conn:
        rows = conn.execute(query + " ORDER BY outbox_id", params).fetchall()
    return [dict(r) for r in rows]


def latest_invitation_email(invitee_id: int, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM email_outbox WHERE kind = 'invitation' AND invitee_id = ? ORDER BY outbox_id DESC LIMIT 1",
            (invitee_id,),
        ).fetchone()
    return dict(row) if row else None


def count_invitation_emails(invitee_id: int, db_path: Optional[Path | str] = None) -> int:
    with get_conn(db_path) as conn:
        return int(conn.execute(
            "SELECT COUNT(*) FROM email_outbox WHERE kind = 'invitation' AND invitee_id = ?", (invitee_id,),
        ).fetchone()[0])


def claim_due_emails(now_iso: str, limit: int = 20, db_path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    """Move due queued messages to 'sending'. A row is claimed by one dispatcher only."""
    claimed = []
    with get_conn(db_path) as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT outbox_id FROM email_outbox WHERE status = 'queued' AND (next_attempt_at IS NULL "
            "OR next_attempt_at <= ?) ORDER BY outbox_id LIMIT ?",
            (now_iso, limit),
        )]
        for outbox_id in ids:
            cursor = conn.execute(
                "UPDATE email_outbox SET status = 'sending', claimed_at = ?, updated_at = ? "
                "WHERE outbox_id = ? AND status = 'queued'",
                (now_iso, now_iso, outbox_id),
            )
            if cursor.rowcount:
                claimed.append(dict(conn.execute("SELECT * FROM email_outbox WHERE outbox_id = ?",
                                                 (outbox_id,)).fetchone()))
    return claimed


_OUTBOX_FIELDS = ("status", "provider", "provider_message_id", "attempts", "next_attempt_at", "last_error",
                  "sent_at", "delivered_at", "claimed_at")


def update_email(outbox_id: int, only_if_status: Optional[tuple] = None,
                 db_path: Optional[Path | str] = None, **fields: Any) -> bool:
    changes = {k: v for k, v in fields.items() if k in _OUTBOX_FIELDS}
    changes["updated_at"] = _now_iso()
    sets = ", ".join(f"{k} = ?" for k in changes)
    query = f"UPDATE email_outbox SET {sets} WHERE outbox_id = ?"
    params: list = [*changes.values(), outbox_id]
    if only_if_status:
        query += f" AND status IN ({','.join('?' * len(only_if_status))})"
        params.extend(only_if_status)
    with get_conn(db_path) as conn:
        return bool(conn.execute(query, params).rowcount)


def cancel_queued_emails(meeting_id: str, invitee_id: Optional[int] = None, reason: str = "",
                         db_path: Optional[Path | str] = None) -> int:
    query = ("UPDATE email_outbox SET status = 'cancelled', last_error = ?, updated_at = ? "
             "WHERE meeting_id = ? AND kind = 'invitation' AND status = 'queued'")
    params: list = [reason, _now_iso(), meeting_id]
    if invitee_id is not None:
        query += " AND invitee_id = ?"
        params.append(invitee_id)
    with get_conn(db_path) as conn:
        return conn.execute(query, params).rowcount


def fail_stale_sending_emails(before_iso: str, db_path: Optional[Path | str] = None) -> int:
    """Messages left 'sending' by a stopped process: the provider may or may not have accepted them."""
    with get_conn(db_path) as conn:
        return conn.execute(
            "UPDATE email_outbox SET status = 'failed', last_error = ?, updated_at = ? "
            "WHERE status = 'sending' AND claimed_at < ?",
            ("Sending was interrupted; the provider may or may not have accepted this message. "
             "Check before retrying.", _now_iso(), before_iso),
        ).rowcount


def find_email_by_message_id(message_id: str, db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM email_outbox WHERE provider_message_id = ?", (message_id,)).fetchone()
    return dict(row) if row else None


def record_email_event(outbox_id: int, event: str, reason: Optional[str], event_ts: Optional[str],
                       db_path: Optional[Path | str] = None) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            "INSERT INTO email_events (outbox_id, event, reason, event_ts, received_at) VALUES (?, ?, ?, ?, ?)",
            (outbox_id, event, (reason or "")[:300] or None, event_ts, _now_iso()),
        )


def list_email_events(outbox_ids: List[int], db_path: Optional[Path | str] = None) -> Dict[int, List[Dict[str, Any]]]:
    if not outbox_ids:
        return {}
    marks = ",".join("?" * len(outbox_ids))
    with get_conn(db_path) as conn:
        rows = conn.execute(f"SELECT * FROM email_events WHERE outbox_id IN ({marks}) ORDER BY event_id",
                            outbox_ids).fetchall()
    out: Dict[int, List[Dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["outbox_id"], []).append(dict(r))
    return out


def get_approved_snapshot_version(meeting_id: str, version: int,
                                  db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM approved_snapshots WHERE meeting_id = ? AND version = ?",
                           (meeting_id, version)).fetchone()
    if not row:
        return None
    res = dict(row)
    res["mom_payload"] = json.loads(res["mom_payload"]) if res["mom_payload"] else {}
    return res


def list_meeting_members(meeting_id: str, db_path: Optional[Path | str] = None) -> List[Dict[str, Any]]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT m.user_id, m.role, u.email, u.display_name FROM meeting_members m "
            "JOIN users u ON u.user_id = m.user_id WHERE m.meeting_id = ? ORDER BY m.added_at",
            (meeting_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_scheduled_meeting(meeting_id: str, fields: Dict[str, Any], db_path: Optional[Path | str] = None) -> bool:
    """Edit a meeting that has not started. False if it is no longer 'scheduled'."""
    allowed = ("title", "agenda", "scheduled_start_utc", "timezone", "duration_minutes")
    changes = {k: v for k, v in fields.items() if k in allowed}
    if not changes:
        return get_scheduled_meeting(meeting_id, db_path=db_path) is not None
    sets = ", ".join(f"{k} = ?" for k in changes)
    with get_conn(db_path) as conn:
        cursor = conn.execute(
            f"UPDATE scheduled_meetings SET {sets} WHERE meeting_id = ? AND status = 'scheduled'",
            (*changes.values(), meeting_id),
        )
    return bool(cursor.rowcount)


def start_scheduled_meeting(meeting_id: str, room_sid: Optional[str] = None,
                            db_path: Optional[Path | str] = None) -> Optional[Dict[str, Any]]:
    """
    Open the live room for a scheduled meeting under the scheduled meeting's ID,
    so its members and invitations carry over. Returns the live meeting, or
    None when the meeting is not 'scheduled' (cancelled, or already held).
    """
    now = _now_iso()
    with get_conn(db_path) as conn:
        schedule = conn.execute(
            "SELECT * FROM scheduled_meetings WHERE meeting_id = ? AND status = 'scheduled'", (meeting_id,),
        ).fetchone()
        if schedule is None:
            return None
        conn.execute("UPDATE scheduled_meetings SET status = 'started' WHERE meeting_id = ?", (meeting_id,))
        conn.execute(
            "UPDATE meetings SET status = 'live', filename = ? WHERE meeting_id = ?",
            (f"live-{schedule['room_name']}.wav", meeting_id),
        )
        conn.execute(
            """
            INSERT INTO live_meetings (meeting_id, room_name, room_sid, title, status, created_at)
            VALUES (?, ?, ?, ?, 'active', ?)
            """,
            (meeting_id, schedule["room_name"], room_sid, schedule["title"], now),
        )
    record_live_event(meeting_id, "meeting_created",
                      detail={"room_name": schedule["room_name"], "scheduled": True}, db_path=db_path)
    return get_live_meeting(meeting_id, db_path=db_path)


def remove_meeting_member(meeting_id: str, user_id: str, db_path: Optional[Path | str] = None) -> None:
    """Remove a non-owner member (owners are never removed this way)."""
    with get_conn(db_path) as conn:
        conn.execute(
            "DELETE FROM meeting_members WHERE meeting_id = ? AND user_id = ? AND role != 'owner'",
            (meeting_id, user_id),
        )



def delete_invitation_token(token_hash: str, db_path: Optional[Path | str] = None) -> None:
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM meeting_invitations WHERE token_hash = ?", (token_hash,))
