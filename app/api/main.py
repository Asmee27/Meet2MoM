import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.console import make_stdio_unicode_safe

make_stdio_unicode_safe()

from app.api.meeting_routes import router as meeting_router  # noqa: E402
from app.api.live_routes import router as live_router  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Recover recordings a previous process left unfinished; finalize active ones on shutdown."""
    from app.core import config, db
    from app.services.live.recorder import get_recording_manager, recover_interrupted_segments

    try:
        db.init_db()
        recover_interrupted_segments(get_recording_manager())
        # Inline jobs died with the previous process; the worker recovers its own.
        if (getattr(config, "JOB_RUNNER", "inline") or "inline").strip().lower() != "worker":
            db.fail_interrupted_inline_jobs()
    except Exception:
        logging.getLogger("main").exception("Recording/job recovery failed at startup")
    try:
        # Phase 8.4: send queued email (and retries) from this process unless
        # EMAIL_DISPATCH says the worker does it, or nothing should send.
        from app.services.email.outbox import start_background_dispatcher

        start_background_dispatcher()
    except Exception:
        logging.getLogger("main").exception("Email dispatcher did not start")
    yield
    await get_recording_manager().shutdown()


app = FastAPI(
    lifespan=lifespan,
    title="Meet2MoM API",
    version="2.0.0",
    description="Meet2MoM backend connected to the recorded-meeting intelligence pipeline.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.api.auth_routes import router as auth_router  # noqa: E402
from app.api.schedule_routes import router as schedule_router  # noqa: E402
from app.api.email_routes import router as email_router  # noqa: E402

app.include_router(auth_router)
app.include_router(schedule_router)
app.include_router(email_router)
app.include_router(meeting_router)
app.include_router(live_router)


@app.get("/")
def root():
    return {
        "message": "Meet2MoM backend is running",
        "status": "ok",
        "pipeline": "recorded_meeting_intelligence",
    }

@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "service": "Meet2MoM API",
    }
