"""Shared fixtures. Tests never call a live model."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import ROOT, Settings
from app.llm.base import LLMError, NullLLM
from app.main import create_app

DEMO = (
    "I'm the policyholder. My name is Margaret Chen, policy POL-9921. "
    "I'm calling about my denied healthcare claim from January. "
    "DOB is 1985-03-15, SSN last four is 4472."
)


class ScriptLLM:
    """Extractor returns nothing. The phraser returns a fixed draft so the guard can be tested."""

    def __init__(self, text: str = "", error: LLMError | None = None) -> None:
        self.text = text
        self.error = error

    def complete_json(self, system: str, user: str, temperature: float = 0.0) -> dict:
        if self.error:
            raise self.error
        return {}

    def complete_text(self, system: str, user: str, temperature: float = 0.4) -> str:
        if self.error:
            raise self.error
        return self.text


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    # A placeholder key marks the desk "configured" so tests exercise the SOP.
    # NullLLM returns empty drafts, so replies come from the phase templates.
    settings = Settings(data_dir=tmp_path, fixtures_dir=ROOT / "fixtures", llm_api_key="sk-test", trace_enabled=True)
    return TestClient(create_app(settings, NullLLM()))


def say(client: TestClient, session_id: str, message: str) -> dict:
    response = client.post(f"/api/session/{session_id}/message", json={"message": message})
    assert response.status_code == 200, response.text
    return response.json()
