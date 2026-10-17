"""HTTP surface: health, redaction, and the page."""

from fastapi.testclient import TestClient

from tests.conftest import DEMO, say


def test_llm_check_rejects_empty_and_bad_key(client: TestClient, monkeypatch) -> None:
    from app.llm.base import LLMError
    from app.llm.client import ChatClient

    empty = client.post("/api/llm/check", json={"llm_api_key": ""})
    assert empty.status_code == 422

    def boom(self, system, user, temperature=0.4):
        raise LLMError("nope", status=401, config=True)

    def no_listing(self):
        raise LLMError("no models endpoint", status=404, config=True)

    monkeypatch.setattr(ChatClient, "list_models", no_listing)
    monkeypatch.setattr(ChatClient, "complete_text", boom)
    bad = client.post(
        "/api/llm/check",
        json={"llm_provider": "openai", "llm_model": "gpt-4o-mini", "llm_api_key": "sk-bad"},
    )
    assert bad.status_code == 200
    body = bad.json()
    assert body["ok"] is False
    assert "401" in body["error"]
    assert "sk-bad" not in body["error"]


def test_llm_check_switches_a_retired_model(client: TestClient, monkeypatch) -> None:
    from app.llm.client import ChatClient

    used: list[str] = []

    def listing(self):
        return ["openai/gpt-oss-120b", "qwen/qwen3.6-27b"]

    def ok(self, system, user, temperature=0.4):
        used.append(self._settings.llm_model)
        return "ok"

    monkeypatch.setattr(ChatClient, "list_models", listing)
    monkeypatch.setattr(ChatClient, "complete_text", ok)
    result = client.post(
        "/api/llm/check",
        json={"llm_provider": "groq", "llm_model": "llama-3.3-70b-versatile", "llm_api_key": "gsk-test"},
    ).json()
    assert result["ok"] is True
    assert result["switched"] is True
    assert result["model"] == "openai/gpt-oss-120b"
    assert used == ["openai/gpt-oss-120b"]
    assert "qwen/qwen3.6-27b" in result["models"]


def test_llm_check_explains_a_missing_model(client: TestClient, monkeypatch) -> None:
    from app.llm.base import LLMError
    from app.llm.client import ChatClient

    def no_listing(self):
        raise LLMError("no models endpoint", status=404, config=True)

    def missing(self, system, user, temperature=0.4):
        raise LLMError("Model host returned HTTP 404.", status=404, config=True)

    monkeypatch.setattr(ChatClient, "list_models", no_listing)
    monkeypatch.setattr(ChatClient, "complete_text", missing)
    result = client.post(
        "/api/llm/check",
        json={"llm_provider": "together", "llm_model": "retired-model", "llm_api_key": "tg-test"},
    ).json()
    assert result["ok"] is False
    assert "retired-model" in result["error"]
    assert "404" in result["error"]


def test_groq_default_is_a_live_model() -> None:
    from app.config import PROVIDER_PRESETS

    assert PROVIDER_PRESETS["groq"]["model"] == "openai/gpt-oss-120b"


def test_providers_and_visitor_key_are_not_stored(client: TestClient) -> None:
    catalog = client.get("/api/providers")
    assert catalog.status_code == 200
    ids = [item["id"] for item in catalog.json()["providers"]]
    assert "claude" in ids and "openai" in ids
    assert "sk-" not in catalog.text
    opened = client.post("/api/session", json={}).json()
    secret = "sk-visitor-secret-should-not-echo"
    result = client.post(
        f"/api/session/{opened['session_id']}/message",
        json={"message": "hello", "llm_provider": "claude", "llm_model": "claude-haiku", "llm_api_key": secret},
    )
    assert result.status_code == 200
    assert secret not in result.text
    snapshot = client.get(f"/api/session/{opened['session_id']}")
    assert secret not in snapshot.text


def test_health_and_page(client: TestClient) -> None:
    health = client.get("/healthz")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["llm_configured"] is False
    page = client.get("/")
    assert page.status_code == 200
    assert "Claims desk" in page.text
    missing = client.post("/api/session/nope/message", json={"message": "hello"})
    assert missing.status_code == 404
    invalid = client.post("/api/session", json={"consent_scenario": "nope"})
    assert invalid.status_code == 422


def test_transcript_is_redacted(client: TestClient) -> None:
    opened = client.post("/api/session", json={}).json()
    say(client, opened["session_id"], DEMO)
    snapshot = client.get(f"/api/session/{opened['session_id']}")
    assert snapshot.status_code == 200
    transcript = snapshot.json()["transcript"]
    user = next(turn["text"] for turn in transcript if turn["role"] == "user")
    assert "[DATE]" in user
    assert "[ID4]" in user
    assert "1985-03-15" not in user
    assert "4472" not in user
    assert "slots" not in snapshot.json()["state"]
