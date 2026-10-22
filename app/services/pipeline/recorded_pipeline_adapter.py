from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import logging


# ============================================================
# PROJECT PATHS
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[3]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import STORAGE_ROOT
from app.core.storage import MeetingStorage

logger = logging.getLogger(__name__)

STORAGE_DIR = STORAGE_ROOT
PIPELINE_CACHE_DIR = STORAGE_DIR / "_pipeline_cache"
PIPELINE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# SUBPROCESS ENVIRONMENT
# ============================================================

env = os.environ.copy()

existing_pythonpath = env.get("PYTHONPATH", "")

env["PYTHONPATH"] = (
    str(PROJECT_ROOT)
    if not existing_pythonpath
    else str(PROJECT_ROOT) + os.pathsep + existing_pythonpath
)
env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUTF8"] = "1"
env["PYTHONUNBUFFERED"] = "1"

# ============================================================
# PIPELINE LOCK
# ============================================================

# Your existing recorded pipeline still writes to shared output/
# locations. Until every service accepts a per-meeting output
# directory, only one recorded pipeline should run at a time.
_PIPELINE_LOCK = threading.Lock()



def speech_pipeline_mode() -> str:
    """
    Where stages 1-9 run (Phase 6): "subprocess" starts a fresh Python process
    per meeting (isolated, but reloads every library and model); "inprocess"
    runs them inside the job runner and reuses loaded libraries and models.
    "auto" (the default) uses inprocess only inside scripts/worker.py, so the
    API process that holds live recordings never runs heavy ASR itself.
    """
    from app.core import config

    mode = (getattr(config, "SPEECH_PIPELINE", "auto") or "auto").strip().lower()
    if mode == "auto":
        return "inprocess" if os.environ.get(PROCESS_ROLE_ENV) == "worker" else "subprocess"
    return mode if mode in ("inprocess", "subprocess") else "subprocess"


PROCESS_ROLE_ENV = "M2M_PROCESS_ROLE"
_SPEECH_MODULE = "scripts.run_recorded_pipeline"


def _run_speech_pipeline_inprocess(meeting_id: str, input_file: Path) -> None:
    """Run stages 1-9 in this process, importing the libraries only once."""
    import importlib

    first_import = _SPEECH_MODULE not in sys.modules
    started = time.monotonic()
    module = importlib.import_module(_SPEECH_MODULE)
    startup_seconds = time.monotonic() - started if first_import else None
    module.run(
        meeting_id,
        input_file,
        skip_post_transcript=True,
        startup_seconds=startup_seconds,
        startup_note=(
            "In-process: first import of the speech libraries in this process"
            if first_import else None
        ),
    )
_CACHE_LOCK = threading.Lock()


# ============================================================
# CUSTOM ERROR
# ============================================================

class RecordedPipelineError(RuntimeError):
    pass


class PostTranscriptStageError(RuntimeError):
    def __init__(self, stage: str, cause: Exception):
        self.stage = stage
        self.cause = cause
        super().__init__(f"{stage}: {cause}")


# ============================================================
# JSON HELPERS
# ============================================================

def _read_json(path: Path):
    if not path.exists():
        return None

    try:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    except Exception:
        return None


def _write_json(
    path: Path,
    data
):
    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    path.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


# ============================================================
# FILE COPY HELPER
# ============================================================

def _copy_if_exists(
    source: Path,
    destination: Path
):
    if not source.exists():
        return False

    destination.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    if source.is_dir():

        if destination.exists():
            shutil.rmtree(
                destination
            )

        shutil.copytree(
            source,
            destination
        )

    else:

        shutil.copy2(
            source,
            destination
        )

    return True



# ============================================================
# CONTENT-HASH CACHE
# ============================================================

def _sha256_file(
    path: Path,
    chunk_size: int = 8 * 1024 * 1024
):
    """
    Compute a content hash for an uploaded meeting.

    This makes cache reuse independent of meeting_id/path, so uploading
    the exact same MP4 again can reuse the completed ASR + diarization
    artifacts instead of spending minutes processing them again.
    """

    digest = hashlib.sha256()

    with open(path, "rb") as file:
        while True:
            chunk = file.read(chunk_size)

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def _cache_directory(
    content_hash: str
):
    return (
        PIPELINE_CACHE_DIR
        / content_hash
    )


