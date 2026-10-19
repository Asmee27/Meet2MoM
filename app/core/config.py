from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


# ==========================================================
# PROJECT PATHS
# ==========================================================

import os
import sys

INPUT_DIR = PROJECT_ROOT / "input"
STORAGE_ROOT = PROJECT_ROOT / "storage"
DB_PATH = STORAGE_ROOT / "meet2mom.db"

# LLM Configuration
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "stub")
LLM_MODEL = os.getenv("LLM_MODEL", "mock-v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", None)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", None)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", None)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", None)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", None)
# Seconds to wait for one LLM request before treating it as timed out.
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "60") or 60)
EXTRACTOR = os.getenv("EXTRACTOR", "llm").strip().lower()


def get_hf_token() -> str | None:
    """Resolve the Hugging Face token from either accepted variable name."""
    return os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN") or None

_active_meeting_id = (
    os.environ.get("MEETING_ID")
    or os.environ.get("MEET2MOM_MEETING_ID")
    or "default"
)

OUTPUT_DIR = STORAGE_ROOT / _active_meeting_id
AUDIO_DIR = OUTPUT_DIR / "audio"
CHUNK_DIR = OUTPUT_DIR / "chunks"
METADATA_DIR = OUTPUT_DIR / "metadata"
TRANSCRIPT_DIR = OUTPUT_DIR / "transcript"
DIARIZATION_DIR = OUTPUT_DIR / "diarization"


def set_active_meeting(meeting_id: str) -> None:
    global _active_meeting_id, OUTPUT_DIR, AUDIO_DIR, CHUNK_DIR, METADATA_DIR
    global TRANSCRIPT_DIR, DIARIZATION_DIR, FINAL_HANDOFF_FILE, PROCESSING_REPORT_FILE, PIPELINE_CACHE_FILE

    _active_meeting_id = meeting_id
    os.environ["MEETING_ID"] = meeting_id
    os.environ["MEET2MOM_MEETING_ID"] = meeting_id

    OUTPUT_DIR = STORAGE_ROOT / meeting_id
    AUDIO_DIR = OUTPUT_DIR / "audio"
    CHUNK_DIR = OUTPUT_DIR / "chunks"
    METADATA_DIR = OUTPUT_DIR / "metadata"
    TRANSCRIPT_DIR = OUTPUT_DIR / "transcript"
    DIARIZATION_DIR = OUTPUT_DIR / "diarization"

    FINAL_HANDOFF_FILE = TRANSCRIPT_DIR / "meeting_transcript_ready.json"
    PROCESSING_REPORT_FILE = METADATA_DIR / "processing_report.json"
    PIPELINE_CACHE_FILE = METADATA_DIR / "pipeline_cache.json"

    for mod_name, mod in list(sys.modules.items()):
        if mod and (mod_name.startswith("app.") or mod_name.startswith("scripts.")):
            if hasattr(mod, "AUDIO_DIR"):
                setattr(mod, "AUDIO_DIR", AUDIO_DIR)
            if hasattr(mod, "CHUNK_DIR"):
                setattr(mod, "CHUNK_DIR", CHUNK_DIR)
            if hasattr(mod, "METADATA_DIR"):
                setattr(mod, "METADATA_DIR", METADATA_DIR)
            if hasattr(mod, "TRANSCRIPT_DIR"):
                setattr(mod, "TRANSCRIPT_DIR", TRANSCRIPT_DIR)
            if hasattr(mod, "DIARIZATION_DIR"):
                setattr(mod, "DIARIZATION_DIR", DIARIZATION_DIR)
            if hasattr(mod, "FINAL_HANDOFF_FILE"):
                setattr(mod, "FINAL_HANDOFF_FILE", FINAL_HANDOFF_FILE)
            if hasattr(mod, "PROCESSING_REPORT_FILE"):
                setattr(mod, "PROCESSING_REPORT_FILE", PROCESSING_REPORT_FILE)
            if hasattr(mod, "PIPELINE_CACHE_FILE"):
                setattr(mod, "PIPELINE_CACHE_FILE", PIPELINE_CACHE_FILE)



# ==========================================================
# SUPPORTED INPUT TYPES
# ==========================================================

SUPPORTED_EXTENSIONS = {
    ".mp4",
    ".mp3",
    ".wav",
    ".m4a",
    ".webm",
    ".mov",
    ".mkv",
    ".aac",
    ".flac"
}


# ==========================================================
# AUDIO STANDARDIZATION
# ==========================================================

SAMPLE_RATE = 16000
CHANNELS = 1


# ==========================================================
# LONG MEETING SETTINGS
# ==========================================================

CHUNK_LENGTH_SECONDS = 300

# 8-second overlap protects sentence boundaries
CHUNK_OVERLAP_SECONDS = 8


# ==========================================================
# AUDIO ENHANCEMENT
# ==========================================================

