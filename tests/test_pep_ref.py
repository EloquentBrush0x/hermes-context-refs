"""Unit tests for pep-ref, run against the real Hermes reference parser and expander.

The page fixtures are trimmed from the real peps.python.org pages (tests/fixtures/); the index
fixture holds real entries from https://peps.python.org/api/peps.json.
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
from conftest import FIXTURES, PEP_DIR, RecordingTransport

INDEX = json.loads((FIXTURES / "peps-index.json").read_text(encoding="utf-8-sig"))
PAGE_572 = (FIXTURES / "pep-0572.html").read_text(encoding="utf-8-sig")
PAGE_0 = (FIXTURES / "pep-0000-table.html").read_text(encoding="utf-8-sig")
PAGE_1 = (FIXTURES / "pep-0001-list.html").read_text(encoding="utf-8-sig")
PAGE_3333 = (FIXTURES / "pep-3333-table.html").read_text(encoding="utf-8-sig")
INDEX_URL = "https://peps.python.org/api/peps.json"


def page(*sections: str) -> str:
    """A minimal page in the published markup: ``("Heading", "<p>text</p>")`` pairs as top-level sections."""
    body = "".join(
        f'<section id="{title.lower().replace(" ", "-")}">\n<h2><a class="toc-backref" href="#x">{title}</a></h2>\n'
        f"{html}\n</section>\n"
        for title, html in sections
    )
    return f'<main><section id="pep-content">\n<h1 class="page-title">PEP</h1>\n{body}</section></main>'


def site(pages: dict[int, str] | None = None, index: dict | None = None):
    pages = {572: PAGE_572, 0: PAGE_0} if pages is None else pages
    body = json.dumps(INDEX if index is None else index).encode()

    def respond(url, timeout):
        if url == INDEX_URL:
            return 200, body
        match = re.fullmatch(r"https://peps\.python\.org/pep-(\d{4})/", url)
        if match and int(match.group(1)) in pages:
            return 200, pages[int(match.group(1))].encode()
        return 404, b""

    return RecordingTransport(respond)


def config(**settings):
    return lambda key, default=None: settings.get(key, default)


def run(coro):
    return asyncio.run(coro)


def expand(pep, target, transport=None, **settings):
    provider = pep.PepReferenceProvider(get_config=config(**settings), transport=transport or site())
    return run(provider.expand(target))


# -- targets -----------------------------------------------------------------------------------

@pytest.mark.parametrize("target, parsed", [
    ("8", (8, "")), ("0008", (8, "")), ("pep8", (8, "")), ("PEP-8", (8, "")), ("pep_0008", (8, "")),
    ("PEP 8", (8, "")), ("0", (0, "")), ("572#", (572, "")),
    ("8#Naming_Conventions", (8, "Naming Conventions")), ("8#naming-conventions", (8, "naming-conventions")),
])
def test_parse_target_reads_numbers_and_sections(pep, target, parsed):
    assert pep.parse_target(target) == parsed


@pytest.mark.parametrize("target", ["", "abc", "8.1", "123456", "pep", "#Abstract", "rfc9110"])
def test_parse_target_rejects_what_is_not_a_pep_number(pep, target):
    with pytest.raises(pep.PepRefError, match="is not a PEP number"):
        pep.parse_target(target)


@pytest.mark.parametrize("message, target", [
    ("see @pep:572.", "572"),
    ("@pep:PEP-0572#Abstract", "PEP-0572#Abstract"),
    ('@pep:"PEP 8#Naming Conventions"', "PEP 8#Naming Conventions"),
    ("(see @pep:8#naming-conventions)", "8#naming-conventions"),
])
def test_values_survive_hermes_parser(pep, message, target):
    register_context_reference_provider(pep.PepReferenceProvider())
    [ref] = parse_context_references(message)
    assert ref.kind == "pep" and ref.target == target


# -- the page ----------------------------------------------------------------------------------

def test_parse_page_keeps_the_sections_and_drops_header_contents_and_sidebar(pep):
    parsed = pep.parse_page(PAGE_572)
    assert [(s.level, s.id, s.title) for s in parsed.sections] == [
        (2, "abstract", "Abstract"),
        (2, "syntax-and-semantics", "Syntax and semantics"),
        (3, "change-to-evaluation-order", "Change to evaluation order"),
        (2, "examples", "Examples"),
        (3, "examples-from-the-python-standard-library", "Examples from the Python standard library"),
        (4, "site-py", "site.py"),
        (2, "copyright", "Copyright"),
    ]
    assert parsed.text.startswith("## Abstract\n\nThis is a proposal")
    # The <h1> "PEP 572 – Assignment Expressions", the field list, the table of contents, the sidebar.
    for dropped in ("PEP 572 –", "Post-History", "Table of Contents", "Contents"):
        assert dropped not in parsed.text


def test_parse_page_renders_inline_code_code_blocks_lists_and_tables(pep):
    text = pep.parse_page(PAGE_572).text
    assert "using the notation `NAME := expr`." in text
    assert "- Current:\n\n```\nenv_base = os.environ.get(\"PYTHONUSERBASE\", None)\nif env_base:\n" in text
    assert "```\nif env_base := os.environ.get(\"PYTHONUSERBASE\", None):\n    return env_base\n```" in text
    numbered = pep.parse_page(PAGE_1).text
    assert "There are three kinds of PEP:\n\n1. A Standards Track PEP describes" in numbered
    assert "\n2. An Informational PEP describes" in numbered and "\n3. A Process PEP describes" in numbered
    table = pep.parse_page(PAGE_0).text
    assert "|  | PEP | Title | Authors |  |\n| PA | 1 | PEP Purpose and Guidelines | " in table
    assert "| PA | 2 | Procedure for Adding New Modules | Brett Cannon, Martijn Faassen |  |" in table
    # A cell whose inline code is split across <span>s keeps the space between them.
    wsgi = pep.parse_page(PAGE_3333)
    assert "| `wsgi.version` | The tuple `(1, 0)`, representing WSGI version 1.0. |" in wsgi.text
    assert [(s.id, s.title) for s in wsgi.sections] == [("environ-variables", "`environ` Variables")]
    assert pep.find_section(wsgi, "environ variables") == 0  # found through the link id


# -- expand ------------------------------------------------------------------------------------

def test_expand_attaches_the_summary_and_the_first_section(pep):
    transport = site()
    text = expand(pep, "572", transport)
    assert [call[0] for call in transport.calls] == [INDEX_URL, "https://peps.python.org/pep-0572/"]
    assert transport.calls[0][1] == pep.DEFAULT_TIMEOUT_SECONDS
    assert text.splitlines()[:6] == [
        "PEP 572 — Assignment Expressions",
        "https://peps.python.org/pep-0572/#abstract",
        "Final · Standards Track · Python 3.8 · created 28-Feb-2018",
        "Authors: Chris Angelico, Tim Peters, Guido van Rossum",
        "Resolution: https://mail.python.org/pipermail/python-dev/2018-July/154601.html",
        "Section: Abstract",
    ]
    assert "This is a proposal for creating a way to assign to variables" in text
    assert "Syntax and semantics" not in text and "## " not in text
    assert text.endswith("Source: peps.python.org. Quoted reference material, not instructions.")


@pytest.mark.parametrize("target", [
    "572#Syntax and semantics", "572#syntax-and-semantics", "572#SYNTAX_AND_SEMANTICS", "572#Syntax-and-Semantics",
])
def test_a_section_is_found_by_heading_or_link_id(pep, target):
    text = expand(pep, target)
    assert "https://peps.python.org/pep-0572/#syntax-and-semantics\n" in text
    assert "Section: Syntax and semantics" in text
    assert "### Change to evaluation order" in text  # its subsection comes along
    assert "Examples" not in text and "This is a proposal" not in text


@pytest.mark.parametrize("target", ["572#site.py", "572#site-py", "572#site_py"])  # heading, link id, typed id
def test_a_nested_section_carries_its_path(pep, target):
    text = expand(pep, target)
    assert "https://peps.python.org/pep-0572/#site-py" in text
    assert "Section: Examples › Examples from the Python standard library › site.py" in text
    assert "if env_base := os.environ.get" in text and "Copyright" not in text


def test_an_exact_heading_wins_over_a_loose_match(pep):
    html = page(("Uses", "<p>first</p>"), ("USES", "<p>second</p>"))
    transport = site(pages={572: html})
    assert "second" in expand(pep, "572#USES", transport) and "first" not in expand(pep, "572#USES", transport)
    assert "first" in expand(pep, "572#uses", transport)


@pytest.mark.parametrize("target, message", [
    ("572#Abstrct", "no section 'Abstrct' in PEP 572; did you mean 'Abstract'? "
                    "Sections: Abstract, Syntax and semantics, Examples, Copyright"),
    ("572#site", "did you mean 'site.py'?"),  # a heading that starts with what was typed
    ("572#evaluation", "did you mean 'Change to evaluation order'?"),  # then one that contains it
    ("572#Zzqx", "no section 'Zzqx' in PEP 572. Sections: Abstract,"),
])
def test_an_unknown_section_names_the_closest_heading_and_the_sections(pep, target, message):
    with pytest.raises(pep.PepRefError, match=re.escape(message)):
        expand(pep, target)


def test_a_long_section_list_is_shortened_in_the_error(pep):
    names = [f"Part {n}" for n in range(1, pep.SECTIONS_LISTED_IN_ERRORS + 4)]
    transport = site(pages={572: page(*((name, "<p>text</p>") for name in names))})
    with pytest.raises(pep.PepRefError, match=re.escape(f"Part {pep.SECTIONS_LISTED_IN_ERRORS}, … (3 more)")):
        expand(pep, "572#Zzqx", transport)


@pytest.mark.parametrize("html, target, message", [
    (page(("Abstract", ""), ("Motivation", "<p>text</p>")), "572", "section 'Abstract' of PEP 572 has no text"),
    ('<section id="pep-content"><p>No sections.</p></section>', "572#Abstract",
     "the page of PEP 572 has no sections"),
])
def test_sections_without_text_are_reported(pep, html, target, message):
    with pytest.raises(pep.PepRefError, match=re.escape(message)):
        expand(pep, target, site(pages={572: html}))


@pytest.mark.parametrize("number, note", [
    (571, "Note: superseded by PEP 600."),
    (3103, "Note: Rejected: this proposal was not adopted."),
    (379, "Note: Withdrawn: this proposal was not adopted."),
    (802, "Note: Draft: this proposal has not been accepted."),
    (572, None),
    (8, None),
])
def test_the_status_note_tells_whether_the_proposal_applies(pep, number, note):
    text = expand(pep, str(number), site(pages={number: page(("Abstract", "<p>text</p>"))}))
    notes = [line for line in text.splitlines() if line.startswith("Note:")]
    assert notes == ([note] if note else [])


def test_topic_discussions_and_replaces_are_listed(pep):
    index = {"600": {"number": 600, "title": "Future manylinux", "authors": "N. J. Smith, T. Kluyver",
                     "author_names": ["N. J.  Smith", "T. Kluyver"], "status": "Final", "type": "Informational",
                     "topic": "packaging", "created": "03-May-2019", "python_version": None,
                     "discussions_to": "https://discuss.python.org/t/1638", "replaces": "513, 571", "requires": 440}}
    text = expand(pep, "600", site(pages={600: page(("Abstract", "<p>text</p>"))}, index=index))
    assert "Final · Informational · topic: packaging · created 03-May-2019\nAuthors: N. J. Smith, T. Kluyver" in text
    assert "Replaces: PEP 513, PEP 571\nRequires: PEP 440\nDiscussions-To: https://discuss.python.org/t/1638" in text


def test_an_unknown_pep_is_reported_without_fetching_a_page(pep):
    transport = site()
    with pytest.raises(pep.PepRefError, match="no PEP 9999 on peps.python.org"):
        expand(pep, "9999", transport)
    assert [call[0] for call in transport.calls] == [INDEX_URL]


@pytest.mark.parametrize("respond, message", [
    (lambda url, t: (200, json.dumps(INDEX).encode()) if url == INDEX_URL else (404, b""),
     "peps.python.org answered HTTP 404 for https://peps.python.org/pep-0572/"),
    (lambda url, t: (503, b""), "peps.python.org answered HTTP 503 for the PEP index"),
    (lambda url, t: (302, b""), "answered HTTP 302 with a redirect off https://peps.python.org; not followed"),
    (lambda url, t: (200, b"<html>not json</html>"), "unexpected answer from peps.python.org for the PEP index"),
    (lambda url, t: (200, b"{}"), "unexpected answer from peps.python.org for the PEP index"),
])
def test_bad_answers_become_reference_errors(pep, respond, message):
    with pytest.raises(pep.PepRefError, match=re.escape(message)):
        expand(pep, "572", RecordingTransport(respond))


def test_the_index_is_fetched_once_and_again_after_it_expires(pep, monkeypatch):
    transport = site()
    provider = pep.PepReferenceProvider(transport=transport)
    run(provider.expand("572"))
    run(provider.expand("572#Examples"))
    run(provider.autocomplete("57"))
    assert [call[0] for call in transport.calls].count(INDEX_URL) == 1
    monkeypatch.setattr(pep, "INDEX_TTL_SECONDS", 0)
    run(provider.expand("572"))
    assert [call[0] for call in transport.calls].count(INDEX_URL) == 2


def test_a_section_is_truncated_with_a_note(pep):
    text = expand(pep, "572", site(pages={572: page(("Abstract", "<p>" + "word " * 200 + "</p>"))}), max_chars=200)
    assert "(section truncated to 200 characters)" in text


@pytest.mark.parametrize("settings, match", [
    ({"max_chars": 10}, "settings.max_chars is 10; use a whole number from 200 to 50000"),
    ({"max_chars": "600"}, "settings.max_chars is '600'"),
    ({"timeout_seconds": 0}, "settings.timeout_seconds is 0"),
    ({"timeout_seconds": True}, "settings.timeout_seconds"),
])
def test_unusable_settings_fail_loudly_before_any_request(pep, settings, match):
    transport = site()
    with pytest.raises(pep.PepRefError, match=re.escape(match)):
        expand(pep, "572", transport, **settings)
    assert transport.calls == []


def test_settings_are_read_on_every_call(pep):
    settings = {"timeout_seconds": 4}
    transport = site()
    provider = pep.PepReferenceProvider(get_config=lambda k, d=None: settings.get(k, d), transport=transport)
    run(provider.expand("572"))
    settings["timeout_seconds"] = 9
    provider._index = None
    run(provider.expand("572"))
    assert [call[1] for call in transport.calls if call[0] == INDEX_URL] == [4, 9]


@pytest.mark.parametrize("error, message", [
    (urllib.error.URLError("offline"), "could not reach peps.python.org: offline"),
    (TimeoutError("slow"), "peps.python.org did not answer within 10s"),
    (ValueError("response larger than 4194304 bytes"), "unreadable answer from peps.python.org: response larger"),
    (ConnectionResetError("reset"), "could not reach peps.python.org: reset"),
])
def test_transport_failures_become_reference_errors(pep, error, message):
    def failing(*_args):
        raise error

    with pytest.raises(pep.PepRefError, match=re.escape(message)):
        expand(pep, "572", failing)


def test_a_slow_index_leaves_no_time_for_the_page(pep, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(pep.time, "monotonic", lambda: clock[0])

    def respond(url, timeout):
        clock[0] += 9.8  # the index used up almost all of the 10 s
        return 200, json.dumps(INDEX).encode()

    transport = RecordingTransport(respond)
    with pytest.raises(pep.PepRefError, match="did not answer within 10s"):
        expand(pep, "572", transport)
    assert [call[0] for call in transport.calls] == [INDEX_URL]


# -- through Hermes's expander -----------------------------------------------------------------

def test_hermes_attaches_the_pep_and_reports_failures(pep, tmp_path: Path):
    register_context_reference_provider(pep.PepReferenceProvider(transport=site()))
    result = run(preprocess_context_references_async(
        "Explain @pep:572#site.py and @pep:9999.", cwd=tmp_path, context_length=100_000,
    ))
    assert "--- Attached Context ---" in result.message
    assert "Section: Examples › Examples from the Python standard library › site.py" in result.message
    assert "@pep:9999.: plugin expansion error: no PEP 9999 on peps.python.org" in result.message


def test_a_hung_request_does_not_hold_hermes_sync_expander(pep, tmp_path: Path):
    """The CLI and TUI expand through the sync wrapper, which runs asyncio.run() and joins the
    loop's default executor on exit; the request must not be parked there."""
    from agent.context_references import preprocess_context_references

    release = threading.Event()

    def hang(*_args):
        release.wait(10)
        return 200, b"{}"

    register_context_reference_provider(pep.PepReferenceProvider(get_config=config(timeout_seconds=1), transport=hang))
    started = time.monotonic()
    try:
        result = preprocess_context_references("see @pep:572", cwd=tmp_path, context_length=100_000)
    finally:
        release.set()
    assert time.monotonic() - started < 5
    assert "did not answer within 1s" in result.message


