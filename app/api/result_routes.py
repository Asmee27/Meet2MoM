import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException


router = APIRouter()


# ---------------------------------------------------------
# PROJECT PATHS
# ---------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

OUTPUT_DIR = PROJECT_ROOT / "output"

TRANSCRIPT_FILE = (
    OUTPUT_DIR
    / "transcripts"
    / "meeting_transcript_ready.json"
)

TOPICS_FILE = (
    OUTPUT_DIR
    / "intelligence"
    / "topics.json"
)

DECISIONS_FILE = (
    OUTPUT_DIR
    / "intelligence"
    / "decisions.json"
)


# ---------------------------------------------------------
# JSON READER
# ---------------------------------------------------------

def read_json_file(
    file_path: Path
) -> Any:
    """
    Safely load a JSON output generated
    by the Meet2MoM processing pipeline.
    """

    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"Result not found: "
                f"{file_path.name}"
            )
        )

    try:
        with file_path.open(
            "r",
            encoding="utf-8"
        ) as file:
            return json.load(file)

    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Invalid JSON file: "
                f"{file_path.name}"
            )
        ) from exc

    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                f"Could not read: "
                f"{file_path.name}"
            )
        ) from exc


# ---------------------------------------------------------
# TRANSCRIPT
# ---------------------------------------------------------

@router.get("/transcript")
def get_transcript():
    return read_json_file(
        TRANSCRIPT_FILE
    )


# ---------------------------------------------------------
# TOPICS
# ---------------------------------------------------------

@router.get("/topics")
def get_topics():
    return read_json_file(
        TOPICS_FILE
    )


# ---------------------------------------------------------
# DECISIONS
# ---------------------------------------------------------

@router.get("/decisions")
def get_decisions():
    return read_json_file(
        DECISIONS_FILE
    )


# ---------------------------------------------------------
# COMPLETE MEETING INTELLIGENCE
# ---------------------------------------------------------

@router.get("/all")
def get_all_results():

    result = {}

    if TRANSCRIPT_FILE.exists():
        result["transcript"] = (
            read_json_file(
                TRANSCRIPT_FILE
            )
        )

    if TOPICS_FILE.exists():
        result["topics"] = (
            read_json_file(
                TOPICS_FILE
            )
        )

    if DECISIONS_FILE.exists():
        result["decisions"] = (
            read_json_file(
                DECISIONS_FILE
            )
        )

    if not result:
        raise HTTPException(
            status_code=404,
            detail=(
                "No processed meeting results "
                "are currently available."
            )
        )

    return result