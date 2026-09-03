import json
from pathlib import Path

from app.core.config import (
    METADATA_DIR,
    TRANSCRIPT_DIR,
    PROCESSING_REPORT_FILE
)


class ProcessingReportGenerator:

    @staticmethod
    def load_json(path):
        path = Path(path)

        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as file:
                return json.load(file)

        except Exception:
            return None

    def generate(self):

        recording_info = (
            self.load_json(
                METADATA_DIR / "recording_info.json"
            )
            or {}
        )

        audio_quality = (
            self.load_json(
                METADATA_DIR / "audio_quality.json"
            )
            or {}
        )

        chunk_metadata = (
            self.load_json(
                METADATA_DIR / "chunk_metadata.json"
            )
            or []
        )

        transcript = (
            self.load_json(
                TRANSCRIPT_DIR / "speaker_transcript.json"
            )
            or []
        )

        # --------------------------------------------------
        # SPEAKERS
        # --------------------------------------------------

        speakers = sorted({
            item.get("speaker")
            for item in transcript
            if item.get("speaker")
            not in (None, "UNKNOWN")
        })

        # --------------------------------------------------
        # RELIABILITY STATISTICS
        # --------------------------------------------------

        low_asr = sum(
            1
            for item in transcript
            if item.get("confidence_flag") == "low"
        )

        uncertain_speaker = sum(
            1
            for item in transcript
            if item.get("speaker_confidence_flag")
            == "uncertain"
        )

        overlap_segments = sum(
            1
            for item in transcript
            if item.get("possible_overlap", False)
        )

        review_segments = sum(
            1
            for item in transcript
            if item.get("requires_review", False)
        )

        failed_chunks = sum(
            1
            for item in chunk_metadata
            if item.get("status") == "failed"
        )

        total_segments = len(transcript)

        review_rate = (
            (review_segments / total_segments) * 100
            if total_segments
            else 0
        )

        # --------------------------------------------------
        # REPORT
        # --------------------------------------------------

        report = {
            "recording": {
                "duration_seconds":
                    recording_info.get("duration_seconds"),

                "container":
                    recording_info.get("container"),

                "original_audio_codec":
                    recording_info.get("audio_codec"),

                "original_sample_rate":
                    recording_info.get("sample_rate"),

                "original_channels":
                    recording_info.get("channels")
            },

            "audio_quality": {
                "quality":
                    audio_quality.get("quality"),

                "mean_volume_db":
                    audio_quality.get("mean_volume_db"),

                "warnings":
                    audio_quality.get("warnings", [])
            },

            "processing": {
                "total_chunks":
                    len(chunk_metadata),

                "failed_chunks":
                    failed_chunks,

                "resume_supported":
                    True
            },

            "speech_recognition": {
                "total_segments":
                    total_segments,

                "low_confidence_segments":
                    low_asr
            },

            "speaker_diarization": {
                "detected_speakers":
                    len(speakers),

                "speaker_labels":
                    speakers,

                "uncertain_speaker_segments":
                    uncertain_speaker,

                "possible_overlap_segments":
                    overlap_segments
            },

            "reliability": {
                "segments_requiring_review":
                    review_segments,

                "review_rate_percent":
                    round(review_rate, 2)
            }
        }

        with open(
            PROCESSING_REPORT_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                report,
                file,
                indent=4,
                ensure_ascii=False
            )

        print("\n✓ Processing report generated")

        print(
            f"  Speakers: {len(speakers)}"
        )

        print(
            f"  Low ASR confidence: {low_asr}"
        )

        print(
            f"  Possible overlap: {overlap_segments}"
        )

        print(
            f"  Review required: "
            f"{review_segments}/{total_segments}"
        )

        print(
            f"✓ Report: {PROCESSING_REPORT_FILE}"
        )

        return PROCESSING_REPORT_FILE