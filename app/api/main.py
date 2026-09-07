from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.meeting_routes import router as meeting_router

app = FastAPI(
    title="Meet2MoM API",
    version="2.0.0",
    description="Meet2MoM backend connected to the recorded-meeting intelligence pipeline.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meeting_router)

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
