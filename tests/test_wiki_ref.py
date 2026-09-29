"""Unit tests for wiki-ref, run against the real Hermes reference parser and expander."""

from __future__ import annotations

import asyncio
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from agent.context_references import (
    ContextReferenceProvider,
    parse_context_references,
    preprocess_context_references_async,
    register_context_reference_provider,
)
from conftest import WIKI_DIR, RecordingTransport

TURING = {
    "pageid": 1208, "ns": 0, "title": "Alan Turing",
    "description": "English computer scientist (1912–1954)",
    "fullurl": "https://en.wikipedia.org/wiki/Alan_Turing",
    "extract": "Alan Mathison Turing was an English mathematician and computer scientist.",
}


def answer(page):
    return lambda url, params, timeout: {"query": {"pages": [page]}}


def config(**settings):
    return lambda key, default=None: settings.get(key, default)


def run(coro):
    return asyncio.run(coro)


# -- value round trip through Hermes's parser --------------------------------------------------

ROUND_TRIP_TITLES = [
    "Alan Turing",
    "Python (programming language)",
    "Washington, D.C.",
    "C++",
    "Why?",
    "Rock 'n' roll",
    'The "Talking" Drum',
    "Café",
    "AT&T",
    "Mercury (planet)",
    "Straße",
    "Sex, Drugs & Rock 'n' Roll",
    "Smile :)",  # unbalanced closer: Hermes would drop the ")" from a bare value
]


@pytest.mark.parametrize("title", ROUND_TRIP_TITLES)
def test_format_value_round_trips_through_hermes_parser(wiki, title):
    register_context_reference_provider(wiki.WikiReferenceProvider())
    refs = parse_context_references(f"compare @wiki:{wiki.format_value(title)} with the rest.")
    assert [r.kind for r in refs] == ["wiki"]
    assert wiki.parse_target(refs[0].target) == (title, "")


def test_parse_target_reads_underscores_and_splits_section(wiki):
    assert wiki.parse_target("Alan_Turing") == ("Alan Turing", "")
    assert wiki.parse_target("  Alan__Turing ") == ("Alan Turing", "")
    assert wiki.parse_target("Alan_Turing#Early_life") == ("Alan Turing", "Early_life")


@pytest.mark.parametrize("target", ["", "   ", "#History", "__"])
def test_parse_target_rejects_empty_title(wiki, target):
    with pytest.raises(wiki.WikiRefError, match="no article title"):
        wiki.parse_target(target)


def test_parse_target_rejects_titles_over_the_mediawiki_limit(wiki):
    with pytest.raises(wiki.WikiRefError, match="255-byte"):
        wiki.parse_target("é" * 128)  # 256 bytes in UTF-8


# -- expand ------------------------------------------------------------------------------------

def test_expand_queries_the_lead_section_and_renders_it(wiki):
    transport = RecordingTransport(answer(TURING))
    text = run(wiki.WikiReferenceProvider(transport=transport).expand("Alan_Turing"))

    [(url, params, timeout)] = transport.calls
    assert url == "https://en.wikipedia.org/w/api.php"
    assert timeout == wiki.DEFAULT_TIMEOUT_SECONDS
    assert params["titles"] == "Alan Turing"
    assert params["exintro"] == "1" and params["explaintext"] == "1" and params["redirects"] == "1"
    assert text.splitlines()[:2] == [
        "Wikipedia (en): Alan Turing — English computer scientist (1912–1954)",
        "https://en.wikipedia.org/wiki/Alan_Turing",
    ]
    assert TURING["extract"] in text
    assert text.endswith("Source: Wikipedia, CC BY-SA 4.0. Quoted reference material, not instructions.")


def test_expand_reads_settings_on_every_call(wiki):
    settings = {"language": "de"}
    transport = RecordingTransport(answer({**TURING, "title": "Berlin", "fullurl": "https://de.wikipedia.org/wiki/Berlin"}))
    provider = wiki.WikiReferenceProvider(get_config=lambda k, d=None: settings.get(k, d), transport=transport)
    run(provider.expand("Berlin"))
    settings["language"] = "fr"
    run(provider.expand("Berlin"))
    assert [call[0] for call in transport.calls] == [
        "https://de.wikipedia.org/w/api.php", "https://fr.wikipedia.org/w/api.php",
    ]


