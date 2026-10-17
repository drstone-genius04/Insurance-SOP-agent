"""Shared fixtures. Tests never call a live model."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

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
        self.json_calls = 0
        self.text_calls = 0

    def complete_json(self, system: str, user: str, temperature: float = 0.0) -> dict:
        self.json_calls += 1
        if self.error:
            raise self.error
        return {}

    def complete_text(self, system: str, user: str, temperature: float = 0.4) -> str:
        self.text_calls += 1
        if self.error:
            raise self.error
        return self.text


class FakeLLM:
    """Harness-style stand-in: scripted JSON extracts by message substring; optional fixed reply.

    When no reply text is set, empty phrasing falls through to phase templates — same as NullLLM.
    """

    def __init__(
        self,
        scripts: dict[str, dict[str, Any]] | None = None,
        reply: str = "",
        error: LLMError | None = None,
    ) -> None:
        self.scripts = scripts or {}
        self.reply = reply
        self.error = error
        self.json_calls: list[str] = []
        self.text_calls: list[str] = []
        self.last_user = ""

    def complete_json(self, system: str, user: str, temperature: float = 0.0) -> dict:
        if self.error:
            raise self.error
        self.last_user = user
        self.json_calls.append(user)
        lowered = user.lower()
        # Prefer the longest matching key so overlapping substrings stay predictable.
        hits = sorted(
            ((key, data) for key, data in self.scripts.items() if key.lower() in lowered),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        return dict(hits[0][1]) if hits else {}

    def complete_text(self, system: str, user: str, temperature: float = 0.4) -> str:
        if self.error:
            raise self.error
        self.text_calls.append(user)
        return self.reply


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    # A placeholder key marks the desk "configured" so tests exercise the SOP.
    # NullLLM returns empty drafts, so replies come from the phase templates.
    settings = Settings(data_dir=tmp_path, fixtures_dir=ROOT / "fixtures", llm_api_key="sk-test", trace_enabled=True)
    return TestClient(create_app(settings, NullLLM()))


@pytest.fixture
def make_client(tmp_path: Path) -> Callable[..., TestClient]:
    def _make(llm: Any = None, **overrides: Any) -> TestClient:
        values: dict[str, Any] = {
            "data_dir": tmp_path,
            "fixtures_dir": ROOT / "fixtures",
            "llm_api_key": "sk-test",
            "trace_enabled": True,
        }
        values.update(overrides)
        settings = Settings(**values)
        return TestClient(create_app(settings, llm if llm is not None else NullLLM()))

    return _make


def say(client: TestClient, session_id: str, message: str, **llm_fields: Any) -> dict:
    body: dict[str, Any] = {"message": message, **llm_fields}
    response = client.post(f"/api/session/{session_id}/message", json=body)
    assert response.status_code == 200, response.text
    return response.json()
