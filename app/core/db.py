from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

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
