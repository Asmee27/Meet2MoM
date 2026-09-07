from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class ActionItemExtractor:
    """
    Meet2MoM - Context-Aware Action Item Extraction

    Inputs:
        output/transcripts/meeting_transcript_ready.json
        output/intelligence/topics.json

    Output:
        output/intelligence/action_items.json

    Goals:
        - Detect genuine commitments / assignments
        - Extract explicit and implicit owners
        - Preserve transcript evidence + timestamps
        - Detect deadlines
        - Reject proposals, questions, past events and future facts
        - Flag conditional / ambiguous items for review
        - Avoid grammar-only false positives
        - Remain lightweight for long meetings
    """

    def __init__(
        self,
        output_dir: str = "output/intelligence",
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # --------------------------------------------------
        # Strong commitment / assignment indicators
        # --------------------------------------------------

        self.commitment_patterns = [
            r"\bi(?:'ll| will)\b",
            r"\bwe(?:'ll| will)\b",
            r"\bi can\b",
            r"\bwe can\b",
            r"\bi(?:'m| am) going to\b",
            r"\bwe(?:'re| are) going to\b",
            r"\bi(?:'ll| will) take care of\b",
            r"\bi(?:'ll| will) handle\b",
            r"\bi(?:'ll| will) send\b",
            r"\bi(?:'ll| will) prepare\b",
            r"\bi(?:'ll| will) create\b",
            r"\bi(?:'ll| will) update\b",
            r"\bi(?:'ll| will) check\b",
            r"\bi(?:'ll| will) review\b",
            r"\bi(?:'ll| will) follow up\b",
            r"\bi(?:'ll| will) contact\b",
            r"\bi(?:'ll| will) arrange\b",
        ]

        # Named-person assignments:
        # "Maria will develop..."
        self.named_assignment_pattern = re.compile(
            r"^\s*"
            r"([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'-]{1,40})"
            r"\s+"
            r"(?:will|shall|can|is going to|is to)"
            r"\s+"
            r"(.+)",
            re.IGNORECASE,
        )

        # Direct assignment:
        # "David, please send the report."
        self.direct_assignment_pattern = re.compile(
            r"^\s*"
            r"([A-Z][A-Za-zÀ-ÖØ-öø-ÿ'-]{1,40})"
            r"\s*,\s*"
            r"(?:please\s+)?"
            r"(.+)",
            re.IGNORECASE,
        )

        # --------------------------------------------------
        # Proposal / hypothetical indicators
        # --------------------------------------------------

        self.proposal_patterns = [
            r"\bwe could\b",
            r"\bi could\b",
            r"\bcould we\b",
            r"\bperhaps\b",
            r"\bmaybe\b",
            r"\bwhat if\b",
            r"\bhow about\b",
            r"\bwhat about\b",
            r"\bi suggest\b",
            r"\bi propose\b",
            r"\bi recommend\b",
            r"\bwe might\b",
            r"\bwe should consider\b",
            r"\bcould consider\b",
            r"\bi wondered if\b",
        ]

        # --------------------------------------------------
        # Questions
        # --------------------------------------------------

        self.question_patterns = [
            r"\?$",
            r"^\s*(?:what|why|when|where|who|how)\b",
            r"^\s*(?:can|could|would|should|will)\s+(?:you|we|i)\b",
        ]

        # --------------------------------------------------
        # Past / completed event indicators
        # --------------------------------------------------

        self.past_patterns = [
            r"\bwas completed\b",
            r"\bwere completed\b",
            r"\bhas been completed\b",
            r"\bhave been completed\b",
            r"\balready completed\b",
            r"\balready sent\b",
            r"\bwas sent\b",
            r"\bwere sent\b",
            r"\bhad sent\b",
            r"\bfinished yesterday\b",
            r"\bcompleted yesterday\b",
            r"\blast week\b",
            r"\blast month\b",
        ]

        # --------------------------------------------------
        # Statements that often describe future state,
        # not someone's task.
        # --------------------------------------------------

        self.non_action_future_patterns = [
            r"\bwill be affected\b",
            r"\bwill be enough\b",
            r"\bwill happen\b",
            r"\bwill occur\b",
            r"\bwill depend\b",
            r"\bwill probably\b",
            r"\bwill likely\b",
        ]

        # --------------------------------------------------
        # Uncertainty
        # --------------------------------------------------

        self.uncertainty_patterns = [
            r"\bnot sure\b",
            r"\bunsure\b",
            r"\bdon'?t know\b",
            r"\bdo not know\b",
            r"\bdepends\b",
            r"\bwe'?ll see\b",
            r"\bwe will see\b",
        ]

        self.future_decision_patterns = [
    r"\bwe'?ll decide\b",
    r"\bwe will decide\b",
    r"\bwe can decide\b",
    r"\bwe could decide\b",
    r"\bwe'?ll be able to decide\b",
    r"\bwe will be able to decide\b",
    r"\bbe able to decide\b",
    r"\bdecide later\b",
    r"\bdecide what\b",
    r"\bdecide whether\b",
    r"\bdecide how\b",
    r"\bneed to decide\b",
    r"\bstill need to decide\b",
    r"\bdecision will be made\b",
]

        # --------------------------------------------------
        # Conditional statements
        # --------------------------------------------------

        self.conditional_patterns = [
            r"\bif\b",
            r"\bunless\b",
            r"\bprovided that\b",
            r"\bsubject to\b",
            r"\bdepending on\b",
        ]

        # --------------------------------------------------
        # Deadline expressions
        # --------------------------------------------------

        self.deadline_patterns = [
            r"\bby\s+(today|tomorrow|tonight)\b",
            r"\b(today|tomorrow|tonight)\b",
            r"\bby\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            r"\b(?:on\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            r"\bby\s+(?:the\s+)?end\s+of\s+(?:the\s+)?(?:day|week|month)\b",
            r"\b(?:before|by)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
            r"\b(?:before|by)\s+\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b",
            r"\b(?:before|by)\s+[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?\b",
            r"\bnext\s+(?:week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            r"\bwithin\s+\d+\s+(?:hour|hours|day|days|week|weeks)\b",
        ]

        # --------------------------------------------------
        # Words that should not become owners
        # --------------------------------------------------

        self.invalid_owner_words = {
            "who",
            "what",
            "when",
            "where",
            "why",
            "how",
            "course",
            "report",
            "training",
            "customer",
            "customers",
            "staff",
            "team",
            "teams",
            "company",
            "meeting",
            "program",
            "programme",
            "survey",
            "research",
            "approach",
            "problem",
            "problems",
            "issue",
            "issues",
            "this",
            "that",
            "it",
            "there",
        }

    # ======================================================
    # UTILITIES
    # ======================================================

    @staticmethod
    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", str(text)).strip()

    @staticmethod
    def matches(
        text: str,
        patterns: List[str],
    ) -> bool:
        return any(
            re.search(
                pattern,
                text,
                re.IGNORECASE,
            )
            for pattern in patterns
        )

    # ======================================================
    # LOAD JSON
    # ======================================================

    @staticmethod
    def load_json(path: str) -> Any:
        file_path = Path(path)

        if not file_path.exists():
            raise FileNotFoundError(
                f"File not found: {file_path}"
            )

        with file_path.open(
            "r",
            encoding="utf-8",
        ) as file:
            return json.load(file)

    # ======================================================
    # LOAD TRANSCRIPT
    # ======================================================

    def load_transcript(
        self,
        path: str,
    ) -> List[Dict[str, Any]]:

        data = self.load_json(path)

        if isinstance(data, list):
            segments = data

        elif isinstance(data, dict):
            segments = None

            for key in [
                "segments",
                "transcript",
                "speaker_transcript",
            ]:
                value = data.get(key)

                if isinstance(value, list):
                    segments = value
                    break

            if segments is None:
                raise ValueError(
                    "Could not find transcript segments."
                )

        else:
            raise ValueError(
                "Unsupported transcript JSON structure."
            )

        cleaned = []

        for segment in segments:
            text = self.normalize(
                segment.get("text", "")
            )

            if not text:
                continue

            item = dict(segment)
            item["text"] = text

            cleaned.append(item)

        return cleaned

    # ======================================================
    # LOAD TOPICS
    # ======================================================

    def load_topics(
        self,
        path: str,
    ) -> List[Dict[str, Any]]:

        data = self.load_json(path)

        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            topics = data.get(
                "topics",
                [],
            )

            if isinstance(topics, list):
                return topics

        raise ValueError(
            "Unsupported topics JSON structure."
        )

    # ======================================================
    # FIND TOPIC FOR TRANSCRIPT SEGMENT
    # ======================================================

    @staticmethod
    def find_topic(
        segment: Dict[str, Any],
        topics: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:

        start = float(
            segment.get("start", 0)
        )

        end = float(
            segment.get("end", start)
        )

        midpoint = (
            start + end
        ) / 2

        for topic in topics:
            topic_start = float(
                topic.get("start", 0)
            )

            topic_end = float(
                topic.get(
                    "end",
                    topic_start,
                )
            )

            if (
                topic_start
                <= midpoint
                <= topic_end
            ):
                return topic

        return None

    # ======================================================
    # DEADLINE
    # ======================================================

    def extract_deadline(
        self,
        text: str,
    ) -> Optional[str]:

        for pattern in self.deadline_patterns:
            match = re.search(
                pattern,
                text,
                re.IGNORECASE,
            )

            if match:
                return self.normalize(
                    match.group(0)
                )

        return None

    # ======================================================
    # CLEAN TASK
    # ======================================================

    def clean_task(
        self,
        text: str,
        owner: Optional[str],
    ) -> str:

        task = self.normalize(text)

        # Remove owner prefix:
        # "Maria will develop..." -> "develop..."
        if owner:
            task = re.sub(
                rf"^\s*{re.escape(owner)}"
                r"\s+(?:will|shall|can|"
                r"is going to|is to)\s+",
                "",
                task,
                flags=re.IGNORECASE,
            )

            task = re.sub(
                rf"^\s*{re.escape(owner)}"
                r"\s*,\s*(?:please\s+)?",
                "",
                task,
                flags=re.IGNORECASE,
            )

        # Speaker commitment:
        # "I'll send..." -> "send..."
        task = re.sub(
            r"^\s*i(?:'ll| will)\s+",
            "",
            task,
            flags=re.IGNORECASE,
        )

        task = re.sub(
            r"^\s*i(?:'m| am) going to\s+",
            "",
            task,
            flags=re.IGNORECASE,
        )

        task = re.sub(
            r"^\s*i can\s+",
            "",
            task,
            flags=re.IGNORECASE,
        )

        # Group commitment
        task = re.sub(
            r"^\s*we(?:'ll| will)\s+",
            "",
            task,
            flags=re.IGNORECASE,
        )

        task = re.sub(
            r"^\s*we(?:'re| are) going to\s+",
            "",
            task,
            flags=re.IGNORECASE,
        )

        task = re.sub(
            r"^\s*we can\s+",
            "",
            task,
            flags=re.IGNORECASE,
        )

        return task.strip(
            " .,:;-"
        )

    # ======================================================
    # OWNER DETECTION
    # ======================================================

    def detect_owner(
        self,
        text: str,
        speaker: str,
    ) -> Tuple[
        Optional[str],
        str,
        Optional[str],
    ]:

        # --------------------------------------------------
        # 1. First-person commitment
        # --------------------------------------------------

        if re.search(
            r"\bi(?:'ll| will| can|'m going to| am going to)\b",
            text,
            re.IGNORECASE,
        ):
            return (
                speaker,
                "speaker_commitment",
                None,
            )

        # --------------------------------------------------
        # 2. Named-person assignment
        # --------------------------------------------------

        match = self.named_assignment_pattern.search(
            text
        )

        if match:
            candidate = self.normalize(
                match.group(1)
            )

            if (
                candidate.lower()
                not in self.invalid_owner_words
            ):
                return (
                    candidate,
                    "explicit_name",
                    match.group(2),
                )

        # --------------------------------------------------
        # 3. Direct address
        # --------------------------------------------------

        direct_match = (
            self.direct_assignment_pattern.search(
                text
            )
        )

        if direct_match:
            candidate = self.normalize(
                direct_match.group(1)
            )

            remainder = self.normalize(
                direct_match.group(2)
            )

            if (
                candidate.lower()
                not in self.invalid_owner_words
                and self.looks_like_imperative(
                    remainder
                )
            ):
                return (
                    candidate,
                    "direct_assignment",
                    remainder,
                )

        # --------------------------------------------------
        # 4. Group commitment
        # --------------------------------------------------

        if re.search(
            r"\bwe(?:'ll| will|'re going to| are going to| can)\b",
            text,
            re.IGNORECASE,
        ):
            return (
                "GROUP",
                "group_commitment",
                None,
            )

        return (
            None,
            "unassigned",
            None,
        )

    # ======================================================
    # IMPERATIVE HEURISTIC
    # ======================================================

    @staticmethod
    def looks_like_imperative(
        text: str,
    ) -> bool:

        if not text:
            return False

        first_word = (
            text.split()[0]
            .lower()
            .strip(".,!?")
        )

        likely_action_verbs = {
            "send",
            "prepare",
            "review",
            "check",
            "update",
            "create",
            "develop",
            "run",
            "contact",
            "call",
            "email",
            "share",
            "schedule",
            "arrange",
            "complete",
            "finish",
            "submit",
            "upload",
            "download",
            "write",
            "collect",
            "analyse",
            "analyze",
            "investigate",
            "confirm",
            "verify",
            "follow",
            "look",
            "discuss",
        }

        return (
            first_word
            in likely_action_verbs
        )

    # ======================================================
    # ACTION VERB CHECK
    # ======================================================

    @staticmethod
    def contains_action_verb(
        text: str,
    ) -> bool:

        verbs = {
            "send",
            "prepare",
            "review",
            "check",
            "update",
            "create",
            "develop",
            "run",
            "contact",
            "call",
            "email",
            "share",
            "schedule",
            "arrange",
            "complete",
            "finish",
            "submit",
            "upload",
            "write",
            "collect",
            "analyse",
            "analyze",
            "investigate",
            "confirm",
            "verify",
            "implement",
            "deliver",
            "provide",
            "organize",
            "organise",
            "coordinate",
            "follow",
            "look",
            "address",
            "handle",
        }

        words = set(
            re.findall(
                r"[A-Za-z]+",
                text.lower(),
            )
        )

        return bool(
            words.intersection(verbs)
        )

    # ======================================================
    # CLASSIFY SEGMENT
    # ======================================================

    def classify_segment(
        self,
        text: str,
    ) -> Dict[str, bool]:

        return {
            "proposal": self.matches(
                text,
                self.proposal_patterns,
            ),

            "question": self.matches(
                text,
                self.question_patterns,
            ),

            "past_event": self.matches(
                text,
                self.past_patterns,
            ),

            "non_action_future": self.matches(
                text,
                self.non_action_future_patterns,
            ),

            "uncertainty": self.matches(
                text,
                self.uncertainty_patterns,
            ),

            "future_decision": self.matches(
                text,
                self.future_decision_patterns,
    ),

            "conditional": self.matches(
                text,
                self.conditional_patterns,
            ),

            "commitment": self.matches(
                text,
                self.commitment_patterns,
            ),
        }

    # ======================================================
    # CREATE ACTION RECORD
    # ======================================================

    def create_action_record(
        self,
        segment: Dict[str, Any],
        topic: Optional[Dict[str, Any]],
        task: str,
        owner: Optional[str],
        owner_type: str,
        deadline: Optional[str],
        confidence: float,
        conditional: bool,
        requires_review: bool,
        review_reasons: List[str],
    ) -> Dict[str, Any]:

        return {
            "task": task,

            "owner": owner,

            "owner_type": owner_type,

            "due_date": None,

            "deadline_text": deadline,

            "status": "active",

            "confidence": round(
                confidence,
                3,
            ),

            "speaker": segment.get(
                "speaker",
                "UNKNOWN",
            ),

            "timestamp": round(
                float(
                    segment.get(
                        "start",
                        0,
                    )
                ),
                2,
            ),

            "end_timestamp": round(
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

            "topic_id": (
                topic.get("topic_id")
                if topic
                else None
            ),

            "topic_label": (
                topic.get(
                    "topic_label",
                    "Unknown Topic",
                )
                if topic
                else "Unknown Topic"
            ),

            "evidence_text": segment.get(
                "text",
                "",
            ),

            "conditional": conditional,

            "requires_review": (
                requires_review
            ),

            "review_reasons": (
                review_reasons
            ),

            # Preserve reliability information
            # from speaker-merging stage.
            "speaker_attribution_confidence":
                segment.get(
                    "speaker_attribution_confidence"
                ),

            "speaker_confidence_flag":
                segment.get(
                    "speaker_confidence_flag"
                ),

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
        }

    # ======================================================
    # EXTRACT FROM ONE SEGMENT
    # ======================================================

    def extract_from_segment(
        self,
        segment: Dict[str, Any],
        topic: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:

        text = self.normalize(
            segment.get(
                "text",
                "",
            )
        )

        if not text:
            return None

        flags = self.classify_segment(
            text
        )

        # --------------------------------------------------
        # Reject obvious non-actions
        # --------------------------------------------------

        if flags["proposal"]:
            return None

        if flags["question"]:
            return None

        if flags["past_event"]:
            return None

        if flags["non_action_future"]:
            return None

        if flags["uncertainty"]:
            return None
        if flags["future_decision"]:
            return None

        speaker = str(
            segment.get(
                "speaker",
                "UNKNOWN",
            )
        )

        (
            owner,
            owner_type,
            task_override,
        ) = self.detect_owner(
            text,
            speaker,
        )

        # --------------------------------------------------
        # Need commitment/assignment evidence.
        # --------------------------------------------------

        assignment_detected = (
            owner is not None
        )

        if (
            not flags["commitment"]
            and not assignment_detected
        ):
            return None

        task_source = (
            task_override
            if task_override
            else text
        )

        task = self.clean_task(
            task_source,
            owner,
        )

        if not task:
            return None

        # --------------------------------------------------
        # Require meaningful action verb.
        # Helps prevent:
        # "course will be enough"
        # etc.
        # --------------------------------------------------

        if not self.contains_action_verb(
            task
        ):
            return None

        deadline = self.extract_deadline(
            text
        )

        confidence = 0.78

        review_reasons = []

        # --------------------------------------------------
        # Confidence adjustments
        # --------------------------------------------------

        if owner_type == "explicit_name":
            confidence += 0.10

        elif owner_type == "speaker_commitment":
            confidence += 0.08

        elif owner_type == "direct_assignment":
            confidence += 0.08

        elif owner_type == "group_commitment":
            confidence -= 0.08

            review_reasons.append(
                "group_owner_requires_resolution"
            )

        if flags["conditional"]:
            confidence -= 0.18

            review_reasons.append(
                "conditional_action"
            )

        if speaker == "UNKNOWN":
            confidence -= 0.15

            review_reasons.append(
                "unknown_speaker"
            )

        speaker_flag = segment.get(
            "speaker_confidence_flag"
        )

        if speaker_flag in {
            "low",
            "uncertain",
        }:
            confidence -= 0.10

            review_reasons.append(
                "uncertain_speaker_attribution"
            )

        if segment.get(
            "possible_overlap",
            False,
        ):
            confidence -= 0.08

            review_reasons.append(
                "possible_cross_talk"
            )

        if segment.get(
            "requires_review",
            False,
        ):
            review_reasons.append(
                "source_segment_requires_review"
            )

        confidence = max(
            0.0,
            min(
                confidence,
                0.98,
            ),
        )

        requires_review = (
            confidence < 0.70
            or bool(review_reasons)
        )

        return self.create_action_record(
            segment=segment,
            topic=topic,
            task=task,
            owner=owner,
            owner_type=owner_type,
            deadline=deadline,
            confidence=confidence,
            conditional=flags[
                "conditional"
            ],
            requires_review=requires_review,
            review_reasons=review_reasons,
        )

    # ======================================================
    # DEDUPLICATION
    # ======================================================

    @staticmethod
    def normalize_for_dedup(
        text: str,
    ) -> str:

        text = text.lower()

        text = re.sub(
            r"[^a-z0-9\s]",
            " ",
            text,
        )

        return re.sub(
            r"\s+",
            " ",
            text,
        ).strip()

    def deduplicate_actions(
        self,
        actions: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:

        unique = []
        seen = set()

        for action in actions:
            key = (
                self.normalize_for_dedup(
                    action.get(
                        "task",
                        "",
                    )
                ),
                str(
                    action.get(
                        "owner",
                        "",
                    )
                ).lower(),
            )

            if key in seen:
                continue

            seen.add(key)
            unique.append(action)

        return unique

    # ======================================================
    # SAVE
    # ======================================================

    def save_results(
        self,
        actions: List[Dict[str, Any]],
    ) -> Path:

        output_file = (
            self.output_dir
            / "action_items.json"
        )

        review_items = [
            item
            for item in actions
            if item.get(
                "requires_review"
            )
        ]

        assigned = [
            item
            for item in actions
            if item.get("owner")
            not in {
                None,
                "",
                "GROUP",
            }
        ]

        group_owned = [
            item
            for item in actions
            if item.get("owner")
            == "GROUP"
        ]

        output = {
            "metadata": {
                "total_action_items":
                    len(actions),

                "assigned_action_items":
                    len(assigned),

                "group_owned_action_items":
                    len(group_owned),

                "requires_review":
                    len(review_items),

                "processing_strategy":
                    "context_aware_transcript_level",

                "proposal_filtering":
                    True,

                "question_filtering":
                    True,

                "past_event_filtering":
                    True,

                "future_fact_filtering":
                    True,

                "speaker_reliability_used":
                    True,

                "topic_context_used":
                    True,
            },

            "action_items": actions,
        }

        with output_file.open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                output,
                file,
                ensure_ascii=False,
                indent=4,
            )

        return output_file

    # ======================================================
    # FULL PROCESS
    # ======================================================

    def process(
        self,
        transcript_path: str,
        topics_path: str,
    ) -> Path:

        print(
            "\n"
            + "=" * 68
        )

        print(
            "Meet2MoM - Context-Aware Action Item Extraction"
        )

        print(
            "=" * 68
        )

        segments = self.load_transcript(
            transcript_path
        )

        topics = self.load_topics(
            topics_path
        )

        print(
            f"\nTranscript segments: "
            f"{len(segments)}"
        )

        print(
            f"Topics loaded: "
            f"{len(topics)}"
        )

        actions = []

        for segment in segments:
            topic = self.find_topic(
                segment,
                topics,
            )

            action = (
                self.extract_from_segment(
                    segment,
                    topic,
                )
            )

            if action is not None:
                actions.append(
                    action
                )

        actions = self.deduplicate_actions(
            actions
        )

        output_file = self.save_results(
            actions
        )

        print(
            "\n"
            + "-" * 68
        )

        print(
            f"Final action items: "
            f"{len(actions)}"
        )

        print(
            "Requires review: "
            f"{sum(1 for item in actions if item['requires_review'])}"
        )

        print(
            "\nAction items saved to:"
        )

        print(
            output_file
        )

        if actions:

            print(
                "\nACTION ITEMS:"
            )

            for index, action in enumerate(
                actions,
                start=1,
            ):
                print(
                    f"\n{index}. "
                    f"{action['task']}"
                )

                print(
                    "   Owner: "
                    f"{action['owner']}"
                )

                print(
                    "   Owner type: "
                    f"{action['owner_type']}"
                )

                print(
                    "   Confidence: "
                    f"{action['confidence']}"
                )

                if action.get(
                    "deadline_text"
                ):
                    print(
                        "   Deadline: "
                        f"{action['deadline_text']}"
                    )

                if action.get(
                    "requires_review"
                ):
                    print(
                        "   Review: "
                        + ", ".join(
                            action[
                                "review_reasons"
                            ]
                        )
                    )

        else:
            print(
                "\nNo reliable action items "
                "were detected."
            )

        print(
            "\n"
            + "=" * 68
        )

        return output_file


# ==========================================================
# RUN DIRECTLY
# ==========================================================

if __name__ == "__main__":

    transcript_file = (
        "output/transcripts/"
        "meeting_transcript_ready.json"
    )

    topics_file = (
        "output/intelligence/"
        "topics.json"
    )

    extractor = ActionItemExtractor()

    extractor.process(
        transcript_file,
        topics_file,
    )