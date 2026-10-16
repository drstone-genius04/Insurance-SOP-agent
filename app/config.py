"""Runtime settings loaded from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    """Process configuration. Thresholds are settings, not hard-coded behavior."""

    llm_provider: str = "openai"
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


def load_settings() -> Settings:
    """Load `.env` from the project root, then read environment variables."""
    load_dotenv(ROOT / ".env")
    provider = (os.getenv("LLM_PROVIDER") or "openai").strip().lower()
    if provider not in {"openai", "anthropic"}:
        provider = "openai"
    if provider == "anthropic":
        default_model = "claude-haiku-4-5-20251001"
        default_base = "https://api.anthropic.com"
    else:
        default_model = "gpt-4o-mini"
        default_base = "https://api.openai.com/v1"
    data_dir = Path(os.getenv("DATA_DIR") or (ROOT / "data"))
    fixtures = Path(os.getenv("FIXTURES_DIR") or (ROOT / "fixtures"))
    return Settings(
        llm_provider=provider,
        llm_api_key=(os.getenv("LLM_API_KEY") or "").strip(),
        llm_model=(os.getenv("LLM_MODEL") or "").strip() or default_model,
        llm_base_url=(os.getenv("LLM_BASE_URL") or "").strip() or default_base,
        max_verify_attempts=_int("MAX_VERIFY_ATTEMPTS", 3),
        max_off_topic=_int("MAX_OFF_TOPIC", 3),
        max_frustration_turns=_int("MAX_FRUSTRATION_TURNS", 3),
        trace_enabled=_flag("TRACE_ENABLED", True),
        data_dir=data_dir,
        fixtures_dir=fixtures,
    )
