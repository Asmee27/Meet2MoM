import json
import re
import subprocess

from pathlib import Path

from app.core.config import METADATA_DIR


class AudioQualityAnalyzer:

    def __init__(self):

        METADATA_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

    # ======================================================
    # VOLUME ANALYSIS
    # ======================================================

    def analyze(self, audio_file):

        audio_file = Path(audio_file)

        command = [
            "ffmpeg",
            "-i",
            str(audio_file),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-"
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True
        )

        output = result.stderr

        mean_match = re.search(
            r"mean_volume:\s*(-?[\d.]+)\s*dB",
            output
        )

        max_match = re.search(
            r"max_volume:\s*(-?[\d.]+)\s*dB",
            output
        )

        mean_volume = (
            float(mean_match.group(1))
            if mean_match
            else None
        )

        max_volume = (
            float(max_match.group(1))
            if max_match
            else None
        )

        # ----------------------------------------------
        # Quality classification
        # ----------------------------------------------

        warnings = []

        quality = "good"

        if mean_volume is not None:

            if mean_volume < -35:

                warnings.append(
                    "Audio volume is very low."
                )

                quality = "poor"

            elif mean_volume < -28:

                warnings.append(
                    "Audio volume is relatively low."
                )

                quality = "fair"

        if max_volume is not None:

            if max_volume >= -0.2:

                warnings.append(
                    "Possible clipping detected."
                )

                if quality == "good":
                    quality = "fair"

        analysis = {

            "mean_volume_db":
                mean_volume,

            "max_volume_db":
                max_volume,

            "quality":
                quality,

            "warnings":
                warnings
        }

        output_file = (
            METADATA_DIR /
            "audio_quality.json"
        )

        with open(
            output_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                analysis,
                file,
                indent=4
            )

        print(
            f"✓ Audio quality: "
            f"{quality.upper()}"
        )

        if mean_volume is not None:

            print(
                f"  Mean volume: "
                f"{mean_volume} dB"
            )

        for warning in warnings:

            print(
                f"  ⚠ {warning}"
            )

        return analysis