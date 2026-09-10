import json
import os
import wave

import numpy as np
import torch

from pyannote.audio import Pipeline

from app.core import config
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

        # ======================================================
        # HUGGING FACE TOKEN
        # ======================================================

        token = config.get_hf_token()

        if not token:
            raise RuntimeError(
                "Hugging Face token is missing. Set HF_TOKEN or "
                "HUGGINGFACE_TOKEN in .env; see .env.example."
            )

        # ======================================================
        # LOAD PYANNOTE MODEL
        # ======================================================

        print(
            f"Loading diarization model: "
            f"{DIARIZATION_MODEL}"
        )

        self.pipeline = Pipeline.from_pretrained(
            DIARIZATION_MODEL,
            token=token
        )

        # ======================================================
        # DEVICE SELECTION
        # ======================================================

        if torch.cuda.is_available():

            self.device = torch.device("cuda")

            self.pipeline.to(
                self.device
            )

            print(
                "✓ Diarization device: GPU"
            )

            print(
                f"✓ GPU: "
                f"{torch.cuda.get_device_name(0)}"
            )

        else:

            self.device = torch.device("cpu")

            # Use available CPU cores
            cpu_threads = max(
                1,
                os.cpu_count() or 1
            )

            torch.set_num_threads(
                cpu_threads
            )

            print(
                f"✓ Diarization device: CPU"
            )

            print(
                f"✓ Torch CPU threads: "
                f"{cpu_threads}"
            )

        print(
            "✓ Diarization model loaded"
        )

    # ======================================================
    # LOAD WAV WITHOUT TORCHCODEC
    # ======================================================

    @staticmethod
    def load_wav_as_tensor(audio_file):

        with wave.open(
            str(audio_file),
            "rb"
        ) as wav_file:

            channels = (
                wav_file.getnchannels()
            )

            sample_rate = (
                wav_file.getframerate()
            )

            sample_width = (
                wav_file.getsampwidth()
            )

            frames = wav_file.readframes(
                wav_file.getnframes()
            )

        # ------------------------------------------------------
        # REQUIRE 16-BIT PCM
        # ------------------------------------------------------

        if sample_width != 2:

            raise RuntimeError(
                "Expected 16-bit PCM WAV audio."
            )

        # ------------------------------------------------------
        # NUMPY AUDIO
        # ------------------------------------------------------

        audio = np.frombuffer(
            frames,
            dtype=np.int16
        ).astype(
            np.float32
        )

        audio /= 32768.0

        # ------------------------------------------------------
        # CONVERT STEREO → MONO IF REQUIRED
        # ------------------------------------------------------

        if channels > 1:

            audio = audio.reshape(
                -1,
                channels
            ).mean(
                axis=1
            )

        # ------------------------------------------------------
        # NUMPY → TORCH
        # ------------------------------------------------------

        waveform = torch.from_numpy(
            audio
        ).unsqueeze(
            0
        )

        return (
            waveform,
            sample_rate
        )

    # ======================================================
    # BUILD SPEAKER PARAMETERS
    # ======================================================

    @staticmethod
    def _get_speaker_kwargs():

        kwargs = {}

        # ------------------------------------------------------
        # KNOWN SPEAKER COUNT
        # ------------------------------------------------------

        if KNOWN_NUM_SPEAKERS is not None:

            kwargs[
                "num_speakers"
            ] = (
                KNOWN_NUM_SPEAKERS
            )

        # ------------------------------------------------------
        # UNKNOWN SPEAKER COUNT
        # ------------------------------------------------------

        else:

            if MIN_SPEAKERS is not None:

                kwargs[
                    "min_speakers"
                ] = (
                    MIN_SPEAKERS
                )

            if MAX_SPEAKERS is not None:

                kwargs[
                    "max_speakers"
                ] = (
                    MAX_SPEAKERS
                )

        return kwargs

    # ======================================================
    # RUN DIARIZATION
    # ======================================================

    def diarize(
        self,
        audio_file
    ):

        print(
            "\nLoading normalized audio "
            "directly into memory..."
        )

        # ======================================================
        # LOAD AUDIO
        # ======================================================

        waveform, sample_rate = (
            self.load_wav_as_tensor(
                audio_file
            )
        )

        print(
            f"✓ Audio loaded: "
            f"{sample_rate} Hz"
        )

        print(
            f"✓ Audio duration: "
            f"{round(waveform.shape[1] / sample_rate, 2)} seconds"
        )

        # ======================================================
        # AUDIO INPUT
        # ======================================================

        audio_input = {
            "waveform":
                waveform,

            "sample_rate":
                sample_rate
        }

        # ======================================================
        # SPEAKER SETTINGS
        # ======================================================

        kwargs = (
            self._get_speaker_kwargs()
        )

        if (
            "num_speakers"
            in kwargs
        ):

            print(
                f"✓ Expected speakers: "
                f"{kwargs['num_speakers']}"
            )

        else:

            print(
                "✓ Speaker range: "
                f"{kwargs.get('min_speakers')} "
                f"to "
                f"{kwargs.get('max_speakers')}"
            )

        # ======================================================
        # RUN PYANNOTE
        # ======================================================

        print(
            "Running speaker diarization..."
        )

        # inference_mode avoids unnecessary
        # gradient tracking during inference
        with torch.inference_mode():

            result = self.pipeline(
                audio_input,
                **kwargs
            )

        # ======================================================
        # GET DIARIZATION OUTPUT
        # ======================================================

        diarization = (
            result.speaker_diarization
        )

        speaker_turns = []

        for turn, speaker in diarization:

            start = round(
                float(
                    turn.start
                ),
                3
            )

            end = round(
                float(
                    turn.end
                ),
                3
            )

            # --------------------------------------------------
            # IGNORE INVALID / ZERO-LENGTH SEGMENTS
            # --------------------------------------------------

            if end <= start:
                continue

            speaker_turns.append(
                {
                    "start":
                        start,

                    "end":
                        end,

                    "speaker":
                        speaker
                }
            )

        # ======================================================
        # SORT BY TIME
        # ======================================================

        speaker_turns.sort(
            key=lambda item:
                (
                    item["start"],
                    item["end"]
                )
        )

        # ======================================================
        # SAVE OUTPUT
        # ======================================================

        output_file = (
            DIARIZATION_DIR
            /
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

        # ======================================================
        # DETECT UNIQUE SPEAKERS
        # ======================================================

        speakers = sorted(
            {
                item[
                    "speaker"
                ]

                for item
                in speaker_turns
            }
        )

        # ======================================================
        # LOG RESULTS
        # ======================================================

        print(
            f"✓ Speakers detected: "
            f"{len(speakers)}"
        )

        if speakers:

            print(
                "  "
                +
                ", ".join(
                    speakers
                )
            )

        print(
            f"✓ Speaker turns: "
            f"{len(speaker_turns)}"
        )

        print(
            f"✓ Speaker turns saved to: "
            f"{output_file}"
        )

        return output_file