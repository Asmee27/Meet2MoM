"""
Transcript-quality fixes (Phase 6, after the "by Friday" -> "by FFmpeg" error).

* The vocabulary prompt is opt-in: it biased Whisper towards listed terms.
* Decoded segments are split at long pauses, so an utterance never spans the
  other participant's turn (that looked like speaker overlap).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import config
from app.services.transcription import transcriber as T


def word(text, start, end, probability=0.9):
    return SimpleNamespace(word=text, start=start, end=end, probability=probability)


def segment(words, **extra):
    return SimpleNamespace(
        text="".join(w.word for w in words), start=words[0].start if words else 0.0,
        end=words[-1].end if words else 0.0, words=words,
        avg_logprob=extra.get("avg_logprob", -0.4), no_speech_prob=0.01,
        compression_ratio=1.2, temperature=0.0,
    )


def test_segment_spanning_a_long_pause_is_split():
    seg = segment([word(" Please", 7.8, 8.1), word(" check", 8.1, 8.4),
                   word(" the", 38.9, 39.0), word(" layout.", 39.0, 39.6)], avg_logprob=-0.36)
    parts = T.split_at_pauses([seg], 1.5)
    assert [(p.start, p.end, p.text) for p in parts] == [
        (7.8, 8.4, "Please check"), (38.9, 39.6, "the layout."),
    ]
    assert all(p.avg_logprob == -0.36 and p.no_speech_prob == 0.01 for p in parts)


def test_short_pauses_and_segments_without_words_are_kept():
    seg = segment([word(" I", 1.0, 1.1), word(" will", 1.2, 1.4), word(" go.", 2.5, 2.8)])
    no_words = SimpleNamespace(text=" Hello", start=3.0, end=4.0, words=None)
    parts = T.split_at_pauses([seg, no_words], 1.5)
    assert len(parts) == 2 and parts[0].text == "I will go." and parts[1] is no_words
    assert T.split_at_pauses([seg], 0) == [seg]                   # splitting disabled


@pytest.mark.parametrize("mode, expected", [
    ("none", None),
    ("language", T.LANGUAGE_PROMPT),
])
def test_prompt_policy(monkeypatch, mode, expected):
    monkeypatch.setattr(config, "WHISPER_PROMPT", mode)
    assert T.whisper_initial_prompt() == expected


def test_vocabulary_prompt_is_opt_in_and_still_available(monkeypatch):
    assert config.WHISPER_PROMPT == "none"                        # the default
    monkeypatch.setattr(config, "WHISPER_PROMPT", "vocabulary")
    assert "FFmpeg" in T.whisper_initial_prompt()                 # the term that replaced "Friday"


def test_run_whisper_requests_word_times_and_splits(monkeypatch):
    calls = []
    seg = segment([word(" one", 0.0, 0.5), word(" two", 30.0, 30.4)])

    class FakeModel:
        def transcribe(self, path, **kwargs):
            calls.append(kwargs)
            return iter([seg]), SimpleNamespace(language="en", language_probability=1.0)

    tr = T.MeetingTranscriber.__new__(T.MeetingTranscriber)
    tr.model, tr.initial_prompt = FakeModel(), None
    monkeypatch.setattr(config, "WHISPER_SPLIT_GAP_SECONDS", 1.5)
    parts, info = tr.run_whisper("x.wav", 5)
    assert calls[0]["word_timestamps"] is True and calls[0]["initial_prompt"] is None
    assert [p.text for p in parts] == ["one", "two"]
