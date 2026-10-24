import json
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import numpy as np

try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

WORD_TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?\b")

EXPANDED_STOP_WORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "can't", "cannot", "could",
    "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down",
    "during", "each", "few", "for", "from", "further", "had", "hadn't", "has",
    "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her",
    "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's",
    "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it",
    "it's", "its", "itself", "let", "lets", "let's", "me", "more", "most", "mustn't",
    "my", "myself", "no", "nor", "not", "of", "off", "on", "once", "only", "or",
    "other", "ought", "our", "ours", "ourselves", "out", "over", "own", "same",
    "shan't", "she", "she'd", "she'll", "she's", "should", "shouldn't", "so",
    "some", "such", "than", "that", "that's", "the", "their", "theirs", "them",
    "themselves", "then", "there", "there's", "these", "they", "they'd", "they'll",
    "they're", "they've", "this", "those", "through", "to", "too", "under", "until",
    "up", "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were",
    "weren't", "what", "what's", "when", "when's", "where", "where's", "which",
    "while", "who", "who's", "whom", "why", "why's", "with", "won't", "would",
    "wouldn't", "you", "you'd", "you'll", "you're", "you've", "your", "yours",
    "yourself", "yourselves",
    # Conversational fillers
    "yes", "yeah", "yep", "okay", "ok", "right", "well", "actually", "basically",
    "like", "just", "really", "think", "know", "mean", "maybe", "probably", "sure",
    "good", "great", "fine", "start", "started", "starting", "way", "thing", "things",
    "going", "want", "need", "said", "say", "saying", "hello", "thanks", "thank",
    "please", "hmm", "uh", "um", "ah", "oh", "general", "discussion",
}