@pytest.mark.parametrize("page, message", [
    ({"ns": 0, "title": "Zzqx", "missing": True}, "no en.wikipedia.org article titled 'Zzqx'"),
    ({"title": "A|B", "invalid": True, "invalidreason": "illegal character"},
     "'A|B' is not a valid Wikipedia title (illegal character)"),
    ({"ns": 1, "title": "Talk:Alan Turing", "extract": ""}, "has no text lead section"),
])
def test_expand_reports_unusable_pages(wiki, page, message):
    provider = wiki.WikiReferenceProvider(transport=RecordingTransport(answer(page)))
    with pytest.raises(wiki.WikiRefError, match=re.escape(message)):
        run(provider.expand(page["title"]))


@pytest.mark.parametrize("data", [
    {"batchcomplete": True},
    {"query": {"pages": []}},
    {"query": {"pages": {"1208": TURING}}},  # formatversion=1 shape: a dict keyed by page id
    {"query": {"pages": ["not a page"]}},
    ["opensearch", "shape"],
])
def test_expand_rejects_an_answer_without_pages(wiki, data):
    provider = wiki.WikiReferenceProvider(transport=RecordingTransport(lambda *a: data))
    with pytest.raises(wiki.WikiRefError, match="unexpected answer from en.wikipedia.org for 'Alan Turing'"):
        run(provider.expand("Alan Turing"))


def test_expand_notes_redirects_disambiguation_and_sections(wiki):
    page = {**TURING, "title": "Mercury", "pageprops": {"disambiguation": ""}}
    text = run(wiki.WikiReferenceProvider(transport=RecordingTransport(answer(page))).expand("mercury#Planet"))
    assert "(resolved from 'mercury')" in text
    assert "disambiguation page" in text
    assert "not #Planet" in text


# Openings of real English Wikipedia extracts (TextExtracts, explaintext=1), 2026-09-29.
@pytest.mark.parametrize("raw, cleaned", [
    ("Alan Mathison Turing (; 23 June 1912 – 7 June 1954) was",
     "Alan Mathison Turing (23 June 1912 – 7 June 1954) was"),
    ("Johann Carl Friedrich Gauss ( ; German: Gauß; 30 April 1777 – 23 February 1855)",
     "Johann Carl Friedrich Gauss (German: Gauß; 30 April 1777 – 23 February 1855)"),
    ("Sir Isaac Newton ( ; 4 January 1643 [O.S. 25 December 1642] – 31 March 1727",
     "Sir Isaac Newton (4 January 1643 [O.S. 25 December 1642] – 31 March 1727"),
    ("Leonhard Euler ( OY-lər; 15 April 1707 – 18 September 1783)",
     "Leonhard Euler (OY-lər; 15 April 1707 – 18 September 1783)"),
    ("Pyotr Ilyich Tchaikovsky (  chy-KOF-skee; 7 May 1840 – 6 November 1893)",
     "Pyotr Ilyich Tchaikovsky (chy-KOF-skee; 7 May 1840 – 6 November 1893)"),
    ("Kurt Gödel ( GUR-dəl; German: [ˈkʊʁt ˈɡøːdl̩] ; April 28, 1906 – January 14, 1978)",
     "Kurt Gödel (GUR-dəl; German: [ˈkʊʁt ˈɡøːdl̩]; April 28, 1906 – January 14, 1978)"),
    ("Jean-Paul Sartre (, US also ; French: [saʁtʁ]; 21 June 1905 – 15 April 1980)",
     "Jean-Paul Sartre (French: [saʁtʁ]; 21 June 1905 – 15 April 1980)"),
    ("René Descartes ( day-KART, also  DAY-kart; French: [ʁəne dekaʁt] ; 31 March 1596",
     "René Descartes (day-KART, also DAY-kart; French: [ʁəne dekaʁt]; 31 March 1596"),
    ("Wrocław (Polish: [ˈvrɔt͡swaf] ; German: Breslau [ˈbʁɛslaʊ] ; also known by other names)",
     "Wrocław (Polish: [ˈvrɔt͡swaf]; German: Breslau [ˈbʁɛslaʊ]; also known by other names)"),
    ("Maria Salomea Skłodowska Curie  (née Skłodowska; 7 November 1867 – 4 July 1934)",
     "Maria Salomea Skłodowska Curie (née Skłodowska; 7 November 1867 – 4 July 1934)"),
])
def test_clean_extract_drops_empty_pronunciation_remnants(wiki, raw, cleaned):
    assert wiki.clean_extract(raw) == cleaned


