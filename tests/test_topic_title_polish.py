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


def _llm_client(monkeypatch, title=None, error=None):
    """A mock limited to LLMClient's real API: calling a method it lacks fails."""
    from app.core.llm_client import LLMClient

    client = MagicMock(spec=LLMClient)
    if error is not None:
        client.complete_json.side_effect = error
    else:
        client.complete_json.return_value = {"title": title}
    monkeypatch.setattr("app.core.llm_client.LLMClient", lambda: client)
    monkeypatch.setattr(
        "app.services.intelligence.extraction_pipeline.llm_unavailable_reason", lambda client=None: None
    )
    return client


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
    mock_client = _llm_client(monkeypatch, title="Frontend Design System")

    label, method = segmenter.generate_topic_label(text, return_method=True)
    assert method == "llm"
    assert label == "Frontend Design System"
    mock_client.complete_json.assert_called_once()


def test_llm_mode_rejected_when_hallucinating(segmenter, monkeypatch):
    """When LLM introduces words absent from the source utterances, it is rejected and falls back to extractive."""
    monkeypatch.setattr(config, "TOPIC_TITLES", "llm")

    text = "We should prioritize the frontend redesign."
    # Mock LLM returning hallucinated words ('Roadmap' and 'Strategy' are not in text)
    _llm_client(monkeypatch, title="Frontend Roadmap Strategy")

    label, method = segmenter.generate_topic_label(text, return_method=True)
    assert method in ("extractive", "tfidf_fallback")
    # Rejected title words must not be present
    assert "Roadmap" not in label
    assert "Strategy" not in label



# ---------------------------------------------------------------------------
# Phase 5: the LLM title path uses the real client API and every fallback is
# explicit (recorded on the topic and reported to the topics stage).
# ---------------------------------------------------------------------------

def test_llm_titles_use_the_real_client_interface():
    from app.core.llm_client import LLMClient

    assert hasattr(LLMClient, "complete_json")
    assert not hasattr(LLMClient, "complete")          # the old call could never work


def test_unavailable_llm_falls_back_with_a_recorded_reason(segmenter, monkeypatch):
    monkeypatch.setattr(config, "TOPIC_TITLES", "llm")
    monkeypatch.setattr(
        "app.services.intelligence.extraction_pipeline.llm_unavailable_reason",
        lambda client=None: "LLM_PROVIDER=gemini has no API key configured",
    )
    segments = [{"speaker": "A", "text": "We should prioritize the frontend design system overhaul.",
                 "start": 0.0, "end": 3.0}]
    entry = segmenter.create_topic(0, segments)
    assert entry["title_method"] in ("extractive", "tfidf_fallback")
    assert "no API key" in entry["title_fallback_reason"]
    assert "no API key" in segmenter.title_fallback_reason
    assert segmenter.title_counts == {"llm": 0, "fallback": 1}


def test_provider_error_stops_further_title_requests(segmenter, monkeypatch):
    from app.core.llm_client import LLMClientError

    monkeypatch.setattr(config, "TOPIC_TITLES", "llm")
    client = _llm_client(monkeypatch, error=LLMClientError("LLM request timed out after 60s (2 attempts)."))
    for text in ("We should prioritize the frontend design system overhaul.",
                 "The quarterly budget review covers marketing spend."):
        label, method = segmenter.generate_topic_label(text, return_method=True)
        assert method in ("extractive", "tfidf_fallback") and label
    assert client.complete_json.call_count == 1          # no request after the failure
    assert "timed out" in segmenter.title_fallback_reason


def test_rejected_llm_title_is_explained_on_the_topic(segmenter, monkeypatch):
    monkeypatch.setattr(config, "TOPIC_TITLES", "llm")
    _llm_client(monkeypatch, title="Frontend Roadmap Strategy")
    segments = [{"speaker": "A", "text": "We should prioritize the frontend redesign.", "start": 0.0, "end": 3.0}]
    entry = segmenter.create_topic(0, segments)
    assert entry["title_fallback_reason"] == "LLM title rejected: uses words not in the topic's utterances"


def test_extractive_default_makes_no_llm_call(segmenter, monkeypatch):
    monkeypatch.setattr(config, "TOPIC_TITLES", "extractive")
    client = _llm_client(monkeypatch, title="Frontend Design System")
    segments = [{"speaker": "A", "text": "We should prioritize the frontend design system overhaul.",
                 "start": 0.0, "end": 3.0}]
    entry = segmenter.create_topic(0, segments)
    assert "title_fallback_reason" not in entry
    client.complete_json.assert_not_called()


def test_topics_stage_reports_title_fallback():
    from app.core.stage_timings import StageHandle
    from app.services.pipeline.recorded_pipeline_adapter import _describe_topics

    class Seg:
        title_mode = "llm"
        title_counts = {"llm": 2, "fallback": 1}
        title_fallback_reason = None

    h = StageHandle()
    _describe_topics(h, Seg())
    assert h.status == "fallback" and "1 LLM title(s) rejected" in h.detail["title_fallback_reason"]
    Seg.title_mode, h2 = "extractive", StageHandle()
    _describe_topics(h2, Seg())
    assert h2.status is None
