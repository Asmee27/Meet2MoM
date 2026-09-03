from pathlib import Path


# ==========================================================
# PROJECT PATHS
# ==========================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

INPUT_DIR = PROJECT_ROOT / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"

AUDIO_DIR = OUTPUT_DIR / "audio"
CHUNK_DIR = OUTPUT_DIR / "chunks"
METADATA_DIR = OUTPUT_DIR / "metadata"
TRANSCRIPT_DIR = OUTPUT_DIR / "transcripts"


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