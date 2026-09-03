import sys
import time
from pathlib import Path

from app.core.config import (
    INPUT_DIR,
    TRANSCRIPT_DIR,
    DIARIZATION_DIR,
    CACHE_TRANSCRIPTION,
    CACHE_DIARIZATION
)

from app.core.cache_manager import PipelineCacheManager

from app.services.ingestion.audio_processor import AudioProcessor
from app.services.ingestion.audio_quality import AudioQualityAnalyzer
from app.services.transcription.transcriber import MeetingTranscriber
from app.services.diarization.diarizer import SpeakerDiarizer
from app.services.diarization.speaker_merger import SpeakerTranscriptMerger
from app.services.reporting.processing_report import ProcessingReportGenerator


def main():

    pipeline_start = time.perf_counter()

    print("\n" + "=" * 72)
    print("        Meet2MoM - Recorded Meeting Intelligence Pipeline")
    print("=" * 72)

    # ======================================================
    # INPUT RECORDING
    # ======================================================

    if len(sys.argv) > 1:
        input_file = Path(sys.argv[1]).resolve()
    else:
        input_file = (
            INPUT_DIR / "test_meeting.mp4"
        ).resolve()

    print(f"\nRecording: {input_file}")

    try:

        # ==================================================
        # CACHE INITIALIZATION
        # ==================================================

        cache_manager = PipelineCacheManager()

        cache_status = cache_manager.prepare_recording(
            input_file
        )

        same_recording = cache_status.get(
            "same_recording",
            False
        )

        # ==================================================
        # 1 — VALIDATION
        # ==================================================

        print("\n[1/9] Validating recording...")

        processor = AudioProcessor(
            input_file
        )

        processor.validate_input()

        # ==================================================
        # 2 — MEDIA INSPECTION
        # ==================================================

        print("\n[2/9] Inspecting media...")

        processor.inspect_recording()

        # ==================================================
        # 3 — AUDIO PREPROCESSING
        # ==================================================

        print("\n[3/9] Enhancing speech audio...")

        normalized_audio = (
            processor.normalize_audio()
        )

        # ==================================================
        # 4 — AUDIO QUALITY
        # ==================================================

        print("\n[4/9] Analyzing audio quality...")

        analyzer = AudioQualityAnalyzer()

        analyzer.analyze(
            normalized_audio
        )

        # ==================================================
        # 5 — LONG AUDIO HANDLING
        # ==================================================

        print(
            "\n[5/9] Preparing long-meeting chunks..."
        )

        metadata_file = (
            processor.create_chunks(
                normalized_audio
            )
        )

        # ==================================================
        # OUTPUT PATHS USED BY CACHE
        # ==================================================

        transcript_file = (
            TRANSCRIPT_DIR /
            "final_transcript.json"
        )

        diarization_file = (
            DIARIZATION_DIR /
            "speaker_turns.json"
        )

        # ==================================================
        # 6 — MULTILINGUAL ASR
        # ==================================================

        print(
            "\n[6/9] Running multilingual ASR..."
        )

        transcript_cached = (
            same_recording
            and
            CACHE_TRANSCRIPTION
            and
            cache_manager.is_stage_cached(
                "transcription",
                transcript_file
            )
        )

        if transcript_cached:

            print(
                "⚡ Cached transcript found"
            )

            print(
                "  Skipping Whisper transcription"
            )

        else:

            asr_start = time.perf_counter()

            transcriber = MeetingTranscriber()

            transcript_file = (
                transcriber.transcribe(
                    metadata_file
                )
            )

            cache_manager.mark_stage_complete(
                "transcription",
                transcript_file
            )

            asr_time = (
                time.perf_counter()
                - asr_start
            )

            print(
                f"✓ ASR processing time: "
                f"{asr_time:.2f} seconds"
            )

        # ==================================================
        # 7 — SPEAKER DIARIZATION
        # ==================================================

        print(
            "\n[7/9] Detecting speakers..."
        )

        diarization_cached = (
            same_recording
            and
            CACHE_DIARIZATION
            and
            cache_manager.is_stage_cached(
                "diarization",
                diarization_file
            )
        )

        if diarization_cached:

            print(
                "⚡ Cached diarization found"
            )

            print(
                "  Skipping pyannote diarization"
            )

        else:

            diarization_start = (
                time.perf_counter()
            )

            diarizer = SpeakerDiarizer()

            diarization_file = (
                diarizer.diarize(
                    normalized_audio
                )
            )

            cache_manager.mark_stage_complete(
                "diarization",
                diarization_file
            )

            diarization_time = (
                time.perf_counter()
                - diarization_start
            )

            print(
                f"✓ Diarization processing time: "
                f"{diarization_time:.2f} seconds"
            )

        # ==================================================
        # 8 — RELIABILITY-AWARE SPEAKER ATTRIBUTION
        # ==================================================

        print(
            "\n[8/9] Running reliability-aware "
            "speaker attribution..."
        )

        merger = SpeakerTranscriptMerger()

        speaker_transcript = (
            merger.merge(
                transcript_file,
                diarization_file
            )
        )

        # ==================================================
        # 9 — PROCESSING REPORT
        # ==================================================

        print(
            "\n[9/9] Generating processing report..."
        )

        report_generator = (
            ProcessingReportGenerator()
        )

        report_file = (
            report_generator.generate()
        )

        # ==================================================
        # TOTAL TIME
        # ==================================================

        total_time = (
            time.perf_counter()
            - pipeline_start
        )

        minutes = int(
            total_time // 60
        )

        seconds = (
            total_time % 60
        )

        # ==================================================
        # SUCCESS
        # ==================================================

        print("\n" + "=" * 72)

        print(
            "             Meet2MoM Recorded Pipeline SUCCESS ✓"
        )

        print("=" * 72)

        print(
            f"\nTotal processing time: "
            f"{minutes}m {seconds:.2f}s"
        )

        print(
            "\nSpeaker-attributed transcript:"
        )

        print(
            speaker_transcript
        )

        print(
            "\nProcessing report:"
        )

        print(
            report_file
        )

    except KeyboardInterrupt:

        print(
            "\n⚠ Processing interrupted."
        )

        print(
            "Completed processing stages "
            "remain recoverable."
        )

    except Exception as error:

        print(
            "\n✗ MEET2MOM PIPELINE FAILED"
        )

        print(
            f"Reason: {error}"
        )

        sys.exit(1)


if __name__ == "__main__":
    main()