@pytest.mark.parametrize("text", [
    "Sir Timothy John Berners-Lee (born 8 June 1955), also known as TimBL, is",
    "Gdańsk (Kashubian: Gduńsk; German: Danzig) is",
    "The printf() function writes output.",
    "Paris est la capitale de la France ; elle compte 2 millions d'habitants.",
    "First paragraph.\nSecond paragraph.",
])
def test_clean_extract_leaves_ordinary_text_alone(wiki, text):
    assert wiki.clean_extract(text) == text


def test_expand_attaches_the_cleaned_lead_section(wiki):
    page = {**TURING, "extract": "Alan Mathison Turing (; 23 June 1912 – 7 June 1954) was an English mathematician."}
    text = run(wiki.WikiReferenceProvider(transport=RecordingTransport(answer(page))).expand("Alan Turing"))
    assert "Alan Mathison Turing (23 June 1912 – 7 June 1954) was an English mathematician." in text
    assert "(;" not in text


def test_expand_truncates_at_a_word_boundary(wiki):
    page = {**TURING, "extract": "word " * 200}
    provider = wiki.WikiReferenceProvider(get_config=config(max_chars=200), transport=RecordingTransport(answer(page)))
    text = run(provider.expand("Alan Turing"))
    body = text.split("\n\n")[1]
    assert body.endswith("word […]") and len(body) <= 200 + len(" […]")
    assert "(lead section truncated to 200 characters)" in text


@pytest.mark.parametrize("settings, match", [
    ({"language": "en.evil.example"}, "settings.language"),
    ({"language": "EN/../x"}, "settings.language"),
    ({"language": ""}, "settings.language"),
    ({"max_chars": 50}, "settings.max_chars"),
    ({"max_chars": "6000"}, "settings.max_chars"),
    ({"max_chars": True}, "settings.max_chars"),
    ({"timeout_seconds": True}, "settings.timeout_seconds"),  # True == 1 is in range; only the bool check rejects it
    ({"timeout_seconds": 0}, "settings.timeout_seconds"),
    ({"timeout_seconds": 31}, "settings.timeout_seconds"),
])
def test_unusable_settings_fail_loudly_before_any_request(wiki, settings, match):
    transport = RecordingTransport(answer(TURING))
    provider = wiki.WikiReferenceProvider(get_config=config(**settings), transport=transport)
    with pytest.raises(wiki.WikiRefError, match=match):
        run(provider.expand("Alan Turing"))
    assert transport.calls == []


def test_language_setting_is_normalized(wiki):
    provider = wiki.WikiReferenceProvider(get_config=config(language=" Zh-Yue "))
    assert provider.language() == "zh-yue"


def test_a_failing_config_reader_falls_back_to_defaults(wiki):
    def broken(key, default=None):
        raise OSError("config unreadable")

    assert wiki.WikiReferenceProvider(get_config=broken).language() == "en"


@pytest.mark.parametrize("error, message", [
    (urllib.error.HTTPError("u", 503, "busy", {}, None), "en.wikipedia.org answered HTTP 503"),
    (urllib.error.URLError("no route"), "could not reach en.wikipedia.org: no route"),
    (ConnectionResetError("reset"), "could not reach en.wikipedia.org: reset"),
    (ValueError("Expecting value"), "unreadable answer from en.wikipedia.org"),
])
def test_transport_failures_become_reference_errors(wiki, error, message):
    def failing(*_args):
        raise error

    with pytest.raises(wiki.WikiRefError, match=message):
        run(wiki.WikiReferenceProvider(transport=failing).expand("Alan Turing"))


