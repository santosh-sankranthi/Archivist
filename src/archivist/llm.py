"""Minimal OpenAI-compatible chat client (stdlib-only).

Why hand-rolled: the public repo must run against *any* OpenAI-compatible
endpoint (opencode zen, OpenRouter, vLLM, llama.cpp, OpenAI, ...) with zero
heavy dependencies, and the agent loop wants full control over the wire
format so every call can be traced and replayed faithfully.

Configuration (environment; a repo-root ``.env`` is honoured by the CLI):

* ``ARCHIVIST_LLM_BASE_URL`` endpoint root, e.g. ``https://opencode.ai/zen/v1``
* ``ARCHIVIST_LLM_API_KEY``  bearer token (omit for keyless local servers)
* ``ARCHIVIST_LLM_MODEL``    model id, e.g. ``deepseek-v4-pro``

``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` / ``OPENAI_MODEL`` are accepted as
fallbacks so plain OpenAI setups work with no extra configuration.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class LLMError(RuntimeError):
    """Configuration or transport failure talking to the chat endpoint."""


@dataclass
class ToolCall:
    """One tool call requested by the model.

    ``raw_arguments`` keeps the exact string the model sent; ``arguments`` is
    the parsed dict when it was valid JSON (``parse_error`` records why not,
    so the agent loop can feed the problem back to the model).
    """

    id: str
    name: str
    raw_arguments: str
    arguments: dict | None = None
    parse_error: str | None = None


@dataclass
class LLMResponse:
    """Normalized chat response: text, tool calls, raw payload, usage."""

    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)


def _parse_tool_call(raw: dict) -> ToolCall:
    fn = raw.get("function") or {}
    args_text = fn.get("arguments")
    call = ToolCall(
        id=str(raw.get("id") or f"call_{fn.get('name') or 'unknown'}"),
        name=str(fn.get("name") or ""),
        raw_arguments=(
            args_text if isinstance(args_text, str) else json.dumps(args_text or {})
        ),
    )
    if isinstance(args_text, dict):  # some servers send objects already
        call.arguments = args_text
        return call
    text = (args_text or "").strip()
    if not text:
        call.arguments = {}
        return call
    try:
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise TypeError(
                f"arguments must be a JSON object, got {type(parsed).__name__}"
            )
        call.arguments = parsed
    except (ValueError, TypeError) as exc:  # json.JSONDecodeError is a ValueError
        call.parse_error = str(exc)
    return call


class ChatLLM:
    """Small OpenAI-compatible client with per-call accounting."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        *,
        timeout: float = 90.0,
        temperature: float = 0.0,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("ARCHIVIST_LLM_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")
        self.api_key = (
            api_key
            or os.environ.get("ARCHIVIST_LLM_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
        self.model = (
            model
            or os.environ.get("ARCHIVIST_LLM_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or ""
        )
        self.timeout = timeout
        self.temperature = temperature
        self.calls = 0
        self.usage: dict[str, int] = {}

    def is_configured(self) -> bool:
        """Best-effort check that the CLI should attempt a call at all."""
        return bool(self.model and (self.api_key or self.base_url != DEFAULT_BASE_URL))

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        """One chat completion round. Raises :class:`LLMError` on failure."""
        if not self.model:
            raise LLMError(
                "no model configured — set ARCHIVIST_LLM_MODEL (or OPENAI_MODEL)"
            )
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            method="POST",
        )
        request.add_header("Content-Type", "application/json")
        if self.api_key:
            request.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            snippet = exc.read().decode(errors="replace")[:300]
            raise LLMError(f"HTTP {exc.code} from {self.base_url}: {snippet}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"cannot reach {self.base_url}: {exc.reason}") from exc

        self.calls += 1
        message = ((data.get("choices") or [{}])[0]).get("message") or {}
        usage = {
            key: value
            for key, value in (data.get("usage") or {}).items()
            if isinstance(value, int)
        }
        for key, value in usage.items():
            self.usage[key] = self.usage.get(key, 0) + value
        return LLMResponse(
            content=message.get("content"),
            tool_calls=[_parse_tool_call(tc) for tc in message.get("tool_calls") or []],
            raw=data,
            usage=usage,
        )
