from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
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

    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
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
                updated_at TEXT
            );
            """
        )
        _migrate(conn)


# ---------------------------------------------------------------------------
# Versioned schema migrations (PRAGMA user_version)
# ---------------------------------------------------------------------------
# Migrations only ADD tables/indexes; they never rewrite or drop existing
# data. Each step is idempotent (IF NOT EXISTS) and bumps user_version.

SCHEMA_VERSION = 2

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


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current < 1:
        conn.executescript(_MIGRATION_V1)
        conn.execute("PRAGMA user_version = 1")
        current = 1
    if current < 2:
        conn.executescript(_MIGRATION_V2)
        conn.execute("PRAGMA user_version = 2")


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

    result = []
    for r in rows:
        lang_tags = json.loads(r["lang_tags"]) if r["lang_tags"] else []
        conf_src = r["conf_source"] if "conf_source" in r.keys() and r["conf_source"] is not None else "missing"
        result.append(
            Utterance(
                meeting_id=r["meeting_id"],
                utt_id=r["utt_id"],
                start=r["start"],
                end=r["end"],
                speaker_id=r["speaker_id"],
                speaker_name=r["speaker_name"],
                text=r["text"],
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


def upsert_job_status(
    job: JobStatus,
    db_path: Optional[Path | str] = None,
) -> None:
    """Upsert a JobStatus record."""
    with get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO jobs (meeting_id, stage, progress, state, error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job.meeting_id,
                job.stage,
                job.progress,
                job.state,
                job.error,
                job.updated_at.isoformat(),
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
            SELECT meeting_id, stage, progress, state, error, updated_at
            FROM jobs
            WHERE meeting_id = ?
            """,
            (meeting_id,),
        )
        row = cursor.fetchone()

    if not row:
        return None

    return JobStatus(
        meeting_id=row["meeting_id"],
        stage=row["stage"],
        progress=row["progress"],
        state=row["state"],
        error=row["error"],
        updated_at=datetime.fromisoformat(row["updated_at"]),
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