def _content_cache_enabled() -> bool:
    from app.core import config

    return bool(getattr(config, "ENABLE_PIPELINE_CACHE", True))


def _cache_is_complete(
    content_hash: str
):
    """
    A cache entry is considered reusable only when the critical artifacts
    needed by the frontend and downstream intelligence pipeline exist.
    """

    cache_dir = _cache_directory(
        content_hash
    )

    # Check MeetingStorage layout
    required = (
        cache_dir / "transcript" / "meeting_transcript_ready.json",
        cache_dir / "diarization" / "speaker_turns.json",
        cache_dir / "metadata" / "processing_report.json",
    )

    if all(path.exists() and path.is_file() and path.stat().st_size > 0 for path in required):
        return True

    # Legacy results layout check
    legacy_required = (
        cache_dir / "results" / "transcripts" / "meeting_transcript_ready.json",
        cache_dir / "results" / "diarization" / "speaker_turns.json",
        cache_dir / "results" / "metadata" / "processing_report.json",
    )

    return all(
        path.exists()
        and path.is_file()
        and path.stat().st_size > 0
        for path in legacy_required
    )


def _restore_cached_results(
    meeting_id: str,
    content_hash: str
):
    """
    Copy cached completed pipeline artifacts into the current meeting's
    storage directory. The cache remains immutable from the meeting's
    perspective.
    """

    cache_dir = _cache_directory(
        content_hash
    )

    storage = MeetingStorage(meeting_id)
    storage.ensure_dirs()

    # Restore from MeetingStorage layout
    for cat in ("transcript", "diarization", "metadata"):
        cached_cat = cache_dir / cat
        target_cat = getattr(storage, cat)
        if cached_cat.exists():
            shutil.copytree(cached_cat, target_cat, dirs_exist_ok=True)

    # Restore from legacy results layout if present
    cached_results = cache_dir / "results"
    if cached_results.exists():
        if (cached_results / "transcripts").exists():
            shutil.copytree(cached_results / "transcripts", storage.transcript, dirs_exist_ok=True)
        if (cached_results / "diarization").exists():
            shutil.copytree(cached_results / "diarization", storage.diarization, dirs_exist_ok=True)
        if (cached_results / "metadata").exists():
            shutil.copytree(cached_results / "metadata", storage.metadata, dirs_exist_ok=True)

    # Restore frontend transcript
    for candidate in (
        cache_dir / "transcript" / "frontend_transcript.json",
        cache_dir / "frontend_transcript.json",
    ):
        if candidate.exists():
            shutil.copy2(candidate, storage.path_for("transcript", "frontend_transcript.json"))
            shutil.copy2(candidate, storage.root / "frontend_transcript.json")
            break

    return True


def _save_results_to_cache(
    meeting_id: str,
    content_hash: str
):
    """
    Persist the completed per-meeting snapshot into a content-addressed
    cache. Write to a temporary directory first, then atomically replace
    the cache entry so an interrupted write is never treated as complete.
    """

    storage = MeetingStorage(meeting_id)
    cache_dir = _cache_directory(
        content_hash
    )

    temporary_dir = (
        PIPELINE_CACHE_DIR
        / f".{content_hash}.tmp"
    )

    if temporary_dir.exists():
        shutil.rmtree(
            temporary_dir
        )

    temporary_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    # Copy storage categories into temporary cache
    for cat in ("transcript", "diarization", "metadata"):
        src = getattr(storage, cat)
        if src.exists():
            shutil.copytree(
                src,
                temporary_dir / cat
            )

    frontend_transcript = storage.path_for("transcript", "frontend_transcript.json")
    if not frontend_transcript.exists():
        frontend_transcript = storage.root / "frontend_transcript.json"

    if frontend_transcript.exists():
        (temporary_dir / "transcript").mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            frontend_transcript,
            temporary_dir
            / "transcript"
            / "frontend_transcript.json"
        )
        shutil.copy2(
            frontend_transcript,
            temporary_dir
            / "frontend_transcript.json"
        )

    _write_json(
        temporary_dir
        / "cache_metadata.json",
        {
            "content_hash":
                content_hash,

            "source_meeting_id":
                meeting_id,

            "created_at":
                time.time()
        }
    )

    if cache_dir.exists():
        shutil.rmtree(
            cache_dir
        )

    temporary_dir.replace(
        cache_dir
    )

    return True


