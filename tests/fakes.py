"""Local stand-ins for the model endpoint and the Wikipedia API, used by the end-to-end tests."""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MODEL_ID = "fake-model"
REPLY = "Noted."

# Canned ``action=query`` pages keyed by (language, normalized title).
ARTICLES: dict[tuple[str, str], dict[str, Any]] = {
    ("en", "Alan Turing"): {
        "pageid": 1208, "ns": 0, "title": "Alan Turing",
        "description": "English computer scientist (1912–1954)",
        "fullurl": "https://en.wikipedia.org/wiki/Alan_Turing",
        "extract": "Alan Mathison Turing was an English mathematician and computer scientist.",
    },
    ("de", "Berlin"): {
        "pageid": 2552494, "ns": 0, "title": "Berlin",
        "description": "Hauptstadt der Bundesrepublik Deutschland",
        "fullurl": "https://de.wikipedia.org/wiki/Berlin",
        "extract": "Berlin ist die Hauptstadt der Bundesrepublik Deutschland.",
    },
}


def _query_answer(language: str, title: str) -> dict[str, Any]:
    normalized = " ".join(title.replace("_", " ").split())
    page = ARTICLES.get((language, normalized))
    if page is None:
        page = {"ns": 0, "title": normalized, "missing": True}
    return {"batchcomplete": True, "query": {"pages": [page]}}


def _opensearch_answer(language: str, search: str) -> list[Any]:
    titles = [t for (lang, t) in ARTICLES if lang == language and t.lower().startswith(search.lower())]
    return [search, titles, ["" for _ in titles], [ARTICLES[(language, t)]["fullurl"] for t in titles]]


class FakeServer:
    """One HTTP server playing both the OpenAI-compatible model API and ``/w/api.php``."""

    def __init__(self) -> None:
        self.chat_requests: list[dict[str, Any]] = []
        self.wiki_requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def origin(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> FakeServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()

    def user_messages(self) -> list[str]:
        """Every user-role message content the model endpoint received, in order."""
        out = []
        for body in self.chat_requests:
            for message in body.get("messages", []):
                if message.get("role") == "user":
                    content = message.get("content")
                    if isinstance(content, list):
                        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
                    out.append(content or "")
        return out

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: object) -> None:
                pass

            def _json(self, status: int, payload: Any) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                url = urllib.parse.urlsplit(self.path)
                if url.path == "/w/api.php":
                    params = dict(urllib.parse.parse_qsl(url.query))
                    language = self.headers.get("X-Test-Wiki-Language", "")
                    with server._lock:
                        server.wiki_requests.append({
                            "language": language, "params": params,
                            "user_agent": self.headers.get("User-Agent", ""),
                        })
                    if params.get("action") == "opensearch":
                        self._json(200, _opensearch_answer(language, params.get("search", "")))
                    else:
                        self._json(200, _query_answer(language, params.get("titles", "")))
                    return
                if url.path.rstrip("/").endswith("/models"):
                    self._json(200, {"object": "list", "data": [
                        {"id": MODEL_ID, "object": "model", "context_length": 128000}]})
                    return
                self._json(404, {"error": {"message": "not found"}})

            def do_POST(self) -> None:  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
                body = json.loads(raw or b"{}")
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    self._json(404, {"error": {"message": f"unsupported path {self.path}"}})
                    return
                with server._lock:
                    server.chat_requests.append(body)
                usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
                if not body.get("stream"):
                    self._json(200, {
                        "id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()),
                        "model": MODEL_ID, "usage": usage,
                        "choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant", "content": REPLY}}],
                    })
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                deltas = (({"role": "assistant", "content": ""}, None), ({"content": REPLY}, None), ({}, "stop"))
                for delta, finish in deltas:
                    chunk = {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": int(time.time()),
                             "model": MODEL_ID, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                    if finish:
                        chunk["usage"] = usage
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True

        return Handler
