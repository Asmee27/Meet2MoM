# Phase 4.2 — Server-side LiveKit audio recording

## Architecture

The API process runs an in-process **recorder bot** built on the official LiveKit
Python RTC SDK (`livekit==1.1.21`). When the host starts a recording, the bot
joins the room as a **hidden, subscribe-only** participant (`hidden`, `recorder`,
`can_publish=False`) and writes each consented participant's audio track to disk.

LiveKit Egress was not used: it needs cloud object storage (S3/GCS/Azure) that
this project doesn't have configured, and egress minutes are billed separately.

```
host clicks Start ─► POST /recording/start ─► roster from LiveKit + consent check (DB)
                                             └► RecordingManager.start ─► recorder joins (auto_subscribe=False)
                                                   └► re-checks roster ─► subscribes to consented audio tracks only
audio frames ─► RecordingSession.audio_frame ─► per-track PCM on one shared clock
halt (stop / revoke / unconsented join / empty room / end / disconnect / shutdown)
   └► finalize: per-track WAV + time-aligned segment mix ─► recording/recording.wav + manifest
host clicks Process ─► POST /recording/process ─► jobs row (insert-once) ─► run_pipeline / scripts/worker.py
```

| File | Role |
|---|---|
| `app/services/live/recorder.py` | `RecordingSession` (consent-enforcing state machine), `LiveKitRecorderRoom` (SDK adapter), `RecordingManager`, recovery |
| `app/services/live/recording_audio.py` | PCM writer, frame conversion, finalize/mix, segment assembly, speaker attribution |
| `app/services/live/livekit_service.py` | recorder token, participant-token verification, consent metadata merge |
| `app/api/live_routes.py` | consent and recording endpoints |
| `app/core/db.py` | migration v3 and data access |

### Audio timing

All tracks in a segment share one clock that starts when capture is armed. A
track's first frame fixes its offset; gaps (mute, network stalls, late
publication) are padded with silence, so concurrent speakers stay aligned. The
segment mix sums the tracks sample by sample: it doesn't concatenate them.
Audio is converted to 16 kHz mono int16 by the SDK, with a fallback downmix and
resample in `normalize_frame`.

Segments are sequential in time. `recording.wav` holds them in order, with 1 s
of silence between segments. `recording_manifest.json` gives every segment's
offset and every track's participant identity, name, track SID and offset.

### Storage layout

```
storage/<meeting_id>/recording/
  recording.wav                 # what the pipeline processes
  recording_manifest.json
  segments/<seq>_<segment_id>/
    tracks.json
    track_<identity>_<sid>.wav  # per-participant source tracks
    segment.wav
```

During capture, tracks are written as raw `.pcm`, which survives a crash. All
temporary files are named `tmp_*` so the existing `recording.*` lookups never
pick up a partial artifact.

## Consent and recording policy (server-enforced)

* Consent is granted **only** by an explicit action: the join-form checkbox
  (recorded when the token is issued) or the in-room toggle. A missing row or a
  `0` means *not consented*. The token's `consented` metadata reflects the
  server's record, not the client's claim.
* Consent and recording calls are authenticated with the participant's own
  LiveKit JWT (`Authorization: Bearer`). Participants can change only their own
  consent. Only the bound host identity can start, stop or process a recording,
  or end the meeting.
* **Start** requires every participant LiveKit reports as connected to have
  consented. The recorder checks again from its own view of the room before it
  subscribes to anything.
* **Halt immediately** (first reason wins) on: a revoked consent (halted before
  the revocation is persisted), an unconsented participant joining, audio from a
  present but unconsented identity, everyone leaving, the host ending the meeting,
  the recorder disconnecting, a recorder error, or server shutdown.
* **Data policy for a halted segment:** audio captured while every connected
  participant had consented is kept and finalized. Nothing after the halt is
  written. Empty or silent segments are discarded.
* **Never auto-resumes.** Granting consent again doesn't restart capture. The
  host must start a new segment.
* Every consent change, roster change and recording transition is written to
  `live_events`. You can read them at `GET /api/live/meetings/{id}/events`
  (host only).

## Database (migration v3, additive)

`live_meetings`, `live_participants` (authoritative consent and connection
state), `live_events` (audit log) and `recording_segments` (state, timestamps,
stop reason, participants, tracks, artifact, duration, error). A partial unique
index allows at most one active (`starting`/`recording`/`stopping`) segment per
meeting. Processing reuses the existing `jobs` table: `enqueue_job_once` inserts
only when no row exists, so a recording is processed exactly once.

## Endpoints

