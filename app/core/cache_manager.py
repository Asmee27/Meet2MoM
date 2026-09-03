import hashlib
import json
from pathlib import Path

from app.core.config import (
    ENABLE_PIPELINE_CACHE,
    PIPELINE_CACHE_FILE
)


class PipelineCacheManager:

    def __init__(self):
        PIPELINE_CACHE_FILE.parent.mkdir(
            parents=True,
            exist_ok=True
        )

    # ======================================================
    # FILE FINGERPRINT
    # ======================================================

    @staticmethod
    def calculate_fingerprint(file_path):

        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(
                f"Cannot fingerprint missing file: {file_path}"
            )

        sha256 = hashlib.sha256()

        # Read in blocks so even large meeting recordings
        # do not need to be loaded completely into RAM.
        with open(file_path, "rb") as file:

            while True:

                block = file.read(
                    1024 * 1024
                )

                if not block:
                    break

                sha256.update(block)

        stat = file_path.stat()

        return {
            "filename": file_path.name,
            "size_bytes": stat.st_size,
            "modified_time_ns": stat.st_mtime_ns,
            "sha256": sha256.hexdigest()
        }

    # ======================================================
    # LOAD CACHE
    # ======================================================

    @staticmethod
    def load_cache():

        if not PIPELINE_CACHE_FILE.exists():
            return {}

        try:

            with open(
                PIPELINE_CACHE_FILE,
                "r",
                encoding="utf-8"
            ) as file:

                return json.load(file)

        except (
            json.JSONDecodeError,
            OSError
        ):

            return {}

    # ======================================================
    # SAVE CACHE
    # ======================================================

    @staticmethod
    def save_cache(cache):

        with open(
            PIPELINE_CACHE_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                cache,
                file,
                indent=4,
                ensure_ascii=False
            )

    # ======================================================
    # START / VALIDATE MEETING
    # ======================================================

    def prepare_recording(
        self,
        recording_path
    ):

        if not ENABLE_PIPELINE_CACHE:

            return {
                "cache_enabled": False,
                "same_recording": False
            }

        current_fingerprint = (
            self.calculate_fingerprint(
                recording_path
            )
        )

        cache = self.load_cache()

        previous_fingerprint = (
            cache.get(
                "recording_fingerprint"
            )
        )

        same_recording = (
            previous_fingerprint
            == current_fingerprint
        )

        if not same_recording:

            # New/modified recording.
            # Existing stage cache must NOT be trusted.
            cache = {
                "recording_fingerprint":
                    current_fingerprint,

                "stages": {}
            }

            self.save_cache(
                cache
            )

            print(
                "✓ New or modified recording detected"
            )

            print(
                "  Previous processing cache invalidated"
            )

        else:

            print(
                "⚡ Same recording detected"
            )

            print(
                "  Existing processing cache is eligible for reuse"
            )

        return {
            "cache_enabled": True,
            "same_recording": same_recording,
            "fingerprint": current_fingerprint
        }

    # ======================================================
    # CHECK STAGE
    # ======================================================

    def is_stage_cached(
        self,
        stage_name,
        output_path
    ):

        if not ENABLE_PIPELINE_CACHE:
            return False

        output_path = Path(
            output_path
        )

        if not output_path.exists():
            return False

        cache = self.load_cache()

        stage = (
            cache
            .get("stages", {})
            .get(stage_name, {})
        )

        return (
            stage.get("completed", False)
            is True
        )

    # ======================================================
    # MARK STAGE COMPLETE
    # ======================================================

    def mark_stage_complete(
        self,
        stage_name,
        output_path
    ):

        if not ENABLE_PIPELINE_CACHE:
            return

        output_path = Path(
            output_path
        )

        cache = self.load_cache()

        cache.setdefault(
            "stages",
            {}
        )

        cache["stages"][
            stage_name
        ] = {
            "completed": True,
            "output_path": str(
                output_path
            )
        }

        self.save_cache(
            cache
        )

    # ======================================================
    # INVALIDATE ONE STAGE
    # ======================================================

    def invalidate_stage(
        self,
        stage_name
    ):

        cache = self.load_cache()

        stages = cache.get(
            "stages",
            {}
        )

        stages.pop(
            stage_name,
            None
        )

        cache["stages"] = stages

        self.save_cache(
            cache
        )