def test_a_hung_request_is_bounded_by_the_timeout_setting(wiki):
    release = threading.Event()

    def hang(*_args):
        release.wait(10)
        return {"query": {"pages": [TURING]}}

    provider = wiki.WikiReferenceProvider(get_config=config(timeout_seconds=1), transport=hang)
    started = time.monotonic()
    try:
        with pytest.raises(wiki.WikiRefError, match="did not answer within 1s"):
            run(provider.expand("Alan Turing"))
    finally:
        release.set()
    assert time.monotonic() - started < 5


# -- through Hermes's expander -----------------------------------------------------------------

def test_hermes_attaches_the_article_and_reports_failures(wiki, tmp_path: Path):
    pages = {"Alan Turing": TURING}

    def responder(url, params, timeout):
        return {"query": {"pages": [pages.get(params["titles"], {"title": params["titles"], "missing": True})]}}

    register_context_reference_provider(wiki.WikiReferenceProvider(transport=RecordingTransport(responder)))
    result = run(preprocess_context_references_async(
        "Summarize @wiki:Alan_Turing and @wiki:Zzqx.", cwd=tmp_path, context_length=100_000,
    ))
    assert "--- Attached Context ---" in result.message
    assert "Wikipedia (en): Alan Turing" in result.message
    # Hermes labels the warning with the raw token (trailing "." included); the title itself is "Zzqx".
    assert "--- Context Warnings ---" in result.message
    assert "plugin expansion error: no en.wikipedia.org article titled 'Zzqx'" in result.message


def test_a_hung_request_does_not_hold_hermes_sync_expander(wiki, tmp_path: Path):
    """The CLI and TUI expand through the sync wrapper, which runs asyncio.run() and joins the
    loop's default executor on exit; the request must not be parked there."""
    from agent.context_references import preprocess_context_references

    release = threading.Event()

    def hang(*_args):
        release.wait(10)
        return {"query": {"pages": [TURING]}}

    register_context_reference_provider(
        wiki.WikiReferenceProvider(get_config=config(timeout_seconds=1), transport=hang)
    )
    started = time.monotonic()
    try:
        result = preprocess_context_references("see @wiki:Alan_Turing", cwd=tmp_path, context_length=100_000)
    finally:
        release.set()
    assert time.monotonic() - started < 5
    assert "did not answer within 1s" in result.message


# -- autocomplete ------------------------------------------------------------------------------

def test_autocomplete_suggests_values_that_parse_back(wiki):
    titles = ["Washington, D.C.", "Washington (state)"]
    transport = RecordingTransport(lambda url, params, timeout: ["Washington", titles, ["", ""], ["", ""]])
    provider = wiki.WikiReferenceProvider(transport=transport)
    items = run(provider.autocomplete('"Washington', limit=50))

    [(url, params, timeout)] = transport.calls
    assert params["action"] == "opensearch" and params["search"] == "Washington" and params["limit"] == "20"
    assert timeout == wiki.AUTOCOMPLETE_TIMEOUT_SECONDS
    assert [item.display for item in items] == titles
    assert {item.meta for item in items} == {"Wikipedia (en)"}
    register_context_reference_provider(provider)
    for item, title in zip(items, titles, strict=True):
        [ref] = parse_context_references(f"@wiki:{item.text} ")
        assert wiki.parse_target(ref.target)[0] == title


@pytest.mark.parametrize("query", ["", "  ", '"'])
def test_autocomplete_skips_empty_queries_without_a_request(wiki, query):
    transport = RecordingTransport(lambda *a: ["", [], [], []])
    assert run(wiki.WikiReferenceProvider(transport=transport).autocomplete(query)) == []
    assert transport.calls == []


def test_autocomplete_stays_quiet_on_errors_and_bad_settings(wiki):
    def failing(*_args):
        raise urllib.error.URLError("offline")

    assert run(wiki.WikiReferenceProvider(transport=failing).autocomplete("Tur")) == []
    bad = wiki.WikiReferenceProvider(get_config=config(language="x.y"), transport=RecordingTransport(lambda *a: []))
    assert run(bad.autocomplete("Tur")) == []


