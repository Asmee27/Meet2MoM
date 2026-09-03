import json
import re
from pathlib import Path

from faster_whisper import WhisperModel

from app.core.config import (
    CHUNK_DIR,
    TRANSCRIPT_DIR,
    WHISPER_MODEL,
    DEVICE,
    COMPUTE_TYPE,
    BEAM_SIZE,
    RETRY_BEAM_SIZE,
    USE_VAD,
    VAD_PARAMETERS,
    FORCE_LANGUAGE,
    TARGET_LANGUAGES,
    TECHNICAL_VOCABULARY,
    PARTICIPANT_NAMES,
    LOW_CONFIDENCE_THRESHOLD,
    NO_SPEECH_THRESHOLD,
    COMPRESSION_RATIO_THRESHOLD,
    LOG_PROB_THRESHOLD,
    LOW_CONFIDENCE_RETRY_RATIO
)

from app.services.transcription.language_utils import (
    classify_script_usage,
    detect_possible_code_switch,
    language_name
)


class MeetingTranscriber:

    def __init__(self):

        TRANSCRIPT_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

        print(
            f"Loading Whisper model: "
            f"{WHISPER_MODEL}"
        )

        self.model = WhisperModel(
            WHISPER_MODEL,
            device=DEVICE,
            compute_type=COMPUTE_TYPE
        )

        print("✓ Whisper model loaded")

        self.initial_prompt = (
            self.build_context_prompt()
        )

    # ======================================================
    # CONTEXT PROMPT
    # ======================================================

    @staticmethod
    def build_context_prompt():

        vocabulary = ", ".join(
            TECHNICAL_VOCABULARY
        )

        names = ", ".join(
            PARTICIPANT_NAMES
        )

        return (
            "This is a professional technical meeting. "
            "The conversation may contain English, Hindi, "
            "Marathi, Hinglish, Marathi-English, or mixed speech. "
            "Preserve the language actually spoken. "
            "Do not translate the conversation. "
            f"Technical vocabulary may include: {vocabulary}. "
            f"Participant names may include: {names}."
        )

    # ======================================================
    # TEXT NORMALIZATION
    # ======================================================

    @staticmethod
    def normalize_text(text):

        text = text.lower().strip()

        text = re.sub(
            r"[^\w\s]",
            "",
            text,
            flags=re.UNICODE
        )

        text = re.sub(
            r"\s+",
            " ",
            text
        )

        return text

    # ======================================================
    # DUPLICATE CHECK
    # ======================================================

    def is_duplicate(
        self,
        text,
        start,
        transcript
    ):

        current = self.normalize_text(
            text
        )

        if not current:
            return True

        for previous in transcript[-15:]:

            previous_text = (
                self.normalize_text(
                    previous["text"]
                )
            )

            time_difference = abs(
                previous["start"]
                - start
            )

            if (
                current == previous_text
                and
                time_difference <= 15
            ):
                return True

            if (
                len(current) > 20
                and
                len(previous_text) > 20
                and
                time_difference <= 15
            ):

                if (
                    current in previous_text
                    or
                    previous_text in current
                ):
                    return True

        return False

    # ======================================================
    # CONFIDENCE ANALYSIS
    # ======================================================

    @staticmethod
    def analyze_confidence(segment):

        avg_logprob = getattr(
            segment,
            "avg_logprob",
            None
        )

        no_speech_prob = getattr(
            segment,
            "no_speech_prob",
            None
        )

        compression_ratio = getattr(
            segment,
            "compression_ratio",
            None
        )

        reasons = []

        if (
            avg_logprob is not None
            and
            avg_logprob
            < LOW_CONFIDENCE_THRESHOLD
        ):
            reasons.append(
                "low_log_probability"
            )

        if (
            no_speech_prob is not None
            and
            no_speech_prob
            > NO_SPEECH_THRESHOLD
        ):
            reasons.append(
                "high_no_speech_probability"
            )

        if (
            compression_ratio is not None
            and
            compression_ratio
            > COMPRESSION_RATIO_THRESHOLD
        ):
            reasons.append(
                "possible_repetition_or_ASR_hallucination"
            )

        return {
            "avg_logprob":
                round(avg_logprob, 4)
                if avg_logprob is not None
                else None,

            "no_speech_probability":
                round(no_speech_prob, 4)
                if no_speech_prob is not None
                else None,

            "compression_ratio":
                round(compression_ratio, 4)
                if compression_ratio is not None
                else None,

            "confidence_flag":
                "low"
                if reasons
                else "normal",

            "confidence_reasons":
                reasons
        }

    # ======================================================
    # WHISPER RUN
    # ======================================================

    def run_whisper(
        self,
        chunk_path,
        beam_size
    ):

        segments, info = (
            self.model.transcribe(
                str(chunk_path),

                task="transcribe",

                language=FORCE_LANGUAGE,

                beam_size=beam_size,

                vad_filter=USE_VAD,

                vad_parameters=
                    VAD_PARAMETERS,

                initial_prompt=
                    self.initial_prompt,

                condition_on_previous_text=
                    True,

                compression_ratio_threshold=
                    COMPRESSION_RATIO_THRESHOLD,

                log_prob_threshold=
                    LOG_PROB_THRESHOLD,

                no_speech_threshold=
                    NO_SPEECH_THRESHOLD
            )
        )

        return list(segments), info

    # ======================================================
    # BUILD CHUNK RESULTS
    # ======================================================

    def build_chunk_results(
        self,
        segments,
        info,
        chunk,
        final_transcript
    ):

        offset = float(
            chunk["start_time"]
        )

        results = []

        low_count = 0

        detected_language = (
            info.language
        )

        for segment in segments:

            text = (
                segment.text
                .strip()
            )

            if not text:
                continue

            actual_start = (
                float(segment.start)
                + offset
            )

            actual_end = (
                float(segment.end)
                + offset
            )

            combined_transcript = (
                final_transcript
                + results
            )

            if self.is_duplicate(
                text,
                actual_start,
                combined_transcript
            ):
                continue

            confidence = (
                self.analyze_confidence(
                    segment
                )
            )

            if (
                confidence[
                    "confidence_flag"
                ]
                == "low"
            ):
                low_count += 1

            script_type = (
                classify_script_usage(
                    text
                )
            )

            possible_code_switch = (
                detect_possible_code_switch(
                    text,
                    detected_language
                )
            )

            results.append({
                "segment_id":
                    len(final_transcript)
                    + len(results)
                    + 1,

                "chunk_id":
                    chunk["chunk_id"],

                "start":
                    round(
                        actual_start,
                        3
                    ),

                "end":
                    round(
                        actual_end,
                        3
                    ),

                "text":
                    text,

                "detected_language":
                    detected_language,

                "detected_language_name":
                    language_name(
                        detected_language
                    ),

                "script_type":
                    script_type,

                "possible_code_switch":
                    possible_code_switch,

                **confidence
            })

        return (
            results,
            low_count
        )

    # ======================================================
    # MAIN TRANSCRIPTION
    # ======================================================

    def transcribe(
        self,
        metadata_file
    ):

        metadata_file = Path(
            metadata_file
        )

        with open(
            metadata_file,
            "r",
            encoding="utf-8"
        ) as file:

            metadata = json.load(file)

        output_file = (
            TRANSCRIPT_DIR /
            "final_transcript.json"
        )

        # ==================================================
        # FRESH RUN VS RESUME
        # ==================================================

        final_transcript = []

        fresh_run = all(
            chunk.get("status") == "ready"
            for chunk in metadata
        )

        if fresh_run:

            print(
                "✓ Fresh processing run detected - "
                "starting transcript from scratch"
            )

            if output_file.exists():
                output_file.unlink()

        else:

            if output_file.exists():

                try:

                    with open(
                        output_file,
                        "r",
                        encoding="utf-8"
                    ) as file:

                        final_transcript = (
                            json.load(file)
                        )

                    print(
                        f"✓ Resuming with "
                        f"{len(final_transcript)} "
                        f"saved segments"
                    )

                except json.JSONDecodeError:

                    print(
                        "⚠ Existing transcript is invalid. "
                        "Starting fresh."
                    )

                    final_transcript = []

        # ==================================================
        # PROCESS CHUNKS
        # ==================================================

        for chunk in metadata:

            if chunk["status"] == "done":

                print(
                    f"Skipping "
                    f"{chunk['filename']} "
                    f"(done)"
                )

                continue

            chunk_path = (
                CHUNK_DIR /
                chunk["filename"]
            )

            if not chunk_path.exists():

                chunk["status"] = "failed"

                chunk["error"] = (
                    "Chunk file missing"
                )

                self.save_json(
                    metadata_file,
                    metadata
                )

                continue

            print(
                f"\nTranscribing "
                f"{chunk['filename']}..."
            )

            chunk["status"] = (
                "processing"
            )

            self.save_json(
                metadata_file,
                metadata
            )

            try:

                # FIRST PASS
                segments, info = (
                    self.run_whisper(
                        chunk_path,
                        BEAM_SIZE
                    )
                )

                (
                    chunk_results,
                    low_count
                ) = (
                    self.build_chunk_results(
                        segments,
                        info,
                        chunk,
                        final_transcript
                    )
                )

                segment_count = len(
                    chunk_results
                )

                low_ratio = (
                    low_count / segment_count
                    if segment_count > 0
                    else 0
                )

                retry_used = False

                # ==========================================
                # SELECTIVE RETRY
                # ==========================================

                if (
                    segment_count > 0
                    and
                    low_ratio
                    >
                    LOW_CONFIDENCE_RETRY_RATIO
                ):

                    print(
                        "  ⚠ High low-confidence ratio. "
                        "Retrying..."
                    )

                    (
                        retry_segments,
                        retry_info
                    ) = (
                        self.run_whisper(
                            chunk_path,
                            RETRY_BEAM_SIZE
                        )
                    )

                    (
                        retry_results,
                        retry_low_count
                    ) = (
                        self.build_chunk_results(
                            retry_segments,
                            retry_info,
                            chunk,
                            final_transcript
                        )
                    )

                    retry_total = len(
                        retry_results
                    )

                    retry_ratio = (
                        retry_low_count
                        / retry_total
                        if retry_total > 0
                        else 1
                    )

                    if (
                        retry_total > 0
                        and
                        retry_ratio
                        < low_ratio
                    ):

                        chunk_results = (
                            retry_results
                        )

                        low_count = (
                            retry_low_count
                        )

                        info = (
                            retry_info
                        )

                        retry_used = True

                # ==========================================
                # SAVE CHUNK
                # ==========================================

                final_transcript.extend(
                    chunk_results
                )

                self.save_json(
                    output_file,
                    final_transcript
                )

                detected_code = (
                    info.language
                )

                chunk["status"] = "done"

                chunk[
                    "detected_language"
                ] = detected_code

                chunk[
                    "detected_language_name"
                ] = language_name(
                    detected_code
                )

                chunk[
                    "language_probability"
                ] = round(
                    info.language_probability,
                    4
                )

                chunk[
                    "language_in_target_scope"
                ] = (
                    detected_code
                    in TARGET_LANGUAGES
                )

                chunk[
                    "segments_saved"
                ] = len(
                    chunk_results
                )

                chunk[
                    "low_confidence_segments"
                ] = low_count

                chunk[
                    "retry_used"
                ] = retry_used

                chunk.pop(
                    "error",
                    None
                )

                self.save_json(
                    metadata_file,
                    metadata
                )

                print(
                    f"✓ {chunk['filename']} completed"
                )

                print(
                    f"  Language: "
                    f"{language_name(detected_code)} "
                    f"({detected_code})"
                )

                print(
                    f"  Language confidence: "
                    f"{info.language_probability:.2%}"
                )

                print(
                    f"  Segments: "
                    f"{len(chunk_results)}"
                )

                print(
                    f"  Low confidence: "
                    f"{low_count}"
                )

                print(
                    f"  Retry used: "
                    f"{retry_used}"
                )

            except Exception as error:

                chunk["status"] = (
                    "failed"
                )

                chunk[
                    "retry_count"
                ] = (
                    chunk.get(
                        "retry_count",
                        0
                    )
                    + 1
                )

                chunk["error"] = (
                    str(error)
                )

                self.save_json(
                    metadata_file,
                    metadata
                )

                print(
                    f"✗ Chunk failed: "
                    f"{error}"
                )

        self.save_json(
            output_file,
            final_transcript
        )

        return output_file

    # ======================================================
    # SAVE JSON
    # ======================================================

    @staticmethod
    def save_json(
        path,
        data
    ):

        with open(
            path,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                data,
                file,
                indent=4,
                ensure_ascii=False
            )