def _format_title_words(tokens: List[str], source_casing: Dict[str, str]) -> str:
    """Format token list in Title Case preserving domain acronyms / uppercase."""
    formatted = []
    for tok in tokens:
        orig = source_casing.get(tok.lower(), tok)
        if orig.isupper() or (len(orig) > 1 and any(c.isupper() for c in orig[1:])):
            formatted.append(orig)
        else:
            formatted.append(orig.capitalize())
    return " ".join(formatted)


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

        # Topic-title mode and an explicit record of why LLM titles were not
        # used (Phase 5). The LLM client is created once per run.
        from app.core import config as _config

        self.title_mode = (getattr(_config, "TOPIC_TITLES", "extractive") or "extractive").strip().lower()
        self.title_fallback_reason: Optional[str] = None
        self.title_counts: Dict[str, int] = {"llm": 0, "fallback": 0}
        self._title_client = None
        self._title_client_ready = False
        self._last_title_fallback: Optional[str] = None

        if SENTENCE_TRANSFORMERS_AVAILABLE:
            print("Loading multilingual semantic model...")

            # Shared per-process instance (same model the retrieval index and
            # dialogue model use) instead of reloading it on every call.
            from app.core.model_registry import get_sentence_transformer

            self.model = get_sentence_transformer(
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

        label, method = self.generate_topic_label(
            text, speakers=speakers, return_method=True
        )
        title_fallback = self._last_title_fallback if method != "llm" else None

        return {
            "topic_id": topic_id,
            "topic_label": label,
            "title_method": method,
            **({"title_fallback_reason": title_fallback} if title_fallback else {}),
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
        text: str,
        speakers: Optional[List[str]] = None,
        return_method: bool = False,
    ) -> str | Tuple[str, str]:
        """
        Produce a clean extractive topic title (2–5 words), built ONLY from words
        that appear in that topic's utterances, Title Case, no stopword-only titles,
        no duplicate words, and no truncated words (e.g. preserves 'Aren't' instead of 'Aren').

        If config.TOPIC_TITLES == 'llm', attempts LLM generation first, but rejects
        any title containing words absent from the utterances (falling back to extractive).

        Returns title (or (title, method) when return_method is True).
        """
        from app.core import config

        if not text or not text.strip():
            return ("General Discussion", "default") if return_method else "General Discussion"

        # 1. Verbatim source tokens and casing map
        source_tokens = WORD_TOKEN_PATTERN.findall(text)
        if not source_tokens:
            return ("General Discussion", "default") if return_method else "General Discussion"

        source_words_set = {w.lower() for w in source_tokens}
        source_casing: Dict[str, str] = {}
        for tok in source_tokens:
            low = tok.lower()
            if low not in source_casing:
                source_casing[low] = tok

        # 2. Known speaker names / labels to exclude unless subject
        speaker_names = {"speaker", "speaker_00", "speaker_01", "speaker_02", "speaker_03", "unknown"}
        if speakers:
            for s in speakers:
                speaker_names.add(s.lower())
                for part in WORD_TOKEN_PATTERN.findall(s.lower()):
                    speaker_names.add(part)

        # 3. Optional LLM generation; every fallback keeps its reason
        self._last_title_fallback = None
        mode = getattr(config, "TOPIC_TITLES", "extractive") or "extractive"
        if mode.strip().lower() == "llm":
            llm_title, reason = self._try_llm_title(text, source_words_set, source_casing)
            if llm_title:
                self.title_counts["llm"] = self.title_counts.get("llm", 0) + 1
                return (llm_title, "llm") if return_method else llm_title
            self._last_title_fallback = reason
            self.title_counts["fallback"] = self.title_counts.get("fallback", 0) + 1

        # 4. Clean Extractive Title Generation
        extractive_title, method = self._generate_extractive_title(
            text, source_tokens, source_words_set, source_casing, speaker_names
        )
        return (extractive_title, method) if return_method else extractive_title

    def _llm_title_client(self):
        """
        One LLM client per run, or None with self.title_fallback_reason set.
        Titles get their own short time budget so they never delay extraction
        for long.
        """
        if self._title_client_ready:
            return self._title_client
        self._title_client_ready = True
        try:
            from app.core import config as _config
            from app.core.llm_client import LLMClient
            from app.services.intelligence.extraction_pipeline import llm_unavailable_reason

            unavailable = llm_unavailable_reason()
            if unavailable:
                self.title_fallback_reason = f"LLM topic titles not used: {unavailable}"
            else:
                client = LLMClient()
                if hasattr(client, "start_budget"):
                    client.start_budget(getattr(_config, "TOPIC_TITLE_LLM_BUDGET_SECONDS", 60))
                self._title_client = client
        except Exception as exc:
            self.title_fallback_reason = f"LLM topic titles not used: {type(exc).__name__}: {exc}"[:300]
        if self.title_fallback_reason:
            print(f"⚠ {self.title_fallback_reason}; using extractive titles")
        return self._title_client

    def _try_llm_title(
        self,
        text: str,
        source_words_set: set[str],
        source_casing: Dict[str, str],
    ) -> Tuple[Optional[str], Optional[str]]:
        """(title, None) when the LLM title is accepted, else (None, reason)."""
        client = self._llm_title_client()
        if client is None:
            return None, self.title_fallback_reason
        system_prompt = (
            "You are a meeting assistant. Generate a concise 2 to 5 word topic title summarizing the text. "
            "CRITICAL CONSTRAINT: You MUST use ONLY words that appear verbatim in the source text below. "
            "Do NOT invent, infer, or extrapolate any new words. "
            'Return ONLY JSON of the form {"title": "Title Words In Title Case"}.'
        )
        user_prompt = f"Transcript excerpt:\n{text[:1500]}"
        try:
            data = client.complete_json(system=system_prompt, user=user_prompt, max_tokens=60)
        except Exception as exc:
            # A provider error or exhausted budget: stop asking for this run.
            self._title_client = None
            self.title_fallback_reason = f"LLM topic title request failed: {type(exc).__name__}: {exc}"[:300]
            print(f"⚠ {self.title_fallback_reason}; using extractive titles")
            return None, self.title_fallback_reason
        resp_clean = str((data or {}).get("title") or "").strip("\"' \n\r\t")
        words = WORD_TOKEN_PATTERN.findall(resp_clean)
        if not (2 <= len(words) <= 5):
            return None, "LLM title rejected: not 2 to 5 words"
        # Every word must appear in the source text
        if not all(w.lower() in source_words_set for w in words):
            return None, "LLM title rejected: uses words not in the topic's utterances"
        # Must not be stopword-only
        if not any(w.lower() not in EXPANDED_STOP_WORDS for w in words):
            return None, "LLM title rejected: only stop words"
        # No duplicate words
        lowers = [w.lower() for w in words]
        if len(set(lowers)) != len(lowers):
            return None, "LLM title rejected: repeated words"
        return _format_title_words(words, source_casing), None

    def _generate_extractive_title(
        self,
        text: str,
        source_tokens: List[str],
        source_words_set: set[str],
        source_casing: Dict[str, str],
        speaker_names: set[str],
    ) -> Tuple[str, str]:
        def is_content(w: str) -> bool:
            return (
                len(w) >= 3
                and w.lower() not in EXPANDED_STOP_WORDS
                and not w.isdigit()
                and w.lower() not in speaker_names
            )

        # Split text into clean sentences/clauses
        raw_sentences = [
            s.strip() for s in re.split(r"[.?!;\n]+", text)
            if len(s.strip()) > 5
        ]
        if not raw_sentences:
            raw_sentences = [text.strip()]

        # Collect candidate continuous phrases of 2–5 words
        candidate_phrases: List[List[str]] = []
        seen_phrases = set()

        for s in raw_sentences:
            s_toks = WORD_TOKEN_PATTERN.findall(s)
            for L in (2, 3, 4, 5):
                for i in range(len(s_toks) - L + 1):
                    phrase = s_toks[i : i + L]
                    # First and last token must NOT be in stopwords
                    if phrase[0].lower() in EXPANDED_STOP_WORDS or phrase[-1].lower() in EXPANDED_STOP_WORDS:
                        continue
                    # Must contain at least one content word
                    if not any(is_content(w) for w in phrase):
                        continue
                    # No speaker names
                    if any(w.lower() in speaker_names for w in phrase):
                        continue
                    # No duplicate words in candidate phrase
                    lowers = tuple(w.lower() for w in phrase)
                    if len(set(lowers)) != len(lowers):
                        continue
                    if lowers not in seen_phrases:
                        seen_phrases.add(lowers)
                        candidate_phrases.append(phrase)

        # Score words with TF-IDF over the sentences
        word_scores: Dict[str, float] = {}
        try:
            vectorizer = TfidfVectorizer(
                token_pattern=r"(?u)\b[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?\b",
                stop_words=list(EXPANDED_STOP_WORDS),
                max_features=80,
            )
            matrix = vectorizer.fit_transform(raw_sentences)
            terms = vectorizer.get_feature_names_out()
            sums = np.asarray(matrix.sum(axis=0)).flatten()
            word_scores = dict(zip(terms, sums))
        except Exception:
            pass

        if candidate_phrases:
            def score_phrase(p: List[str]) -> float:
                base = sum(word_scores.get(w.lower(), 0.05) for w in p)
                # Slight preference for 2-3 word cohesive phrases
                length_mult = 1.2 if 2 <= len(p) <= 3 else 1.0
                return base * length_mult

            scored = [(p, score_phrase(p)) for p in candidate_phrases]
            scored.sort(key=lambda item: (item[1], -len(item[0])), reverse=True)

            if scored and scored[0][1] > 0:
                best_phrase = scored[0][0]
                title = _format_title_words(best_phrase, source_casing)
                return title, "extractive"

        # Fallback: top 2-3 content words ranked by TF-IDF / occurrence
        content_tokens = [w for w in source_tokens if is_content(w)]
        unique_content: List[str] = []
        seen = set()
        for w in content_tokens:
            if w.lower() not in seen:
                seen.add(w.lower())
                unique_content.append(w)

        if len(unique_content) >= 2:
            ranked = sorted(
                unique_content,
                key=lambda w: word_scores.get(w.lower(), 0.0),
                reverse=True,
            )
            top_set = set(w.lower() for w in ranked[:3])
            ordered = [w for w in unique_content if w.lower() in top_set][:3]
            title = _format_title_words(ordered, source_casing)
            return title, "tfidf_fallback"

        if len(unique_content) == 1:
            title = _format_title_words(unique_content, source_casing)
            return title, "tfidf_fallback"

        return "General Discussion", "default"


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