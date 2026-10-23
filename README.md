# Meet2MoM / MeetTrace

Transcript-grounded meeting intelligence with human-in-the-loop review, automated claim verification, and exportable Minutes of Meeting.

## Running the Application

### 1. Prerequisites & Environment Setup
Ensure Python 3.11+ and Node.js 18+ are installed.

```powershell
# In Windows PowerShell:
.\.venv\Scripts\Activate.ps1
```

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
