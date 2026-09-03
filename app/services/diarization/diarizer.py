import json
import os
import wave

import numpy as np
import torch

from pyannote.audio import Pipeline

from app.core.config import (
    DIARIZATION_MODEL,
    DIARIZATION_DIR,
    KNOWN_NUM_SPEAKERS,
    MIN_SPEAKERS,
    MAX_SPEAKERS
)


class SpeakerDiarizer:

    def __init__(self):

        DIARIZATION_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

        token = os.getenv("HF_TOKEN")

        if not token:
            raise RuntimeError(
                "HF_TOKEN environment variable is missing."
            )

        print(
            f"Loading diarization model: "
            f"{DIARIZATION_MODEL}"
        )

        self.pipeline = Pipeline.from_pretrained(
            DIARIZATION_MODEL,
            token=token
        )

        print("✓ Diarization model loaded")

    # ======================================================
    # LOAD WAV WITHOUT TORCHCODEC
    # ======================================================

    @staticmethod
    def load_wav_as_tensor(audio_file):

        with wave.open(
            str(audio_file),
            "rb"
        ) as wav_file:

            channels = wav_file.getnchannels()
            sample_rate = wav_file.getframerate()
            sample_width = wav_file.getsampwidth()
            frames = wav_file.readframes(
                wav_file.getnframes()
            )

        if sample_width != 2:
            raise RuntimeError(
                "Expected 16-bit PCM WAV audio."
            )

        audio = np.frombuffer(
            frames,
            dtype=np.int16
        ).astype(np.float32)

        audio /= 32768.0

        if channels > 1:

            audio = audio.reshape(
                -1,
                channels
            ).mean(axis=1)

        waveform = torch.from_numpy(
            audio
        ).unsqueeze(0)

        return waveform, sample_rate

    # ======================================================
    # RUN DIARIZATION
    # ======================================================

    def diarize(self, audio_file):

        print(
            "\nLoading normalized audio "
            "directly into memory..."
        )

        waveform, sample_rate = (
            self.load_wav_as_tensor(
                audio_file
            )
        )

        print(
            f"✓ Audio loaded: "
            f"{sample_rate} Hz"
        )

        audio_input = {
            "waveform": waveform,
            "sample_rate": sample_rate
        }

        kwargs = {}

        if KNOWN_NUM_SPEAKERS is not None:

            kwargs["num_speakers"] = (
                KNOWN_NUM_SPEAKERS
            )

        else:

            kwargs["min_speakers"] = (
                MIN_SPEAKERS
            )

            kwargs["max_speakers"] = (
                MAX_SPEAKERS
            )

        print(
            "Running speaker diarization..."
        )

        result = self.pipeline(
            audio_input,
            **kwargs
        )

        speaker_turns = []

        diarization = (
            result.speaker_diarization
        )

        for turn, speaker in diarization:

            speaker_turns.append({
                "start": round(
                    float(turn.start),
                    3
                ),
                "end": round(
                    float(turn.end),
                    3
                ),
                "speaker": speaker
            })

        output_file = (
            DIARIZATION_DIR /
            "speaker_turns.json"
        )

        with open(
            output_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                speaker_turns,
                file,
                indent=4,
                ensure_ascii=False
            )

        speakers = sorted(
            {
                item["speaker"]
                for item in speaker_turns
            }
        )

        print(
            f"✓ Speakers detected: "
            f"{len(speakers)}"
        )

        if speakers:
            print(
                "  " + ", ".join(speakers)
            )

        print(
            f"✓ Speaker turns saved to: "
            f"{output_file}"
        )

        return output_file