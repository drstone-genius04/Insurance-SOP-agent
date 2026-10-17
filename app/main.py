"""HTTP API and the claims-desk page."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import Settings, caller_settings, load_settings, provider_catalog
from app.llm.base import LLMClient, LLMError
from app.llm.client import build_client
from app.sop.engine import Harness
from app.store import SessionStore
from app.tools.records import Records

STATIC = Path(__file__).resolve().parent / "static"


class SessionIn(BaseModel):
    """Optional consent script for a representative call."""

    consent_scenario: str = "default"


class MessageIn(BaseModel):
    """One caller turn. A visitor key is used for this request and is not stored."""

    message: str = Field(min_length=1, max_length=4000)
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None


class LlmCheckIn(BaseModel):
    """Probe a visitor key. The key is not stored."""

    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str = Field(min_length=1, max_length=512)
    llm_base_url: str | None = None


def _scrub_secret(value: object, secret: str) -> object:
    """Drop a visitor key if a model error echoed it back."""
    if isinstance(value, str):
        return value.replace(secret, "[key]")
    if isinstance(value, list):
        return [_scrub_secret(item, secret) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_secret(item, secret) for key, item in value.items()}
    return value


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings and a fake model."""
    settings = settings or load_settings()
    records = Records.load(settings.fixtures_dir)
    store = SessionStore(settings.data_dir / "sessions.db")
    client = llm if llm is not None else build_client(settings)
    harness = Harness(settings, records, client, store)

    app = FastAPI(title="Claims Desk", version="1.0.0")
    app.state.harness = harness
    app.state.settings = settings
    # Tests inject a fake model. A visitor key must not replace that client.
    app.state.allow_caller_key = llm is None

    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "status": "ok",
            "provider": settings.llm_provider,
            "transport": settings.llm_transport,
            "model": settings.llm_model,
            "host": settings.llm_host,
            "llm_configured": settings.llm_configured,
            "bring_your_own_key": True,
        }

    @app.get("/api/providers")
    def providers() -> dict:
        return {"providers": provider_catalog()}

    @app.post("/api/llm/check")
    def check_llm(body: LlmCheckIn) -> dict:
        """Confirm a visitor key works before the call uses it. Nothing is stored."""
        override = caller_settings(
            settings,
            body.llm_provider,
            body.llm_model,
            body.llm_api_key,
            body.llm_base_url,
        )
        if override is None:
            return {"ok": False, "error": "Paste an API key first."}
        probe = build_client(override)
        try:
            probe.complete_text("Reply with exactly: ok", "Say ok.", temperature=0.0)
        except LLMError as exc:
            detail = str(exc)
            if body.llm_api_key.strip() in detail:
                detail = detail.replace(body.llm_api_key.strip(), "[key]")
            if exc.config:
                if exc.status == 401:
                    detail = "That key was rejected (HTTP 401). Check the key and provider."
                elif exc.status == 403:
                    detail = "That key is not allowed for this model (HTTP 403)."
                elif exc.status == 404:
                    detail = "That model name was not found (HTTP 404). Pick another model."
            return {
                "ok": False,
                "error": detail,
                "status": exc.status,
                "provider": override.llm_provider,
                "model": override.llm_model,
            }
        finally:
            if hasattr(probe, "close"):
                probe.close()
        return {
            "ok": True,
            "provider": override.llm_provider,
            "model": override.llm_model,
            "host": override.llm_host,
        }

    @app.post("/api/session")
    def open_session(body: SessionIn | None = None) -> dict:
        scenario = body.consent_scenario if body else "default"
        try:
            return harness.start(scenario)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def _caller_client(body: MessageIn) -> tuple[LLMClient, bool, bool]:
        """Use the visitor key for this turn only. The key is never written to the session."""
        if not app.state.allow_caller_key:
            return harness.llm, settings.llm_configured, False
        override = caller_settings(
            settings,
            body.llm_provider,
            body.llm_model,
            body.llm_api_key,
            body.llm_base_url,
        )
        if override is None:
            return harness.llm, settings.llm_configured, False
        return build_client(override), True, True

    @app.post("/api/session/{session_id}/message")
    def post_message(session_id: str, body: MessageIn) -> dict:
        caller, configured, owns = _caller_client(body)
        try:
            result = harness.message(session_id, body.message, llm=caller, configured=configured)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Call not found.") from exc
        finally:
            if owns:
                caller.close()
        secret = (body.llm_api_key or "").strip()
        if secret:
            result = _scrub_secret(result, secret)
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
