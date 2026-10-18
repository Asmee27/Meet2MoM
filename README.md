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
.\.venv\Scripts\uvicorn.exe app.api.main:app --host 127.0.0.1 --port 8000 --reload
```

**Terminal 2 — Start the Background Worker:**
```powershell
$env:JOB_RUNNER="worker"
.\.venv\Scripts\python.exe scripts/worker.py
```

The worker script:
- Polls the `jobs` table every second (`WORKER_POLL_INTERVAL_SECONDS=1.0`).
- Atomically claims jobs (`BEGIN IMMEDIATE` / atomic UPDATE with rowcount verification) so multiple concurrent workers never process the same meeting.
- Updates an active heartbeat (`heartbeat_at`) during execution.
- Automatically recovers stale jobs older than `WORKER_STALE_TIMEOUT_SECONDS=300.0` upon startup, failing jobs after `WORKER_MAX_RETRIES=3`.
- Handles `Ctrl+C` / `SIGTERM` gracefully by releasing the active job back to the queue.

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
