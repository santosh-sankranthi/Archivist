"""Chat-client tests (M2b) — wire format against a local fake server.

The fake HTTP server exercises the real urllib path (headers, payload shape,
error handling) without any network dependency; the responses are canned.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from archivist.llm import USER_AGENT, ChatLLM, LLMError


class _Handler(BaseHTTPRequestHandler):
    responses: ClassVar[list[tuple[int, object]]] = []
    requests: ClassVar[list[dict]] = []
    delay: ClassVar[float] = 0.0

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append(
            {"path": self.path, "headers": dict(self.headers), "body": body}
        )
        if type(self).delay:  # simulate a slow endpoint (body-read timeout tests)
            time.sleep(type(self).delay)
        status, payload = type(self).responses.pop(0)
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except OSError:  # client hung up (read timeout in tests)
            pass

    def log_message(self, *args):  # keep test output clean
        pass


@pytest.fixture()
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    _Handler.responses = []
    _Handler.requests = []
    _Handler.delay = 0.0
    yield f"http://127.0.0.1:{httpd.server_port}/v1"
    httpd.shutdown()


def test_chat_roundtrip_parses_tool_calls(server):
    _Handler.responses.append(
        (
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "overview",
                                        "arguments": '{"a": 1}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )
    )
    llm = ChatLLM(base_url=server, api_key="sekrit", model="test-model")
    response = llm.chat(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "overview"}}],
    )
    assert response.tool_calls[0].name == "overview"
    assert response.tool_calls[0].arguments == {"a": 1}
    assert response.tool_calls[0].parse_error is None
    assert llm.usage == {"prompt_tokens": 10, "completion_tokens": 5}
    assert llm.calls == 1

    sent = _Handler.requests[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer sekrit"
    assert sent["body"]["model"] == "test-model"
    assert sent["body"]["tools"][0]["function"]["name"] == "overview"


def test_chat_omits_tools_when_none(server):
    _Handler.responses.append((200, {"choices": [{"message": {"content": "ok"}}]}))
    llm = ChatLLM(base_url=server, model="m")  # keyless local server
    response = llm.chat([{"role": "user", "content": "hi"}])
    assert response.content == "ok"
    sent = _Handler.requests[0]
    assert "tools" not in sent["body"]
    assert "Authorization" not in sent["headers"]


def test_user_agent_sent_and_no_session_header_for_local(server):
    _Handler.responses.append((200, {"choices": [{"message": {"content": "ok"}}]}))
    llm = ChatLLM(base_url=server, api_key="k", model="m")
    llm.chat([{"role": "user", "content": "hi"}])
    sent = _Handler.requests[0]
    assert sent["headers"]["User-Agent"] == USER_AGENT
    assert "x-opencode-session" not in sent["headers"]


def test_opencode_hosts_get_stable_session_header():
    llm = ChatLLM(base_url="https://opencode.ai/zen/go/v1", api_key="k", model="m")
    first = llm._request_headers()
    assert first["User-Agent"] == USER_AGENT
    assert first["x-opencode-session"] == llm.session_id
    assert llm._request_headers()["x-opencode-session"] == llm.session_id  # stable
    other = ChatLLM(base_url="https://opencode.ai/zen/go/v1", api_key="k", model="m")
    assert other.session_id != llm.session_id  # one client == one conversation
    opens = ChatLLM(base_url="https://api.openai.com/v1", api_key="k", model="m")
    assert "x-opencode-session" not in opens._request_headers()
    assert opens._request_headers()["Authorization"] == "Bearer k"


def test_chat_http_error_raises_carries_status(server):
    _Handler.responses.append((401, {"error": {"message": "bad key"}}))
    llm = ChatLLM(base_url=server, api_key="x", model="m")
    with pytest.raises(LLMError) as exc:
        llm.chat([{"role": "user", "content": "hi"}])
    assert "401" in str(exc.value)


def test_bad_argument_json_is_flagged_not_fatal(server):
    _Handler.responses.append(
        (
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c",
                                    "function": {"name": "f", "arguments": "{broken"},
                                }
                            ],
                        }
                    }
                ]
            },
        )
    )
    llm = ChatLLM(base_url=server, api_key="x", model="m")
    call = llm.chat([{"role": "user", "content": "hi"}]).tool_calls[0]
    assert call.arguments is None and call.parse_error
    assert call.raw_arguments == "{broken"


def test_config_from_env_and_overrides(monkeypatch):
    for name in (
        "ARCHIVIST_LLM_BASE_URL",
        "ARCHIVIST_LLM_API_KEY",
        "ARCHIVIST_LLM_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    assert ChatLLM().is_configured() is False

    monkeypatch.setenv("ARCHIVIST_LLM_BASE_URL", "http://env.example/v1")
    monkeypatch.setenv("ARCHIVIST_LLM_API_KEY", "env-key")
    monkeypatch.setenv("ARCHIVIST_LLM_MODEL", "env-model")
    from_env = ChatLLM()
    assert from_env.base_url == "http://env.example/v1"
    assert from_env.model == "env-model"
    assert from_env.is_configured() is True

    explicit = ChatLLM(base_url="http://cli.example/v1", model="cli-model")
    assert explicit.base_url == "http://cli.example/v1"
    assert explicit.model == "cli-model"


def test_retry_on_429_then_success(server):
    _Handler.responses.extend(
        [
            (429, {"error": {"message": "slow down"}}),
            (200, {"choices": [{"message": {"content": "ok"}}]}),
        ]
    )
    llm = ChatLLM(base_url=server, api_key="x", model="m", retries=1)
    llm._backoff = lambda attempt, retry_after: None  # no real sleep in tests
    response = llm.chat([{"role": "user", "content": "hi"}])
    assert response.content == "ok"
    assert len(_Handler.requests) == 2  # first attempt + one retry
    assert llm.calls == 1


def test_401_is_not_retried(server):
    _Handler.responses.append((401, {"error": {"message": "bad key"}}))
    llm = ChatLLM(base_url=server, api_key="x", model="m", retries=2)
    llm._backoff = lambda attempt, retry_after: None
    with pytest.raises(LLMError):
        llm.chat([{"role": "user", "content": "hi"}])
    assert len(_Handler.requests) == 1


def test_malformed_200_body_raises_llmerror(server):
    _Handler.responses.append((200, b"<html>gateway timeout</html>"))
    llm = ChatLLM(base_url=server, api_key="x", model="m", retries=2)
    with pytest.raises(LLMError, match="malformed response"):
        llm.chat([{"role": "user", "content": "hi"}])
    assert len(_Handler.requests) == 1  # server bugs are not retried


def test_non_object_200_body_raises_llmerror(server):
    _Handler.responses.append((200, [1, 2, 3]))
    llm = ChatLLM(base_url=server, api_key="x", model="m")
    with pytest.raises(LLMError, match="expected an object"):
        llm.chat([{"role": "user", "content": "hi"}])


def test_body_read_timeout_raises_llmerror(server):
    _Handler.responses.append((200, {"choices": [{"message": {"content": "x"}}]}))
    _Handler.delay = 0.4
    llm = ChatLLM(base_url=server, api_key="x", model="m", retries=0, timeout=0.05)
    with pytest.raises(LLMError, match="cannot reach"):
        llm.chat([{"role": "user", "content": "hi"}])


def test_odd_response_shapes_do_not_crash(server):
    _Handler.responses.append(
        (
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "hello "},
                                {"type": "text", "text": "world"},
                            ],
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "function": {"name": "f", "arguments": [1, 2]},
                                },
                                {"id": "2", "function": "not-a-dict"},
                                "not-a-dict-either",
                            ],
                        }
                    }
                ]
            },
        )
    )
    llm = ChatLLM(base_url=server, api_key="x", model="m")
    response = llm.chat([{"role": "user", "content": "hi"}], tools=[])
    assert response.content == "hello world"
    assert len(response.tool_calls) == 2  # non-dict entries skipped
    broken = response.tool_calls[0]
    assert broken.parse_error and "got list" in broken.parse_error
    assert response.tool_calls[1].name == ""
    assert response.tool_calls[1].parse_error is None


def test_openai_fallbacks(monkeypatch):
    for name in (
        "ARCHIVIST_LLM_BASE_URL",
        "ARCHIVIST_LLM_API_KEY",
        "ARCHIVIST_LLM_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://openai.example/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_MODEL", "m")
    llm = ChatLLM()
    assert llm.base_url == "http://openai.example/v1"
    assert llm.model == "m"
    assert llm.is_configured() is True
