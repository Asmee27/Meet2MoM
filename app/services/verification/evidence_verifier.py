from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class EvidenceVerifier:
    """
    Meet2MoM - Transcript Evidence Verification Layer

    Verifies extracted decisions and action items against the original
    speaker-attributed transcript.

    Responsibilities:
    - Anchor decisions/actions to transcript evidence
    - Recover speaker IDs
    - Recover timestamps
    - Verify proposal + confirmation pairs
    - Calculate evidence confidence
    - Use diarization/speaker reliability
    - Detect cross-talk / unreliable speaker attribution
    - Mark evidence as:
        verified
        verified_with_warning
        review
        rejected

    This class does NOT generate new decisions or action items.
    It only verifies claims already produced by the intelligence layer.
    """

    def __init__(
        self,
        output_dir: str = "output/verification",
        strong_match_threshold: float = 0.82,
        acceptable_match_threshold: float = 0.65,
        timestamp_tolerance: float = 8.0,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.strong_match_threshold = (
            strong_match_threshold
        )

        self.acceptable_match_threshold = (
            acceptable_match_threshold
        )

        self.timestamp_tolerance = (
            timestamp_tolerance
        )

    # ==========================================================
    # JSON HELPERS
    # ==========================================================

    @staticmethod
    def load_json(
        path: str | Path,
    ) -> Any:

        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(
                f"File not found: {path}"
            )

        with path.open(
            "r",
            encoding="utf-8",
        ) as file:

            return json.load(file)

    # ==========================================================
    # TEXT NORMALIZATION
    # ==========================================================

    @staticmethod
    def normalize(
        text: Any,
    ) -> str:

        text = str(
            text or ""
        ).lower()

        text = re.sub(
            r"[^\w\s']",
            " ",
            text,
            flags=re.UNICODE,
        )

        return re.sub(
            r"\s+",
            " ",
            text,
        ).strip()

    # ==========================================================
    # LOAD TRANSCRIPT
    # ==========================================================

    def load_transcript(
        self,
        path: str | Path,
    ) -> List[Dict[str, Any]]:

        data = self.load_json(
            path
        )

        if isinstance(
            data,
            list,
        ):
            segments = data

        elif isinstance(
            data,
            dict,
        ):
            segments = None

            for key in (
                "segments",
                "transcript",
                "speaker_transcript",
            ):

                value = data.get(
                    key
                )

                if isinstance(
                    value,
                    list,
                ):
                    segments = value
                    break

            if segments is None:
                raise ValueError(
                    "Transcript JSON does not contain a segment list."
                )

        else:
            raise ValueError(
                "Unsupported transcript JSON structure."
            )

        cleaned = []

        for index, segment in enumerate(
            segments
        ):

            text = str(
                segment.get(
                    "text",
                    "",
                )
            ).strip()

            if not text:
                continue

            item = dict(
                segment
            )

            item[
                "_segment_index"
            ] = index

            item[
                "text"
            ] = text

            try:
                item[
                    "start"
                ] = float(
                    segment.get(
                        "start",
                        0,
                    )
                    or 0
                )

            except (
                TypeError,
                ValueError,
            ):
                item[
                    "start"
                ] = 0.0

            try:
                item[
                    "end"
                ] = float(
                    segment.get(
                        "end",
                        item[
                            "start"
                        ],
                    )
                    or item[
                        "start"
                    ]
                )

            except (
                TypeError,
                ValueError,
            ):
                item[
                    "end"
                ] = item[
                    "start"
                ]

            cleaned.append(
                item
            )

        return cleaned

    # ==========================================================
    # TEXT SIMILARITY
    # ==========================================================

    def text_similarity(
        self,
        first: str,
        second: str,
    ) -> float:

        a = self.normalize(
            first
        )

        b = self.normalize(
            second
        )

        if not a or not b:
            return 0.0

        # Exact normalized match
        if a == b:
            return 1.0

        # One text may be contained inside a larger ASR segment
        if a in b or b in a:

            shorter = min(
                len(
                    a.split()
                ),
                len(
                    b.split()
                ),
            )

            longer = max(
                len(
                    a.split()
                ),
                len(
                    b.split()
                ),
            )

            coverage = (
                shorter / longer
                if longer
                else 0.0
            )

            return min(
                1.0,
                0.88
                + (
                    0.12
                    * coverage
                ),
            )

        sequence_score = (
            SequenceMatcher(
                None,
                a,
                b,
            ).ratio()
        )

        a_words = set(
            a.split()
        )

        b_words = set(
            b.split()
        )

        if (
            a_words
            and b_words
        ):

            intersection = len(
                a_words
                & b_words
            )

            union = len(
                a_words
                | b_words
            )

            jaccard = (
                intersection
                / union
                if union
                else 0.0
            )

        else:
            jaccard = 0.0

        return round(
            (
                0.70
                * sequence_score
            )
            + (
                0.30
                * jaccard
            ),
            4,
        )

    # ==========================================================
    # TIMESTAMP SCORE
    # ==========================================================

    def timestamp_score(
        self,
        expected_timestamp: Optional[
            float
        ],
        actual_timestamp: float,
    ) -> float:

        if (
            expected_timestamp
            is None
        ):
            return 0.5

        try:
            expected = float(
                expected_timestamp
            )

        except (
            TypeError,
            ValueError,
        ):
            return 0.5

        difference = abs(
            expected
            - actual_timestamp
        )

        if difference <= 0.75:
            return 1.0

        if difference <= 2.0:
            return 0.95

        if difference <= 5.0:
            return 0.85

        if (
            difference
            <= self.timestamp_tolerance
        ):
            return 0.70

        if difference <= 20:
            return 0.40

        return 0.0

    # ==========================================================
    # FIND BEST TRANSCRIPT SEGMENT
    # ==========================================================

    def find_best_segment(
        self,
        target_text: str,
        transcript: List[
            Dict[str, Any]
        ],
        expected_timestamp: Optional[
            float
        ] = None,
    ) -> Tuple[
        Optional[
            Dict[str, Any]
        ],
        float,
        float,
        float,
    ]:

        best_segment = None

        best_combined = 0.0
        best_text_score = 0.0
        best_time_score = 0.0

        for segment in transcript:

            text_score = (
                self.text_similarity(
                    target_text,
                    segment.get(
                        "text",
                        "",
                    ),
                )
            )

            time_score = (
                self.timestamp_score(
                    expected_timestamp,
                    float(
                        segment.get(
                            "start",
                            0,
                        )
                    ),
                )
            )

            if (
                expected_timestamp
                is None
            ):
                combined = (
                    text_score
                )

            else:
                combined = (
                    0.82
                    * text_score
                    + 0.18
                    * time_score
                )

            if (
                combined
                > best_combined
            ):

                best_combined = (
                    combined
                )

                best_text_score = (
                    text_score
                )

                best_time_score = (
                    time_score
                )

                best_segment = (
                    segment
                )

        return (
            best_segment,
            round(
                best_combined,
                4,
            ),
            round(
                best_text_score,
                4,
            ),
            round(
                best_time_score,
                4,
            ),
        )

    # ==========================================================
    # BUILD EVIDENCE RECORD
    # ==========================================================

    def build_evidence(
        self,
        segment: Optional[
            Dict[str, Any]
        ],
        combined_score: float,
        text_score: float,
        time_score: float,
        evidence_role: str,
    ) -> Optional[
        Dict[str, Any]
    ]:

        if segment is None:
            return None

        return {
            "role":
                evidence_role,

            "segment_index":
                segment.get(
                    "_segment_index"
                ),

            "speaker":
                segment.get(
                    "speaker",
                    "UNKNOWN",
                ),

            "start":
                round(
                    float(
                        segment.get(
                            "start",
                            0,
                        )
                    ),
                    2,
                ),

            "end":
                round(
                    float(
                        segment.get(
                            "end",
                            segment.get(
                                "start",
                                0,
                            ),
                        )
                    ),
                    2,
                ),

            "text":
                segment.get(
                    "text",
                    "",
                ),

            "match_score":
                combined_score,

            "text_similarity":
                text_score,

            "timestamp_similarity":
                time_score,

            "possible_overlap":
                bool(
                    segment.get(
                        "possible_overlap",
                        False,
                    )
                ),

            "source_requires_review":
                bool(
                    segment.get(
                        "requires_review",
                        False,
                    )
                ),

            "speaker_confidence_flag":
                segment.get(
                    "speaker_confidence_flag"
                ),

            "speaker_attribution_confidence":
                segment.get(
                    "speaker_attribution_confidence"
                ),
        }

    # ==========================================================
    # RELIABILITY PENALTY
    # ==========================================================

    @staticmethod
    def reliability_penalty(
        evidence: Optional[
            Dict[str, Any]
        ],
    ) -> Tuple[
        float,
        List[str],
    ]:

        if not evidence:
            return (
                0.0,
                [],
            )

        penalty = 0.0
        reasons = []

        # ------------------------------------------------------
        # Cross-talk / overlap
        # ------------------------------------------------------

        if evidence.get(
            "possible_overlap"
        ):

            penalty += 0.06

            reasons.append(
                "possible_cross_talk"
            )

        # ------------------------------------------------------
        # Upstream transcript reliability
        # ------------------------------------------------------

        if evidence.get(
            "source_requires_review"
        ):

            penalty += 0.05

            reasons.append(
                "source_segment_requires_review"
            )

        # ------------------------------------------------------
        # Explicit speaker flag
        # ------------------------------------------------------

        speaker_flag = (
            evidence.get(
                "speaker_confidence_flag"
            )
        )

        if speaker_flag in {
            "low",
            "uncertain",
        }:

            penalty += 0.08

            reasons.append(
                "uncertain_speaker_attribution"
            )

        # ------------------------------------------------------
        # Numeric speaker confidence
        # ------------------------------------------------------

        speaker_confidence = (
            evidence.get(
                "speaker_attribution_confidence"
            )
        )

        if (
            speaker_confidence
            is not None
        ):

            try:

                speaker_confidence = float(
                    speaker_confidence
                )

                if (
                    speaker_confidence
                    < 0.60
                ):

                    penalty += 0.10

                    reasons.append(
                        "low_speaker_confidence"
                    )

                elif (
                    speaker_confidence
                    < 0.80
                ):

                    penalty += 0.04

                    reasons.append(
                        "moderate_speaker_confidence"
                    )

            except (
                TypeError,
                ValueError,
            ):
                pass

        # ------------------------------------------------------
        # Unknown speaker
        # ------------------------------------------------------

        if evidence.get(
            "speaker"
        ) in {
            None,
            "",
            "UNKNOWN",
            "SPEAKER_UNKNOWN",
        }:

            penalty += 0.08

            reasons.append(
                "unknown_speaker"
            )

        return (
            penalty,
            reasons,
        )

    # ==========================================================
    # WARNING DETECTION
    # ==========================================================

    @staticmethod
    def has_reliability_warning(
        reasons: List[str],
    ) -> bool:

        warning_reasons = {
            "possible_cross_talk",
            "source_segment_requires_review",
            "uncertain_speaker_attribution",
            "low_speaker_confidence",
        }

        return any(
            reason
            in warning_reasons
            for reason
            in reasons
        )

    # ==========================================================
    # VERIFY DECISION
    # ==========================================================

    def verify_decision(
        self,
        decision: Dict[str, Any],
        transcript: List[
            Dict[str, Any]
        ],
    ) -> Dict[str, Any]:

        decision_text = str(
            decision.get(
                "original_proposal"
            )
            or decision.get(
                "text",
                "",
            )
        )

        (
            proposal_segment,
            proposal_match,
            proposal_text_score,
            proposal_time_score,
        ) = self.find_best_segment(
            decision_text,
            transcript,
            decision.get(
                "timestamp"
            ),
        )

        proposal_evidence = (
            self.build_evidence(
                proposal_segment,
                proposal_match,
                proposal_text_score,
                proposal_time_score,
                "proposal_or_decision",
            )
        )

        confirmation_text = (
            decision.get(
                "confirmation_text"
            )
            or decision.get(
                "confirmed_by"
            )
        )

        confirmation_evidence = (
            None
        )

        confirmation_match = (
            None
        )

        if confirmation_text:

            (
                confirmation_segment,
                confirmation_match,
                confirmation_text_score,
                confirmation_time_score,
            ) = self.find_best_segment(
                str(
                    confirmation_text
                ),
                transcript,
                decision.get(
                    "confirmation_timestamp"
                ),
            )

            confirmation_evidence = (
                self.build_evidence(
                    confirmation_segment,
                    confirmation_match,
                    confirmation_text_score,
                    confirmation_time_score,
                    "confirmation",
                )
            )

        # ------------------------------------------------------
        # Base evidence confidence
        # ------------------------------------------------------

        if confirmation_text:

            base_confidence = (
                0.65
                * proposal_match
                + 0.35
                * (
                    confirmation_match
                    or 0.0
                )
            )

        else:

            base_confidence = (
                proposal_match
            )

        review_reasons = []

        # ------------------------------------------------------
        # Proposal reliability
        # ------------------------------------------------------

        (
            proposal_penalty,
            reasons,
        ) = self.reliability_penalty(
            proposal_evidence
        )

        review_reasons.extend(
            reasons
        )

        # ------------------------------------------------------
        # Confirmation reliability
        # ------------------------------------------------------

        confirmation_penalty = (
            0.0
        )

        if confirmation_evidence:

            (
                confirmation_penalty,
                reasons,
            ) = self.reliability_penalty(
                confirmation_evidence
            )

            review_reasons.extend(
                reasons
            )

        # ------------------------------------------------------
        # Final evidence confidence
        # ------------------------------------------------------

        confidence = (
            base_confidence
            - proposal_penalty
            - confirmation_penalty
        )

        confidence = max(
            0.0,
            min(
                confidence,
                1.0,
            ),
        )

        # ------------------------------------------------------
        # Evidence match state
        # ------------------------------------------------------

        if (
            proposal_match
            >= self.strong_match_threshold
            and (
                not confirmation_text
                or (
                    confirmation_match
                    is not None
                    and confirmation_match
                    >= self.strong_match_threshold
                )
            )
        ):

            verification_status = (
                "verified"
            )

        elif (
            proposal_match
            >= self.acceptable_match_threshold
        ):

            verification_status = (
                "review"
            )

            review_reasons.append(
                "evidence_match_not_strong"
            )

        else:

            verification_status = (
                "rejected"
            )

            review_reasons.append(
                "insufficient_transcript_evidence"
            )

        # ------------------------------------------------------
        # Reliability warning state
        # ------------------------------------------------------

        has_warning = (
            self.has_reliability_warning(
                review_reasons
            )
        )

        if (
            verification_status
            == "verified"
            and (
                confidence < 0.70
                or has_warning
            )
        ):

            verification_status = (
                "verified_with_warning"
            )

            if confidence < 0.70:

                review_reasons.append(
                    "evidence_reliability_below_threshold"
                )

        # Remove duplicate reasons
        review_reasons = list(
            dict.fromkeys(
                review_reasons
            )
        )

        # ------------------------------------------------------
        # Build result
        # ------------------------------------------------------

        verified = dict(
            decision
        )

        verified.update(
            {
                "verification_status":
                    verification_status,

                "evidence_confidence":
                    round(
                        confidence,
                        4,
                    ),

                "proposal_speaker":
                    (
                        proposal_evidence.get(
                            "speaker"
                        )
                        if proposal_evidence
                        else "UNKNOWN"
                    ),

                "proposal_timestamp":
                    (
                        proposal_evidence.get(
                            "start"
                        )
                        if proposal_evidence
                        else None
                    ),

                "confirmation_speaker":
                    (
                        confirmation_evidence.get(
                            "speaker"
                        )
                        if confirmation_evidence
                        else None
                    ),

                "verified_confirmation_timestamp":
                    (
                        confirmation_evidence.get(
                            "start"
                        )
                        if confirmation_evidence
                        else None
                    ),

                "evidence":
                    [
                        evidence
                        for evidence in (
                            proposal_evidence,
                            confirmation_evidence,
                        )
                        if evidence
                    ],

                "verification_requires_review":
                    (
                        verification_status
                        in {
                            "review",
                            "verified_with_warning",
                        }
                    ),

                "verification_review_reasons":
                    review_reasons,
            }
        )

        return verified

    # ==========================================================
    # VERIFY ACTION ITEM
    # ==========================================================

    def verify_action_item(
        self,
        action: Dict[str, Any],
        transcript: List[
            Dict[str, Any]
        ],
    ) -> Dict[str, Any]:

        evidence_text = str(
            action.get(
                "evidence_text"
            )
            or action.get(
                "source_text"
            )
            or action.get(
                "task",
                "",
            )
        )

        (
            segment,
            match_score,
            text_score,
            time_score,
        ) = self.find_best_segment(
            evidence_text,
            transcript,
            (
                action.get(
                    "timestamp"
                )
                or action.get(
                    "source_start"
                )
            ),
        )

        evidence = (
            self.build_evidence(
                segment,
                match_score,
                text_score,
                time_score,
                "action_commitment",
            )
        )

        (
            penalty,
            reasons,
        ) = self.reliability_penalty(
            evidence
        )

        confidence = max(
            0.0,
            min(
                match_score
                - penalty,
                1.0,
            ),
        )

        # ------------------------------------------------------
        # Evidence state
        # ------------------------------------------------------

        if (
            match_score
            >= self.strong_match_threshold
        ):

            status = (
                "verified"
            )

        elif (
            match_score
            >= self.acceptable_match_threshold
        ):

            status = (
                "review"
            )

            reasons.append(
                "evidence_match_not_strong"
            )

        else:

            status = (
                "rejected"
            )

            reasons.append(
                "insufficient_transcript_evidence"
            )

        # ------------------------------------------------------
        # Reliability warning state
        # ------------------------------------------------------

        has_warning = (
            self.has_reliability_warning(
                reasons
            )
        )

        if (
            status
            == "verified"
            and (
                confidence < 0.70
                or has_warning
            )
        ):

            status = (
                "verified_with_warning"
            )

            if confidence < 0.70:

                reasons.append(
                    "evidence_reliability_below_threshold"
                )

        reasons = list(
            dict.fromkeys(
                reasons
            )
        )

        verified = dict(
            action
        )

        verified.update(
            {
                "verification_status":
                    status,

                "evidence_confidence":
                    round(
                        confidence,
                        4,
                    ),

                "verified_speaker":
                    (
                        evidence.get(
                            "speaker"
                        )
                        if evidence
                        else "UNKNOWN"
                    ),

                "verified_timestamp":
                    (
                        evidence.get(
                            "start"
                        )
                        if evidence
                        else None
                    ),

                "evidence":
                    (
                        [evidence]
                        if evidence
                        else []
                    ),

                "verification_requires_review":
                    (
                        status
                        in {
                            "review",
                            "verified_with_warning",
                        }
                    ),

                "verification_review_reasons":
                    reasons,
            }
        )

        return verified

    # ==========================================================
    # PROCESS
    # ==========================================================

    def process(
        self,
        transcript_path: str | Path,
        decisions_path: str | Path,
        action_items_path: str | Path,
    ) -> Path:

        print(
            "\n"
            + "=" * 68
        )

        print(
            "Meet2MoM - Transcript Evidence Verification"
        )

        print(
            "=" * 68
        )

        # ------------------------------------------------------
        # Load inputs
        # ------------------------------------------------------

        transcript = (
            self.load_transcript(
                transcript_path
            )
        )

        decisions_data = (
            self.load_json(
                decisions_path
            )
        )

        actions_data = (
            self.load_json(
                action_items_path
            )
        )

        # ------------------------------------------------------
        # Read decisions
        # ------------------------------------------------------

        if isinstance(
            decisions_data,
            dict,
        ):

            decisions = (
                decisions_data.get(
                    "final_decisions",
                    decisions_data.get(
                        "decisions",
                        [],
                    ),
                )
            )

        else:

            decisions = (
                decisions_data
            )

        # ------------------------------------------------------
        # Read actions
        # ------------------------------------------------------

        if isinstance(
            actions_data,
            dict,
        ):

            actions = (
                actions_data.get(
                    "action_items",
                    [],
                )
            )

        else:

            actions = (
                actions_data
            )

        if not isinstance(
            decisions,
            list,
        ):
            raise ValueError(
                "Decision output must contain a list."
            )

        if not isinstance(
            actions,
            list,
        ):
            raise ValueError(
                "Action-item output must contain a list."
            )

        print(
            f"\nTranscript segments: "
            f"{len(transcript)}"
        )

        print(
            f"Decisions to verify: "
            f"{len(decisions)}"
        )

        print(
            f"Action items to verify: "
            f"{len(actions)}"
        )

        # ======================================================
        # VERIFY DECISIONS
        # ======================================================

        verified_decisions = []

        for decision in decisions:

            result = (
                self.verify_decision(
                    decision,
                    transcript,
                )
            )

            verified_decisions.append(
                result
            )

        # ======================================================
        # VERIFY ACTION ITEMS
        # ======================================================

        verified_actions = []

        for action in actions:

            result = (
                self.verify_action_item(
                    action,
                    transcript,
                )
            )

            verified_actions.append(
                result
            )

        # ======================================================
        # DECISION COUNTS
        # ======================================================

        decision_verified = sum(
            1
            for item
            in verified_decisions
            if item.get(
                "verification_status"
            )
            in {
                "verified",
                "verified_with_warning",
            }
        )

        decision_warning = sum(
            1
            for item
            in verified_decisions
            if item.get(
                "verification_status"
            )
            == "verified_with_warning"
        )

        decision_review = sum(
            1
            for item
            in verified_decisions
            if item.get(
                "verification_status"
            )
            == "review"
        )

        decision_rejected = sum(
            1
            for item
            in verified_decisions
            if item.get(
                "verification_status"
            )
            == "rejected"
        )

        # ======================================================
        # ACTION COUNTS
        # ======================================================

        action_verified = sum(
            1
            for item
            in verified_actions
            if item.get(
                "verification_status"
            )
            in {
                "verified",
                "verified_with_warning",
            }
        )

        action_warning = sum(
            1
            for item
            in verified_actions
            if item.get(
                "verification_status"
            )
            == "verified_with_warning"
        )

        action_review = sum(
            1
            for item
            in verified_actions
            if item.get(
                "verification_status"
            )
            == "review"
        )

        action_rejected = sum(
            1
            for item
            in verified_actions
            if item.get(
                "verification_status"
            )
            == "rejected"
        )

        # ======================================================
        # OUTPUT JSON
        # ======================================================

        output = {
            "metadata": {

                "transcript_segments":
                    len(
                        transcript
                    ),

                "decisions_processed":
                    len(
                        verified_decisions
                    ),

                "decisions_verified":
                    decision_verified,

                "decisions_verified_with_warning":
                    decision_warning,

                "decisions_review":
                    decision_review,

                "decisions_rejected":
                    decision_rejected,

                "action_items_processed":
                    len(
                        verified_actions
                    ),

                "action_items_verified":
                    action_verified,

                "action_items_verified_with_warning":
                    action_warning,

                "action_items_review":
                    action_review,

                "action_items_rejected":
                    action_rejected,

                "verification_strategy":
                    "transcript_grounded_evidence_matching",

                "speaker_recovery":
                    True,

                "timestamp_recovery":
                    True,

                "confirmation_verification":
                    True,

                "source_reliability_used":
                    True,
            },

            "verified_decisions":
                verified_decisions,

            "verified_action_items":
                verified_actions,
        }

        # ======================================================
        # SAVE
        # ======================================================

        output_path = (
            self.output_dir
            / "verified_intelligence.json"
        )

        with output_path.open(
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                output,
                file,
                ensure_ascii=False,
                indent=4,
            )

        # ======================================================
        # CONSOLE REPORT
        # ======================================================

        print(
            "\n"
            + "-" * 68
        )

        print(
            "DECISION VERIFICATION"
        )

        print(
            f"Evidence-supported: "
            f"{decision_verified}"
        )

        print(
            f"With warnings:      "
            f"{decision_warning}"
        )

        print(
            f"Review:             "
            f"{decision_review}"
        )

        print(
            f"Rejected:           "
            f"{decision_rejected}"
        )

        print(
            "\nACTION ITEM VERIFICATION"
        )

        print(
            f"Evidence-supported: "
            f"{action_verified}"
        )

        print(
            f"With warnings:      "
            f"{action_warning}"
        )

        print(
            f"Review:             "
            f"{action_review}"
        )

        print(
            f"Rejected:           "
            f"{action_rejected}"
        )

        # ======================================================
        # DECISION DETAILS
        # ======================================================

        if verified_decisions:

            print(
                "\nVERIFIED DECISION DETAILS:"
            )

            for (
                index,
                item,
            ) in enumerate(
                verified_decisions,
                start=1,
            ):

                print(
                    f"\n{index}. "
                    f"{item.get('text')}"
                )

                print(
                    "   Status: "
                    f"{item.get('verification_status')}"
                )

                print(
                    "   Evidence confidence: "
                    f"{item.get('evidence_confidence')}"
                )

                print(
                    "   Proposal speaker: "
                    f"{item.get('proposal_speaker')}"
                )

                print(
                    "   Proposal timestamp: "
                    f"{item.get('proposal_timestamp')}"
                )

                if item.get(
                    "confirmation_speaker"
                ):

                    print(
                        "   Confirmation speaker: "
                        f"{item.get('confirmation_speaker')}"
                    )

                    print(
                        "   Confirmation timestamp: "
                        f"{item.get('verified_confirmation_timestamp')}"
                    )

                if item.get(
                    "verification_review_reasons"
                ):

                    print(
                        "   Review reasons: "
                        + ", ".join(
                            item.get(
                                "verification_review_reasons"
                            )
                        )
                    )

        print(
            "\nSaved to:"
        )

        print(
            output_path
        )

        print(
            "\n"
            + "=" * 68
        )

        return output_path


# ==============================================================
# DIRECT TEST
# ==============================================================

if __name__ == "__main__":

    verifier = (
        EvidenceVerifier()
    )

    verifier.process(

        transcript_path=(
            "output/transcripts/"
            "meeting_transcript_ready.json"
        ),

        decisions_path=(
            "output/intelligence/"
            "decisions.json"
        ),

        action_items_path=(
            "output/intelligence/"
            "action_items.json"
        ),
    )