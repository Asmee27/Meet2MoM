"""
Load the fallback demo meetings (Phase 10) so the demo works without a live
recording or network access.

    .venv\\Scripts\\python.exe scripts\\seed_demo_meetings.py --owner-email you@example.com

Each meeting in demo/fallback_meetings.json gets a new id; existing meetings
are never touched. The transcript is taken as given (no speech recognition),
then indexing, topics, rule-based extraction and verification run as in a real
job. Delete a demo meeting from the meeting list like any other.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import db  # noqa: E402
from app.core.storage import MeetingStorage  # noqa: E402
from app.schemas.job import JobStatus  # noqa: E402

DATASET = ROOT / "demo" / "fallback_meetings.json"


def seed_meeting(meeting: dict, owner_id: str | None) -> str:
    from app.services.pipeline.intelligence_service import ensure_verification
    from app.services.pipeline.recorded_pipeline_adapter import run_post_transcript_stages

    meeting_id = f"demo-{uuid4().hex[:12]}"
    db.insert_meeting(meeting_id, filename=f"{meeting['name']}.json")
    if owner_id:
        db.add_meeting_member(meeting_id, owner_id, "owner")

    segments = [{**s, "avg_logprob": -0.1, "speaker_attribution_confidence": 0.95}
                for s in meeting["segments"]]
    storage = MeetingStorage(meeting_id)
    storage.ensure_dirs()
    for name, payload in (("meeting_transcript_ready.json", {"segments": segments}),
                          ("frontend_transcript.json", {"language": "en", "segments": segments})):
        storage.path_for("transcript", name).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    stats = run_post_transcript_stages(meeting_id, mode="rules")
    verification = ensure_verification(meeting_id, force=True)
    db.set_meeting_status(meeting_id, "completed")
    db.upsert_job_status(JobStatus(meeting_id=meeting_id, stage="done", progress=1.0, state="done",
                                   error=None, updated_at=datetime.now(timezone.utc)))
    print(f"{meeting_id}  {meeting['name']}: {stats.get('accepted', 0)} items, "
          f"verification {verification.get('status')}")
    return meeting_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner-email", help="Account that owns the demo meetings (needed when sign-in is on).")
    args = parser.parse_args()

    db.init_db()
    owner_id = None
    if args.owner_email:
        user = db.get_user_by_email(args.owner_email.strip().lower())
        if user is None:
            print(f"No account for {args.owner_email}. Register in the app first.", file=sys.stderr)
            return 1
        owner_id = user["user_id"]

    for meeting in json.loads(DATASET.read_text(encoding="utf-8"))["meetings"]:
        seed_meeting(meeting, owner_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
