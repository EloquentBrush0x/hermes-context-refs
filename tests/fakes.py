"""Local stand-ins for the model endpoint and the Wikipedia, OSV, GitHub, PEP and RFC sites (end-to-end tests)."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MODEL_ID = "fake-model"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPLY = "Noted."

# Canned ``action=query`` pages keyed by (language, normalized title).
ARTICLES: dict[tuple[str, str], dict[str, Any]] = {
    ("en", "Alan Turing"): {
        "pageid": 1208, "ns": 0, "title": "Alan Turing",
        "description": "English computer scientist (1912–1954)",
        "fullurl": "https://en.wikipedia.org/wiki/Alan_Turing",
        "extract": "Alan Mathison Turing was an English mathematician and computer scientist.",
    },
    ("en", "Berlin"): {
        "pageid": 3354, "ns": 0, "title": "Berlin",
        "description": "Capital and largest city of Germany",
        "fullurl": "https://en.wikipedia.org/wiki/Berlin",
        "extract": "Berlin is the capital and largest city of Germany.",
        # Served as "extract" when a request asks for the whole article (no exintro).
        "full_extract": (
            "Berlin is the capital and largest city of Germany.\n\n\n"
            "== History ==\n\n\n=== Etymology ===\n"
            "Berlin lies in northeastern Germany, in an area formerly settled by Slavs.\n\n\n"
            "== Geography ==\n\n\n=== Topography ===\n\n"
            "Berlin is in northeastern Germany, in an area of low-lying marshy woodlands."
        ),
    },
    ("de", "Berlin"): {
        "pageid": 2552494, "ns": 0, "title": "Berlin",
        "description": "Hauptstadt der Bundesrepublik Deutschland",
        "fullurl": "https://de.wikipedia.org/wiki/Berlin",
        "extract": "Berlin ist die Hauptstadt der Bundesrepublik Deutschland.",
    },
}


# Canned ``/v1/vulns/<id>`` records, trimmed from the real OSV answers.
VULNS: dict[str, dict[str, Any]] = {
    "CVE-2024-3651": {
        "id": "CVE-2024-3651", "summary": "Denial of Service via Quadratic Complexity in kjd/idna",
        "aliases": ["GHSA-jjg7-2v4v-x38h", "PYSEC-2024-60"],
        "published": "2024-07-07T17:22:10.032Z", "modified": "2026-09-08T12:39:14.237711128Z",
        "details": "A vulnerability was identified in the kjd/idna library, in the `idna.encode()` function.",
        "affected": [{"ranges": [{"type": "GIT", "repo": "https://github.com/kjd/idna", "events": [
            {"introduced": "001644567c3f1e1c7e62cfff806be7dad1be8cd3"},
            {"fixed": "1d365e17e10d72d0b7876316fc7b9ca0eebdd38d"}]}]}],
        "references": [{"type": "ADVISORY", "url": "https://nvd.nist.gov/vuln/detail/CVE-2024-3651"}],
    },
    "GHSA-jjg7-2v4v-x38h": {
        "id": "GHSA-jjg7-2v4v-x38h", "aliases": ["CVE-2024-3651", "PYSEC-2024-60"],
        "summary": "IDNA vulnerable to denial of service from specially crafted inputs to idna.encode",
        "database_specific": {"severity": "MODERATE", "cwe_ids": ["CWE-1333"]},
        "affected": [{"package": {"ecosystem": "PyPI", "name": "idna"},
                      "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "3.7"}]}]}],
        "references": [{"type": "FIX", "url": "https://github.com/kjd/idna/commit/1d365e17e10d72d0b7876316fc7b9ca0eebdd38d"}],
    },
}


def _vuln_answer(identifier: str) -> tuple[int, dict[str, Any]]:
    if identifier in VULNS:
        return 200, VULNS[identifier]
    return 404, {"code": 5, "message": "Vulnerability not found"}


# Canned GitHub REST answers keyed by API path, trimmed from real ones.
GITHUB: dict[str, Any] = {
    "/repos/NousResearch/hermes-agent/issues/26193": {
        "number": 26193, "state": "closed", "state_reason": "completed", "comments": 2,
        "title": "feat(plugins): allow plugins to register custom @<prefix>:<value> context references",
        "html_url": "https://github.com/NousResearch/hermes-agent/issues/26193",
        "comments_url": "https://api.github.com/repos/NousResearch/hermes-agent/issues/26193/comments",
        "user": {"login": "iHeyTang"}, "created_at": "2026-05-15T00:00:00Z", "closed_at": "2026-08-13T00:00:00Z",
        "labels": [{"name": "type/feature"}, {"name": "comp/plugins"}],
        "body": "## Summary\n<!-- template hint -->\nLet plugins register new @-prefixes.",
    },
    "/repos/NousResearch/hermes-agent/issues/26193/comments": [
        {"user": {"login": "teknium1"}, "created_at": "2026-06-01T00:00:00Z", "body": "Adopted for round 4."},
        {"user": {"login": "teknium1"}, "created_at": "2026-08-13T00:00:00Z", "body": "Shipped in #84937."},
    ],
}


def _github_answer(path: str) -> tuple[int, Any]:
    if path in GITHUB:
        return 200, GITHUB[path]
    return 404, {"message": "Not Found", "status": "404"}


def _query_answer(language: str, title: str, whole_article: bool) -> dict[str, Any]:
    normalized = " ".join(title.replace("_", " ").split())
    prefix, sep, rest = normalized.partition(":")
    if sep and any(lang == prefix for lang, _ in ARTICLES):
        # A language prefix: Wikipedia answers with an interwiki link (iwurl=1 adds its address).
        url = f"https://{prefix}.wikipedia.org/wiki/{urllib.parse.quote(rest.strip().replace(' ', '_'))}"
        return {"batchcomplete": True, "query": {"interwiki": [{"title": normalized, "iw": prefix, "url": url}]}}
    page = dict(ARTICLES.get((language, normalized)) or {"ns": 0, "title": normalized, "missing": True})
    full = page.pop("full_extract", None)
    if whole_article and full:
        page["extract"] = full
    return {"batchcomplete": True, "query": {"pages": [page]}}


def _opensearch_answer(language: str, search: str) -> list[Any]:
    titles = [t for (lang, t) in ARTICLES if lang == language and t.lower().startswith(search.lower())]
    return [search, titles, ["" for _ in titles], [ARTICLES[(language, t)]["fullurl"] for t in titles]]


# peps.python.org: the trimmed real index and PEP 572 page from tests/fixtures.
PEP_INDEX = (_FIXTURES / "peps-index.json").read_bytes()
PEP_PAGES = {572: (_FIXTURES / "pep-0572.html").read_bytes()}


def _pep_answer(path: str) -> tuple[int, str, bytes]:
    if path == "/api/peps.json":
        return 200, "application/json", PEP_INDEX
    match = re.fullmatch(r"/pep-(\d{4})/", path)
    if match and int(match.group(1)) in PEP_PAGES:
        return 200, "text/html; charset=utf-8", PEP_PAGES[int(match.group(1))]
    return 404, "text/html; charset=utf-8", b"<html>Page not found</html>"


# www.rfc-editor.org: the real RFC 9110 record and the trimmed real text from tests/fixtures.
RFC_RECORDS = {9110: (_FIXTURES / "rfc" / "rfc9110.json").read_bytes()}
RFC_TEXTS = {9110: (_FIXTURES / "rfc" / "rfc9110.txt").read_bytes()}


def _rfc_answer(path: str) -> tuple[int, str, bytes]:
    match = re.fullmatch(r"/rfc/rfc(\d+)\.(json|txt)", path)
    number = int(match.group(1)) if match else None
    if match and match.group(2) == "json" and number in RFC_RECORDS:
        return 200, "application/json;charset=utf-8", RFC_RECORDS[number]
    if match and match.group(2) == "txt" and number in RFC_TEXTS:
        return 200, "text/plain; charset=utf-8", RFC_TEXTS[number]
    return 404, "text/plain;charset=utf-8", b"404 - Not found"


# api.crossref.org and api.datacite.org: the real records from tests/fixtures/doi.
DOI_RECORDS = {
    ("api.crossref.org", "10.1038/nature14539"): (_FIXTURES / "doi" / "crossref-nature14539.json").read_bytes(),
    ("api.datacite.org", "10.48550/arxiv.1706.03762"):
        (_FIXTURES / "doi" / "datacite-arxiv-1706.03762.json").read_bytes(),
}


def _doi_answer(host: str, path: str) -> tuple[int, str, bytes]:
    match = re.fullmatch(r"/(?:works|dois)/(.+)", path)
    record = DOI_RECORDS.get((host, urllib.parse.unquote(match.group(1)).lower())) if match else None
    if record:
        return 200, "application/json", record
    if host == "api.crossref.org":
        return 404, "text/plain", b"Resource not found."
    return 404, "application/vnd.api+json", b'{"errors":[{"status":"404","title":"The resource does not exist."}]}'


class FakeServer:
    """One HTTP server playing the model API, ``/w/api.php``, OSV, GitHub, peps.python.org, the RFC Editor,
    Crossref and DataCite."""

    def __init__(self) -> None:
        self.chat_requests: list[dict[str, Any]] = []
        self.wiki_requests: list[dict[str, Any]] = []
        self.osv_requests: list[dict[str, Any]] = []
        self.github_requests: list[dict[str, Any]] = []
        self.pep_requests: list[dict[str, Any]] = []
        self.rfc_requests: list[dict[str, Any]] = []
        self.doi_requests: list[dict[str, Any]] = []
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
                self._raw(status, "application/json", json.dumps(payload).encode())

            def _raw(self, status: int, content_type: str, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                url = urllib.parse.urlsplit(self.path)
                if url.path == "/w/api.php":
                    params = dict(urllib.parse.parse_qsl(url.query))
                    language = self.headers.get("X-Test-Host", "").removesuffix(".wikipedia.org")
                    with server._lock:
                        server.wiki_requests.append({
                            "language": language, "params": params,
                            "user_agent": self.headers.get("User-Agent", ""),
                        })
                    if params.get("action") == "opensearch":
                        self._json(200, _opensearch_answer(language, params.get("search", "")))
                    else:
                        self._json(200, _query_answer(language, params.get("titles", ""), "exintro" not in params))
                    return
                if self.headers.get("X-Test-Host") == "peps.python.org":
                    with server._lock:
                        server.pep_requests.append({"path": url.path, "user_agent": self.headers.get("User-Agent", "")})
                    self._raw(*_pep_answer(url.path))
                    return
                if self.headers.get("X-Test-Host") in ("api.crossref.org", "api.datacite.org"):
                    host = self.headers["X-Test-Host"]
                    with server._lock:
                        server.doi_requests.append({
                            "host": host, "path": url.path, "user_agent": self.headers.get("User-Agent", ""),
                        })
                    self._raw(*_doi_answer(host, url.path))
                    return
                if self.headers.get("X-Test-Host") == "www.rfc-editor.org":
                    with server._lock:
                        server.rfc_requests.append({"path": url.path, "user_agent": self.headers.get("User-Agent", "")})
                    self._raw(*_rfc_answer(url.path))
                    return
                if self.headers.get("X-Test-Host") == "api.github.com":
                    with server._lock:
                        server.github_requests.append({
                            "path": url.path, "query": url.query, "user_agent": self.headers.get("User-Agent", ""),
                        })
                    self._json(*_github_answer(url.path))
                    return
                if url.path.startswith("/v1/vulns/") and self.headers.get("X-Test-Host") == "api.osv.dev":
                    identifier = urllib.parse.unquote(url.path[len("/v1/vulns/"):])
                    with server._lock:
                        server.osv_requests.append({"id": identifier, "user_agent": self.headers.get("User-Agent", "")})
                    self._json(*_vuln_answer(identifier))
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
