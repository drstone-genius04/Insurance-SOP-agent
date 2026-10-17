"""OpenAI-compatible and Anthropic chat clients."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.config import Settings
from app.llm.base import LLMError, NullLLM

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
JSON_ONLY = "Return one JSON object and nothing else. No markdown fences."
_NON_CHAT = re.compile(
    r"whisper|tts|embed|guard|moderation|dall-e|image|audio|transcri|realtime|"
    r"orpheus|playai|distil|rerank|search|computer-use|vision-only",
    re.IGNORECASE,
)


def build_client(settings: Settings) -> Any:
    """Return a live client when a key is set, otherwise a null client."""
    if not settings.llm_configured:
        return NullLLM()
    return ChatClient(settings)


class ChatClient:
    """HTTP client for Anthropic Messages and OpenAI-compatible chat completions."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._http = httpx.Client(timeout=httpx.Timeout(45.0, connect=10.0))
        self._json_mode_ok = True

    def complete_json(self, system: str, user: str, temperature: float = 0.0) -> dict:
        text = self._complete(f"{system}\n\n{JSON_ONLY}", user, temperature, json_mode=True)
        return _parse_json(text)

    def complete_text(self, system: str, user: str, temperature: float = 0.4) -> str:
        return self._complete(system, user, temperature, json_mode=False).strip()

    def close(self) -> None:
        """Close the HTTP client. Visitor-key clients are one request long."""
        self._http.close()

    def list_models(self) -> list[str]:
        """Chat model ids this key can use, from the provider's own /models endpoint."""
        base = self._settings.llm_base_url.rstrip("/")
        if self._settings.llm_transport == "anthropic":
            url = base + ("/models" if base.endswith("/v1") else "/v1/models")
            headers = {"x-api-key": self._settings.llm_api_key, "anthropic-version": "2023-06-01"}
        else:
            url = base + "/models"
            headers = {"Authorization": f"Bearer {self._settings.llm_api_key}"}
        try:
            response = self._http.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"Could not reach the model host: {exc}") from exc
        if response.status_code >= 400:
            raise _http_error(response)
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise LLMError("The model host returned invalid JSON.") from exc
        rows = body.get("data") if isinstance(body, dict) else None
        ids: list[str] = []
        for row in rows or []:
            if not isinstance(row, dict) or row.get("active") is False:
                continue
            model_id = str(row.get("id") or "").removeprefix("models/")
            if model_id and not _NON_CHAT.search(model_id):
                ids.append(model_id)
        return sorted(set(ids))

    def _complete(self, system: str, user: str, temperature: float, json_mode: bool) -> str:
        if self._settings.llm_transport == "anthropic":
            return self._anthropic(system, user, temperature)
        try:
            return self._openai(system, user, temperature, json_mode and self._json_mode_ok)
        except LLMError as exc:
            if json_mode and exc.status == 400 and self._json_mode_ok:
                self._json_mode_ok = False
                return self._openai(system, user, temperature, False)
            raise

    def _openai(self, system: str, user: str, temperature: float, json_mode: bool) -> str:
        url = self._settings.llm_base_url.rstrip("/") + "/chat/completions"
        payload: dict[str, Any] = {
            "model": self._settings.llm_model,
            "temperature": temperature,
            "max_tokens": 900,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if "groq.com" in self._settings.llm_base_url and "gpt-oss" in self._settings.llm_model:
            # Reasoning tokens share max_tokens; low effort leaves room for the reply.
            payload["reasoning_effort"] = "low"
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        # OpenRouter recommends these; harmless for other hosts.
        if "openrouter.ai" in self._settings.llm_base_url:
            headers["HTTP-Referer"] = "https://github.com/drstone-genius04/Insurance-SOP-agent"
            headers["X-Title"] = "Claims Desk"
        response = self._post(url, payload, headers)
        try:
            return str(response["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("The model response did not include assistant text.") from exc

    def _anthropic(self, system: str, user: str, temperature: float) -> str:
        base = self._settings.llm_base_url.rstrip("/")
        # Accept either https://api.anthropic.com or .../v1 as the configured base.
        url = base + ("/messages" if base.endswith("/v1") else "/v1/messages")
        payload = {
            "model": self._settings.llm_model,
            "max_tokens": 900,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        response = self._post(
            url,
            payload,
            {
                "x-api-key": self._settings.llm_api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
        )
        try:
            blocks = response["content"]
            return "".join(str(block.get("text", "")) for block in blocks if isinstance(block, dict))
        except (KeyError, TypeError) as exc:
            raise LLMError("The model response did not include assistant text.") from exc

    def _post(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        last: LLMError | None = None
        for attempt in range(2):
            try:
                response = self._http.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                last = LLMError(f"Could not reach the model host: {exc}")
                if attempt == 0:
                    continue
                raise last from exc
            if response.status_code in {429, 500, 502, 503} and attempt == 0:
                last = LLMError("The model host was busy.", status=response.status_code)
                continue
            if response.status_code >= 400:
                raise _http_error(response)
            try:
                body = response.json()
            except json.JSONDecodeError as exc:
                raise LLMError("The model host returned invalid JSON.") from exc
            if not isinstance(body, dict):
                raise LLMError("The model host returned an unexpected payload.")
            return body
        raise last or LLMError("The model call failed.")


def _http_error(response: httpx.Response) -> LLMError:
    """Turn a 4xx/5xx into an LLMError that keeps the provider's own message."""
    detail = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                detail = str(err.get("message") or "")[:200]
            elif isinstance(err, str):
                detail = err[:200]
    except Exception:
        detail = ""
    message = f"Model host returned HTTP {response.status_code}."
    if detail:
        message = f"{message} {detail}"
    return LLMError(message, status=response.status_code, config=response.status_code in {401, 403, 404})


def _parse_json(text: str) -> dict:
    raw = text.strip()
    fenced = _FENCE.search(raw)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise LLMError("The extractor did not return JSON.")
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError("The extractor did not return JSON.") from exc
    if not isinstance(data, dict):
        raise LLMError("The extractor JSON was not an object.")
    return data
