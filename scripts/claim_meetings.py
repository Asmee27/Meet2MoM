"""
Give an account ownership of meetings created before accounts existed (Phase 7).

With AUTH_REQUIRED=true a meeting without an owner is visible to nobody. Run
once after registering:

    .venv\\Scripts\\python.exe scripts\\claim_meetings.py --owner-email you@example.com          # list
    .venv\\Scripts\\python.exe scripts\\claim_meetings.py --owner-email you@example.com --apply  # claim

Only meetings that have no owner yet are changed.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--apply", action="store_true", help="Claim the listed meetings (default: list only).")
    args = parser.parse_args()

    db.init_db()
    user = db.get_user_by_email(args.owner_email)
    if user is None:
        print(f"No account for {args.owner_email}. Register in the app first.", file=sys.stderr)
        return 1
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT meeting_id, filename, created_at FROM meetings WHERE owner_user_id IS NULL ORDER BY created_at"
        ).fetchall()
    for meeting_id, filename, created_at in rows:
        if args.apply:
            db.add_meeting_member(meeting_id, user["user_id"], "owner")
        print(f"{'claimed' if args.apply else 'unowned'}  {meeting_id}  {created_at}  {filename}")
    print(f"{len(rows)} meeting(s){'' if args.apply else ' (dry run; add --apply to claim)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