ENABLE_AUDIO_ENHANCEMENT = True

HIGH_PASS_HZ = 80
LOW_PASS_HZ = 7600

DENOISE_LEVEL = 25


# ==========================================================
# WHISPER
# ==========================================================

WHISPER_MODEL = "small"

DEVICE = "cpu"
COMPUTE_TYPE = "int8"

BEAM_SIZE = 5
RETRY_BEAM_SIZE = 8

USE_VAD = True


# ==========================================================
# LANGUAGE SCOPE
# ==========================================================

TARGET_LANGUAGES = {
    "en": "English",
    "hi": "Hindi",
    "mr": "Marathi"
}

# IMPORTANT:
# Keep None so Whisper automatically detects language.
FORCE_LANGUAGE = None


# ==========================================================
# TECHNICAL / DOMAIN VOCABULARY
# ==========================================================

TECHNICAL_VOCABULARY = [
    "Meet2MoM",
    "Minutes of Meeting",
    "MoM",
    "API",
    "backend",
    "frontend",
    "database",
    "React",
    "FastAPI",
    "Python",
    "Whisper",
    "FFmpeg",
    "GitHub",
    "Docker",
    "Jenkins",
    "AWS",
    "Azure",
    "Google Cloud",
    "deployment",
    "testing",
    "production",
    "authentication",
    "machine learning",
    "artificial intelligence",
    "RAG",
    "LLM"
]


# Add known participant/project names here later.
PARTICIPANT_NAMES = [
    "Asmee",
    "Madhura",
    "Samarth",
    "Shashwat"
]


# ==========================================================
# CONFIDENCE
# ==========================================================

LOW_CONFIDENCE_THRESHOLD = -1.0
NO_SPEECH_THRESHOLD = 0.60
COMPRESSION_RATIO_THRESHOLD = 2.4
LOG_PROB_THRESHOLD = -1.0


# Retry chunk if too much transcript looks unreliable
LOW_CONFIDENCE_RETRY_RATIO = 0.20


# ==========================================================
# VAD
# ==========================================================

VAD_PARAMETERS = {
    "min_silence_duration_ms": 500,
    "speech_pad_ms": 300
}

# ==========================================================
# SPEAKER DIARIZATION
# ==========================================================

DIARIZATION_MODEL = (
    "pyannote/speaker-diarization-community-1"
)

DIARIZATION_DIR = OUTPUT_DIR / "diarization"

# Leave None when number of speakers is unknown.
KNOWN_NUM_SPEAKERS = None

# Optional bounds for automatic speaker counting.
MIN_SPEAKERS = 1
MAX_SPEAKERS = 10

# ==========================================================
# RELIABILITY / REVIEW SETTINGS
# ==========================================================

# Speaker attribution >= this value is considered strong
HIGH_SPEAKER_CONFIDENCE = 0.80

# Below this value, speaker identity is considered uncertain
LOW_SPEAKER_CONFIDENCE = 0.60

# Minimum overlap between two different speaker turns
# to consider it possible cross-talk
OVERLAP_DETECTION_SECONDS = 0.15

# Final downstream output
FINAL_HANDOFF_FILE = (
    TRANSCRIPT_DIR / "meeting_transcript_ready.json"
)

PROCESSING_REPORT_FILE = (
    METADATA_DIR / "processing_report.json"
)

# ==========================================================
# PERFORMANCE / CACHE SETTINGS
# ==========================================================

ENABLE_PIPELINE_CACHE = True

# Expensive stages that may be reused when their input
# recording has not changed.
CACHE_NORMALIZED_AUDIO = True
CACHE_TRANSCRIPTION = True
CACHE_DIARIZATION = True

# Cache information for identifying the processed recording.
PIPELINE_CACHE_FILE = (
    METADATA_DIR / "pipeline_cache.json"
)

# ==========================================================
# ASYNC JOB RUNNER & WORKER SETTINGS
# ==========================================================

# Execution mode: "inline" (default, runs in FastAPI BackgroundTasks) or "worker" (standalone process)
JOB_RUNNER = os.getenv("JOB_RUNNER", "inline").strip().lower()

WORKER_POLL_INTERVAL_SECONDS = float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "1.0"))
WORKER_STALE_TIMEOUT_SECONDS = float(os.getenv("WORKER_STALE_TIMEOUT_SECONDS", "300.0"))
WORKER_MAX_RETRIES = int(os.getenv("WORKER_MAX_RETRIES", "3"))

# Topic title generation mode: "extractive" (default) or "llm"
TOPIC_TITLES = os.getenv("TOPIC_TITLES", "extractive").strip().lower()

# ==========================================================
# LIVEKIT SETTINGS (Phase 4 Real Live Meetings)
# ==========================================================

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "").strip()
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "").strip()
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "").strip()
