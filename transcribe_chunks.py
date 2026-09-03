from faster_whisper import WhisperModel
import os
import json

CHUNK_DIR = "output/chunks"
OUTPUT_FILE = "output/final_transcript.json"

# Same values we used while creating chunks
CHUNK_LENGTH = 300       # 5 minutes
OVERLAP = 5              # 5 seconds
STEP_SIZE = CHUNK_LENGTH - OVERLAP

model = WhisperModel(
    "small",
    device="cpu",
    compute_type="int8"
)

chunk_files = sorted(
    file for file in os.listdir(CHUNK_DIR)
    if file.endswith(".wav")
)

final_transcript = []

for index, chunk_file in enumerate(chunk_files):

    chunk_path = os.path.join(CHUNK_DIR, chunk_file)

    print(f"\nTranscribing {chunk_file}...")

    # Calculate where this chunk actually starts
    # in the ORIGINAL meeting
    chunk_offset = index * STEP_SIZE

    segments, info = model.transcribe(
        chunk_path,
        beam_size=5,
        vad_filter=True
    )

    for segment in segments:
        actual_start = segment.start + chunk_offset
        actual_end = segment.end + chunk_offset
        text = segment.text.strip()

        is_duplicate = False

        # Check only the last few transcript entries
        for previous in final_transcript[-5:]:
            same_text = previous["text"].lower() == text.lower()
            close_time = abs(previous["start"] - actual_start) < 8

            if same_text and close_time:
                is_duplicate = True
                break

        if not is_duplicate:
            final_transcript.append({
                "start": round(actual_start, 2),
                "end": round(actual_end, 2),
                "text": text
            })

print("\nSaving final transcript...")

with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
    json.dump(
        final_transcript,
        file,
        indent=4,
        ensure_ascii=False
    )

print(f"Done! Final transcript saved to {OUTPUT_FILE}")