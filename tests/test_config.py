"""Provider aliases and key resolution."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from app.config import Settings, caller_settings, load_settings, resolve_provider


@contextmanager
def env(**values: str | None) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_provider_aliases() -> None:
    assert resolve_provider("claude") == ("claude", "anthropic")
    assert resolve_provider("anthropic") == ("anthropic", "anthropic")
    assert resolve_provider("gpt") == ("gpt", "openai")
    assert resolve_provider("openrouter") == ("openrouter", "openai")
    assert resolve_provider("ollama") == ("ollama", "openai")


def test_claude_key_from_anthropic_env(monkeypatch) -> None:
    monkeypatch.setattr("app.config.load_dotenv", lambda *_a, **_k: False)
    cleared = [
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "CLAUDE_API_KEY",
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
        "TOGETHER_API_KEY",
        "DEEPSEEK_API_KEY",
        "MISTRAL_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "LLM_MODEL",
        "LLM_BASE_URL",
    ]
    with env(
        LLM_PROVIDER="claude",
        ANTHROPIC_API_KEY="sk-ant-test",
        **{name: None for name in cleared},
    ):
        settings = load_settings()
    assert settings.llm_provider == "claude"
    assert settings.llm_transport == "anthropic"
    assert settings.llm_api_key == "sk-ant-test"
    assert settings.llm_configured is True
    assert "claude" in settings.llm_model or "haiku" in settings.llm_model
    assert "anthropic" in settings.llm_base_url


def test_openrouter_preset(monkeypatch) -> None:
    monkeypatch.setattr("app.config.load_dotenv", lambda *_a, **_k: False)
    cleared = [
        "LLM_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "CLAUDE_API_KEY",
        "GROQ_API_KEY",
        "LLM_MODEL",
        "LLM_BASE_URL",
    ]
    with env(
        LLM_PROVIDER="openrouter",
        OPENROUTER_API_KEY="or-test",
        **{name: None for name in cleared},
    ):
        settings = load_settings()
    assert settings.llm_transport == "openai"
    assert settings.llm_api_key == "or-test"
    assert "openrouter.ai" in settings.llm_base_url


def test_caller_settings_override_the_server_key() -> None:
    base = Settings(llm_api_key="", llm_provider="openai", llm_model="gpt-4o-mini")
    assert caller_settings(base, "claude", None, "  ", None) is None
    chosen = caller_settings(base, "claude", "claude-sonnet-4-5", "sk-visitor", None)
    assert chosen is not None
    assert chosen.llm_provider == "claude"
    assert chosen.llm_transport == "anthropic"
    assert chosen.llm_api_key == "sk-visitor"
    assert chosen.llm_model == "claude-sonnet-4-5"
    assert chosen.llm_base_url == "https://api.anthropic.com"
    assert base.llm_api_key == ""
