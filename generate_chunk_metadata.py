import os
import json
from pydub import AudioSegment

CHUNK_DIR = "output/chunks"
OUTPUT_FILE = "output/chunk_metadata.json"

CHUNK_LENGTH = 300   # 5 minutes
OVERLAP = 5
STEP_SIZE = CHUNK_LENGTH - OVERLAP

metadata = []

chunk_files = sorted(
    file for file in os.listdir(CHUNK_DIR)
    if file.endswith(".wav")
)

for index, chunk_file in enumerate(chunk_files, start=1):

    chunk_path = os.path.join(CHUNK_DIR, chunk_file)

    audio = AudioSegment.from_wav(chunk_path)
    duration_sec = len(audio) / 1000

    start_time = (index - 1) * STEP_SIZE
    end_time = start_time + duration_sec

    metadata.append({
        "chunk_id": index,
        "filename": chunk_file,
        "start_time": round(start_time, 2),
        "end_time": round(end_time, 2),
        "duration_seconds": round(duration_sec, 2),
        "status": "ready"
    })

with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
    json.dump(metadata, file, indent=4)

print(f"Chunk metadata saved to {OUTPUT_FILE}")