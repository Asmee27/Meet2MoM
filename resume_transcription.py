from faster_whisper import WhisperModel
import os
import json

CHUNK_DIR = "output/chunks"
METADATA_FILE = "output/chunk_metadata.json"
OUTPUT_FILE = "output/resume_transcript.json"

# Load Whisper model
model = WhisperModel(
    "small",
    device="cpu",
    compute_type="int8"
)

# Load chunk metadata
with open(METADATA_FILE, "r", encoding="utf-8") as file:
    metadata = json.load(file)

# Load existing transcript if available
final_transcript = []

if os.path.exists(OUTPUT_FILE):
    with open(OUTPUT_FILE, "r", encoding="utf-8") as file:
        final_transcript = json.load(file)


for chunk in metadata:

    # Skip chunks that were already successfully processed
    if chunk["status"] == "done":
        print(f"Skipping {chunk['filename']} - already done")
        continue

    chunk_path = os.path.join(CHUNK_DIR, chunk["filename"])

    # Original starting position of this chunk in the meeting
    chunk_offset = chunk["start_time"]

    print(f"\nProcessing {chunk['filename']}...")

    # Mark chunk as processing
    chunk["status"] = "processing"

    with open(METADATA_FILE, "w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=4)

    try:

        # Transcribe chunk
        segments, info = model.transcribe(
            chunk_path,
            beam_size=5,
            vad_filter=True
        )

        chunk_results = []

        for segment in segments:

            # Convert local chunk timestamps
            # into timestamps from the original meeting
            actual_start = segment.start + chunk_offset
            actual_end = segment.end + chunk_offset

            text = segment.text.strip()

            # Skip empty segments
            if not text:
                continue

            # Whisper confidence-related value
            avg_logprob = getattr(
                segment,
                "avg_logprob",
                None
            )

            # Simple confidence flag
            if avg_logprob is not None:
                confidence_flag = (
                    "low"
                    if avg_logprob < -1.0
                    else "normal"
                )
            else:
                confidence_flag = "unknown"

            # -----------------------------
            # DUPLICATE DETECTION
            # -----------------------------

            is_duplicate = False

            # Check recent transcript items
            # because duplicates mainly appear
            # around overlapping chunk boundaries
            recent_items = (
                final_transcript[-5:]
                + chunk_results[-5:]
            )

            for previous in recent_items:

                same_text = (
                    previous["text"]
                    .strip()
                    .lower()
                    ==
                    text.lower()
                )

                close_time = (
                    abs(
                        previous["start"]
                        - actual_start
                    )
                    < 8
                )

                if same_text and close_time:
                    is_duplicate = True
                    break

            # Only save unique segments
            if not is_duplicate:

                chunk_results.append({
                    "chunk_id": chunk["chunk_id"],
                    "start": round(actual_start, 2),
                    "end": round(actual_end, 2),
                    "text": text,
                    "avg_logprob": (
                        round(avg_logprob, 3)
                        if avg_logprob is not None
                        else None
                    ),
                    "confidence_flag": confidence_flag
                })

        # Add current chunk transcript
        final_transcript.extend(chunk_results)

        # Mark successful chunk
        chunk["status"] = "done"

        # Remove old error message if retry succeeded
        if "error" in chunk:
            del chunk["error"]

        # Save transcript immediately
        # so progress is not lost
        with open(
            OUTPUT_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                final_transcript,
                file,
                indent=4,
                ensure_ascii=False
            )

        # Save updated metadata
        with open(
            METADATA_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                metadata,
                file,
                indent=4
            )

        print(
            f"Completed {chunk['filename']} "
            f"- {len(chunk_results)} segments saved"
        )

    except Exception as error:

        # Mark failed chunk
        chunk["status"] = "failed"
        chunk["error"] = str(error)

        with open(
            METADATA_FILE,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                metadata,
                file,
                indent=4
            )

        print(
            f"Failed {chunk['filename']}: "
            f"{error}"
        )


print("\nResume-safe transcription completed.")
print(f"Transcript saved to: {OUTPUT_FILE}")