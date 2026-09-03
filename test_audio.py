from faster_whisper import WhisperModel
import json

model = WhisperModel(
    "small",
    device="cpu",
    compute_type="int8"
)

segments, info = model.transcribe(
    "output/test_meeting.wav",
    beam_size=5,
    vad_filter=True
)

transcript_data = []

for segment in segments:
    transcript_data.append({
        "start": round(segment.start, 2),
        "end": round(segment.end, 2),
        "text": segment.text.strip()
    })

with open("output/transcript.json", "w", encoding="utf-8") as file:
    json.dump(transcript_data, file, indent=4, ensure_ascii=False)

print("Transcript saved to output/transcript.json")