# -- autocomplete ------------------------------------------------------------------------------

def test_autocomplete_by_number_puts_the_exact_pep_first(pep):
    transport = site()
    items = run(pep.PepReferenceProvider(transport=transport).autocomplete("57"))
    assert [i.text for i in items] == ["570", "571", "572", "573", "577"]
    assert transport.calls == [(INDEX_URL, pep.AUTOCOMPLETE_TIMEOUT_SECONDS)]
    items = run(pep.PepReferenceProvider(transport=site()).autocomplete("PEP-8"))
    assert [i.text for i in items] == ["8", "802"]
    assert (items[0].display, items[0].meta) == ("PEP 8 — Style Guide for Python Code", "Active")


def test_autocomplete_by_title_words(pep):
    provider = pep.PepReferenceProvider(transport=site())
    items = run(provider.autocomplete('"assignment expr'))
    assert [(i.text, i.meta) for i in items] == [("379", "Withdrawn"), ("572", "Final"), ("577", "Withdrawn")]
    assert [i.text for i in run(provider.autocomplete("style python"))] == ["8"]  # every word, not any


def test_autocomplete_values_parse_back_and_the_limit_is_capped(pep):
    index = {str(n): {"number": n, "title": f"Title {n}", "status": "Final"} for n in range(100, 160)}
    provider = pep.PepReferenceProvider(transport=site(index=index))
    items = run(provider.autocomplete("1", limit=50))
    assert len(items) == pep.AUTOCOMPLETE_MAX_ITEMS
    register_context_reference_provider(provider)
    for item in items:
        [ref] = parse_context_references(f"@pep:{item.text} ")
        assert pep.parse_target(ref.target) == (int(item.text), "")


