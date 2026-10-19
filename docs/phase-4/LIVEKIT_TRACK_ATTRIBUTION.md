# Fix: track-aware transcription and speaker attribution for LiveKit meetings

## Symptom (meeting `750ae9fdee724afdb88a647be51a304c`)

The meeting had two participants with separate LiveKit tracks: Arya (host,
`user_8zvyy6va`, offset 0.569 s) and Guest Participant (`user_8k2qzyep`,
offset 22.194 s). The transcript, the speaker turns and the PDF still reported a
single speaker, `SPEAKER_00`. The guest's segment was also marked as an
uncertain speaker.

## Root cause

* The recorded pipeline (`scripts/run_recorded_pipeline.py`) only ever processed
  the mixed `recording.wav`. It ran Whisper on the mix, then pyannote on the mix,
  then `SpeakerTranscriptMerger`. The participant tracks and
  `recording_manifest.json` were never read for transcription. Only the
  after-the-fact `/speakers` endpoint used them.
* The meeting contained about 7.5 s of speech in total. Pyannote returned two
  turns (36.16–39.64 s and 50.18–54.28 s) and put both in one cluster,
  `SPEAKER_00`. Everything downstream inherited that label.
* Whisper's second segment on the mix covered 39.99–53.95 s, which swallowed
  10 s of silence. Its overlap with the diarization turn was only 0.27, so the
  segment was flagged as an uncertain speaker. The guest actually spoke
  50.2–55.7 s, measured from the guest's own track.

## Fix

When `storage/<id>/recording/recording_manifest.json` describes LiveKit
participant tracks, and every track passes validation, each track is
transcribed on its own. The participant's microphone track is the source of
identity.

* `app/services/live/track_transcription.py` validates every track:
  * the path must resolve inside the recording directory;
  * the file must exist and be a readable WAV with frames;
  * the track must fit inside the recording;
  * a silent track is valid but isn't transcribed.

  It then merges the per-track segments into one chronological transcript:
  * Each segment carries `speaker` (the participant identity), `speaker_name`,
    `track_sid` and `attribution_source: "livekit_track"`.
  * Speech that two participants really spoke at the same time is kept for both
    and flagged `possible_cross_talk` for review.
  * When the same words appear on two microphones at once (acoustic bleed), only
    the copy from the louder, own track is kept.
  * Low-confidence ASR still sets `requires_review`.
* `MeetingTranscriber.transcribe_track` reuses the existing first pass and
  selective retry (extracted into `transcribe_with_retry`) and
  `build_chunk_results`. The track offset is applied exactly once, as the chunk
  start time.
* `SpeakerTranscriptMerger.write_outputs` is shared by both paths. The handoff
  adds `speaker_name` / `attribution_source` / `track_sid` /
  `overlapping_speakers` only when they are present, so diarization output is
  unchanged.
* `transcript_index` now persists `speaker_name`. The DB, the transcript page,
  the evidence panel and the MoM participants (the PDF "N speakers") already
  display `speaker_name or speaker_id`.
* Fallback: uploads, legacy recordings, or any LiveKit meeting with a missing,
  empty, corrupt, unsafe or out-of-range track use the unchanged mixed-recording
  ASR + diarization path. That path keeps every participant's speech, because
  it is all in the mix. The script logs the reason.
* Caching: the content-hash cache key includes the track manifest and track
  audio for track-mode meetings, so old mixed-recording results are never
  replayed. Track mode also invalidates the per-meeting stage cache.

Result on a copy of the original recording, using the real Whisper `small`
model:

| | Before | After |
|---|---|---|
| Speakers | `SPEAKER_00` only | Arya, Guest Participant |
| Host | 35.99–39.99 "…scheduled for Tuesday." | 35.92–39.92 "…scheduled for Tuesday." (Arya) |
| Guest | 39.99–53.95 "…marketing report. Bye for now. Thank you." (uncertain speaker) | 49.89–54.39 "…marketing report **by Friday**. Thank you." (Guest Participant) |
| MoM participants | 1 | 2 |

## The "Friday" / "Tuesday" investigation

* The day the test was looking for is in the **guest's** sentence. On the mix,
  Whisper misheard the guest's "by Friday" as "Bye for now". When the guest's
  own track is transcribed, the output is "by Friday".
* The host's track gives "Tuesday" every time, with word probability 0.87–0.94:
  * on the raw track and on the pipeline's normalized mix;
  * with and without the context prompt;
  * at 4× gain.

  Preprocessing and the prompt therefore aren't the cause. Either "Tuesday" is
  what Whisper `small` hears in that audio, or it is what was said. The host's
  speech is quiet (about -36 dBFS RMS). Someone needs to listen to the host
  track at 35.5–39.5 s to settle it. No text was changed and no vocabulary was
  added.

## Remaining limitation

Language detection runs per track. On the guest's short track it reported
Malayalam (`ml`, probability 0.98) although the text it produced is English.
The utterance `lang_tags` therefore record `ml` for that line. The mixed-audio
path had detected `en`. This is left as reported by the model. Setting
`FORCE_LANGUAGE` constrains it when a meeting's language is known.