# -- HTTP layer --------------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    routes: dict = {}
    seen: list = []

    def log_message(self, *_args):
        pass

    def do_GET(self):  # noqa: N802
        type(self).seen.append({"path": self.path, "ua": self.headers.get("User-Agent")})
        if self.path.startswith("/drip"):
            body = b'{"ok": true, "pad": "' + b"x" * 40 + b'"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            for byte in body:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                time.sleep(0.25)
            return
        status, headers, body = type(self).routes[self.path.split("?")[0]]
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
    yield server, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_http_get_json_sends_the_user_agent_and_decodes(wiki, http_server):
    _, origin = http_server
    _Handler.routes = {"/w/api.php": (200, {"Content-Type": "application/json"}, b'{"ok": true}')}
    assert wiki.http_get_json(f"{origin}/w/api.php", {"titles": "A B"}, 5) == {"ok": True}
    assert _Handler.seen[0]["path"] == "/w/api.php?titles=A+B"
    assert _Handler.seen[0]["ua"] == wiki.USER_AGENT


def test_http_get_json_refuses_oversized_bodies(wiki, http_server, monkeypatch):
    _, origin = http_server
    monkeypatch.setattr(wiki, "MAX_RESPONSE_BYTES", 16)
    _Handler.routes = {"/big": (200, {}, b'{"x": "' + b"a" * 64 + b'"}')}
    with pytest.raises(ValueError, match="larger than 16 bytes"):
        wiki.http_get_json(f"{origin}/big", {}, 5)


def test_http_get_json_bounds_a_trickling_answer(wiki, http_server):
    _, origin = http_server
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="no complete answer within 1s"):
        wiki.http_get_json(f"{origin}/drip", {}, 1)
    assert time.monotonic() - started < 3  # each byte arrives well inside the 1s socket timeout


def test_http_get_json_does_not_follow_a_redirect_off_https(wiki, http_server):
    _, origin = http_server
    _Handler.routes = {"/w/api.php": (302, {"Location": f"{origin}/elsewhere"}, b"")}
    with pytest.raises(urllib.error.HTTPError, match="refused redirect"):
        wiki.http_get_json(f"{origin}/w/api.php", {}, 5)
    assert [s["path"] for s in _Handler.seen] == ["/w/api.php?"]


@pytest.mark.parametrize("new_url, allowed", [
    ("https://en.wikipedia.org/w/api.php?x=1", True),
    ("https://evil.example/w/api.php", False),
    ("http://en.wikipedia.org/w/api.php", False),
])
def test_redirects_stay_on_the_same_https_host(wiki, new_url, allowed):
    handler = wiki._SameHostRedirectHandler()
    request = urllib.request.Request("https://en.wikipedia.org/w/api.php")
    if allowed:
        assert handler.redirect_request(request, None, 302, "Found", {}, new_url).full_url == new_url
    else:
        with pytest.raises(urllib.error.HTTPError, match="refused redirect"):
            handler.redirect_request(request, None, 302, "Found", {}, new_url)


# -- packaging ---------------------------------------------------------------------------------

def test_manifest_version_matches_the_module(wiki):
    try:
        import hermes_yaml as yaml  # Hermes after the Sep 2026 YAML switch
    except ImportError:
        import yaml

    manifest = yaml.safe_load((WIKI_DIR / "plugin.yaml").read_text(encoding="utf-8-sig"))
    assert manifest["name"] == wiki.PLUGIN_ID
    assert str(manifest["version"]) == wiki.__version__
    assert set(manifest["config_schema"]) == {"language", "max_chars", "timeout_seconds"}
    assert manifest["config_schema"]["language"]["default"] == wiki.DEFAULT_LANGUAGE
    assert manifest["config_schema"]["max_chars"]["default"] == wiki.DEFAULT_MAX_CHARS
    assert manifest["config_schema"]["timeout_seconds"]["default"] == wiki.DEFAULT_TIMEOUT_SECONDS


def test_register_adds_the_wiki_prefix(wiki):
    registered = []

    class Ctx:
        def get_config(self, key, default=None):
            return default

        def register_context_reference(self, provider):
            registered.append(provider)

    wiki.register(Ctx())
    [provider] = registered
    assert isinstance(provider, ContextReferenceProvider) and provider.prefix == "wiki"
