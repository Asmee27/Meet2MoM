from pydub import AudioSegment
import os

INPUT_AUDIO = "output/test_meeting.wav"
CHUNK_DIR = "output/chunks"

# 5 minutes
CHUNK_LENGTH_MS = 5 * 60 * 1000

# 5 second overlap to avoid cutting sentences
OVERLAP_MS = 5 * 1000

os.makedirs(CHUNK_DIR, exist_ok=True)

audio = AudioSegment.from_wav(INPUT_AUDIO)

duration_ms = len(audio)

start = 0
chunk_number = 1

while start < duration_ms:
    end = min(start + CHUNK_LENGTH_MS, duration_ms)

    chunk = audio[start:end]

    chunk_path = os.path.join(
        CHUNK_DIR,
        f"chunk_{chunk_number:03d}.wav"
    )

    chunk.export(chunk_path, format="wav")

    print(
        f"Created {chunk_path} "
        f"[{start / 1000:.2f}s -> {end / 1000:.2f}s]"
    )

    if end == duration_ms:
        break

    start = end - OVERLAP_MS
    chunk_number += 1

print("\nChunking completed.")