# ============================================================
# TRANSCRIPT NORMALIZER
# ============================================================

def _extract_segments(payload):

    if payload is None:
        return []

    if isinstance(
        payload,
        list
    ):

        raw_segments = payload

    elif isinstance(
        payload,
        dict
    ):

        raw_segments = None

        possible_keys = (
            "segments",
            "transcript",
            "speaker_transcript",
            "items",
            "utterances"
        )

        for key in possible_keys:

            value = payload.get(
                key
            )

            if isinstance(
                value,
                list
            ):
                raw_segments = value
                break

        if raw_segments is None:
            raw_segments = []

    else:
        raw_segments = []

    normalized = []

    for item in raw_segments:

        if not isinstance(
            item,
            dict
        ):
            continue

        text = str(
            item.get(
                "text"
            )
            or ""
        ).strip()

        if not text:
            continue

        start = item.get(
            "start",
            item.get(
                "start_time",
                0
            )
        )

        end = item.get(
            "end",
            item.get(
                "end_time",
                start
            )
        )

        speaker = (
            item.get(
                "speaker"
            )
            or item.get(
                "speaker_label"
            )
            or item.get(
                "speaker_id"
            )
            or "SPEAKER_UNKNOWN"
        )

        normalized.append(
            {
                "start": start,
                "end": end,
                "text": text,

                "speaker": speaker,

                "speaker_attribution_confidence":
                    item.get(
                        "speaker_attribution_confidence"
                    ),

                "speaker_confidence_flag":
                    item.get(
                        "speaker_confidence_flag"
                    ),

                "possible_overlap":
                    item.get(
                        "possible_overlap",
                        False
                    ),

                "overlapping_speakers":
                    item.get(
                        "overlapping_speakers",
                        []
                    ),

                "requires_review":
                    item.get(
                        "requires_review",
                        False
                    ),

                "review_reasons":
                    item.get(
                        "review_reasons",
                        []
                    ),

                "language":
                    item.get(
                        "language"
                    ),

                "avg_logprob":
                    item.get(
                        "avg_logprob"
                    ),

                "no_speech_probability":
                    item.get(
                        "no_speech_probability"
                    ),

                "confidence_flag":
                    item.get(
                        "confidence_flag"
                    )
            }
        )

    return normalized


# ============================================================
# LANGUAGE DETECTION FROM TRANSCRIPT OUTPUT
# ============================================================

def _detect_language(
    payload,
    segments
):

    if isinstance(
        payload,
        dict
    ):

        language = (
            payload.get(
                "language"
            )
            or payload.get(
                "detected_language"
            )
        )

        if language:
            return language

    counts = {}

    for segment in segments:

        language = segment.get(
            "language"
        )

        if language:

            counts[language] = (
                counts.get(
                    language,
                    0
                )
                + 1
            )

    if counts:

        return max(
            counts,
            key=counts.get
        )

    return "unknown"


# ============================================================
# SNAPSHOT MEETING ARTIFACTS
# ============================================================

def _snapshot_outputs(
    meeting_id: str
):
    storage = MeetingStorage(meeting_id)
    copied = []
    for cat, fname in [
        ("transcript", "meeting_transcript_ready.json"),
        ("transcript", "speaker_transcript.json"),
        ("transcript", "final_transcript.json"),
        ("transcript", "transcript_index.json"),
        ("diarization", "speaker_turns.json"),
        ("metadata", "processing_report.json"),
        ("metadata", "audio_quality.json"),
        ("metadata", "recording_info.json"),
        ("metadata", "chunk_metadata.json"),
    ]:
        p = storage.path_for(cat, fname)
        if p.exists():
            copied.append(str(p.relative_to(storage.root)))
    return copied


# ============================================================
# RESULT PATHS
# ============================================================