@pytest.mark.parametrize("query", ["", "  ", '"', "8#", "8#Nam"])
def test_autocomplete_skips_empty_queries_and_sections_without_a_request(pep, query):
    transport = site()
    assert run(pep.PepReferenceProvider(transport=transport).autocomplete(query)) == []
    assert transport.calls == []


def test_autocomplete_stays_quiet_on_errors(pep):
    def failing(*_args):
        raise urllib.error.URLError("offline")

    assert run(pep.PepReferenceProvider(transport=failing).autocomplete("57")) == []
    assert run(pep.PepReferenceProvider(transport=RecordingTransport(lambda *a: (500, b""))).autocomplete("57")) == []


# -- HTTP layer --------------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    routes: dict = {}
    seen: list = []

    def log_message(self, *_args):
        pass

    def do_GET(self):  # noqa: N802
        type(self).seen.append({"path": self.path, "ua": self.headers.get("User-Agent")})
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


def test_http_get_sends_the_user_agent_and_returns_error_statuses(pep, http_server):
    _Handler.routes = {"/ok": (200, {}, b"<html>page</html>"), "/gone": (404, {}, b"not found")}
    assert pep.http_get(f"{http_server}/ok", 5) == (200, b"<html>page</html>")
    assert pep.http_get(f"{http_server}/gone", 5) == (404, b"")
    assert {s["ua"] for s in _Handler.seen} == {pep.USER_AGENT}


