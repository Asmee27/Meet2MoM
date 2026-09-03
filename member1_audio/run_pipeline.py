import sys
from pathlib import Path

from app.core.config import INPUT_DIR

from app.services.ingestion.audio_processor import (
    AudioProcessor
)

from app.services.ingestion.audio_quality import (
    AudioQualityAnalyzer
)

from app.services.transcription.transcriber import (
    MeetingTranscriber
)

from app.services.diarization.diarizer import (
    SpeakerDiarizer
)

from app.services.diarization.speaker_merger import (
    SpeakerTranscriptMerger
)

from app.services.reporting.processing_report import (
    ProcessingReportGenerator
)


def main():

    print(
        "\n" + "=" * 72
    )

    print(
        "     Meet2MoM - Reliable Speaker Intelligence v4.1"
    )

    print(
        "=" * 72
    )

    if len(sys.argv) > 1:

        input_file = Path(
            sys.argv[1]
        )

    else:

        input_file = (
            INPUT_DIR /
            "test_meeting.mp4"
        )

    print(
        f"\nRecording: "
        f"{input_file}"
    )

    try:

        # ==================================================
        # 1
        # ==================================================

        print(
            "\n[1/9] "
            "Validating recording..."
        )

        processor = (
            AudioProcessor(
                input_file
            )
        )

        processor.validate_input()

        # ==================================================
        # 2
        # ==================================================

        print(
            "\n[2/9] "
            "Inspecting media..."
        )

        processor.inspect_recording()

        # ==================================================
        # 3
        # ==================================================

        print(
            "\n[3/9] "
            "Enhancing speech audio..."
        )

        normalized_audio = (
            processor.normalize_audio()
        )

        # ==================================================
        # 4
        # ==================================================

        print(
            "\n[4/9] "
            "Analyzing audio quality..."
        )

        analyzer = (
            AudioQualityAnalyzer()
        )

        analyzer.analyze(
            normalized_audio
        )

        # ==================================================
        # 5
        # ==================================================

        print(
            "\n[5/9] "
            "Preparing long-meeting chunks..."
        )

        metadata_file = (
            processor.create_chunks(
                normalized_audio
            )
        )

        # ==================================================
        # 6
        # ==================================================

        print(
            "\n[6/9] "
            "Running multilingual ASR..."
        )

        transcriber = (
            MeetingTranscriber()
        )

        transcript_file = (
            transcriber.transcribe(
                metadata_file
            )
        )

        # ==================================================
        # 7
        # ==================================================

        print(
            "\n[7/9] "
            "Detecting speakers..."
        )

        diarizer = (
            SpeakerDiarizer()
        )

        diarization_file = (
            diarizer.diarize(
                normalized_audio
            )
        )

        # ==================================================
        # 8
        # ==================================================

        print(
            "\n[8/9] "
            "Running reliability-aware "
            "speaker attribution..."
        )

        merger = (
            SpeakerTranscriptMerger()
        )

        speaker_transcript = (
            merger.merge(
                transcript_file,
                diarization_file
            )
        )

        # ==================================================
        # 9
        # ==================================================

        print(
            "\n[9/9] "
            "Generating processing report..."
        )

        report_generator = (
            ProcessingReportGenerator()
        )

        report_file = (
            report_generator.generate()
        )

        print(
            "\n" + "=" * 72
        )

        print(
            "             Meet2MoM v4.1 SUCCESS ✓"
        )

        print(
            "=" * 72
        )

        print(
            "\nDetailed transcript:"
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
            "Completed work remains recoverable."
        )

    except Exception as error:

        print(
            "\n✗ PIPELINE FAILED"
        )

        print(
            f"Reason: {error}"
        )

        sys.exit(1)


if __name__ == "__main__":
    main()