def get_result_paths(
    meeting_id: str
):
    storage = MeetingStorage(meeting_id)
    return {
        "ready":
            storage.path_for("transcript", "meeting_transcript_ready.json"),

        "speaker":
            storage.path_for("transcript", "speaker_transcript.json"),

        "final":
            storage.path_for("transcript", "final_transcript.json"),

        "index":
            storage.path_for("transcript", "transcript_index.json"),

        "diarization":
            storage.path_for("diarization", "speaker_turns.json"),

        "report":
            storage.path_for("metadata", "processing_report.json"),
    }


# ============================================================
# TRANSCRIPT INDEXER
# ============================================================

def _index_transcript(
    meeting_id: str
):
    """
    Hook called immediately after speaker merging and before topic segmentation.
    Builds a deterministic, sorted, utt_id-keyed index and persists to DB and disk.
    """
    storage = MeetingStorage(meeting_id)
    candidates = (
        storage.path_for("transcript", "meeting_transcript_ready.json"),
        storage.path_for("transcript", "speaker_transcript.json"),
        storage.path_for("transcript", "final_transcript.json"),
        storage.root / "results" / "transcripts" / "meeting_transcript_ready.json",
        storage.root / "results" / "transcripts" / "speaker_transcript.json",
    )

    for candidate in candidates:
        if candidate.exists():
            payload = _read_json(candidate)
            if payload is not None:
                try:
                    from app.services.transcript.transcript_index import (
                        build_transcript_index,
                        persist_transcript_index,
                    )
                    utterances = build_transcript_index(payload, meeting_id)
                    persist_transcript_index(utterances, meeting_id)
                    print(f"✓ Transcript indexed: {len(utterances)} utterances persisted to DB & storage")
                    return utterances
                except Exception as exc:
                    print(f"⚠ Warning: Failed to index transcript: {exc}")
                    return None
    return None


# ============================================================
# LOAD TRANSCRIPT FOR FRONTEND
# ============================================================

def load_frontend_transcript(
    meeting_id: str
):
    storage = MeetingStorage(meeting_id)
    paths = get_result_paths(
        meeting_id
    )

    payload = None
    source = None

    # Prefer the most complete final handoff transcript.
    candidates = (
        paths["ready"],
        paths["speaker"],
        paths["final"],
        storage.root / "results" / "transcripts" / "meeting_transcript_ready.json",
        storage.root / "results" / "transcripts" / "speaker_transcript.json",
        storage.root / "results" / "transcripts" / "final_transcript.json",
    )

    for candidate in candidates:

        if candidate.exists():

            payload = _read_json(
                candidate
            )

            source = candidate

            if payload is not None:
                break

    if payload is None:

        raise RecordedPipelineError(
            "The recorded pipeline completed "
            "but no transcript artifact was found."
        )

    segments = _extract_segments(
        payload
    )

    if not segments:

        raise RecordedPipelineError(
            "The transcript artifact did not "
            "contain usable speech segments."
        )

    full_text = " ".join(
        str(
            segment[
                "text"
            ]
        ).strip()

        for segment in segments

        if str(
            segment.get(
                "text"
            )
            or ""
        ).strip()
    )

    language = _detect_language(
        payload,
        segments
    )

    return {

        "file_id":
            meeting_id,

        "language":
            language,

        "segment_count":
            len(
                segments
            ),

        "segments":
            segments,

        "full_text":
            full_text,

        "source_file":
            source.name
            if source
            else None,

        "status":
            "transcribed",

        "pipeline_features":
            {
                "validation":
                    True,

                "media_inspection":
                    True,

                "normalization":
                    True,

                "audio_quality":
                    True,

                "long_audio_chunking":
                    True,

                "multilingual_asr":
                    True,

                "speaker_diarization":
                    True,

                "speaker_attribution":
                    True,

                "reliability_flags":
                    True
            }
    }


# ============================================================
# RUN EXISTING RECORDED PIPELINE
# ============================================================

