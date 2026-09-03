import json
import re
from pathlib import Path
from typing import Dict, List, Any, Optional


class DecisionExtractor:
    """
    Meet2MoM - Context-Aware Decision Extraction

    Input:
        output/intelligence/topics.json

    Output:
        output/intelligence/decisions.json

    Handles:
        - Suggestions / proposals
        - Confirmed decisions
        - Short acceptance of previous proposal
        - Rejections
        - Decision reversals
        - Uncertainty
        - Future intention to decide
        - Conditional statements
        - Long meetings using topic-by-topic processing
    """

    def __init__(
        self,
        output_dir: str = "output/intelligence"
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Strong explicit decision language
        self.decision_patterns = [
            r"\bwe decided\b",
            r"\bwe have decided\b",
            r"\bwe've decided\b",
            r"\bwe agreed\b",
            r"\bwe have agreed\b",
            r"\bit is agreed\b",
            r"\bwe will go with\b",
            r"\bwe'll go with\b",
            r"\blet'?s go with\b",
            r"\blet'?s use\b",
            r"\blet'?s proceed\b",
            r"\bwe chose\b",
            r"\bwe have chosen\b",
            r"\bwe selected\b",
            r"\bwe have selected\b",
            r"\bapproved\b",
            r"\bconfirmed\b",
            r"\bthe final decision\b",
            r"\bthe decision is\b",
            r"\bwe are proceeding with\b",
            r"\bwe're proceeding with\b"
        ]

        # Proposal / suggestion language
        self.proposal_patterns = [
            r"\bwe could\b",
            r"\bcould we\b",
            r"\bi wondered if\b",
            r"\bperhaps\b",
            r"\bmaybe\b",
            r"\bhow about\b",
            r"\bwhat about\b",
            r"\bwhat if\b",
            r"\bi suggest\b",
            r"\bi propose\b",
            r"\bi recommend\b",
            r"\bwe might\b",
            r"\bwe should consider\b",
            r"\bcould consider\b"
        ]

        # Acceptance of previous proposal
        self.acceptance_patterns = [
            r"^\s*yes\b",
            r"^\s*yeah\b",
            r"^\s*yep\b",
            r"\bi agree\b",
            r"\bagreed\b",
            r"\bsounds good\b",
            r"\bthat works\b",
            r"\bthat would work\b",
            r"\blet'?s do that\b",
            r"\bwe can do that\b",
            r"\bgo ahead with that\b",
            r"\bthat makes sense\b"
        ]

        # Rejection
        self.rejection_patterns = [
            r"^\s*no\b",
            r"\bi disagree\b",
            r"\bdon'?t do that\b",
            r"\bdo not do that\b",
            r"\bwon'?t work\b",
            r"\bwill not work\b",
            r"\bthat won'?t work\b",
            r"\bnot approved\b",
            r"\breject(?:ed)?\b",
            r"\bcancel(?:led)?\b",
            r"\bdrop(?:ped)?\b",
            r"\bforget that\b",
            r"\bnot going with\b",
            r"\bwe won'?t\b",
            r"\bwe will not\b"
        ]

        # Reversal / correction
        self.reversal_patterns = [
            r"\bactually\b",
            r"\binstead\b",
            r"\brather\b",
            r"\bchanged our mind\b",
            r"\bchange our decision\b",
            r"\bchange that\b",
            r"\bforget that\b",
            r"\bcorrection\b",
            r"\bno longer\b"
        ]

        # Uncertainty = NOT a decision
        self.uncertainty_patterns = [
            r"\bdon'?t know\b",
            r"\bdo not know\b",
            r"\bnot sure\b",
            r"\bunsure\b",
            r"\buncertain\b",
            r"\bhaven'?t decided\b",
            r"\bhave not decided\b",
            r"\bnot decided\b",
            r"\bnot yet decided\b",
            r"\byet to decide\b",
            r"\bneed to decide\b",
            r"\bstill deciding\b",
            r"\bdepends\b",
            r"\bwe'll see\b",
            r"\bwe will see\b"
        ]

        # Talking ABOUT a future decision != decision
        self.future_decision_patterns = [
            r"\bwe'?ll decide\b",
            r"\bwe will decide\b",
            r"\bwe can decide\b",
            r"\bwe could decide\b",
            r"\bable to decide\b",
            r"\bdecide later\b",
            r"\bdecide what\b",
            r"\bdecide whether\b",
            r"\bdecision will be made\b",
            r"\bmake a decision later\b"
        ]

        self.conditional_patterns = [
            r"\bif\b",
            r"\bunless\b",
            r"\bprovided that\b",
            r"\bsubject to\b",
            r"\bdepending on\b"
        ]

        # Statements that are normally acknowledgements,
        # not proposals/decisions.
        self.acknowledgement_patterns = [
            r"\bi understand\b",
            r"\bi see your point\b",
            r"\bthat'?s true\b",
            r"\bthat is true\b",
            r"\bi see\b",
            r"\bunderstood\b",
            r"\bi get your point\b"
        ]

    # ======================================================
    # UTILITIES
    # ======================================================

    @staticmethod
    def matches(text: str, patterns: List[str]) -> bool:
        return any(
            re.search(pattern, text, re.IGNORECASE)
            for pattern in patterns
        )

    @staticmethod
    def normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    # ======================================================
    # LOAD TOPICS
    # ======================================================

    def load_topics(self, path: str) -> List[Dict[str, Any]]:

        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(
                f"Topics file not found: {path}"
            )

        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, list):
            topics = data

        elif isinstance(data, dict):
            topics = data.get("topics", [])

        else:
            raise ValueError(
                "Unsupported topics JSON structure."
            )

        if not isinstance(topics, list):
            raise ValueError(
                "'topics' must be a list."
            )

        return topics

    # ======================================================
    # SENTENCE SPLITTING
    # ======================================================

    def split_sentences(self, text: str) -> List[str]:

        if not text:
            return []

        # Handles punctuation while keeping the system
        # lightweight for long transcripts.
        sentences = re.split(
            r"(?<=[.!?])\s+",
            text.strip()
        )

        return [
            self.normalize(sentence)
            for sentence in sentences
            if self.normalize(sentence)
        ]

    # ======================================================
    # ANALYSE SENTENCE
    # ======================================================

    def analyse_sentence(self, sentence: str) -> Dict[str, bool]:

        return {
            "explicit_decision": self.matches(
                sentence,
                self.decision_patterns
            ),

            "proposal": self.matches(
                sentence,
                self.proposal_patterns
            ),

            "acceptance": self.matches(
                sentence,
                self.acceptance_patterns
            ),

            "rejection": self.matches(
                sentence,
                self.rejection_patterns
            ),

            "reversal": self.matches(
                sentence,
                self.reversal_patterns
            ),

            "uncertainty": self.matches(
                sentence,
                self.uncertainty_patterns
            ),

            "future_decision": self.matches(
                sentence,
                self.future_decision_patterns
            ),

            "conditional": self.matches(
                sentence,
                self.conditional_patterns
            ),

            "acknowledgement": self.matches(
                sentence,
                self.acknowledgement_patterns
            )
        }

    # ======================================================
    # APPROXIMATE TIMESTAMP
    # ======================================================

    @staticmethod
    def estimate_timestamp(
        topic: Dict[str, Any],
        sentence_index: int,
        total_sentences: int
    ) -> float:

        start = float(topic.get("start", 0))
        end = float(topic.get("end", start))

        if total_sentences <= 1:
            return round(start, 2)

        ratio = sentence_index / total_sentences

        timestamp = start + ((end - start) * ratio)

        return round(timestamp, 2)

    # ======================================================
    # CREATE RECORD
    # ======================================================

    def create_record(
        self,
        topic: Dict[str, Any],
        sentence: str,
        sentence_index: int,
        total_sentences: int,
        record_type: str,
        confidence: float,
        status: str,
        requires_review: bool = False,
        review_reasons: Optional[List[str]] = None
    ) -> Dict[str, Any]:

        speakers = topic.get("speakers", [])

        speaker = (
            speakers[0]
            if len(speakers) == 1
            else "UNKNOWN"
        )

        return {
            "topic_id": topic.get("topic_id"),

            "topic_label": topic.get(
                "topic_label",
                "Unknown Topic"
            ),

            "text": sentence,

            "decision_type": record_type,

            "speaker": speaker,

            "timestamp": self.estimate_timestamp(
                topic,
                sentence_index,
                total_sentences
            ),

            "topic_start": round(
                float(topic.get("start", 0)),
                2
            ),

            "topic_end": round(
                float(
                    topic.get(
                        "end",
                        topic.get("start", 0)
                    )
                ),
                2
            ),

            "confidence": round(confidence, 3),

            "status": status,

            "requires_review": requires_review,

            "review_reasons": review_reasons or [],

            "superseded_by": None
        }

    # ======================================================
    # CONFIRM PREVIOUS PROPOSAL
    # ======================================================

    def confirm_proposal(
        self,
        proposal: Dict[str, Any],
        acceptance_sentence: str,
        acceptance_timestamp: float
    ) -> Dict[str, Any]:

        """
        Converts a previous proposal into a confirmed
        decision when a later sentence accepts it.

        Example:
            "Perhaps we could review the report."
            "Yes, we could do that."

        The decision keeps the meaningful proposal text
        instead of storing "Yes".
        """

        confirmed = proposal.copy()

        confirmed["decision_type"] = (
            "confirmed_from_proposal"
        )

        confirmed["status"] = "active"

        confirmed["confidence"] = 0.85

        confirmed["requires_review"] = False

        confirmed["review_reasons"] = []

        confirmed["confirmation_text"] = (
            acceptance_sentence
        )

        confirmed["confirmation_timestamp"] = (
            acceptance_timestamp
        )

        confirmed["original_proposal"] = (
            proposal["text"]
        )

        return confirmed

    # ======================================================
    # PROCESS ONE TOPIC
    # ======================================================

    def extract_from_topic(
        self,
        topic: Dict[str, Any]
    ) -> List[Dict[str, Any]]:

        text = str(topic.get("text", ""))

        sentences = self.split_sentences(text)

        records = []

        # Tracks the most recent unresolved proposal.
        pending_proposal = None

        for index, sentence in enumerate(sentences):

            flags = self.analyse_sentence(sentence)

            timestamp = self.estimate_timestamp(
                topic,
                index,
                len(sentences)
            )

            # ----------------------------------------------
            # 1. UNCERTAINTY
            # Definitely not a final decision.
            # ----------------------------------------------

            if flags["uncertainty"]:

                record = self.create_record(
                    topic,
                    sentence,
                    index,
                    len(sentences),
                    "uncertainty",
                    0.90,
                    "excluded"
                )

                records.append(record)

                continue

            # ----------------------------------------------
            # 2. FUTURE INTENTION TO DECIDE
            # "We'll decide later"
            # ----------------------------------------------

            if flags["future_decision"]:

                record = self.create_record(
                    topic,
                    sentence,
                    index,
                    len(sentences),
                    "future_decision",
                    0.90,
                    "excluded"
                )

                records.append(record)

                continue

            # ----------------------------------------------
            # 3. PURE ACKNOWLEDGEMENT
            # ----------------------------------------------

            if (
                flags["acknowledgement"]
                and not flags["explicit_decision"]
                and not flags["acceptance"]
            ):

                continue

            # ----------------------------------------------
            # 4. REJECTION
            # ----------------------------------------------

            if flags["rejection"]:

                record = self.create_record(
                    topic,
                    sentence,
                    index,
                    len(sentences),
                    "rejection",
                    0.85,
                    "rejected"
                )

                if pending_proposal is not None:

                    pending_proposal["status"] = (
                        "rejected"
                    )

                    pending_proposal[
                        "rejected_by"
                    ] = sentence

                    pending_proposal[
                        "rejection_timestamp"
                    ] = timestamp

                    pending_proposal = None

                records.append(record)

                continue

            # ----------------------------------------------
            # 5. ACCEPTANCE OF PREVIOUS PROPOSAL
            # ----------------------------------------------

            if (
                flags["acceptance"]
                and pending_proposal is not None
            ):

                confirmed = self.confirm_proposal(
                    pending_proposal,
                    sentence,
                    timestamp
                )

                # Previous proposal remains in audit trail,
                # but becomes confirmed.
                pending_proposal["status"] = (
                    "confirmed"
                )

                pending_proposal[
                    "confirmed_by"
                ] = sentence

                pending_proposal[
                    "confirmation_timestamp"
                ] = timestamp

                records.append(confirmed)

                pending_proposal = None

                continue

            # ----------------------------------------------
            # 6. REVERSAL + EXPLICIT DECISION
            # ----------------------------------------------

            if (
                flags["reversal"]
                and flags["explicit_decision"]
            ):

                record = self.create_record(
                    topic,
                    sentence,
                    index,
                    len(sentences),
                    "reversal_decision",
                    0.90,
                    "active"
                )

                # Supersede previous active decision
                for previous in reversed(records):

                    if previous.get("status") == "active":

                        previous["status"] = (
                            "superseded"
                        )

                        previous["superseded_by"] = (
                            sentence
                        )

                        break

                records.append(record)

                pending_proposal = None

                continue

            # ----------------------------------------------
            # 7. EXPLICIT DECISION
            # ----------------------------------------------

            if flags["explicit_decision"]:

                review_reasons = []

                confidence = 0.90

                requires_review = False

                if flags["conditional"]:

                    confidence = 0.65

                    requires_review = True

                    review_reasons.append(
                        "conditional_decision"
                    )

                record = self.create_record(
                    topic,
                    sentence,
                    index,
                    len(sentences),
                    "explicit_decision",
                    confidence,
                    "active",
                    requires_review,
                    review_reasons
                )

                records.append(record)

                pending_proposal = None

                continue

            # ----------------------------------------------
            # 8. PROPOSAL
            # ----------------------------------------------

            if flags["proposal"]:

                # Acknowledgement containing "possibly"
                # should not become a proposal.
                if flags["acknowledgement"]:
                    continue

                record = self.create_record(
                    topic,
                    sentence,
                    index,
                    len(sentences),
                    "proposal",
                    0.55,
                    "pending",
                    True,
                    ["proposal_not_yet_confirmed"]
                )

                records.append(record)

                pending_proposal = record

                continue

            # ----------------------------------------------
            # 9. Short acceptance with no proposal
            # ----------------------------------------------

            if flags["acceptance"]:

                record = self.create_record(
                    topic,
                    sentence,
                    index,
                    len(sentences),
                    "unresolved_acceptance",
                    0.40,
                    "review",
                    True,
                    [
                        "acceptance_without_detected_proposal"
                    ]
                )

                records.append(record)

        return records

    # ======================================================
    # FINAL DECISION SELECTION
    # ======================================================

    @staticmethod
    def select_final_decisions(
        records: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:

        allowed_types = {
            "explicit_decision",
            "confirmed_from_proposal",
            "reversal_decision"
        }

        return [
            record
            for record in records
            if (
                record.get("status") == "active"
                and record.get("decision_type")
                in allowed_types
                and record.get("confidence", 0) >= 0.60
            )
        ]

    # ======================================================
    # SAVE
    # ======================================================

    def save_results(
        self,
        records: List[Dict[str, Any]],
        final_decisions: List[Dict[str, Any]]
    ) -> Path:

        output_file = (
            self.output_dir / "decisions.json"
        )

        proposals = [
            item
            for item in records
            if item.get("decision_type") == "proposal"
        ]

        rejected = [
            item
            for item in records
            if item.get("status") == "rejected"
        ]

        excluded = [
            item
            for item in records
            if item.get("status") == "excluded"
        ]

        review = [
            item
            for item in records
            if item.get("requires_review")
        ]

        output = {
            "metadata": {
                "total_records": len(records),
                "final_decisions": len(final_decisions),
                "proposals": len(proposals),
                "rejected_records": len(rejected),
                "excluded_non_decisions": len(excluded),
                "requires_review": len(review),
                "processing_strategy": "contextual_topic_by_topic",
                "contextual_confirmation": True,
                "reversal_detection": True,
                "uncertainty_filtering": True,
                "future_decision_filtering": True
            },

            "final_decisions": final_decisions,

            "proposals": proposals,

            "all_records": records
        }

        with open(
            output_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                output,
                file,
                ensure_ascii=False,
                indent=4
            )

        return output_file

    # ======================================================
    # PROCESS
    # ======================================================

    def process(self, topics_path: str) -> Path:

        print("\n" + "=" * 68)
        print("Meet2MoM - Context-Aware Decision Extraction")
        print("=" * 68)

        topics = self.load_topics(topics_path)

        print(f"\nTopics loaded: {len(topics)}")

        all_records = []

        # Important for scalability:
        # only one topic is analysed at a time.
        for topic in topics:

            topic_id = topic.get("topic_id")

            label = topic.get(
                "topic_label",
                "Unknown Topic"
            )

            print(
                f"\nProcessing Topic "
                f"{topic_id}: {label}"
            )

            records = self.extract_from_topic(topic)

            useful = [
                record
                for record in records
                if record.get("status")
                not in {"excluded"}
            ]

            print(
                f"  Intelligence records: "
                f"{len(useful)}"
            )

            all_records.extend(records)

        final_decisions = (
            self.select_final_decisions(
                all_records
            )
        )

        output_file = self.save_results(
            all_records,
            final_decisions
        )

        proposal_count = sum(
            1
            for item in all_records
            if item.get("decision_type") == "proposal"
        )

        excluded_count = sum(
            1
            for item in all_records
            if item.get("status") == "excluded"
        )

        review_count = sum(
            1
            for item in all_records
            if item.get("requires_review")
        )

        print("\n" + "-" * 68)

        print(
            f"Records analysed: "
            f"{len(all_records)}"
        )

        print(
            f"Proposals detected: "
            f"{proposal_count}"
        )

        print(
            f"Excluded non-decisions: "
            f"{excluded_count}"
        )

        print(
            f"Final decisions: "
            f"{len(final_decisions)}"
        )

        print(
            f"Requires review: "
            f"{review_count}"
        )

        print("\n✓ Decisions saved to:")
        print(output_file)

        print("\n" + "=" * 68)

        # Print final decisions for quick validation
        if final_decisions:

            print("\nFINAL DECISIONS:")

            for index, decision in enumerate(
                final_decisions,
                start=1
            ):

                print(
                    f"\n{index}. "
                    f"{decision['text']}"
                )

                if decision.get(
                    "confirmation_text"
                ):

                    print(
                        "   Confirmed by: "
                        f"{decision['confirmation_text']}"
                    )

                print(
                    "   Confidence: "
                    f"{decision['confidence']}"
                )

        else:
            print(
                "\nNo confirmed final decisions "
                "were detected."
            )

        return output_file


# ==========================================================
# RUN DIRECTLY
# ==========================================================

if __name__ == "__main__":

    topics_file = (
        "output/intelligence/topics.json"
    )

    extractor = DecisionExtractor()

    extractor.process(topics_file)