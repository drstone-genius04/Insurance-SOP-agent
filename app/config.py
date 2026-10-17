"""Runtime settings loaded from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

# Friendly names map to a transport + defaults. OpenAI-compatible hosts share one client.
PROVIDER_ALIASES = {
    "openai": "openai",
    "gpt": "openai",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "openrouter": "openai",
    "ollama": "openai",
    "groq": "openai",
    "together": "openai",
    "deepseek": "openai",
    "fireworks": "openai",
    "mistral": "openai",
    "gemini": "openai",  # OpenAI-compatible Gemini proxy / Google AI Studio openai path
    "google": "openai",
    "azure": "openai",
    "compat": "openai",
    "openai_compatible": "openai",
}

PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "openai": {
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
    },
    "anthropic": {
        "model": "claude-haiku-4-5-20251001",
        "base_url": "https://api.anthropic.com",
    },
    "claude": {
        "model": "claude-haiku-4-5-20251001",
        "base_url": "https://api.anthropic.com",
    },
    "openrouter": {
        "model": "openai/gpt-4o-mini",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "ollama": {
        "model": "llama3.2",
        "base_url": "http://host.docker.internal:11434/v1",
    },
    "groq": {
        "model": "llama-3.3-70b-versatile",
        "base_url": "https://api.groq.com/openai/v1",
    },
    "together": {
        "model": "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        "base_url": "https://api.together.xyz/v1",
    },
    "deepseek": {
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
    },
    "fireworks": {
        "model": "accounts/fireworks/models/llama-v3p1-8b-instruct",
        "base_url": "https://api.fireworks.ai/inference/v1",
    },
    "mistral": {
        "model": "mistral-small-latest",
        "base_url": "https://api.mistral.ai/v1",
    },
    "gemini": {
        "model": "gemini-2.0-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
    },
    "google": {
        "model": "gemini-2.0-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
    },
}

KEY_ENV_CANDIDATES = (
    "LLM_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CLAUDE_API_KEY",
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "TOGETHER_API_KEY",
    "DEEPSEEK_API_KEY",
    "MISTRAL_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
)


@dataclass(frozen=True)
class Settings:
    """Process configuration. Thresholds are settings, not hard-coded behavior."""

    llm_provider: str = "openai"
    llm_transport: str = "openai"  # openai | anthropic
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = "https://api.openai.com/v1"
    max_verify_attempts: int = 3
    max_off_topic: int = 3
    max_frustration_turns: int = 3
    trace_enabled: bool = True
    data_dir: Path = ROOT / "data"
    fixtures_dir: Path = ROOT / "fixtures"

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key.strip())

    @property
    def llm_host(self) -> str:
        """Hostname only, for /healthz. Never includes the key."""
        try:
            return urlparse(self.llm_base_url).netloc or self.llm_base_url
        except Exception:
            return self.llm_base_url


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _first_key() -> str:
    for name in KEY_ENV_CANDIDATES:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


# Shown in the desk. Anthropic is offered as Claude; Google as Gemini.
PUBLIC_PROVIDERS = (
    "claude",
    "openai",
    "openrouter",
    "groq",
    "together",
    "deepseek",
    "mistral",
    "gemini",
    "fireworks",
    "ollama",
)


def provider_catalog() -> list[dict[str, str]]:
    """Provider choices for the page. Never includes an API key."""
    catalog: list[dict[str, str]] = []
    for label in PUBLIC_PROVIDERS:
        preset = PROVIDER_PRESETS[label]
        _, transport = resolve_provider(label)
        catalog.append(
            {
                "id": label,
                "label": label.capitalize(),
                "model": preset["model"],
                "transport": transport,
            }
        )
    return catalog


def caller_settings(
    base: Settings,
    provider: str | None,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
) -> Settings | None:
    """Build request settings from a visitor key. None means keep the server client."""
    key = (api_key or "").strip()
    if not key:
        return None
    label, transport = resolve_provider(provider or "openai")
    preset = PROVIDER_PRESETS.get(label) or PROVIDER_PRESETS.get(transport) or PROVIDER_PRESETS["openai"]
    return replace(
        base,
        llm_provider=label,
        llm_transport=transport,
        llm_api_key=key,
        llm_model=(model or "").strip() or preset["model"],
        llm_base_url=(base_url or "").strip() or preset["base_url"],
    )


def resolve_provider(raw: str | None) -> tuple[str, str]:
    """Return (label, transport). Label keeps the caller's choice for docs/healthz."""
    label = (raw or "openai").strip().lower() or "openai"
    transport = PROVIDER_ALIASES.get(label, "openai")
    return label, transport


def load_settings() -> Settings:
    """Load `.env` from the project root, then read environment variables."""
    load_dotenv(ROOT / ".env")
    label, transport = resolve_provider(os.getenv("LLM_PROVIDER"))
    preset = PROVIDER_PRESETS.get(label) or PROVIDER_PRESETS.get(transport) or PROVIDER_PRESETS["openai"]
    if transport == "anthropic":
        # Claude keys often live under ANTHROPIC_API_KEY / CLAUDE_API_KEY.
        key = (
            (os.getenv("LLM_API_KEY") or "").strip()
            or (os.getenv("ANTHROPIC_API_KEY") or "").strip()
            or (os.getenv("CLAUDE_API_KEY") or "").strip()
            or _first_key()
        )
    elif label in {"openrouter", "groq", "together", "deepseek", "mistral", "gemini", "google"}:
        specific = {
            "openrouter": "OPENROUTER_API_KEY",
            "groq": "GROQ_API_KEY",
            "together": "TOGETHER_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "mistral": "MISTRAL_API_KEY",
            "gemini": "GOOGLE_API_KEY",
            "google": "GOOGLE_API_KEY",
        }.get(label)
        key = (
            (os.getenv("LLM_API_KEY") or "").strip()
            or ((os.getenv(specific) or "").strip() if specific else "")
            or ((os.getenv("GEMINI_API_KEY") or "").strip() if label in {"gemini", "google"} else "")
            or _first_key()
        )
    else:
        key = (os.getenv("LLM_API_KEY") or "").strip() or (os.getenv("OPENAI_API_KEY") or "").strip() or _first_key()

    data_dir = Path(os.getenv("DATA_DIR") or (ROOT / "data"))
    fixtures = Path(os.getenv("FIXTURES_DIR") or (ROOT / "fixtures"))
    return Settings(
        llm_provider=label,
        llm_transport=transport,
        llm_api_key=key,
        llm_model=(os.getenv("LLM_MODEL") or "").strip() or preset["model"],
        llm_base_url=(os.getenv("LLM_BASE_URL") or "").strip() or preset["base_url"],
        max_verify_attempts=_int("MAX_VERIFY_ATTEMPTS", 3),
        max_off_topic=_int("MAX_OFF_TOPIC", 3),
        max_frustration_turns=_int("MAX_FRUSTRATION_TURNS", 3),
        trace_enabled=_flag("TRACE_ENABLED", True),
        data_dir=data_dir,
        fixtures_dir=fixtures,
    )
