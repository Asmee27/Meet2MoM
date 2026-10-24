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
# MEET2MOM_STORAGE_ROOT moves all meeting data and the database (benchmarks,
# deployments); unset, it is the repository's storage/ folder.
STORAGE_ROOT = Path(os.getenv("MEET2MOM_STORAGE_ROOT") or (PROJECT_ROOT / "storage")).resolve()
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
# gemini-3.8-flash measured 51-68 s per request (Oct 2026), so 60 s is too short.
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "180") or 180)
# Extra attempts after a timed-out LLM request. A slow model usually times
# out again on the same input, so this is lower than the 429/5xx retries.
LLM_TIMEOUT_RETRIES = int(os.getenv("LLM_TIMEOUT_RETRIES", "1") or 0)
# Hard cap on all LLM calls for one meeting (every window, retry and backoff).
# When it runs out, extraction falls back to the local extractor and says so.
LLM_EXTRACTION_BUDGET_SECONDS = float(os.getenv("LLM_EXTRACTION_BUDGET_SECONDS", "600") or 0)
EXTRACTOR = os.getenv("EXTRACTOR", "llm").strip().lower()


def _env_flag(name: str, default: bool) -> bool:
    """true/false style setting from the environment; unset or empty keeps the default."""
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")


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

# Overridable from .env (Phase 5); the defaults are the benchmarked setup.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small").strip() or "small"

DEVICE = os.getenv("WHISPER_DEVICE", "cpu").strip() or "cpu"
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8").strip() or "int8"

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

# WHISPER_LANGUAGE (e.g. en, hi, mr) skips per-track language detection, which
# costs about 14 s per track on a 2-core laptop (Phase 6 measurement). Leave it
# empty for automatic detection, e.g. for code-switched meetings.
FORCE_LANGUAGE = (os.getenv("WHISPER_LANGUAGE") or "").strip().lower() or None

# Whisper initial prompt (Phase 6 transcript-quality fix):
#   none (default): no prompt
#   language: a short instruction to keep the spoken language
#   vocabulary: the language instruction plus TECHNICAL_VOCABULARY and
#     PARTICIPANT_NAMES (the old behaviour). On a real meeting this replaced an
#     unclear "by Friday" with "by FFmpeg", a word from the vocabulary list.
WHISPER_PROMPT = (os.getenv("WHISPER_PROMPT") or "none").strip().lower()
# Split a decoded Whisper segment where its words are separated by a pause
# longer than this (seconds). Voice-activity filtering glues separate speech
# chunks together, so one segment could otherwise span 30 s of silence.
WHISPER_SPLIT_GAP_SECONDS = float(os.getenv("WHISPER_SPLIT_GAP_SECONDS", "1.5") or 1.5)


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

ENABLE_PIPELINE_CACHE = _env_flag("ENABLE_PIPELINE_CACHE", True)

# Expensive stages that may be reused when their input
# recording has not changed.
CACHE_NORMALIZED_AUDIO = True
CACHE_TRANSCRIPTION = _env_flag("CACHE_TRANSCRIPTION", True)
CACHE_DIARIZATION = _env_flag("CACHE_DIARIZATION", True)

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

# Where the speech pipeline (audio, ASR, speaker attribution) runs (Phase 6):
#   subprocess: a fresh Python process per meeting (isolated; reloads libraries and models)
#   inprocess:  inside the job runner, reusing loaded libraries and models
#   auto:       inprocess inside scripts/worker.py, subprocess in the API process
SPEECH_PIPELINE = os.getenv("SPEECH_PIPELINE", "auto").strip().lower()
# Load Whisper, pyannote, embeddings and NLI when the worker starts, so even the
# first meeting avoids model start-up (only with the in-process speech pipeline).
WORKER_PRELOAD_MODELS = _env_flag("WORKER_PRELOAD_MODELS", True)

# Accounts and meeting access (Phase 7). On by default: every /api meeting
# route needs a signed-in member. false = the old open local mode.
AUTH_REQUIRED = _env_flag("AUTH_REQUIRED", True)
# Set true when the API is served over HTTPS so the session cookie is Secure.
SESSION_COOKIE_SECURE = _env_flag("SESSION_COOKIE_SECURE", False)

# Topic title generation mode: "extractive" (default) or "llm"
TOPIC_TITLES = os.getenv("TOPIC_TITLES", "extractive").strip().lower()
# Cap on all LLM topic-title requests of one meeting (TOPIC_TITLES=llm).
TOPIC_TITLE_LLM_BUDGET_SECONDS = float(os.getenv("TOPIC_TITLE_LLM_BUDGET_SECONDS", "60") or 0)

# ==========================================================
# LIVEKIT SETTINGS (Phase 4 Real Live Meetings)
# ==========================================================

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "").strip()
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "").strip()
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "").strip()

# ---------------------------------------------------------------------------
# Transactional email (Phase 8.4)
# ---------------------------------------------------------------------------
# console (default): development mode. Nothing is sent; each message is logged
#   without its links and marked "not sent (development mode)".
# brevo: Brevo transactional API. Needs BREVO_API_KEY and a sender address
#   verified in Brevo (EMAIL_SENDER_ADDRESS); see docs/phase-8/PHASE_8_4_EMAIL.md.
EMAIL_PROVIDER = (os.getenv("EMAIL_PROVIDER") or "console").strip().lower()
BREVO_API_KEY = os.getenv("BREVO_API_KEY") or None
BREVO_API_URL = (os.getenv("BREVO_API_URL") or "https://api.brevo.com/v3/smtp/email").strip()
EMAIL_SENDER_ADDRESS = (os.getenv("EMAIL_SENDER_ADDRESS") or "").strip() or None
EMAIL_SENDER_NAME = (os.getenv("EMAIL_SENDER_NAME") or "Meet2MoM").strip()
# Shared secret Brevo sends with delivery webhooks (custom header
# X-Meet2MoM-Webhook-Token, or "Authorization: Bearer <token>"). Unset: the
# webhook endpoint is disabled and statuses stop at "sent to provider".
BREVO_WEBHOOK_TOKEN = os.getenv("BREVO_WEBHOOK_TOKEN") or None
EMAIL_TIMEOUT_SECONDS = float(os.getenv("EMAIL_TIMEOUT_SECONDS", "15") or 15)
EMAIL_MAX_ATTEMPTS = int(os.getenv("EMAIL_MAX_ATTEMPTS", "4") or 4)
# Where queued email is sent from: api (a background thread in the API
# process), worker (scripts/worker.py), or off (nothing sends; tests).
EMAIL_DISPATCH = (os.getenv("EMAIL_DISPATCH") or "api").strip().lower()
# The address people open links on (the frontend), used in emailed links.
PUBLIC_APP_URL = (os.getenv("PUBLIC_APP_URL") or "http://localhost:5173").strip().rstrip("/")
