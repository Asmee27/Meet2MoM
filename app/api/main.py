from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.meeting_routes import router as meeting_router
from app.api.result_routes import router as result_router


app = FastAPI(
    title="Meet2MoM API",
    version="1.0.0",
    description="Backend API for Meet2MoM Meeting Intelligence Platform"
)


# ---------------------------------------------------------
# CORS
# Allows React frontend running on localhost:5173
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# ROUTERS
# ---------------------------------------------------------

app.include_router(
    meeting_router,
    prefix="/api/meetings",
    tags=["Meetings"]
)

app.include_router(
    result_router,
    prefix="/api/results",
    tags=["Results"]
)


# ---------------------------------------------------------
# HEALTH CHECK
# ---------------------------------------------------------

@app.get("/api/health")
def health_check():
    return {
        "status": "ok",
        "service": "Meet2MoM API"
    }


@app.get("/")
def root():
    return {
        "message": "Meet2MoM backend is running"
    }