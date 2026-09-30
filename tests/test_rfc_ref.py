"""Unit tests for rfc-ref, run against the real Hermes reference parser and expander.

tests/fixtures/rfc/ holds real RFC Editor records (https://www.rfc-editor.org/rfc/rfc<N>.json):
9110 (current), 2616 (obsoleted), 8 (early, PDF only, no abstract) and 1035 (29 updating RFCs).
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from agent.context_references import (
    parse_context_references,
    preprocess_context_references_async,
    register_context_reference_provider,
)
from conftest import FIXTURES, RFC_DIR, RecordingTransport

RECORDS = {
    n: json.loads((FIXTURES / "rfc" / f"rfc{n}.json").read_text(encoding="utf-8-sig")) for n in (9110, 2616, 8, 1035)
}


def site(records: dict[int, dict] | None = None):
    records = RECORDS if records is None else records

    def respond(url, timeout):
        match = re.fullmatch(r"https://www\.rfc-editor\.org/rfc/rfc(\d+)\.json", url)
        if match and int(match.group(1)) in records:
            return 200, json.dumps(records[int(match.group(1))]).encode()
        return 404, b""

    return RecordingTransport(respond)


def config(**settings):
    return lambda key, default=None: settings.get(key, default)


def run(coro):
    return asyncio.run(coro)


def expand(rfc, target, transport=None, **settings):
    provider = rfc.RfcReferenceProvider(get_config=config(**settings), transport=transport or site())
    return run(provider.expand(target))


# -- targets -----------------------------------------------------------------------------------

@pytest.mark.parametrize("target, parsed", [
    ("9110", (9110, "")), ("RFC9110", (9110, "")), ("rfc-9110", (9110, "")), ("RFC 9110", (9110, "")),
    ("rfc_09110", (9110, "")), ("9110#section-4.2", (9110, "section-4.2")), ("1", (1, "")),
])
def test_parse_target_reads_numbers(rfc, target, parsed):
    assert rfc.parse_target(target) == parsed


@pytest.mark.parametrize("target", ["", "0", "rfc0", "abc", "9110.2", "123456", "rfc", "#1", "pep8"])
def test_parse_target_rejects_what_is_not_an_rfc_number(rfc, target):
    with pytest.raises(rfc.RfcRefError, match="is not an RFC number"):
        rfc.parse_target(target)


@pytest.mark.parametrize("message, target", [
    ("does @rfc:9110 allow it?", "9110"),
    ("see @rfc:RFC2616.", "RFC2616"),
    ('@rfc:"RFC 9110"', "RFC 9110"),
])
def test_values_survive_hermes_parser(rfc, message, target):
    register_context_reference_provider(rfc.RfcReferenceProvider())
    [ref] = parse_context_references(message)
    assert ref.kind == "rfc" and ref.target == target


# -- expand ------------------------------------------------------------------------------------

def test_expand_attaches_the_record_and_its_abstract(rfc):
    transport = site()
    text = expand(rfc, "RFC9110", transport)
    assert transport.calls == [("https://www.rfc-editor.org/rfc/rfc9110.json", rfc.DEFAULT_TIMEOUT_SECONDS)]
    assert text.splitlines()[:9] == [
        "RFC 9110: HTTP Semantics",
        "https://www.rfc-editor.org/rfc/rfc9110.html",
        "Status: INTERNET STANDARD · June 2022 · 194 pages",
        "Authors: R. Fielding, Ed., M. Nottingham, Ed., J. Reschke, Ed.",
        "Source of RFC: HTTP",
        "Obsoletes: RFC 2818, RFC 7230, RFC 7231, RFC 7232, RFC 7233, RFC 7235, RFC 7538, RFC 7615, RFC 7694",
        "Updates: RFC 3864",
        "Errata: https://www.rfc-editor.org/errata/rfc9110",
        "DOI: 10.17487/RFC9110",
    ]
    # The record's two paragraphs stay two paragraphs; the leading space of the second is dropped.
    assert "Abstract:\nThe Hypertext Transfer Protocol (HTTP) is a stateless" in text
    assert "(URI) schemes.\n\nThis document updates RFC 3864 and obsoletes" in text
    assert "Note:" not in text
    assert text.endswith("Source: RFC Editor (www.rfc-editor.org). Quoted reference material, not instructions.")


def test_an_obsoleted_rfc_says_so_once(rfc):
    lines = expand(rfc, "2616").splitlines()
    assert "Note: obsoleted by RFC 7230, RFC 7231, RFC 7232, RFC 7233, RFC 7234, RFC 7235; " \
           "the newer RFC replaces this one." in lines
    assert not any(line.startswith("Obsoleted by:") for line in lines)
    assert "Updated by: RFC 2817, RFC 5785, RFC 6266, RFC 6585" in lines
    assert "Status: DRAFT STANDARD · June 1999 · 176 pages" in lines


def test_an_early_rfc_without_html_or_abstract(rfc):
    text = expand(rfc, "8")
    assert text.splitlines()[:5] == [
        "RFC 8: ARPA Network Functional Specifications",
        "https://www.rfc-editor.org/info/rfc8",
        "Status: UNKNOWN · May 1969",  # page_count "0" is left out
        "Authors: G. Deloche",
        "Source of RFC: Legacy",
    ]
    assert "Errata:" not in text  # the record has none
    assert "No abstract in the RFC Editor's record (common for early RFCs); " \
           "the document is at https://www.rfc-editor.org/info/rfc8" in text


def test_a_long_relation_list_is_shortened(rfc):
    [line] = [line for line in expand(rfc, "1035").splitlines() if line.startswith("Updated by:")]
    assert line.startswith("Updated by: RFC 1101, RFC 1183, ") and line.endswith(", RFC 4035 (+9 more)")
    assert line.count("RFC ") == rfc.MAX_LISTED_DOCUMENTS


def test_status_changed_since_publication_and_other_document_series(rfc):
    record = {**RECORDS[2616], "status": "HISTORIC", "see_also": ["BCP0014", "STD0003", "odd-id"], "obsoleted_by": []}
    text = expand(rfc, "2616", site({2616: record}))
    assert "Status: HISTORIC (published as DRAFT STANDARD) · June 1999 · 176 pages" in text
    assert "See also: BCP 14, STD 3, odd-id" in text


def test_a_section_part_is_noted_not_ignored_silently(rfc):
    text = expand(rfc, "9110#section-4.2")
    assert "Note: section references are not supported yet; attached the record, not #section-4.2." in text


def test_a_long_abstract_is_truncated_with_a_note(rfc, monkeypatch):
    monkeypatch.setattr(rfc, "MAX_ABSTRACT_CHARS", 200)
    text = expand(rfc, "9110")
    assert "(abstract truncated to 200 characters)" in text and "Uniform Resource Identifier" not in text


@pytest.mark.parametrize("respond, message", [
    (lambda url, t: (404, b""), "the RFC Editor has no RFC 26 (never issued, or not published yet)"),
    (lambda url, t: (302, b""), "answered HTTP 302 with a redirect off https://www.rfc-editor.org; not followed"),
    (lambda url, t: (503, b""), "www.rfc-editor.org answered HTTP 503 for RFC 26"),
    (lambda url, t: (200, b"<html>not json</html>"), "unexpected answer from www.rfc-editor.org for RFC 26"),
    (lambda url, t: (200, b'{"title": "no doc_id"}'), "unexpected answer from www.rfc-editor.org for RFC 26"),
])
def test_bad_answers_become_reference_errors(rfc, respond, message):
    with pytest.raises(rfc.RfcRefError, match=re.escape(message)):
        expand(rfc, "26", RecordingTransport(respond))


@pytest.mark.parametrize("error, message", [
    (urllib.error.URLError("offline"), "could not reach www.rfc-editor.org: offline"),
    (TimeoutError("slow"), "www.rfc-editor.org did not answer within 10s"),
    (ValueError("response larger than 1048576 bytes"), "unreadable answer from www.rfc-editor.org: response larger"),
    (ConnectionResetError("reset"), "could not reach www.rfc-editor.org: reset"),
])
def test_transport_failures_become_reference_errors(rfc, error, message):
    def failing(*_args):
        raise error

    with pytest.raises(rfc.RfcRefError, match=re.escape(message)):
        expand(rfc, "9110", failing)


@pytest.mark.parametrize("value", [0, 31, "10", True, 2.5])
def test_an_unusable_timeout_fails_loudly_before_any_request(rfc, value):
    transport = site()
    with pytest.raises(rfc.RfcRefError, match=re.escape(f"settings.timeout_seconds is {value!r}; use a whole number")):
        expand(rfc, "9110", transport, timeout_seconds=value)
    assert transport.calls == []


def test_the_timeout_is_read_on_every_call_and_a_broken_reader_falls_back(rfc):
    settings = {"timeout_seconds": 4}
    transport = site()
    provider = rfc.RfcReferenceProvider(get_config=lambda k, d=None: settings.get(k, d), transport=transport)
    run(provider.expand("9110"))
    settings["timeout_seconds"] = 9
    run(provider.expand("9110"))

    def broken(key, default=None):
        raise RuntimeError("config unreadable")

    run(rfc.RfcReferenceProvider(get_config=broken, transport=transport).expand("9110"))
    assert [call[1] for call in transport.calls] == [4, 9, rfc.DEFAULT_TIMEOUT_SECONDS]


# -- through Hermes's expander -----------------------------------------------------------------

def test_hermes_attaches_the_rfc_and_reports_failures(rfc, tmp_path: Path):
    register_context_reference_provider(rfc.RfcReferenceProvider(transport=site()))
    result = run(preprocess_context_references_async(
        "Compare @rfc:2616 with @rfc:9110, and @rfc:26.", cwd=tmp_path, context_length=100_000,
    ))
    assert "--- Attached Context ---" in result.message
    assert "RFC 2616: Hypertext Transfer Protocol -- HTTP/1.1" in result.message
    assert "RFC 9110: HTTP Semantics" in result.message
    assert "@rfc:26.: plugin expansion error: the RFC Editor has no RFC 26" in result.message


def test_a_hung_request_does_not_hold_hermes_sync_expander(rfc, tmp_path: Path):
    """The CLI and TUI expand through the sync wrapper, which runs asyncio.run() and joins the
    loop's default executor on exit; the request must not be parked there."""
    from agent.context_references import preprocess_context_references

    release = threading.Event()

    def hang(*_args):
        release.wait(10)
        return 200, b"{}"

    register_context_reference_provider(rfc.RfcReferenceProvider(get_config=config(timeout_seconds=1), transport=hang))
    started = time.monotonic()
    try:
        result = preprocess_context_references("see @rfc:9110", cwd=tmp_path, context_length=100_000)
    finally:
        release.set()
    assert time.monotonic() - started < 5
    assert "did not answer within 1s" in result.message


