import argparse
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

# Parse arguments early to set active meeting before downstream imports
parser = argparse.ArgumentParser(description="Meet2MoM - Recorded Meeting Intelligence Pipeline")
parser.add_argument("input_file", nargs="?", default=None, help="Path to input recording")
parser.add_argument("--meeting-id", dest="meeting_id", default=None, help="Meeting ID for isolated storage")
parser.add_argument(
    "--skip-post-transcript",
    dest="skip_post_transcript",
    action="store_true",
    help="Stop after the transcript (the calling process runs extraction and verification)",
)
args, _ = parser.parse_known_args()

meeting_id = args.meeting_id or uuid4().hex
os.environ["MEETING_ID"] = meeting_id
os.environ["MEET2MOM_MEETING_ID"] = meeting_id

from app.core.console import make_stdio_unicode_safe

make_stdio_unicode_safe()

from app.core.config import (
    INPUT_DIR,
    TRANSCRIPT_DIR,
    DIARIZATION_DIR,
    CACHE_TRANSCRIPTION,
    CACHE_DIARIZATION,
    set_active_meeting,
)
from app.core.storage import MeetingStorage

from app.core.cache_manager import PipelineCacheManager

from app.services.ingestion.audio_processor import AudioProcessor
from app.services.ingestion.audio_quality import AudioQualityAnalyzer
from app.services.transcription.transcriber import MeetingTranscriber
from app.services.diarization.diarizer import SpeakerDiarizer
from app.services.diarization.speaker_merger import SpeakerTranscriptMerger
from app.services.reporting.processing_report import ProcessingReportGenerator


def main():

    pipeline_start = time.perf_counter()

    storage = MeetingStorage(meeting_id)
    storage.ensure_dirs()
    set_active_meeting(meeting_id)

    print("\n" + "=" * 72)
    print("        Meet2MoM - Recorded Meeting Intelligence Pipeline")
    print("=" * 72)
    print(f"Meeting ID: {meeting_id}")
    print(f"Storage root: {storage.root}")

    # ======================================================
    # INPUT RECORDING
    # ======================================================

    if args.input_file:
        input_file = Path(args.input_file).resolve()
    elif len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
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

        transcript_file = storage.path_for(
            "transcript",
            "final_transcript.json"
        )

        diarization_file = storage.path_for(
            "diarization",
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

        # Complete the shared post-transcript path for direct CLI callers.
        from app.services.pipeline.recorded_pipeline_adapter import (
            load_frontend_transcript,
            run_post_transcript_stages,
        )
        frontend_transcript = load_frontend_transcript(meeting_id)
        frontend_json = json.dumps(frontend_transcript, ensure_ascii=False, indent=2)
        storage.path_for("transcript", "frontend_transcript.json").write_text(
            frontend_json,
            encoding="utf-8",
        )
        (storage.root / "frontend_transcript.json").write_text(
            frontend_json,
            encoding="utf-8",
        )
        if not args.skip_post_transcript:
            from app.services.pipeline.intelligence_service import ensure_verification

            run_post_transcript_stages(meeting_id)
            ensure_verification(meeting_id, force=True)

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