import json
from pathlib import Path

from app.core.config import (
    TRANSCRIPT_DIR,
    HIGH_SPEAKER_CONFIDENCE,
    LOW_SPEAKER_CONFIDENCE,
    OVERLAP_DETECTION_SECONDS,
    FINAL_HANDOFF_FILE
)


class SpeakerTranscriptMerger:

    # ======================================================
    # TIME OVERLAP
    # ======================================================

    @staticmethod
    def overlap_duration(
        start1,
        end1,
        start2,
        end2
    ):

        overlap_start = max(
            start1,
            start2
        )

        overlap_end = min(
            end1,
            end2
        )

        return max(
            0.0,
            overlap_end - overlap_start
        )

    # ======================================================
    # SPEAKER OVERLAP ANALYSIS
    # ======================================================

    def analyze_speakers(
        self,
        segment,
        speaker_turns
    ):

        segment_start = float(
            segment["start"]
        )

        segment_end = float(
            segment["end"]
        )

        duration = max(
            segment_end - segment_start,
            0.001
        )

        speaker_overlap = {}

        overlapping_speakers = set()

        for turn in speaker_turns:

            turn_start = float(
                turn["start"]
            )

            turn_end = float(
                turn["end"]
            )

            overlap = (
                self.overlap_duration(
                    segment_start,
                    segment_end,
                    turn_start,
                    turn_end
                )
            )

            if overlap <= 0:
                continue

            speaker = turn["speaker"]

            speaker_overlap[speaker] = (
                speaker_overlap.get(
                    speaker,
                    0.0
                )
                + overlap
            )

        # No speaker matched
        if not speaker_overlap:

            return {
                "speaker": "UNKNOWN",
                "speaker_attribution_confidence": 0.0,
                "speaker_confidence_flag": "uncertain",
                "possible_overlap": False,
                "overlapping_speakers": [],
                "speaker_distribution": {}
            }

        # Sort speakers by amount of overlap
        ranked = sorted(
            speaker_overlap.items(),
            key=lambda item: item[1],
            reverse=True
        )

        best_speaker = ranked[0][0]
        best_overlap = ranked[0][1]

        confidence = min(
            best_overlap / duration,
            1.0
        )

        # ==================================================
        # POSSIBLE CROSS-TALK
        # ==================================================

        for speaker, overlap in ranked:

            if (
                speaker != best_speaker
                and
                overlap >= OVERLAP_DETECTION_SECONDS
            ):

                overlapping_speakers.add(
                    speaker
                )

        possible_overlap = (
            len(overlapping_speakers) > 0
        )

        # ==================================================
        # SPEAKER CONFIDENCE
        # ==================================================

        if (
            confidence
            >= HIGH_SPEAKER_CONFIDENCE
            and
            not possible_overlap
        ):

            confidence_flag = "high"

        elif (
            confidence
            < LOW_SPEAKER_CONFIDENCE
        ):

            confidence_flag = "uncertain"

        else:

            confidence_flag = "medium"

        distribution = {}

        for speaker, overlap in ranked:

            distribution[speaker] = round(
                min(
                    overlap / duration,
                    1.0
                ),
                4
            )

        return {

            "speaker":
                best_speaker,

            "speaker_attribution_confidence":
                round(
                    confidence,
                    4
                ),

            "speaker_confidence_flag":
                confidence_flag,

            "possible_overlap":
                possible_overlap,

            "overlapping_speakers":
                sorted(
                    overlapping_speakers
                ),

            "speaker_distribution":
                distribution
        }

    # ======================================================
    # FINAL REVIEW DECISION
    # ======================================================

    @staticmethod
    def determine_review_status(
        segment
    ):

        reasons = []

        # -----------------------------
        # ASR reliability
        # -----------------------------

        if (
            segment.get(
                "confidence_flag"
            )
            == "low"
        ):

            reasons.append(
                "low_asr_confidence"
            )

        # -----------------------------
        # Speaker reliability
        # -----------------------------

        if (
            segment.get(
                "speaker_confidence_flag"
            )
            == "uncertain"
        ):

            reasons.append(
                "uncertain_speaker"
            )

        if (
            segment.get(
                "speaker"
            )
            == "UNKNOWN"
        ):

            reasons.append(
                "unknown_speaker"
            )

        # -----------------------------
        # Cross-talk
        # -----------------------------

        if segment.get(
            "possible_overlap",
            False
        ):

            reasons.append(
                "possible_cross_talk"
            )

        return {
            "requires_review":
                len(reasons) > 0,

            "review_reasons":
                reasons
        }

    # ======================================================
    # MERGE
    # ======================================================

    def merge(
        self,
        transcript_file,
        diarization_file
    ):

        transcript_file = Path(
            transcript_file
        )

        diarization_file = Path(
            diarization_file
        )

        with open(
            transcript_file,
            "r",
            encoding="utf-8"
        ) as file:

            transcript = json.load(
                file
            )

        with open(
            diarization_file,
            "r",
            encoding="utf-8"
        ) as file:

            speaker_turns = json.load(
                file
            )

        merged = []

        unknown_count = 0
        review_count = 0
        overlap_count = 0
        uncertain_count = 0

        for segment in transcript:

            speaker_data = (
                self.analyze_speakers(
                    segment,
                    speaker_turns
                )
            )

            merged_segment = (
                segment.copy()
            )

            merged_segment.update(
                speaker_data
            )

            review_data = (
                self.determine_review_status(
                    merged_segment
                )
            )

            merged_segment.update(
                review_data
            )

            if (
                merged_segment["speaker"]
                == "UNKNOWN"
            ):

                unknown_count += 1

            if merged_segment[
                "possible_overlap"
            ]:

                overlap_count += 1

            if (
                merged_segment[
                    "speaker_confidence_flag"
                ]
                == "uncertain"
            ):

                uncertain_count += 1

            if merged_segment[
                "requires_review"
            ]:

                review_count += 1

            merged.append(
                merged_segment
            )

        # ==================================================
        # DETAILED SPEAKER TRANSCRIPT
        # ==================================================

        speaker_output = (
            TRANSCRIPT_DIR /
            "speaker_transcript.json"
        )

        with open(
            speaker_output,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                merged,
                file,
                indent=4,
                ensure_ascii=False
            )

        # ==================================================
        # CLEAN HANDOFF FOR MEMBER 2
        # ==================================================

        handoff = []

        for item in merged:

            handoff.append({

                "segment_id":
                    item.get(
                        "segment_id"
                    ),

                "start":
                    item["start"],

                "end":
                    item["end"],

                "speaker":
                    item["speaker"],

                "text":
                    item["text"],

                "language":
                    item.get(
                        "detected_language",
                        item.get(
                            "language"
                        )
                    ),

                "asr_confidence":
                    item.get(
                        "confidence_flag"
                    ),

                "speaker_confidence":
                    item.get(
                        "speaker_confidence_flag"
                    ),

                "speaker_attribution_confidence":
                    item.get(
                        "speaker_attribution_confidence"
                    ),

                "possible_overlap":
                    item.get(
                        "possible_overlap",
                        False
                    ),

                "requires_review":
                    item.get(
                        "requires_review",
                        False
                    ),

                "review_reasons":
                    item.get(
                        "review_reasons",
                        []
                    )
            })

        with open(
            FINAL_HANDOFF_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                handoff,
                file,
                indent=4,
                ensure_ascii=False
            )

        print(
            "\n✓ Reliability analysis completed"
        )

        print(
            f"  Transcript segments: "
            f"{len(merged)}"
        )

        print(
            f"  Unknown speakers: "
            f"{unknown_count}"
        )

        print(
            f"  Possible overlap: "
            f"{overlap_count}"
        )

        print(
            f"  Uncertain speakers: "
            f"{uncertain_count}"
        )

        print(
            f"  Requires review: "
            f"{review_count}"
        )

        print(
            f"✓ Detailed transcript: "
            f"{speaker_output}"
        )

        print(
            f"✓ Member-2 handoff: "
            f"{FINAL_HANDOFF_FILE}"
        )

        return speaker_output