def run_post_transcript_stages(meeting_id: str, mode: str | None = None) -> dict:
    from app.core import db
    from app.services.transcript.transcript_index import (
        build_transcript_index,
        persist_transcript_index,
    )
    from app.services.intelligence.topic_segmenter import TopicSegmenter
    from app.services.intelligence.extraction_pipeline import run_extraction

    from app.core.stage_timings import timed_stage

    storage = MeetingStorage(meeting_id)
    db.init_db()
    try:
        with timed_stage("transcript_index") as index_stage:
            payload = json.loads(
                storage.path_for("transcript", "meeting_transcript_ready.json")
                .read_text(encoding="utf-8")
            )
            utterances = build_transcript_index(payload, meeting_id)
            persist_transcript_index(utterances, meeting_id)
            index_stage.detail["utterances"] = len(utterances)
    except Exception as exc:
        logger.exception("Post-transcript stage 'index' failed for %s", meeting_id)
        raise PostTranscriptStageError("index", exc) from exc
    try:
        with timed_stage("topic_segmentation") as topic_stage:
            frontend_path = storage.path_for("transcript", "frontend_transcript.json")
            segmenter = TopicSegmenter(output_dir=str(storage.intelligence))
            segmenter.process(str(frontend_path))
            _describe_topics(topic_stage, segmenter)
    except Exception as exc:
        logger.exception("Post-transcript stage 'topics' failed for %s", meeting_id)
        raise PostTranscriptStageError("topics", exc) from exc
    try:
        with timed_stage("extraction") as extraction_stage:
            stats = run_extraction(meeting_id, mode=mode)
            _describe_extraction(extraction_stage, stats)
        logger.info(
            "Extraction progress for %s: accepted=%d dropped=%d",
            meeting_id, stats.get("accepted", 0), stats.get("dropped", 0),
        )
        return stats
    except Exception as exc:
        logger.exception("Post-transcript stage 'extraction' failed for %s", meeting_id)
        raise PostTranscriptStageError("extraction", exc) from exc


def _describe_topics(handle, segmenter) -> None:
    """Topic count, and an explicit fallback when LLM titles were not used."""
    mode = getattr(segmenter, "title_mode", "extractive")
    handle.detail["title_mode"] = mode
    if mode != "llm":
        return
    counts = getattr(segmenter, "title_counts", None) or {}
    handle.detail["llm_titles"] = counts.get("llm", 0)
    handle.detail["extractive_title_fallbacks"] = counts.get("fallback", 0)
    reason = getattr(segmenter, "title_fallback_reason", None)
    if reason or counts.get("fallback"):
        handle.status = "fallback"
        handle.detail["title_fallback_reason"] = str(
            reason or f"{counts.get('fallback')} LLM title(s) rejected by the word check"
        )[:300]


def _describe_extraction(handle, stats: dict) -> None:
    """Which extractor produced the claims, and what the LLM cost."""
    keys = (
        "status", "mode_requested", "mode_used", "extractor_version", "accepted", "dropped",
        "llm_provider", "llm_model", "llm_windows", "llm_requests", "llm_provider_seconds",
        "llm_cache_hits",
    )
    handle.detail.update({k: stats[k] for k in keys if stats.get(k) is not None})
    if stats.get("fallback_reason"):
        handle.detail["fallback_reason"] = str(stats["fallback_reason"])[:300]
    if stats.get("status") == "failed":
        handle.status = "failed"
        handle.detail["reason"] = str(stats.get("reason") or "")[:300]
    elif stats.get("mode_requested") == "llm" and stats.get("mode_used") != "llm":
        handle.status = "fallback"


_run_post_transcript_stages = run_post_transcript_stages

