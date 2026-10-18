"""
Phase 3 Task 4: Topic Title Polish Tests:
1. No title contains a truncated token (every title word appears as a full word in the source utterances).
2. The apostrophe case is fixed (e.g. 'Aren\'t' is never truncated to 'Aren').
3. Clean extractive titles: 2–5 words, Title Case, no duplicate words, no stopword-only titles.
4. Speaker names filtered out unless topic subject.
5. Title generation method is recorded on topic items ('extractive', 'tfidf_fallback', 'llm', 'default').
6. Output is strictly deterministic for identical input across multiple invocations.
7. LLM title mode validation: rejected if LLM produces words absent from utterances.
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock
import pytest

from app.core import config
from app.services.intelligence.topic_segmenter import TopicSegmenter, WORD_TOKEN_PATTERN


@pytest.fixture()
def segmenter(monkeypatch):
    monkeypatch.setattr("app.services.intelligence.topic_segmenter.SENTENCE_TRANSFORMERS_AVAILABLE", False)
    return TopicSegmenter()


def test_apostrophe_and_no_truncated_tokens(segmenter):
    """Verify 'Aren\'t', 'Don\'t', 'It\'s' are never truncated to 'Aren', 'Don', 'It'."""
    text = (
        "Aren't we configuring the PostgreSQL replica servers for the primary database? "
        "Don't worry, it's going to be fully configured by Friday."
    )
    source_words = [w.lower() for w in WORD_TOKEN_PATTERN.findall(text)]

    label, method = segmenter.generate_topic_label(text, return_method=True)
    assert method in ("extractive", "tfidf_fallback")
    title_words = WORD_TOKEN_PATTERN.findall(label)

    # 1. Title must be 2-5 words
    assert 2 <= len(title_words) <= 5

    # 2. No truncated tokens: every word in the title MUST appear as a whole word in source
    for w in title_words:
        assert w.lower() in source_words, f"Word '{w}' in title was not a full word in source utterances"

    # 3. Explicitly verify 'Aren' does not appear (since source had 'Aren\'t')
    assert "Aren" not in title_words
    assert "Don" not in title_words


def test_clean_extractive_title_constraints(segmenter):
    text = (
        "We need to discuss the Kubernetes deployment cluster migration next week. "
        "The production rollout timeline must be reviewed by the engineering team."
    )
    source_words = [w.lower() for w in WORD_TOKEN_PATTERN.findall(text)]

    label, method = segmenter.generate_topic_label(text, return_method=True)
    words = label.split()

    # Length 2-5 words
    assert 2 <= len(words) <= 5

    # No duplicate words
    lower_words = [w.lower() for w in words]
    assert len(set(lower_words)) == len(lower_words), f"Title contains duplicate words: {words}"

    # Every word exists in source
    for w in words:
        cleaned = re.sub(r"[^\w']", "", w)
        assert cleaned.lower() in source_words

    # Title Case: every word starts with capital (or is uppercase acronym)
    for w in words:
        assert w[0].isupper(), f"Word '{w}' is not capitalized in title '{label}'"

    # Method stored
    assert method == "extractive"


def test_deterministic_output(segmenter):
    """Running generate_topic_label multiple times on the same input produces identical output."""
    text = (
        "We are evaluating Redis in-memory cache layer performance for low latency session state management. "
        "The cache invalidation strategy needs thorough testing."
    )
    first_label, first_method = segmenter.generate_topic_label(text, return_method=True)

    for _ in range(5):
        label, method = segmenter.generate_topic_label(text, return_method=True)
        assert label == first_label
        assert method == first_method


def test_speaker_names_filtered(segmenter):
    """Speaker labels and names should be filtered out from topic titles."""
    text = "SPEAKER_01 and Alice discussed the database index optimization and query latency."
    label = segmenter.generate_topic_label(text, speakers=["SPEAKER_01", "Alice"])
    words = [w.lower() for w in label.split()]

    assert "speaker_01" not in words
    assert "speaker" not in words
    assert "alice" not in words
    assert 2 <= len(words) <= 5


def test_create_topic_entry_records_title_method(segmenter):
    """create_topic_entry must persist 'title_method' along with 'topic_label'."""
    segments = [
        {"start": 10.0, "end": 15.0, "speaker": "Bob", "text": "We need to discuss cloud infrastructure security."},
        {"start": 15.0, "end": 20.0, "speaker": "Bob", "text": "Network policies and firewall rules are essential."},
    ]
    entry = segmenter.create_topic(1, segments)

    assert "topic_label" in entry
    assert "title_method" in entry
    assert entry["title_method"] in ("extractive", "tfidf_fallback")
    assert 2 <= len(entry["topic_label"].split()) <= 5


def test_llm_mode_accepted_when_constrained(segmenter, monkeypatch):
    """When TOPIC_TITLES=llm and LLM returns words strictly from utterances, it is accepted."""
    monkeypatch.setattr(config, "TOPIC_TITLES", "llm")

    text = "We should prioritize the frontend design system overhaul."
    # Mock LLM returning valid verbatim words
    mock_client = MagicMock()
    mock_client.complete.return_value = "Frontend Design System"
    monkeypatch.setattr("app.core.llm_client.LLMClient", lambda: mock_client)

    label, method = segmenter.generate_topic_label(text, return_method=True)
    assert method == "llm"
    assert label == "Frontend Design System"


def test_llm_mode_rejected_when_hallucinating(segmenter, monkeypatch):
    """When LLM introduces words absent from the source utterances, it is rejected and falls back to extractive."""
    monkeypatch.setattr(config, "TOPIC_TITLES", "llm")

    text = "We should prioritize the frontend redesign."
    # Mock LLM returning hallucinated words ('Roadmap' and 'Strategy' are not in text)
    mock_client = MagicMock()
    mock_client.complete.return_value = "Frontend Roadmap Strategy"
    monkeypatch.setattr("app.core.llm_client.LLMClient", lambda: mock_client)

    label, method = segmenter.generate_topic_label(text, return_method=True)
    assert method in ("extractive", "tfidf_fallback")
    # Rejected title words must not be present
    assert "Roadmap" not in label
    assert "Strategy" not in label