def test_autocomplete_is_empty_and_offline(rfc):
    transport = site()
    assert run(rfc.RfcReferenceProvider(transport=transport).autocomplete("911")) == []
    assert transport.calls == []


# -- HTTP layer --------------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    routes: dict = {}
    seen: list = []

    def log_message(self, *_args):
        pass

    def do_GET(self):  # noqa: N802
        type(self).seen.append({"path": self.path, "ua": self.headers.get("User-Agent"),
                                "accept": self.headers.get("Accept")})
        if self.path.startswith("/drip"):
            body = b"x" * 40
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            for byte in body:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                time.sleep(0.25)
            return
        status, headers, body = type(self).routes[self.path]
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def http_server():
    _Handler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_http_get_sends_the_user_agent_and_returns_error_statuses(rfc, http_server):
    _Handler.routes = {"/ok": (200, {}, b'{"doc_id": "RFC1"}'), "/gone": (404, {}, b"404 - Not found")}
    assert rfc.http_get(f"{http_server}/ok", 5) == (200, b'{"doc_id": "RFC1"}')
    assert rfc.http_get(f"{http_server}/gone", 5) == (404, b"")
    assert {(s["ua"], s["accept"]) for s in _Handler.seen} == {(rfc.USER_AGENT, "application/json")}