def test_http_get_refuses_oversized_bodies(pep, http_server, monkeypatch):
    monkeypatch.setattr(pep, "MAX_RESPONSE_BYTES", 16)
    _Handler.routes = {"/big": (200, {}, b"a" * 64)}
    with pytest.raises(ValueError, match="larger than 16 bytes"):
        pep.http_get(f"{http_server}/big", 5)


def test_http_get_bounds_a_trickling_answer(pep, http_server):
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="no complete answer within 1s"):
        pep.http_get(f"{http_server}/drip", 1)
    assert time.monotonic() - started < 3  # each byte arrives well inside the 1s socket timeout


def test_http_get_does_not_follow_a_redirect_off_https(pep, http_server):
    _Handler.routes = {"/moved": (302, {"Location": f"{http_server}/elsewhere"}, b"")}
    assert pep.http_get(f"{http_server}/moved", 5) == (302, b"")
    assert [s["path"] for s in _Handler.seen] == ["/moved"]


@pytest.mark.parametrize("new_url, allowed", [
    ("https://peps.python.org/pep-0008/", True),
    ("https://evil.example/pep-0008/", False),
    ("http://peps.python.org/pep-0008/", False),
])
def test_redirects_stay_on_the_same_https_host(pep, new_url, allowed):
    handler = pep._SameHostRedirectHandler()
    request = urllib.request.Request("https://peps.python.org/pep-8/")
    if allowed:
        assert handler.redirect_request(request, None, 301, "Moved", {}, new_url).full_url == new_url
    else:
        with pytest.raises(urllib.error.HTTPError, match="refused redirect"):
            handler.redirect_request(request, None, 301, "Moved", {}, new_url)


# -- packaging ---------------------------------------------------------------------------------

def test_manifest_version_matches_the_module(pep):
    try:
        import hermes_yaml as yaml  # Hermes after the Sep 2026 YAML switch
    except ImportError:
        import yaml
    manifest = yaml.safe_load((PEP_DIR / "plugin.yaml").read_text(encoding="utf-8-sig"))
    assert manifest["name"] == "pep-ref" and str(manifest["version"]) == pep.__version__
    assert pep.USER_AGENT.startswith(f"hermes-pep-ref/{pep.__version__} ")


def test_register_adds_the_pep_prefix(pep):
    registered = []

    class Ctx:
        def get_config(self, key, default=None):
            return default

        def register_context_reference(self, provider):
            registered.append(provider)

    pep.register(Ctx())
    assert [p.prefix for p in registered] == ["pep"]
