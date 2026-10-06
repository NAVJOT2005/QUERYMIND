import http.server
import json
import threading

import pytest

from querymind.errors import AllProvidersFailed, AuthError, NoProvidersConfigured, RateLimited
from querymind.llm import openai_compat
from querymind.llm.router import LLMRouter, ProviderHandle


def handle(name, behavior):
    def call(messages):
        if isinstance(behavior, Exception):
            raise behavior
        return behavior
    return ProviderHandle(name, f"{name}-model", call)


def test_falls_back_on_rate_limit_and_reports_provider():
    events = []
    r = LLMRouter([handle("gemini", RateLimited("quota")), handle("groq", "hello")], events.append)
    c = r.complete([{"role": "user", "content": "hi"}])
    assert (c.text, c.provider) == ("hello", "groq") and any("gemini failed" in e for e in events)


def test_sticks_to_working_provider():
    calls = []
    def make(name, exc=None):
        def call(m):
            calls.append(name)
            if exc:
                raise exc
            return "ok"
        return ProviderHandle(name, "m", call)
    r = LLMRouter([make("a", RateLimited("x")), make("b")])
    r.complete([]); r.complete([])
    assert calls == ["a", "b", "b"]  # second request skips the provider that just failed


def test_bad_key_falls_through():
    r = LLMRouter([handle("a", AuthError("bad key")), handle("b", "fine")])
    assert r.complete([]).provider == "b"


def test_all_failed_lists_every_reason():
    r = LLMRouter([handle("a", RateLimited("quota")), handle("b", AuthError("bad key"))])
    with pytest.raises(AllProvidersFailed) as exc:
        r.complete([])
    assert "a: quota" in str(exc.value) and "b: bad key" in str(exc.value)


def test_no_providers_has_actionable_message():
    with pytest.raises(NoProvidersConfigured, match="keys set"):
        LLMRouter([])


# ---- real SDK against a fake OpenAI-compatible server -------------------------------------
class _Handler(http.server.BaseHTTPRequestHandler):
    status, body = 200, {}

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        self.send_response(self.server.status)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(self.server.body).encode())

    def do_GET(self):
        self.do_POST()

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_server():
    servers = []

    def start(status, body):
        srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        srv.status, srv.body = status, body
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_port}/v1"
    yield start
    for s in servers:
        s.shutdown()


def ok_body(text):
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}]}


def sdk_handle(name, url):
    client = openai_compat.make_client(url, "key", 5)
    return ProviderHandle(name, "m", lambda msgs: openai_compat.complete(client, "m", msgs, 0.0, 50))


def test_sdk_success(fake_server):
    url = fake_server(200, ok_body("pong"))
    assert LLMRouter([sdk_handle("p", url)]).complete([{"role": "user", "content": "x"}]).text == "pong"


@pytest.mark.parametrize("status,needle", [(429, "rate limit"), (401, "key rejected"),
                                           (404, "model not found"), (500, "provider-side")])
def test_sdk_http_errors_are_translated_and_fall_back(fake_server, status, needle):
    bad = fake_server(status, {"error": {"message": "nope", "type": "x"}})
    good = fake_server(200, ok_body("recovered"))
    router = LLMRouter([sdk_handle("bad", bad), sdk_handle("good", good)])
    assert router.complete([{"role": "user", "content": "x"}]).text == "recovered"
    with pytest.raises(AllProvidersFailed) as exc:
        LLMRouter([sdk_handle("bad", bad)]).complete([{"role": "user", "content": "x"}])
    assert needle in str(exc.value)


def test_connection_refused_is_unavailable():
    r = LLMRouter([sdk_handle("down", "http://127.0.0.1:9/v1")])
    with pytest.raises(AllProvidersFailed, match="could not connect"):
        r.complete([{"role": "user", "content": "x"}])


def test_empty_model_reply_counts_as_failure(fake_server):
    url = fake_server(200, {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                            "choices": [{"index": 0, "finish_reason": "stop",
                                         "message": {"role": "assistant", "content": ""}}]})
    with pytest.raises(AllProvidersFailed, match="empty"):
        LLMRouter([sdk_handle("p", url)]).complete([])
