"""Shared LLM errors and the client protocol."""

from __future__ import annotations

from typing import Protocol


class LLMError(Exception):
    """A model call failed. `config` means the key, provider, or model name is wrong."""

    def __init__(self, message: str, status: int | None = None, config: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.config = config


class LLMClient(Protocol):
    """Two narrow jobs: structured extraction and phrasing."""

    def complete_json(self, system: str, user: str, temperature: float = 0.0) -> dict:
        """Return a JSON object. Raise LLMError on transport or config failure."""

    def complete_text(self, system: str, user: str, temperature: float = 0.4) -> str:
        """Return assistant text. Raise LLMError on transport or config failure."""


class NullLLM:
    """Stand-in for tests: empty extract/phrase so the phase template is used. Live no-key turns are blocked before this runs."""

    def complete_json(self, system: str, user: str, temperature: float = 0.0) -> dict:
        return {}

    def complete_text(self, system: str, user: str, temperature: float = 0.4) -> str:
        return ""
