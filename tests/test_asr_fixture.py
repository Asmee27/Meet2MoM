"""
Real-model accuracy check on a synthetic speech fixture (marked slow).

The fixture is generated locally by scripts/make_asr_fixture.ps1 (Windows
text-to-speech) and is not committed (*.wav is ignored). It contains a known
reference text, read clearly and at -20 dB. The test measures word error rate
against that reference and checks that no word outside the reference (such as
a vocabulary-list term) is inserted. It does not special-case any phrase.
"""
from __future__ import annotations

import re
import wave
from pathlib import Path

import numpy as np
import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "audio" / "tts_clear.wav"
REFERENCE = ("The quarterly budget review is scheduled for Tuesday. I will submit the report by Friday. "
             "Please check the mobile layout before the demo.")


def _words(text):
    return re.sub(r"[^a-z0-9' ]", " ", text.lower()).split()


def _wer(ref, hyp):
    r, h = _words(ref), _words(hyp)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[len(h)] / len(r)


@pytest.mark.slow
@pytest.mark.skipif(not FIXTURE.exists(), reason="run scripts/make_asr_fixture.ps1 to create the fixture")
@pytest.mark.parametrize("gain", [1.0, 0.1])          # clear and -20 dB
def test_transcript_matches_the_reference(tmp_path, monkeypatch, gain):
    from app.core import config
    from app.services.transcription import transcriber as T

    monkeypatch.setattr(T, "FORCE_LANGUAGE", "en")
    with wave.open(str(FIXTURE)) as w:
        params, x = w.getparams(), np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    path = tmp_path / "fixture.wav"
    with wave.open(str(path), "wb") as w:
        w.setparams(params)
        w.writeframes((x.astype(np.float32) * gain).astype(np.int16).tobytes())

    tr = T.MeetingTranscriber.__new__(T.MeetingTranscriber)
    tr.model = T.load_whisper_model()
    tr.initial_prompt = T.whisper_initial_prompt()
    segments, _ = tr.run_whisper(path, T.BEAM_SIZE)
    text = " ".join(s.text.strip() for s in segments)

    assert _wer(REFERENCE, text) <= 0.10
    assert set(_words(text)) <= set(_words(REFERENCE)) | {"a", "the"}
    for s in segments:                                  # no segment spans a long pause
        words = getattr(s, "words", None) or []
        assert all(b.start - a.end <= config.WHISPER_SPLIT_GAP_SECONDS for a, b in zip(words, words[1:]))
