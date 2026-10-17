"""
Real-model verification of the demo_01 golden fixture (Phase 1 Gate C).

Run as a subprocess by tests/test_semantic_real_models.py so that the real
sentence-transformers / mDeBERTa models are used (some unit-test modules stub
sentence_transformers for the whole pytest session). Usage:

    python -m tests.real_model_checks <empty temp dir>

Prints one JSON object describing the verification of the frozen rules-v1
claims (citations u00030 and u00033) and of fresh rules-v2 claims.
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
from pathlib import Path


def main(root: Path) -> dict:
    from app.core import config

    config.STORAGE_ROOT = root / "storage"
    config.DB_PATH = root / "test.db"
    from app.core import db

    db.DB_PATH = config.DB_PATH
    from app.core.storage import MeetingStorage
    from app.schemas.claim import Decision
    from app.services.intelligence.extraction_pipeline import run_extraction
    from app.services.transcript.transcript_index import build_transcript_index, persist_transcript_index
    from app.services.verification.claim_verification import verify_meeting
    from app.services.verification.claims_view import build_claims_view

    fixture = Path(__file__).parent / "fixtures" / "golden" / "demo_01"
    transcript = json.loads((fixture / "meeting_transcript_ready.json").read_text(encoding="utf-8"))

    def setup(meeting_id: str) -> None:
        db.init_db()
        db.insert_meeting(meeting_id, filename="demo_01.wav")
        with contextlib.redirect_stdout(io.StringIO()):
            persist_transcript_index(build_transcript_index(transcript, meeting_id), meeting_id)

    def describe(meeting_id: str, summary: dict) -> dict:
        claims = []
        for claim in build_claims_view(meeting_id)["claims"]:
            semantic = claim["verification"]["semantic"] or {}
            claims.append({
                "evidence_ids": claim["evidence_ids"],
                "state": claim["state"],
                "text": claim["text"],
                "final": claim["verification"]["final"]["label"],
                "reason_codes": [r["code"] for r in claim["verification"]["final"]["reasons"]],
                "semantic_label": semantic.get("label"),
                "cited_entailment": semantic.get("cited_entailment"),
                "cited_strict": (semantic.get("details") or {}).get("e_cited_strict"),
                "best_support": semantic.get("best_support"),
                "suggestion": (claim["suggestion"] or {}).get("utt_ids"),
                "exportable": claim["exportable"],
            })
        return {"status": summary["status"], "timings": summary["timings"], "claims": claims}

    setup("gate_v1")
    for claim in json.loads((fixture / "claims.json").read_text(encoding="utf-8"))["claims"]:
        db.insert_claim_with_evidence(Decision(**{
            **claim, "meeting_id": "gate_v1", "claim_id": claim["claim_id"].replace("demo_01", "gate_v1"),
        }))
    v1 = describe("gate_v1", verify_meeting("gate_v1"))

    setup("gate_v2")
    storage = MeetingStorage("gate_v2")
    storage.ensure_dirs()
    shutil.copy(fixture / "topics.json", storage.path_for("intelligence", "topics.json"))
    shutil.copy(fixture / "frontend_transcript.json", storage.path_for("transcript", "frontend_transcript.json"))
    with contextlib.redirect_stdout(io.StringIO()):
        run_extraction("gate_v2", mode="rules")
    v2 = describe("gate_v2", verify_meeting("gate_v2"))
    return {"v1": v1, "v2": v2}


if __name__ == "__main__":
    print("RESULT_JSON=" + json.dumps(main(Path(sys.argv[1]).resolve())))
