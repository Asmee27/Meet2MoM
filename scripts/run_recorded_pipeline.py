import argparse
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

# Taken before the heavy imports below, to time the subprocess start-up.
_SCRIPT_STARTED = time.monotonic()

def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Meet2MoM - Recorded Meeting Intelligence Pipeline")
    parser.add_argument("input_file", nargs="?", default=None, help="Path to input recording")
    parser.add_argument("--meeting-id", dest="meeting_id", default=None, help="Meeting ID for isolated storage")
    parser.add_argument(
        "--skip-post-transcript",
        dest="skip_post_transcript",
        action="store_true",
        help="Stop after the transcript (the calling process runs extraction and verification)",
    )
    return parser.parse_known_args(argv)[0]


if __name__ == "__main__":
    # Run as a subprocess: choose the meeting before app.core.config is
    # imported. When imported (in-process mode, Phase 6) the caller passes
    # the meeting to run() instead and set_active_meeting() switches paths.
    args = _parse_args()
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
from app.services.live.track_transcription import (
    load_track_plan,
    speaker_turns as track_speaker_turns,
    transcribe_tracks,
)
from app.services.reporting.processing_report import ProcessingReportGenerator


def main():
    """Command-line entry point (also the per-meeting subprocess)."""
    if args.input_file:
        input_file = Path(args.input_file).resolve()
    elif len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        input_file = Path(sys.argv[1]).resolve()
    else:
        input_file = (
            INPUT_DIR / "test_meeting.mp4"
        ).resolve()
    try:
        run(
            meeting_id,
            input_file,
            skip_post_transcript=args.skip_post_transcript,
            startup_seconds=time.monotonic() - _SCRIPT_STARTED,
        )
    except KeyboardInterrupt:
        print("\n⚠ Processing interrupted.")
        print("Completed processing stages remain recoverable.")
    except Exception as error:
        print("\n✗ MEET2MOM PIPELINE FAILED")
        print(f"Reason: {error}")
        sys.exit(1)


