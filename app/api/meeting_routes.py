import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile


router = APIRouter()


# ---------------------------------------------------------
# PROJECT PATHS
# ---------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

INPUT_DIR = PROJECT_ROOT / "input"
STORAGE_DIR = PROJECT_ROOT / "storage"

INPUT_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------
# SUPPORTED FORMATS
# ---------------------------------------------------------

ALLOWED_EXTENSIONS = {
    ".mp4",
    ".mp3",
    ".wav",
    ".m4a",
    ".webm",
    ".mov",
    ".avi",
}


# ---------------------------------------------------------
# UPLOAD MEETING
# ---------------------------------------------------------

@router.post("/upload")
async def upload_meeting(
    file: UploadFile = File(...)
):
    """
    Upload a recorded meeting.

    Current milestone:
        - validates file type
        - creates meeting_id
        - stores the uploaded recording

    Later:
        - trigger recorded pipeline
        - return processing status
        - maintain separate output folder per meeting
    """

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file has no filename."
        )

    extension = Path(file.filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file format: {extension}. "
                f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            )
        )

    meeting_id = str(uuid.uuid4())

    meeting_folder = STORAGE_DIR / meeting_id
    meeting_folder.mkdir(
        parents=True,
        exist_ok=True
    )

    upload_path = (
        meeting_folder
        / f"recording{extension}"
    )

    try:
        with upload_path.open("wb") as buffer:
            shutil.copyfileobj(
                file.file,
                buffer
            )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save meeting: {exc}"
        ) from exc

    finally:
        await file.close()

    return {
        "success": True,
        "meeting_id": meeting_id,
        "original_filename": file.filename,
        "stored_filename": upload_path.name,
        "message": "Meeting uploaded successfully."
    }