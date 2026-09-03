import json
import shutil
import subprocess
import wave
from pathlib import Path

from app.core.config import (
    AUDIO_DIR,
    CHUNK_DIR,
    METADATA_DIR,
    SAMPLE_RATE,
    CHANNELS,
    CHUNK_LENGTH_SECONDS,
    CHUNK_OVERLAP_SECONDS,
    SUPPORTED_EXTENSIONS,
    ENABLE_AUDIO_ENHANCEMENT,
    HIGH_PASS_HZ,
    LOW_PASS_HZ,
    DENOISE_LEVEL,
)


class AudioProcessor:

    def __init__(self, input_file):

        self.input_file = Path(input_file)

        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        CHUNK_DIR.mkdir(parents=True, exist_ok=True)
        METADATA_DIR.mkdir(parents=True, exist_ok=True)

    # ======================================================
    # BASIC FILE VALIDATION
    # ======================================================

    def validate_input(self):

        if not self.input_file.exists():

            raise FileNotFoundError(
                f"Input file not found: {self.input_file}"
            )

        extension = self.input_file.suffix.lower()

        if extension not in SUPPORTED_EXTENSIONS:

            raise ValueError(
                f"Unsupported file format: {extension}"
            )

        file_size = self.input_file.stat().st_size

        if file_size <= 0:

            raise ValueError(
                "Input recording is empty."
            )

        if shutil.which("ffmpeg") is None:

            raise RuntimeError(
                "FFmpeg was not found in PATH."
            )

        if shutil.which("ffprobe") is None:

            raise RuntimeError(
                "FFprobe was not found in PATH."
            )

        print("✓ File exists")
        print(
            f"✓ File size: "
            f"{file_size / (1024 * 1024):.2f} MB"
        )

        print("✓ Basic validation passed")

    # ======================================================
    # FULL MEDIA INSPECTION
    # ======================================================

    def inspect_recording(self):

        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration,format_name:"
                "stream=index,codec_type,codec_name,"
                "sample_rate,channels"
            ),
            "-of",
            "json",
            str(self.input_file)
        ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:

            raise RuntimeError(
                "FFprobe could not read the recording. "
                "The file may be corrupted or incomplete."
            )

        try:
            data = json.loads(result.stdout)

        except json.JSONDecodeError:

            raise RuntimeError(
                "Invalid FFprobe response."
            )

        streams = data.get("streams", [])

        audio_streams = [
            stream
            for stream in streams
            if stream.get("codec_type") == "audio"
        ]

        if not audio_streams:

            raise ValueError(
                "No audio stream was found in this recording."
            )

        format_info = data.get("format", {})

        duration = format_info.get("duration")

        if duration is not None:
            duration = float(duration)

        audio = audio_streams[0]

        recording_info = {

            "input_file":
                str(self.input_file),

            "container":
                format_info.get(
                    "format_name",
                    "unknown"
                ),

            "duration_seconds":
                round(duration, 3)
                if duration
                else None,

            "audio_codec":
                audio.get(
                    "codec_name",
                    "unknown"
                ),

            "sample_rate":
                int(audio["sample_rate"])
                if audio.get("sample_rate")
                else None,

            "channels":
                audio.get("channels")
        }

        metadata_file = (
            METADATA_DIR /
            "recording_info.json"
        )

        with open(
            metadata_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                recording_info,
                file,
                indent=4
            )

        print("✓ Audio stream detected")

        print(
            f"  Codec: "
            f"{recording_info['audio_codec']}"
        )

        print(
            f"  Sample rate: "
            f"{recording_info['sample_rate']} Hz"
        )

        print(
            f"  Channels: "
            f"{recording_info['channels']}"
        )

        if duration:

            print(
                f"  Duration: "
                f"{duration / 60:.2f} minutes"
            )

        return recording_info

    # ======================================================
    # NORMALIZE / ENHANCE AUDIO
    # ======================================================

    def normalize_audio(self):

        output_file = (
            AUDIO_DIR /
            f"{self.input_file.stem}_normalized.wav"
        )

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(self.input_file),
            "-vn"
        ]

        # ----------------------------------------------
        # Real-world speech enhancement
        # ----------------------------------------------

        if ENABLE_AUDIO_ENHANCEMENT:

            filters = [

                # Remove low-frequency rumble
                f"highpass=f={HIGH_PASS_HZ}",

                # Remove unnecessary high frequencies
                f"lowpass=f={LOW_PASS_HZ}",

                # Frequency-domain noise reduction
                f"afftdn=nf=-{DENOISE_LEVEL}",

                # Normalize loudness
                "loudnorm=I=-16:LRA=11:TP=-1.5"
            ]

            command.extend([
                "-af",
                ",".join(filters)
            ])

        command.extend([
            "-ac",
            str(CHANNELS),

            "-ar",
            str(SAMPLE_RATE),

            "-c:a",
            "pcm_s16le",

            str(output_file)
        ])

        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0:

            raise RuntimeError(
                "Audio normalization failed:\n"
                + result.stderr[-1500:]
            )

        if not output_file.exists():

            raise RuntimeError(
                "Normalized audio was not created."
            )

        print(
            f"✓ Audio standardized to "
            f"{SAMPLE_RATE} Hz mono"
        )

        if ENABLE_AUDIO_ENHANCEMENT:

            print(
                "✓ Noise filtering + "
                "speech enhancement applied"
            )

        return output_file

    # ======================================================
    # WAV DURATION
    # ======================================================

    @staticmethod
    def get_wav_duration(wav_file):

        with wave.open(
            str(wav_file),
            "rb"
        ) as wav:

            frames = wav.getnframes()
            rate = wav.getframerate()

        return frames / float(rate)

    # ======================================================
    # CLEAN CHUNKS
    # ======================================================

    @staticmethod
    def clean_chunk_directory():

        CHUNK_DIR.mkdir(
            parents=True,
            exist_ok=True
        )

        for file in CHUNK_DIR.glob(
            "chunk_*.wav"
        ):

            file.unlink()

    # ======================================================
    # CREATE LONG-AUDIO CHUNKS
    # ======================================================

    def create_chunks(
        self,
        normalized_audio
    ):

        self.clean_chunk_directory()

        duration = self.get_wav_duration(
            normalized_audio
        )

        print(
            f"✓ Normalized duration: "
            f"{duration / 60:.2f} minutes"
        )

        metadata = []

        start_time = 0.0
        chunk_id = 1

        while start_time < duration:

            end_time = min(
                start_time
                + CHUNK_LENGTH_SECONDS,
                duration
            )

            chunk_filename = (
                f"chunk_{chunk_id:03d}.wav"
            )

            chunk_path = (
                CHUNK_DIR /
                chunk_filename
            )

            command = [
                "ffmpeg",
                "-y",

                "-ss",
                str(start_time),

                "-i",
                str(normalized_audio),

                "-t",
                str(
                    end_time
                    - start_time
                ),

                "-ac",
                str(CHANNELS),

                "-ar",
                str(SAMPLE_RATE),

                "-c:a",
                "pcm_s16le",

                str(chunk_path)
            ]

            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True
            )

            if result.returncode != 0:

                raise RuntimeError(
                    f"Could not create "
                    f"{chunk_filename}"
                )

            metadata.append({

                "chunk_id":
                    chunk_id,

                "filename":
                    chunk_filename,

                "start_time":
                    round(
                        start_time,
                        3
                    ),

                "end_time":
                    round(
                        end_time,
                        3
                    ),

                "duration_seconds":
                    round(
                        end_time
                        - start_time,
                        3
                    ),

                "status":
                    "ready",

                "retry_count":
                    0
            })

            print(
                f"  ✓ {chunk_filename}: "
                f"{start_time:.2f}s → "
                f"{end_time:.2f}s"
            )

            if end_time >= duration:

                break

            start_time = (
                end_time
                - CHUNK_OVERLAP_SECONDS
            )

            chunk_id += 1

        metadata_file = (
            METADATA_DIR /
            "chunk_metadata.json"
        )

        with open(
            metadata_file,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                metadata,
                file,
                indent=4
            )

        print(
            f"✓ Created "
            f"{len(metadata)} chunk(s)"
        )

        return metadata_file