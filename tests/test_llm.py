"""Chat-client tests (M2b) — wire format against a local fake server.

The fake HTTP server exercises the real urllib path (headers, payload shape,
error handling) without any network dependency; the responses are canned.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import pytest

from archivist.llm import ChatLLM, LLMError


class _Handler(BaseHTTPRequestHandler):
    responses: ClassVar[list[tuple[int, dict]]] = []
    requests: ClassVar[list[dict]] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append(
            {"path": self.path, "headers": dict(self.headers), "body": body}
        )
        status, payload = type(self).responses.pop(0)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # keep test output clean
        pass


@pytest.fixture()
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    _Handler.responses = []
    _Handler.requests = []
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
