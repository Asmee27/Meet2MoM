import json
from pathlib import Path
from typing import List, Dict, Any

import numpy as np

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


class TopicSegmenter:
    """
    Meet2MoM Semantic Topic Segmenter

    Input:
        output/transcripts/meeting_transcript_ready.json

    Output:
        output/intelligence/topics.json

    Features:
        - Semantic topic-change detection
        - Multilingual embedding support
        - Scalable block-based processing for long meetings
        - Speaker information preservation
        - Timestamp preservation
        - Cleaner topic labels
        - TF-IDF fallback if SentenceTransformer unavailable
    """

    def __init__(
        self,
        output_dir: str = "output/intelligence",
        similarity_threshold: float = 0.55,
        min_segments_per_topic: int = 3,
        block_size: int = 3,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.similarity_threshold = similarity_threshold
        self.min_segments_per_topic = min_segments_per_topic
        self.block_size = block_size

        self.model = None

        if SENTENCE_TRANSFORMERS_AVAILABLE:
            print("Loading multilingual semantic model...")

            self.model = SentenceTransformer(
                "sentence-transformers/"
                "paraphrase-multilingual-MiniLM-L12-v2"
            )

            print("✓ Semantic model loaded")

        else:
            print("⚠ sentence-transformers not installed.")
            print("Using TF-IDF fallback.")

    # ======================================================
    # LOAD TRANSCRIPT
    # ======================================================

    def load_transcript(
        self,
        transcript_path: str
    ) -> List[Dict[str, Any]]:

        transcript_path = Path(transcript_path)

        if not transcript_path.exists():
            raise FileNotFoundError(
                f"Transcript not found: {transcript_path}"
            )

        with open(
            transcript_path,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

        if isinstance(data, list):
            segments = data

        elif isinstance(data, dict):
            possible_keys = [
                "segments",
                "transcript",
                "speaker_transcript"
            ]

            segments = None

            for key in possible_keys:
                if key in data and isinstance(data[key], list):
                    segments = data[key]
                    break

            if segments is None:
                raise ValueError(
                    "Could not find transcript segments inside JSON."
                )

        else:
            raise ValueError(
                "Unsupported transcript JSON structure."
            )

        clean_segments = []

        for segment in segments:
            text = str(
                segment.get("text", "")
            ).strip()

            if not text:
                continue

            clean_segments.append(segment)

        return clean_segments

    # ======================================================
    # CREATE SEMANTIC BLOCKS
    # ======================================================

    def create_blocks(
        self,
        segments: List[Dict]
    ) -> List[Dict]:

        """
        Groups multiple consecutive transcript segments
        before semantic comparison.

        This is more stable and scalable than comparing
        every individual sentence.
        """

        blocks = []

        for i in range(
            0,
            len(segments),
            self.block_size
        ):
            block_segments = segments[
                i:i + self.block_size
            ]

            text = " ".join(
                str(
                    segment.get("text", "")
                ).strip()
                for segment in block_segments
            )

            if not text:
                continue

            blocks.append(
                {
                    "start_index": i,
                    "end_index": (
                        i + len(block_segments) - 1
                    ),
                    "text": text,
                    "segments": block_segments
                }
            )

        return blocks

    # ======================================================
    # GENERATE EMBEDDINGS
    # ======================================================

    def generate_embeddings(
        self,
        blocks: List[Dict]
    ):

        texts = [
            block["text"]
            for block in blocks
        ]

        if self.model is not None:

            embeddings = self.model.encode(
                texts,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False
            )

            method = "multilingual_embeddings"

        else:

            vectorizer = TfidfVectorizer(
                stop_words="english"
            )

            embeddings = vectorizer.fit_transform(
                texts
            ).toarray()

            method = "tfidf_fallback"

        return embeddings, method

    # ======================================================
    # FIND SEMANTIC BOUNDARIES
    # ======================================================

    def detect_boundaries(
        self,
        blocks: List[Dict],
        embeddings
    ) -> List[int]:

        boundaries = []

        if len(blocks) <= 1:
            return boundaries

        print(
            "\nSemantic similarity between discussion blocks:"
        )

        for i in range(
            len(blocks) - 1
        ):

            vector_a = embeddings[i].reshape(
                1,
                -1
            )

            vector_b = embeddings[i + 1].reshape(
                1,
                -1
            )

            similarity = float(
                cosine_similarity(
                    vector_a,
                    vector_b
                )[0][0]
            )

            print(
                f"Block {i + 1} → "
                f"{i + 2}: "
                f"{similarity:.3f}"
            )

            if similarity < self.similarity_threshold:

                boundary_index = (
                    blocks[i + 1]["start_index"]
                )

                boundaries.append(
                    boundary_index
                )

                print(
                    "   ↳ Possible topic change detected"
                )

        return boundaries

    # ======================================================
    # CREATE TOPICS
    # ======================================================

    def build_topics(
        self,
        segments: List[Dict],
        boundaries: List[int]
    ) -> List[Dict]:

        if not segments:
            return []

        split_points = (
            [0]
            + sorted(set(boundaries))
            + [len(segments)]
        )

        raw_topics = []

        for i in range(
            len(split_points) - 1
        ):

            start_index = split_points[i]
            end_index = split_points[i + 1]

            topic_segments = segments[
                start_index:end_index
            ]

            if topic_segments:
                raw_topics.append(
                    topic_segments
                )

        # Merge very tiny accidental topics
        merged_topics = []

        for topic_segments in raw_topics:

            if (
                len(topic_segments)
                < self.min_segments_per_topic
                and merged_topics
            ):

                merged_topics[-1].extend(
                    topic_segments
                )

            else:
                merged_topics.append(
                    topic_segments
                )

        topics = []

        for index, topic_segments in enumerate(
            merged_topics,
            start=1
        ):

            topics.append(
                self.create_topic(
                    index,
                    topic_segments
                )
            )

        return topics

    # ======================================================
    # CREATE SINGLE TOPIC
    # ======================================================

    def create_topic(
        self,
        topic_id: int,
        segments: List[Dict]
    ) -> Dict:

        start_time = float(
            segments[0].get(
                "start",
                0
            )
        )

        end_time = float(
            segments[-1].get(
                "end",
                start_time
            )
        )

        text = " ".join(
            str(
                segment.get(
                    "text",
                    ""
                )
            ).strip()
            for segment in segments
        )

        speakers = sorted(
            {
                segment.get(
                    "speaker",
                    "UNKNOWN"
                )
                for segment in segments
            }
        )

        label = self.generate_topic_label(
            text
        )

        return {
            "topic_id": topic_id,
            "topic_label": label,
            "start": round(
                start_time,
                2
            ),
            "end": round(
                end_time,
                2
            ),
            "duration": round(
                end_time - start_time,
                2
            ),
            "speakers": speakers,
            "segment_count": len(
                segments
            ),
            "text": text
        }

    # ======================================================
    # IMPROVED TOPIC LABEL GENERATION
    # ======================================================

    def generate_topic_label(
        self,
        text: str
    ) -> str:

        """
        Lightweight topic title generation.

        Removes conversational filler words
        and prefers meaningful keywords /
        short phrases.

        Designed to remain fast for long meetings.
        """

        if not text or not text.strip():
            return "General Discussion"

        custom_stop_words = {
            "yes",
            "yeah",
            "yep",
            "okay",
            "ok",
            "right",
            "well",
            "actually",
            "basically",
            "like",
            "just",
            "really",
            "think",
            "know",
            "mean",
            "maybe",
            "probably",
            "sure",
            "good",
            "great",
            "fine",
            "start",
            "started",
            "starting",
            "way",
            "thing",
            "things",
            "going",
            "want",
            "need",
            "said",
            "say",
            "saying",
            "let",
            "lets",
            "hello",
            "thanks",
            "thank",
            "please"
        }

        sentences = [
            sentence.strip()
            for sentence in (
                text.replace("?", ".")
                .replace("!", ".")
                .split(".")
            )
            if len(sentence.strip()) > 10
        ]

        if not sentences:
            return "General Discussion"

        try:

            vectorizer = TfidfVectorizer(
                stop_words="english",
                ngram_range=(1, 2),
                max_features=60
            )

            matrix = vectorizer.fit_transform(
                sentences
            )

            terms = (
                vectorizer
                .get_feature_names_out()
            )

            scores = np.asarray(
                matrix.sum(axis=0)
            ).flatten()

            ranked_indices = scores.argsort()[::-1]

            candidates = []

            for index in ranked_indices:

                term = terms[index].strip()

                words = term.lower().split()

                # Remove filler phrases
                if any(
                    word in custom_stop_words
                    for word in words
                ):
                    continue

                # Ignore meaningless tiny words
                if all(
                    len(word) < 3
                    or word.isdigit()
                    for word in words
                ):
                    continue

                # Score multi-word phrases slightly higher
                phrase_bonus = (
                    1.2
                    if len(words) > 1
                    else 1.0
                )

                final_score = (
                    float(scores[index])
                    * phrase_bonus
                )

                candidates.append(
                    (
                        term,
                        final_score,
                        len(words)
                    )
                )

            if not candidates:
                return "General Discussion"

            candidates.sort(
                key=lambda item: (
                    item[1],
                    item[2]
                ),
                reverse=True
            )

            selected = []

            for term, _, _ in candidates:

                normalized = (
                    term.lower()
                )

                duplicate = any(
                    normalized
                    in existing.lower()
                    or existing.lower()
                    in normalized
                    for existing in selected
                )

                if duplicate:
                    continue

                selected.append(
                    term.title()
                )

                if len(selected) == 2:
                    break

            if not selected:
                return "General Discussion"

            if len(selected) == 1:
                return selected[0]

            return (
                f"{selected[0]} / "
                f"{selected[1]}"
            )

        except (
            ValueError,
            IndexError
        ):

            return "General Discussion"

    # ======================================================
    # SAVE OUTPUT
    # ======================================================

    def save_topics(
        self,
        topics: List[Dict],
        method: str
    ) -> Path:

        output_file = (
            self.output_dir
            / "topics.json"
        )

        output_data = {
            "metadata": {
                "total_topics": len(
                    topics
                ),
                "segmentation_method": method,
                "similarity_threshold":
                    self.similarity_threshold,
                "block_size":
                    self.block_size,
                "min_segments_per_topic":
                    self.min_segments_per_topic
            },

            "topics": topics
        }

        with open(
            output_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                output_data,
                file,
                ensure_ascii=False,
                indent=4
            )

        return output_file

    # ======================================================
    # FULL PROCESS
    # ======================================================

    def process(
        self,
        transcript_path: str
    ) -> Path:

        print(
            "\n"
            + "=" * 65
        )

        print(
            "Meet2MoM - Semantic Topic Segmentation"
        )

        print(
            "=" * 65
        )

        segments = self.load_transcript(
            transcript_path
        )

        print(
            f"\nTranscript segments loaded: "
            f"{len(segments)}"
        )

        if not segments:
            raise ValueError(
                "Transcript contains no usable segments."
            )

        blocks = self.create_blocks(
            segments
        )

        print(
            f"Semantic blocks created: "
            f"{len(blocks)}"
        )

        embeddings, method = (
            self.generate_embeddings(
                blocks
            )
        )

        print(
            f"Segmentation method: "
            f"{method}"
        )

        boundaries = (
            self.detect_boundaries(
                blocks,
                embeddings
            )
        )

        topics = self.build_topics(
            segments,
            boundaries
        )

        print(
            f"\nSemantic topics generated: "
            f"{len(topics)}"
        )

        for topic in topics:

            print(
                f"\nTopic "
                f"{topic['topic_id']}: "
                f"{topic['topic_label']}"
            )

            print(
                f"  Time: "
                f"{topic['start']}s "
                f"→ "
                f"{topic['end']}s"
            )

            print(
                f"  Segments: "
                f"{topic['segment_count']}"
            )

        output_file = (
            self.save_topics(
                topics,
                method
            )
        )

        print(
            "\n✓ Topics saved to:"
        )

        print(
            output_file
        )

        print(
            "\n"
            + "=" * 65
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

    segmenter = TopicSegmenter()

    segmenter.process(
        transcript_file
    )