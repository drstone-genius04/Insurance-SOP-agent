"""HTTP API and the claims-desk page."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import Settings, load_settings
from app.llm.base import LLMClient
from app.llm.client import build_client
from app.sop.engine import Harness
from app.store import SessionStore
from app.tools.records import Records

STATIC = Path(__file__).resolve().parent / "static"


class SessionIn(BaseModel):
    """Optional consent script for a representative call."""

    consent_scenario: str = "default"


class MessageIn(BaseModel):
    """One caller turn. Identity values are redacted before they are stored."""

    message: str = Field(min_length=1, max_length=4000)


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings and a fake model."""
    settings = settings or load_settings()
    records = Records.load(settings.fixtures_dir)
    store = SessionStore(settings.data_dir / "sessions.db")
    client = llm if llm is not None else build_client(settings)
    harness = Harness(settings, records, client, store)

    app = FastAPI(title="Harborline Claims Desk", version="1.0.0")
    app.state.harness = harness
    app.state.settings = settings

    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "status": "ok",
            "provider": settings.llm_provider,
            "transport": settings.llm_transport,
            "model": settings.llm_model,
            "host": settings.llm_host,
            "llm_configured": settings.llm_configured,
        }

    @app.post("/api/session")
    def open_session(body: SessionIn | None = None) -> dict:
        scenario = body.consent_scenario if body else "default"
        try:
            return harness.start(scenario)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/session/{session_id}/message")
    def post_message(session_id: str, body: MessageIn) -> dict:
        try:
            result = harness.message(session_id, body.message)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Call not found.") from exc
        return {"reply": result["reply"], "state": result["state"], "debug": result["debug"]}

    @app.get("/api/session/{session_id}")
    def get_session(session_id: str) -> dict:
        snapshot = harness.snapshot(session_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="Call not found.")
        return snapshot

    if STATIC.exists():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()