def run_recorded_pipeline(
    meeting_id: str,
    input_file: Path,
    run_post_transcript: bool = True,
):

    storage = MeetingStorage(meeting_id)
    storage.ensure_dirs()
    from app.core import db
    db.init_db()
    db.insert_meeting(meeting_id, filename=input_file.name, status="processing")

    status_file = storage.path_for("root", "pipeline_status.json")

    started_at = time.time()

    # ========================================================
    # CONTENT HASH
    # ========================================================

    print(
        "\nCalculating recording fingerprint..."
    )

    hash_started = time.time()

    content_hash = _sha256_file(
        input_file
    )

    # A LiveKit recording with usable participant tracks is attributed from
    # those tracks, so its cache entry must differ from a mixed-recording
    # result for the same recording.wav (and change if the tracks change).
    if Path(input_file).resolve() == (storage.recording / "recording.wav").resolve():
        from app.services.live.track_transcription import track_cache_key

        track_key = track_cache_key(storage.recording)
        if track_key:
            content_hash = hashlib.sha256(
                f"{content_hash}|{track_key}".encode("utf-8")
            ).hexdigest()

    hash_seconds = round(
        time.time()
        - hash_started,
        2
    )

    print(
        f"✓ Recording hash: "
        f"{content_hash[:16]}..."
    )

    print(
        f"✓ Hashing time: "
        f"{hash_seconds}s"
    )

    _write_json(
        status_file,
        {
            "file_id":
                meeting_id,

            "status":
                "processing",

            "started_at":
                started_at,

            "content_hash":
                content_hash,

            "cache_hit":
                False
        }
    )

    # ========================================================
    # FAST PATH: EXACT RECORDING ALREADY PROCESSED
    # ========================================================

    with _CACHE_LOCK:

        cache_hit = _content_cache_enabled() and _cache_is_complete(
            content_hash
        )

        if cache_hit:

            print(
                "\n✓ CACHE HIT"
            )

            print(
                "  Same recording was already "
                "processed successfully."
            )

            print(
                "  Reusing ASR + diarization + "
                "reliability artifacts."
            )

            _restore_cached_results(
                meeting_id,
                content_hash
            )

    if cache_hit:

        transcript = (
            load_frontend_transcript(
                meeting_id
            )
        )

        frontend_transcript_file = storage.path_for(
            "transcript",
            "frontend_transcript.json"
        )

        _write_json(
            frontend_transcript_file,
            transcript
        )
        _write_json(
            storage.root / "frontend_transcript.json",
            transcript
        )
        if run_post_transcript:
            run_post_transcript_stages(meeting_id)

        finished_at = time.time()

        _write_json(
            status_file,
            {
                "file_id":
                    meeting_id,

                "status":
                    "completed",

                "started_at":
                    started_at,

                "finished_at":
                    finished_at,

                "processing_seconds":
                    round(
                        finished_at
                        - started_at,
                        2
                    ),

                "content_hash":
                    content_hash,

                "cache_hit":
                    True,

                "hashing_seconds":
                    hash_seconds,

                "frontend_transcript":
                    str(
                        frontend_transcript_file
                    )
            }
        )

        print(
            f"✓ Cached transcript restored "
            f"in {round(finished_at - started_at, 2)}s"
        )

        return transcript

    # ========================================================
    # CACHE MISS: RUN ORIGINAL STRONG PIPELINE
    # ========================================================

    print(
        "\nCache miss — processing recording "
        "through the full pipeline."
    )

    cmd_env = env.copy()
    cmd_env["MEETING_ID"] = meeting_id
    cmd_env["MEET2MOM_MEETING_ID"] = meeting_id

    command = [
        sys.executable,
        "-u",
        "-m",
        "scripts.run_recorded_pipeline",
        str(
            input_file.resolve()
        ),
        "--meeting-id",
        meeting_id,
        # Post-transcript stages run once, in this process, after the
        # subprocess (run_post_transcript below or the job runner).
        "--skip-post-transcript",
    ]

    print(
        "\nLaunching recorded pipeline:"
    )

    print(
        " ".join(
            command
        )
    )

    print(
        f"Project root: {PROJECT_ROOT}"
    )

    print(
        f"PYTHONPATH: {cmd_env.get('PYTHONPATH')}"
    )

    # --------------------------------------------------------
    # Pipeline execution with per-meeting isolation
    # --------------------------------------------------------

    with _PIPELINE_LOCK:

        if speech_pipeline_mode() == "inprocess":
            try:
                _run_speech_pipeline_inprocess(meeting_id, input_file)
            except Exception as error:
                error_message = f"{type(error).__name__}: {error}"
                _write_json(
                    status_file,
                    {
                        "file_id": meeting_id,
                        "status": "failed",
                        "started_at": started_at,
                        "finished_at": time.time(),
                        "content_hash": content_hash,
                        "cache_hit": False,
                        "speech_pipeline": "inprocess",
                        "error": error_message,
                    }
                )
                raise RecordedPipelineError(error_message) from error
        else:
            process = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                env=cmd_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )

            output_lines = []

            if process.stdout is not None:

                for line in process.stdout:

                    output_lines.append(
                        line
                    )

                    print(
                        line,
                        end="",
                        flush=True
                    )

            return_code = (
                process.wait()
            )

            combined_output = (
                "".join(
                    output_lines
                ).strip()
            )

            # ----------------------------------------------------
            # Pipeline failure
            # ----------------------------------------------------

            if return_code != 0:

                error_message = (
                    combined_output
                    or
                    "Recorded pipeline failed."
                )

                _write_json(
                    status_file,
                    {
                        "file_id":
                            meeting_id,

                        "status":
                            "failed",

                        "started_at":
                            started_at,

                        "finished_at":
                            time.time(),

                        "return_code":
                            return_code,

                        "content_hash":
                            content_hash,

                        "cache_hit":
                            False,

                        "error":
                            error_message
                    }
                )

                raise RecordedPipelineError(
                    error_message
                )

            # ----------------------------------------------------
            # Snapshot shared output into this meeting folder
            # ----------------------------------------------------

        copied = _snapshot_outputs(
            meeting_id
        )

    # --------------------------------------------------------
    # Build and persist transcript index immediately after speaker merging
    # --------------------------------------------------------
    # ========================================================
    # LOAD RESULT FOR FRONTEND
    # ========================================================

    try:

        transcript = (
            load_frontend_transcript(
                meeting_id
            )
        )

    except Exception as error:

        _write_json(
            status_file,
            {
                "file_id":
                    meeting_id,

                "status":
                    "failed",

                "started_at":
                    started_at,

                "finished_at":
                    time.time(),

                "content_hash":
                    content_hash,

                "cache_hit":
                    False,

                "error":
                    str(
                        error
                    ),

                "copied_artifacts":
                    copied
            }
        )

        raise

    # ========================================================
    # SAVE FRONTEND-FRIENDLY VERSION
    # ========================================================

    frontend_transcript_file = storage.path_for(
        "transcript",
        "frontend_transcript.json"
    )

    _write_json(
        frontend_transcript_file,
        transcript
    )
    _write_json(
        storage.root / "frontend_transcript.json",
        transcript
    )
    if run_post_transcript:
        run_post_transcript_stages(meeting_id)

    # ========================================================
    # SAVE COMPLETED RESULT INTO CONTENT-HASH CACHE
    # ========================================================

    cache_saved = False

    try:

        with _CACHE_LOCK:

            # ENABLE_PIPELINE_CACHE=false: neither reuse nor save results.
            cache_saved = (
                _content_cache_enabled()
                and
                _save_results_to_cache(
                    meeting_id,
                    content_hash
                )
            )

        if cache_saved:

            print(
                "\n✓ Completed pipeline artifacts "
                "saved to content-hash cache."
            )

    except Exception as cache_error:

        # Cache failure must never destroy an otherwise successful
        # transcription result.
        print(
            "\n⚠ Cache save failed: "
            f"{cache_error}"
        )

    # ========================================================
    # SUCCESS STATUS
    # ========================================================

    finished_at = time.time()

    _write_json(
        status_file,
        {
            "file_id":
                meeting_id,

            "status":
                "completed",

            "started_at":
                started_at,

            "finished_at":
                finished_at,

            "processing_seconds":
                round(
                    finished_at
                    - started_at,
                    2
                ),

            "content_hash":
                content_hash,

            "cache_hit":
                False,

            "cache_saved":
                cache_saved,

            "hashing_seconds":
                hash_seconds,

            "copied_artifacts":
                copied,

            "frontend_transcript":
                str(
                    frontend_transcript_file
                )
        }
    )

    return transcript


# ============================================================
# PIPELINE STATUS
# ============================================================

def get_pipeline_status(
    meeting_id: str
):

    storage = MeetingStorage(meeting_id)
    status_file = storage.path_for(
        "root",
        "pipeline_status.json"
    )

    if not status_file.exists():

        return {
            "file_id":
                meeting_id,

            "status":
                "not_started"
        }

    result = _read_json(
        status_file
    )

    if result is None:

        return {
            "file_id":
                meeting_id,

            "status":
                "unknown"
        }

    return result


# ============================================================
# EXTRACTION STAGE
# ============================================================

def run_extraction_stage(meeting_id: str, mode: str | None = None) -> dict:
    """Run the validated extraction stage for a meeting."""
    from app.services.intelligence.extraction_pipeline import run_extraction
    return run_extraction(meeting_id, mode=mode)


run_extraction = run_extraction_stage