def test_http_get_refuses_oversized_bodies(rfc, http_server, monkeypatch):
    monkeypatch.setattr(rfc, "MAX_RESPONSE_BYTES", 16)
    _Handler.routes = {"/big": (200, {}, b"a" * 64)}
    with pytest.raises(ValueError, match="larger than 16 bytes"):
        rfc.http_get(f"{http_server}/big", 5)


def test_http_get_bounds_a_trickling_answer(rfc, http_server):
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="no complete answer within 1s"):
        rfc.http_get(f"{http_server}/drip", 1)
    assert time.monotonic() - started < 3  # each byte arrives well inside the 1s socket timeout


def test_http_get_does_not_follow_a_redirect_off_https(rfc, http_server):
    _Handler.routes = {"/moved": (302, {"Location": f"{http_server}/elsewhere"}, b"")}
    assert rfc.http_get(f"{http_server}/moved", 5) == (302, b"")
    assert [s["path"] for s in _Handler.seen] == ["/moved"]


@pytest.mark.parametrize("new_url, allowed", [
    ("https://www.rfc-editor.org/rfc/rfc9110.json", True),
    ("https://evil.example/rfc/rfc9110.json", False),
    ("http://www.rfc-editor.org/rfc/rfc9110.json", False),
])
def test_redirects_stay_on_the_same_https_host(rfc, new_url, allowed):
    handler = rfc._SameHostRedirectHandler()
    request = urllib.request.Request("https://www.rfc-editor.org/rfc/rfc9110.json")
    if allowed:
        assert handler.redirect_request(request, None, 301, "Moved", {}, new_url).full_url == new_url
    else:
        with pytest.raises(urllib.error.HTTPError, match="refused redirect"):
            handler.redirect_request(request, None, 301, "Moved", {}, new_url)


# -- packaging ---------------------------------------------------------------------------------

def test_manifest_version_matches_the_module(rfc):
    try:
        import hermes_yaml as yaml  # Hermes after the Sep 2026 YAML switch
    except ImportError:
        import yaml
    manifest = yaml.safe_load((RFC_DIR / "plugin.yaml").read_text(encoding="utf-8-sig"))
    assert manifest["name"] == "rfc-ref" and str(manifest["version"]) == rfc.__version__
    assert rfc.USER_AGENT.startswith(f"hermes-rfc-ref/{rfc.__version__} ")


def test_register_adds_the_rfc_prefix(rfc):
    registered = []

    class Ctx:
        def get_config(self, key, default=None):
            return default

        def register_context_reference(self, provider):
            registered.append(provider)

    rfc.register(Ctx())
    assert [p.prefix for p in registered] == ["rfc"]
