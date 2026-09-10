from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core import db


def main() -> None:
    db.init_db()
    patterns = (
        "synthetic.wav",
        "background-%",
        "mismatch-%",
        "cache-check-%",
        "cited-none-%",
        "expand-%",
        "clamp-%",
    )
    with db.get_conn() as conn:
        where = " OR ".join(
            ["m.filename = ?" if index == 0 else "m.meeting_id LIKE ?"
             for index in range(len(patterns))]
        )
        params = patterns
        meeting_ids = [
            row[0]
            for row in conn.execute(
                f"SELECT m.meeting_id FROM meetings m WHERE {where}",
                params,
            ).fetchall()
        ]
        for meeting_id in meeting_ids:
            conn.execute(
                "DELETE FROM evidence_links WHERE meeting_id = ?",
                (meeting_id,),
            )
            conn.execute(
                "DELETE FROM dropped_claims WHERE meeting_id = ?",
                (meeting_id,),
            )
            conn.execute(
                "DELETE FROM claims WHERE meeting_id = ?",
                (meeting_id,),
            )
            conn.execute(
                "DELETE FROM utterances WHERE meeting_id = ?",
                (meeting_id,),
            )
            conn.execute(
                "DELETE FROM jobs WHERE meeting_id = ?",
                (meeting_id,),
            )
            conn.execute(
                "DELETE FROM meetings WHERE meeting_id = ?",
                (meeting_id,),
            )
    print(f"meetings removed: {len(meeting_ids)}")


if __name__ == "__main__":
    main()
