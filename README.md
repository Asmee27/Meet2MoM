# Meet2MoM / MeetTrace

Transcript-grounded meeting intelligence with human-in-the-loop review, automated claim verification, and exportable Minutes of Meeting.

## Running the Application

### 1. Prerequisites & Environment Setup
Ensure Python 3.11+ and Node.js 18+ are installed, and `ffmpeg` is on the `PATH` (the audio preparation step calls it). Developed and tested with Python 3.11.4 and Node 22 on Windows.

First-time setup after cloning (Windows PowerShell, repository root):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cd frontend; npm install; cd ..
Copy-Item .env.example .env      # then fill in your own values; never commit .env
```

`.env.example` documents every setting. The ones a full demo needs:

- `HF_TOKEN` — Hugging Face token for pyannote diarization of uploaded recordings (accept the model's conditions on Hugging Face first).
- `LLM_PROVIDER=gemini`, `LLM_MODEL`, `GEMINI_API_KEY` — LLM extraction. Check with `.\.venv\Scripts\python.exe scripts\check_llm.py` (does not print the key). Without a key, the local rule-based extractor is used and the minutes say so.
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` — live meetings (LiveKit Cloud project).
- Email (optional): `EMAIL_PROVIDER=console` (default) sends nothing and the UI says so; `EMAIL_PROVIDER=brevo` with `BREVO_API_KEY` and a Brevo-verified `EMAIL_SENDER_ADDRESS` sends invitations and approved minutes. Details: `docs/phase-8/PHASE_8_4_EMAIL.md`.

**Keep the computer clock synchronised** (Windows: Settings > Time & language > Date & time > Set time automatically, then Sync now). LiveKit rejects tokens signed with a wrong clock ("invalid token"); `GET http://127.0.0.1:8000/api/live/config` reports `clock_warning` when the clock is off (`docs/phase-11/PHASE_11_DEMO_READINESS.md`).

The SQLite database and all meeting data are created under `storage/` on first start (schema migrations run automatically). `storage/` and `.env` are git-ignored; each clone starts with an empty database.

### 2. Standalone Background Worker vs Inline Processing

Meet2MoM supports two job execution modes controlled by the `JOB_RUNNER` environment variable:

- **`JOB_RUNNER=inline` (default)**: The API server processes meeting recordings asynchronously in FastAPI `BackgroundTasks` within the server process.
- **`JOB_RUNNER=worker`**: The upload API enqueues the job into SQLite with state `queued` (returning HTTP 202 immediately) and delegates processing to one or more standalone worker processes. This keeps the API responsive even during heavy ASR and diarization workloads.

#### Running with Standalone Worker (Recommended for Production)

Open two Windows PowerShell terminals in the repository root:

**Terminal 1 — Start the API Server:**
```powershell
$env:JOB_RUNNER="worker"
.\.venv\Scripts\uvicorn.exe app.api.main:app --host 127.0.0.1 --port 8000
```

**Terminal 2 — Start the Background Worker:**
```powershell
$env:JOB_RUNNER="worker"
.\.venv\Scripts\python.exe scripts/worker.py
```

Do not use `--reload` while recording live meetings: a recording lives in the API process and a reload cuts it off.

The worker keeps the speech libraries and models loaded between meetings (`SPEECH_PIPELINE=auto`, Phase 6) and preloads them at start-up (`WORKER_PRELOAD_MODELS=true`). For English-only meetings, `WHISPER_LANGUAGE=en` skips per-track language detection. Measurements: `docs/phase-6/PHASE_6_PROCESSING_SPEED.md`.

The worker script:
- Polls the `jobs` table every second (`WORKER_POLL_INTERVAL_SECONDS=1.0`).
- Atomically claims jobs (`BEGIN IMMEDIATE` / atomic UPDATE with rowcount verification) so multiple concurrent workers never process the same meeting.
- Updates an active heartbeat (`heartbeat_at`) during execution.
- Automatically recovers stale jobs older than `WORKER_STALE_TIMEOUT_SECONDS=300.0` upon startup, failing jobs after `WORKER_MAX_RETRIES=3`.
- Handles `Ctrl+C` / `SIGTERM` gracefully by releasing the active job back to the queue.

### Accounts and access (Phase 7)

With `AUTH_REQUIRED=true` (default) the UI asks you to register or sign in. Passwords are stored as scrypt hashes and the session is an HttpOnly cookie. You see only meetings you own or were invited to; the host of a live meeting invites others with a single-meeting link (valid two days). Live meeting identity and host role come from your account, not from the form. For a local single-user demo, set `AUTH_REQUIRED=false`.

Meetings created before accounts existed have no owner and are hidden while sign-in is on. After registering, claim them with `.venv\Scripts\python.exe scripts\claim_meetings.py --owner-email you@example.com --apply` (without `--apply` it only lists them). If live recording is unavailable, `scripts\seed_demo_meetings.py --owner-email you@example.com` loads two synthetic demo meetings.

Deleting a meeting (owner only, from the meeting list) removes its database rows, recording, transcripts, minutes and caches. It is refused while the meeting is being processed or recorded.

### Scheduling, email and the commitment ledger (Phases 8.3–9.1)

- **Schedule meeting** (dashboard): title, agenda, date, time, timezone and several invitees. Each invitee gets their own meeting-scoped, expiring link (copy it, or it is emailed when Brevo is configured). An account whose email differs from the invited address waits for the host's approval. Scheduling creates no room or recording. See `docs/phase-8/PHASE_8_3_EVIDENCE_AND_SCHEDULING.md`.
- **Send approved minutes** (minutes page, owner or approving reviewer): only to members and invitees of that meeting; statuses distinguish "Sent to provider" from "Delivered".
- **Commitment ledger**: approved action items become commitments; a later meeting *proposes* done / delayed / no update with cited utterances, and only a reviewer's decision changes a status. See `docs/phase-7-10/PHASE_9_1_LEDGER_ACCURACY.md`.

Phase documentation is under `docs/` (one folder per phase).

### 3. Frontend UI

```powershell
cd frontend
npm install
npm run dev
```

Visit `http://localhost:5173` to access the interactive review and verification interface.

### 4. Running Tests

```powershell
# Backend pytest suite (fast tests)
.\.venv\Scripts\python.exe -m pytest -m "not slow" -q

# Worker tests
.\.venv\Scripts\python.exe -m pytest tests/test_worker.py -v

# Frontend vitest suite
cd frontend
npm test -- --run
```