Existing Phase 4.1 routes are kept. `/token` and `/rooms` now also return
`meeting_id`. `/consent` and `/rooms/{room}/end` now require the caller's token.

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/live/meetings/{id}/recording` | participant | Authoritative state: `status` (`idle`, `starting`, `recording`, `stopping`, `recorded`, `processing`, `completed`, `failed`), live frame counters, consent blockers, segments, artifact, processing job |
| POST | `/api/live/meetings/{id}/recording/start` | host | Validate consent, then start a new segment (202). Returns 409 with `blocked` participants otherwise |
| POST | `/api/live/meetings/{id}/recording/stop` | host | Halt, finalize and return the state (idempotent) |
| POST | `/api/live/meetings/{id}/recording/process` | host | Queue the finalized `recording.wav` once (202). Repeat calls return 200 with `already_submitted`. Use `?retry=true` to re-queue a failed job |
| GET | `/api/live/meetings/{id}/speakers` | participant | Map processed utterances to LiveKit participants using per-track energy |
| GET | `/api/live/meetings/{id}/events` | host | Consent, roster and recording audit trail |
| GET | `/api/meetings/{id}/status` | — | Existing pipeline status endpoint, unchanged |

## Configuration and running

* `.env`: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, which already
  exist for Phase 4.1. No other service or storage is required.
* `pip install -r requirements.txt` now also installs `livekit==1.1.21`,
  `livekit-api==1.2.1` and `PyJWT==2.10.1`.
* Run the API as a **single process without `--reload`** while you record:
  `python -m uvicorn app.api.main:app --port 8000`. Active recordings live in
  that process. A reload or a second worker would cut the recording off; the
  next start recovers and finalizes it as `interrupted`.
* Processing follows `JOB_RUNNER` exactly as uploads do. With `inline`, the job
  runs in the API process. With `worker`, it's queued for `scripts/worker.py`.

## Manual two-browser acceptance test

1. Start the backend (`python -m uvicorn app.api.main:app --port 8000`) and the
   frontend (`cd frontend && npm run dev`).
2. **Browser A, the host (for example Chrome):** open `http://localhost:5173`,
   choose **Start Live Meeting**, tick **Explicit Recording & MoM Transcription
   Consent**, then click **Create & Start Live Meeting Room**. Allow microphone
   access. Click **Copy Invite Link**.
3. **Browser B, the guest (a different browser or a private window):** open the
   invite link, enter a name, tick the consent box, join and allow the
   microphone.
4. In browser A, check that the badge reads **Not recording** (not `REC`) and
   the consent pill reads `2/2`. Click **● Start Recording**. The badge must move
   from **Starting capture…** to **REC mm:ss** only once audio arrives.
5. Have each person read a different sentence. For example, A says *"The
   quarterly budget review is on Tuesday."* and B says *"I will send the
   marketing plan by Friday."*
6. Confirm real server activity. `GET
   /api/live/meetings/<meeting_id>/recording` with the host's token should show
   `status: "recording"`, a rising `live.frames_captured`, and two entries in
   `live.tracks`. `storage/<meeting_id>/recording/segments/001_*/` should hold
   two growing `.pcm` files.
7. In browser B, open the Attendees drawer and click **Revoke Consent**. Both
   browsers must leave `REC`, and a banner must say the recording stopped
   because a participant revoked consent.
8. In browser B, click **Grant Consent**. Wait at least 10 s and confirm that
   **REC doesn't come back**. The host's button now reads **● Start New
   Segment**. Click it and say another sentence.
9. In browser A, click **End Meeting**, then **End for Everyone**. The ended
   screen lists the segments: segment 1 stopped because *a participant revoked
   consent*, and segment 2 because *the meeting ended*. It also shows the
   recorded duration and both speakers.
10. Click **⚙️ Process Meeting**. Watch the stage and progress. When it
    finishes, the app opens the existing transcript and review workflow.
11. Check that the transcript contains both sentences. Then check
    `GET /api/live/meetings/<meeting_id>/speakers`, which maps each utterance to
    the participant whose track carried it. Finally, continue through
    verification, review and approval as in Phase 3.

Test fails if REC appears before audio, capture continues after a revocation,
or capture restarts without the host.

## Known limitations

* There are no user accounts. The host is the first identity that asks for a
  host token for a meeting, and it's bound for that meeting. Identity is stable
  per browser tab (`sessionStorage`).
* A recording only runs in the API process that started it. Use a single
  uvicorn worker.
* Consent badges for other participants come from LiveKit metadata the server
  writes, and the server roster is polled every 2 s. The server halts capture
  immediately in either case.
* Speaker attribution (`/speakers`) is energy-based. With crosstalk it returns
  no participant rather than guessing.
