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

Transient failures (429/5xx, timeouts, dropped connections) are retried up
to ``ChatLLM.retries`` times with a small backoff (``Retry-After`` honoured
when present); everything else fails fast as :class:`LLMError`.
"""

from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DEFAULT_BASE_URL = "https://api.openai.com/v1"

# HTTP statuses worth one more attempt: rate limits + transient server faults.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class LLMError(RuntimeError):
    """Configuration or transport failure talking to the chat endpoint."""


def _decode_body(body: bytes, base_url: str) -> dict:
    """Decode a 2xx body into a JSON object or raise a clean :class:`LLMError`."""
    text = body.decode(errors="replace")
    try:
        data = json.loads(text)
    except ValueError as exc:  # json.JSONDecodeError is a ValueError
        raise LLMError(
            f"malformed response from {base_url}: {exc}: {text[:200]!r}"
        ) from exc
    if not isinstance(data, dict):
        raise LLMError(
            f"malformed response from {base_url}: "
            f"expected an object, got {type(data).__name__}"
        )
    return data


def _message_from(data: dict) -> dict:
    """First choice's message dict, tolerating missing or odd shapes."""
    choices = data.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else {}
    first = first if isinstance(first, dict) else {}
    message = first.get("message")
    return message if isinstance(message, dict) else {}


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
    """Normalize one wire tool-call; malformed shapes never raise.

    Anything that is not a usable JSON object is recorded as a
    ``parse_error`` so the agent loop can feed the problem back to the model
    instead of crashing the question.
    """
    raw = raw if isinstance(raw, dict) else {}
    fn = raw.get("function")
    fn = fn if isinstance(fn, dict) else {}
    args = fn.get("arguments")
    if isinstance(args, str):
        raw_arguments = args
    elif args is None:
        raw_arguments = "{}"
    else:
        raw_arguments = json.dumps(args, default=str)
    call = ToolCall(
        id=str(raw.get("id") or f"call_{fn.get('name') or 'unknown'}"),
        name=str(fn.get("name") or ""),
        raw_arguments=raw_arguments,
    )
    if isinstance(args, dict):  # some servers send objects already
        call.arguments = args
        return call
    if args is None or (isinstance(args, str) and not args.strip()):
        call.arguments = {}
        return call
    if not isinstance(args, str):
        call.parse_error = f"arguments must be a JSON object, got {type(args).__name__}"
        return call
    text = args.strip()
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
        retries: int = 2,
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
        self.retries = max(0, int(retries))
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
        """One chat completion round. Raises :class:`LLMError` on failure.

        Transient failures (429/5xx, network timeouts, dropped connections)
        get up to ``self.retries`` retries with a small backoff, so one blip
        does not lose the whole question.
        """
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
        data: dict = {}
        attempt = 0
        while True:  # bounded: continue only while attempt <= self.retries
            attempt += 1
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read()
                data = _decode_body(body, self.base_url)
                break
            except urllib.error.HTTPError as exc:
                snippet = exc.read().decode(errors="replace")[:300]
                if exc.code in RETRYABLE_STATUS and attempt <= self.retries:
                    self._backoff(attempt, exc.headers.get("Retry-After"))
                    continue
                raise LLMError(
                    f"HTTP {exc.code} from {self.base_url}: {snippet}"
                ) from exc
            except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
                reason = getattr(exc, "reason", None) or exc
                if attempt <= self.retries:
                    self._backoff(attempt, None)
                    continue
                raise LLMError(f"cannot reach {self.base_url}: {reason}") from exc

        self.calls += 1
        message = _message_from(data)
        content = message.get("content")
        if isinstance(content, list):  # some servers send content as parts
            content = (
                "".join(
                    part.get("text") or "" for part in content if isinstance(part, dict)
                )
                or None
            )
        elif not isinstance(content, str):
            content = None
        raw_calls = message.get("tool_calls")
        tool_calls = (
            [_parse_tool_call(tc) for tc in raw_calls if isinstance(tc, dict)]
            if isinstance(raw_calls, list)
            else []
        )
        usage = {
            key: value
            for key, value in (data.get("usage") or {}).items()
            if isinstance(value, int)
        }
        for key, value in usage.items():
            self.usage[key] = self.usage.get(key, 0) + value
        return LLMResponse(
            content=content, tool_calls=tool_calls, raw=data, usage=usage
        )

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        """Sleep before retry ``attempt``; honour ``Retry-After`` (both capped)."""
        delay = min(5.0, 0.5 * attempt)
        if retry_after:
            try:
                delay = min(10.0, max(delay, float(retry_after)))
            except (TypeError, ValueError):
                pass
        time.sleep(delay)
