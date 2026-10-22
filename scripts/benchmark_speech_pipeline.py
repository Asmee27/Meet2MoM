"""
Benchmark the speech pipeline (stages 1-9): subprocess vs in-process (Phase 6).

    python scripts/benchmark_speech_pipeline.py <meeting_id> [--runs 2]
        [--modes subprocess,inprocess] [--out results.json] [--keep]

Safety:
* The meeting's recording/ folder is COPIED into a temporary storage root
  (MEET2MOM_STORAGE_ROOT), so the real storage/ folder and database are never
  read for writing or modified.
* Result caching is disabled (ENABLE_PIPELINE_CACHE / CACHE_* = false), so
  every run really transcribes; nothing is reused between runs.
* Only stages 1-9 run: no LLM request, no verification.

In-process runs share this benchmark process: the first one pays the library
import and model loading ("cold"), later ones reuse them ("warm"), which is
what a long-lived worker does for every meeting after the first.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path
from uuid import uuid4

REPO = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Speech pipeline benchmark (Phase 6)")
    parser.add_argument("meeting_id")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--modes", default="subprocess,inprocess")
    parser.add_argument("--out", default=None, help="write the results as JSON here")
    parser.add_argument("--keep", action="store_true", help="keep the temporary storage root")
    args = parser.parse_args(argv)

    source_root = Path(os.environ.get("MEET2MOM_STORAGE_ROOT") or REPO / "storage").resolve()
    source = source_root / args.meeting_id / "recording"
    if not (source / "recording.wav").exists():
        print(f"No recording at {source}")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="m2m_bench_"))
    os.environ["MEET2MOM_STORAGE_ROOT"] = str(tmp / "storage")
    for flag in ("ENABLE_PIPELINE_CACHE", "CACHE_TRANSCRIPTION", "CACHE_DIARIZATION"):
        os.environ[flag] = "false"
    sys.path.insert(0, str(REPO))

    from app.core import config, db
    from app.core.stage_timings import STAGE_FILE
    from app.core.storage import MeetingStorage
    from app.services.pipeline import recorded_pipeline_adapter as adapter

    assert config.STORAGE_ROOT.resolve() != source_root, "refusing to benchmark in the real storage root"
    db.init_db()
    log_path = tmp / "pipeline_output.log"
    results = []
    try:
        for mode in [m.strip() for m in args.modes.split(",") if m.strip()]:
            config.SPEECH_PIPELINE = mode
            for run in range(1, args.runs + 1):
                mid = uuid4().hex
                storage = MeetingStorage(mid)
                storage.ensure_dirs()
                shutil.copytree(source, storage.recording, dirs_exist_ok=True)
                db.insert_meeting(mid, filename="benchmark.wav")
                started = time.monotonic()
                error = None
                with open(log_path, "a", encoding="utf-8") as log, redirect_stdout(log):
                    try:
                        adapter.run_recorded_pipeline(mid, storage.recording / "recording.wav",
                                                      run_post_transcript=False)
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {exc}"[:300]
                total = time.monotonic() - started
                stage_file = storage.path_for("metadata", STAGE_FILE)
                stages = json.loads(stage_file.read_text(encoding="utf-8")) if stage_file.exists() else []
                row = {
                    "transcript_sha": _transcript_fingerprint(storage),
                    "language": config.FORCE_LANGUAGE or "auto",
                    "mode": mode,
                    "run": run,
                    "label": f"{mode} #{run}" + (" (cold)" if mode == "inprocess" and run == 1 else ""),
                    "total_seconds": round(total, 2),
                    "error": error,
                    "stages": {
                        s["stage"]: {
                            "status": s["status"],
                            "seconds": s.get("duration_seconds"),
                            **{k: v for k, v in (s.get("detail") or {}).items()
                               if k in ("model_load_seconds", "model_reused", "mode", "method", "tracks")},
                        }
                        for s in stages
                    },
                }
                results.append(row)
                print(json.dumps(row), flush=True)
    finally:
        summary = {
            "meeting": args.meeting_id,
            "recording_seconds": _duration(source / "recording.wav"),
            "machine": _machine(),
            "results": results,
        }
        if args.out:
            Path(args.out).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        _print_table(results)
        if args.keep:
            print(f"Kept {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    return 0 if all(r["error"] is None for r in results) else 2


def _transcript_fingerprint(storage):
    """SHA-256 of (speaker, start, text) rows: compares runs without printing text."""
    import hashlib

    path = storage.path_for("transcript", "meeting_transcript_ready.json")
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("segments", data) if isinstance(data, dict) else data
    key = [(r.get("speaker"), round(float(r.get("start", 0)), 1), (r.get("text") or "").strip()) for r in rows]
    return hashlib.sha256(json.dumps(key).encode("utf-8")).hexdigest()[:16]


def _duration(path: Path):
    import wave

    with wave.open(str(path)) as w:
        return round(w.getnframes() / w.getframerate(), 2)


def _machine() -> dict:
    import platform

    return {"cpu": platform.processor(), "cores": os.cpu_count(), "python": platform.python_version(),
            "os": platform.platform()}


def _print_table(results) -> None:
    names = ("pipeline_startup", "audio_preparation", "transcription", "speaker_attribution", "transcript_merge")
    print("\n| Run | Language | Total | " + " | ".join(names) + " | Transcript |")
    print("|---|---|---|" + "---|" * len(names) + "---|")
    for r in results:
        cells = []
        for n in names:
            s = r["stages"].get(n, {})
            val = s.get("seconds")
            text = "—" if val is None else f"{val:.1f} s"
            if s.get("status") not in (None, "completed"):
                text += f" ({s['status']})"
            if "model_load_seconds" in s:
                text += f" [load {s['model_load_seconds']} s]"
            cells.append(text)
        print(f"| {r['label']} | {r['language']} | {r['total_seconds']:.1f} s | " + " | ".join(cells)
              + f" | {r['transcript_sha']} |")


if __name__ == "__main__":
    sys.exit(main())