def run(
    meeting_id,
    input_file,
    *,
    skip_post_transcript=False,
    startup_seconds=None,
    startup_note=None,
):
    """
    Run stages 1-9 for one recording and raise on failure. Used by main() in
    a subprocess and directly by a long-lived worker (in-process mode).
    """
    input_file = Path(input_file).resolve()

    pipeline_start = time.perf_counter()

    storage = MeetingStorage(meeting_id)
    storage.ensure_dirs()
    set_active_meeting(meeting_id)

    print("\n" + "=" * 72)
    print("        Meet2MoM - Recorded Meeting Intelligence Pipeline")
    print("=" * 72)
    print(f"Meeting ID: {meeting_id}")
    print(f"Storage root: {storage.root}")

    # Per-stage timings, written to metadata/stage_timings.json after every
    # stage; the job runner stores them in the database (Phase 5).
    from app.core.stage_timings import RUN_ID_ENV, STAGE_FILE, StageRecorder

    timer = StageRecorder(
        meeting_id,
        os.environ.get(RUN_ID_ENV),
        file_path=storage.path_for("metadata", STAGE_FILE),
    )
    if startup_seconds is None:
        timer.record(
            "pipeline_startup",
            "cached",
            detail={"note": startup_note or "Libraries already loaded in this long-lived process"},
        )
    else:
        timer.record(
            "pipeline_startup",
            "completed",
            duration_seconds=startup_seconds,
            detail={"note": startup_note or "Subprocess start-up: importing torch, Whisper, pyannote and other libraries"},
        )

    print(f"\nRecording: {input_file}")

    try:

        # ==================================================
        # CACHE INITIALIZATION
        # ==================================================

        # Cache set-up hashes the recording: part of audio preparation.
        timer.begin("audio_preparation")

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

        timer.end()

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
        # LIVEKIT PARTICIPANT TRACKS (speaker identity)
        # ==================================================
        # A LiveKit recording's per-participant tracks say who spoke.
        # Without usable tracks (uploads, legacy or damaged recordings)
        # the mixed-recording ASR + diarization path below is used.

        track_plan = None
        if input_file == (storage.recording / "recording.wav").resolve():
            track_plan = load_track_plan(storage.recording)
            if not track_plan.usable and track_plan.reason != "no_manifest":
                print(
                    f"\n⚠ LiveKit tracks unusable ({track_plan.reason}); "
                    "using mixed-recording diarization"
                )
                for problem in track_plan.problems:
                    print(f"  - {problem['track']}: {problem['problem']}")

        if track_plan is not None and track_plan.usable:

            asr_stage = timer.begin(
                "transcription",
                mode="livekit_tracks",
                tracks=len(track_plan.tracks),
            )

            print(
                f"\n[6/9] Running ASR on "
                f"{len(track_plan.tracks)} participant tracks..."
            )

            asr_start = time.perf_counter()

            # Track-mode files must never be reused as mixed-recording
            # stage results by a later run.
            cache_manager.invalidate_stage("transcription")
            cache_manager.invalidate_stage("diarization")

            model_started = time.monotonic()
            transcriber = MeetingTranscriber()
            asr_stage.detail["model_load_seconds"] = round(time.monotonic() - model_started, 2)
            asr_stage.detail["model_reused"] = getattr(transcriber, "model_reused", False)

            track_segments, attribution = transcribe_tracks(
                track_plan,
                transcriber.transcribe_track
            )

            transcriber.save_json(
                transcript_file,
                [
                    {
                        key: value
                        for key, value in segment.items()
                        if key not in (
                            "requires_review",
                            "review_reasons",
                        )
                    }
                    for segment in track_segments
                ]
            )

            print(
                f"✓ ASR processing time: "
                f"{time.perf_counter() - asr_start:.2f} seconds"
            )

            timer.end()
            timer.begin("speaker_attribution", method="livekit_tracks")

            print(
                "\n[7/9] Speakers from LiveKit participant tracks "
                "(mixed-audio diarization not needed)"
            )

            transcriber.save_json(
                diarization_file,
                track_speaker_turns(track_segments)
            )

            transcriber.save_json(
                storage.path_for(
                    "metadata",
                    "speaker_attribution.json"
                ),
                attribution
            )

            for item in attribution["per_track"]:
                print(
                    f"  {item['identity']}: "
                    f"{item['segments']} segments"
                )

            timer.begin("transcript_merge", method="livekit_tracks")

            print(
                "\n[8/9] Writing participant-attributed transcript..."
            )

            speaker_transcript = (
                SpeakerTranscriptMerger().write_outputs(
                    track_segments
                )
            )

        else:

            # A stale track-mode report must not describe this run.
            stale_attribution = storage.path_for(
                "metadata",
                "speaker_attribution.json"
            )
            if stale_attribution.exists():
                stale_attribution.unlink()

            # ==================================================
            # 6 — MULTILINGUAL ASR
            # ==================================================

            asr_stage = timer.begin("transcription", mode="mixed_recording")

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

                asr_stage.status = "cached"

            else:

                asr_start = time.perf_counter()

                model_started = time.monotonic()
                transcriber = MeetingTranscriber()
                asr_stage.detail["model_load_seconds"] = round(time.monotonic() - model_started, 2)
                asr_stage.detail["model_reused"] = getattr(transcriber, "model_reused", False)

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

            timer.end()
            diarization_stage = timer.begin(
                "speaker_attribution",
                method="pyannote_diarization",
            )

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

                diarization_stage.status = "cached"

            else:

                diarization_start = (
                    time.perf_counter()
                )

                model_started = time.monotonic()
                diarizer = SpeakerDiarizer()
                diarization_stage.detail["model_load_seconds"] = round(
                    time.monotonic() - model_started, 2
                )
                diarization_stage.detail["model_reused"] = getattr(diarizer, "model_reused", False)

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

            timer.begin("transcript_merge", method="reliability_merge")

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
        timer.end()

        if not skip_post_transcript:
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

    except KeyboardInterrupt as interrupted:

        timer.fail_current(interrupted)
        raise

    except Exception as error:

        timer.fail_current(error)
        raise


if __name__ == "__main__":
    main()