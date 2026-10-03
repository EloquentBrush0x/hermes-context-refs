"""Load the plugins through Hermes's own plugin manager in a fresh process and exercise them."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from conftest import DOI_DIR, GH_DIR, OSV_DIR, PEP_DIR, RFC_DIR, WIKI_DIR

WIKI_PROBE = textwrap.dedent('''
    import asyncio, json, os, sys
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    from agent.context_references import get_context_reference_providers, preprocess_context_references

    discover_plugins(force=True)
    listed = {p["name"]: p for p in get_plugin_manager().list_plugins()}
    provider = get_context_reference_providers().get("wiki")
    out = {"listed": listed.get("wiki-ref"), "provider": type(provider).__name__ if provider else None}
    if provider is not None:
        calls = []
        def fake(url, params, timeout):
            calls.append({"url": url, "params": params, "timeout": timeout})
            if params.get("action") == "opensearch":
                return [params["search"], ["Berlin", "Berlin Wall"], ["", ""], ["", ""]]
            return {"query": {"pages": [{"title": "Berlin", "fullurl": "https://de.wikipedia.org/wiki/Berlin",
                                         "extract": "Berlin ist die Hauptstadt."}]}}
        provider._transport = fake
        result = preprocess_context_references("Fasse @wiki:Berlin zusammen", cwd=os.getcwd(), context_length=100000)
        out["message"] = result.message
        if os.environ.get("WIKI_REF_PROBE_RELANGUAGE"):
            # Same process, edited config.yaml: the next reference must use the new setting.
            path = os.path.join(os.environ["HERMES_HOME"], "config.yaml")
            with open(path, encoding="utf-8-sig") as fh:
                text = fh.read()
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text.replace("language: de", "language: fr"))
            preprocess_context_references("Résume @wiki:Berlin", cwd=os.getcwd(), context_length=100000)
        from tui_gateway import server
        reply = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "complete.path",
                                       "params": {"word": "@wiki:Berl"}})
        out["complete"] = reply.get("result", reply)
        out["calls"] = calls
    # tui_gateway points stdout at stderr (stdout is its JSON-RPC channel), so write a file.
    with open(os.environ["PROBE_OUT"], "w", encoding="utf-8") as fh:
        json.dump(out, fh, default=str)
''')

OSV_PROBE = textwrap.dedent('''
    import json, os
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    from agent.context_references import get_context_reference_providers, preprocess_context_references

    discover_plugins(force=True)
    listed = {p["name"]: p for p in get_plugin_manager().list_plugins()}
    providers = get_context_reference_providers()
    out = {"listed": {name: listed.get(name) for name in ("osv-ref", "wiki-ref")},
           "providers": {prefix: type(p).__name__ for prefix, p in providers.items()}}
    calls = []
    record = {"id": "CVE-2024-3651", "summary": "idna DoS", "details": "word " * 200,
              "affected": [{"package": {"ecosystem": "PyPI", "name": "idna"},
                            "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "3.7"}]}]}]}
    def fake(url, timeout):
        calls.append({"url": url, "timeout": timeout})
        return (200, record) if url.endswith("/CVE-2024-3651") else (404, {"message": "Vulnerability not found"})
    for prefix in ("cve", "ghsa", "osv"):
        if prefix in providers:
            providers[prefix]._transport = fake
    result = preprocess_context_references("Am I affected by @cve:cve-2024-3651?", cwd=os.getcwd(),
                                           context_length=100000)
    out["message"] = result.message
    from tui_gateway import server
    reply = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "complete.path",
                                   "params": {"word": "@cve:CVE-2024"}})
    out["complete"] = reply.get("result", reply)
    out["calls"] = calls
    with open(os.environ["PROBE_OUT"], "w", encoding="utf-8") as fh:
        json.dump(out, fh, default=str)
''')


GH_PROBE = textwrap.dedent('''
    import json, os
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    from agent.context_references import get_context_reference_providers, preprocess_context_references

    discover_plugins(force=True)
    listed = {p["name"]: p for p in get_plugin_manager().list_plugins()}
    providers = get_context_reference_providers()
    out = {"listed": listed.get("gh-ref"), "providers": {k: type(p).__name__ for k, p in providers.items()}}
    calls = []
    issue = {"number": 7, "state": "open", "comments": 3, "title": "Crash on start", "body": "word " * 200,
             "html_url": "https://github.com/o/r/issues/7",
             "comments_url": "https://api.github.com/repos/o/r/issues/7/comments"}
    comments = [{"user": {"login": f"u{i}"}, "created_at": "2026-01-01T00:00:00Z", "body": f"c{i}"} for i in range(3)]
    def fake(url, timeout):
        calls.append({"url": url, "timeout": timeout})
        if "/comments" in url:  # per_page=2: page 1 holds u0, u1; page 2 holds u2
            return 200, {}, comments[:2] if url.endswith("page=1") else comments[2:]
        return 200, {}, issue
    if "gh" in providers:
        providers["gh"]._transport = fake
    result = preprocess_context_references("Why does @gh:o/r#7 happen?", cwd=os.getcwd(), context_length=100000)
    out["message"] = result.message
    from tui_gateway import server
    reply = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "complete.path",
                                   "params": {"word": "@gh:o/r"}})
    out["complete"] = reply.get("result", reply)
    out["calls"] = calls
    with open(os.environ["PROBE_OUT"], "w", encoding="utf-8") as fh:
        json.dump(out, fh, default=str)
''')


PEP_PROBE = textwrap.dedent('''
    import json, os
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    from agent.context_references import get_context_reference_providers, preprocess_context_references

    discover_plugins(force=True)
    listed = {p["name"]: p for p in get_plugin_manager().list_plugins()}
    providers = get_context_reference_providers()
    out = {"listed": listed.get("pep-ref"), "providers": {k: type(p).__name__ for k, p in providers.items()}}
    calls = []
    index = {str(n): {"number": n, "title": t, "status": "Final", "type": "Standards Track"}
             for n, t in ((570, "Positional-Only Parameters"), (572, "Assignment Expressions"), (8, "Style Guide"))}
    page = ('<main><section id="pep-content"><h1>PEP 572</h1><section id="abstract">'
            '<h2><a class="toc-backref" href="#abstract">Abstract</a></h2><p>' + "word " * 200 + '</p></section>'
            '</section></main>')
    def fake(url, timeout):
        calls.append({"url": url, "timeout": timeout})
        if url.endswith("/api/peps.json"):
            return 200, json.dumps(index).encode()
        return (200, page.encode()) if url.endswith("/pep-0572/") else (404, b"")
    if "pep" in providers:
        providers["pep"]._transport = fake
    result = preprocess_context_references("Explain @pep:572 briefly", cwd=os.getcwd(), context_length=100000)
    out["message"] = result.message
    from tui_gateway import server
    reply = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "complete.path",
                                   "params": {"word": "@pep:57"}})
    out["complete"] = reply.get("result", reply)
    out["calls"] = calls
    with open(os.environ["PROBE_OUT"], "w", encoding="utf-8") as fh:
        json.dump(out, fh, default=str)
''')


RFC_PROBE = textwrap.dedent('''
    import json, os
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    from agent.context_references import get_context_reference_providers, preprocess_context_references

    discover_plugins(force=True)
    listed = {p["name"]: p for p in get_plugin_manager().list_plugins()}
    providers = get_context_reference_providers()
    out = {"listed": listed.get("rfc-ref"), "providers": {k: type(p).__name__ for k, p in providers.items()}}
    calls = []
    record = {"doc_id": "RFC9110", "title": "HTTP Semantics", "status": "INTERNET STANDARD",
              "format": ["TEXT", "HTML"], "abstract": "The Hypertext Transfer Protocol (HTTP) is a stateless protocol."}
    text = "9.  Methods\\n\\n" + "   " + "method " * 200 + "\\n"
    def fake(url, timeout):
        calls.append({"url": url, "timeout": timeout})
        if url.endswith("/rfc9110.json"):
            return 200, json.dumps(record).encode()
        return (200, text.encode()) if url.endswith("/rfc9110.txt") else (404, b"")
    if "rfc" in providers:
        providers["rfc"]._transport = fake
    result = preprocess_context_references("Check @rfc:9110 and @rfc:26", cwd=os.getcwd(), context_length=100000)
    out["section"] = preprocess_context_references("See @rfc:9110#9", cwd=os.getcwd(), context_length=100000).message
    out["message"] = result.message
    from tui_gateway import server
    reply = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "complete.path",
                                   "params": {"word": "@rfc:91"}})
    out["complete"] = reply.get("result", reply)
    out["calls"] = calls
    with open(os.environ["PROBE_OUT"], "w", encoding="utf-8") as fh:
        json.dump(out, fh, default=str)
''')


DOI_PROBE = textwrap.dedent('''
    import json, os
    from hermes_cli.plugins import discover_plugins, get_plugin_manager
    from agent.context_references import get_context_reference_providers, preprocess_context_references

    discover_plugins(force=True)
    listed = {p["name"]: p for p in get_plugin_manager().list_plugins()}
    providers = get_context_reference_providers()
    out = {"listed": listed.get("doi-ref"), "providers": {k: type(p).__name__ for k, p in providers.items()}}
    calls = []
    record = {"message": {"DOI": "10.1038/nature14539", "title": ["Deep learning"],
                          "abstract": "<jats:p>" + "word " * 200 + "</jats:p>"}}
    def fake(url, timeout):
        calls.append({"url": url, "timeout": timeout})
        return (200, json.dumps(record).encode()) if url.endswith("/works/10.1038%2Fnature14539") else (404, b"")
    if "doi" in providers:
        providers["doi"]._transport = fake
    result = preprocess_context_references("Check @doi:10.1038/nature14539 and @doi:10.9999/nope", cwd=os.getcwd(),
                                           context_length=100000)
    out["message"] = result.message
    from tui_gateway import server
    reply = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "complete.path",
                                   "params": {"word": "@doi:10.10"}})
    out["complete"] = reply.get("result", reply)
    out["calls"] = calls
    with open(os.environ["PROBE_OUT"], "w", encoding="utf-8") as fh:
        json.dump(out, fh, default=str)
''')


def _write_home(home: Path, plugins: dict[str, str]) -> None:
    """Install each ``{plugin_dir_name: settings_yaml}`` into ``home`` and enable it."""
    entries = ""
    for name, settings in plugins.items():
        source = {
            "wiki-ref": WIKI_DIR, "osv-ref": OSV_DIR, "gh-ref": GH_DIR, "pep-ref": PEP_DIR, "rfc-ref": RFC_DIR,
            "doi-ref": DOI_DIR,
        }[name]
        shutil.copytree(source, home / "plugins" / name, ignore=shutil.ignore_patterns("__pycache__"))
        entries += f"    {name}:\n      settings:\n" + textwrap.indent(settings or "{}\n", "        ")
    (home / "config.yaml").write_text(
        f"plugins:\n  enabled: [{', '.join(plugins)}]\n  entries:\n" + entries,
        encoding="utf-8",
    )


def _probe(probe: str, home: Path, cwd: Path, **extra_env: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "HERMES_"))}
    env.update(extra_env)
    env["HERMES_HOME"] = str(home)
    env["PROBE_OUT"] = str(cwd / "probe.json")
    proc = subprocess.run(
        [sys.executable, "-c", probe], cwd=cwd, env=env, capture_output=True, text=True, timeout=240,
    )
    assert proc.returncode == 0 and (cwd / "probe.json").is_file(), (
        f"probe failed ({proc.returncode})\nstdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-3000:]}"
    )
    return json.loads((cwd / "probe.json").read_text(encoding="utf-8-sig"))


def _dirs(tmp_path: Path) -> tuple[Path, Path]:
    home, work = tmp_path / "home", tmp_path / "work"
    work.mkdir()
    return home, work


# -- wiki-ref ------------------------------------------------------------------------------------

def test_hermes_loads_the_plugin_and_applies_its_settings(tmp_path: Path):
    home, work = _dirs(tmp_path)
    _write_home(home, {"wiki-ref": "language: de\nmax_chars: 500\n"})
    out = _probe(WIKI_PROBE, home, work)

    assert out["provider"] == "WikiReferenceProvider"
    assert out["listed"]["enabled"] is True
    query_calls = [c for c in out["calls"] if c["params"].get("action") == "query"]
    assert [c["url"] for c in query_calls] == ["https://de.wikipedia.org/w/api.php"]
    assert query_calls[0]["params"]["titles"] == "Berlin"
    assert "--- Attached Context ---" in out["message"]
    assert "Wikipedia (de): Berlin" in out["message"]
    assert "Berlin ist die Hauptstadt." in out["message"]


def test_a_settings_change_applies_without_a_restart(tmp_path: Path):
    home, work = _dirs(tmp_path)
    _write_home(home, {"wiki-ref": "language: de\n"})
    out = _probe(WIKI_PROBE, home, work, WIKI_REF_PROBE_RELANGUAGE="1")

    query_urls = [c["url"] for c in out["calls"] if c["params"].get("action") == "query"]
    assert query_urls == ["https://de.wikipedia.org/w/api.php", "https://fr.wikipedia.org/w/api.php"]


def test_hermes_autocomplete_offers_article_titles(tmp_path: Path):
    home, work = _dirs(tmp_path)
    _write_home(home, {"wiki-ref": "language: de\n"})
    out = _probe(WIKI_PROBE, home, work)

    items = out["complete"]["items"]
    assert [i["text"] for i in items] == ["@wiki:Berlin", "@wiki:Berlin_Wall"]
    assert [i["display"] for i in items] == ["Berlin", "Berlin Wall"]
    assert {i["meta"] for i in items} == {"Wikipedia (de)"}
    search_calls = [c for c in out["calls"] if c["params"].get("action") == "opensearch"]
    assert search_calls and search_calls[0]["params"]["search"] == "Berl"


# -- osv-ref -------------------------------------------------------------------------------------

def test_hermes_loads_osv_ref_next_to_wiki_ref_and_applies_its_settings(tmp_path: Path):
    home, work = _dirs(tmp_path)
    _write_home(home, {"osv-ref": "max_chars: 300\ntimeout_seconds: 7\n", "wiki-ref": ""})
    out = _probe(OSV_PROBE, home, work)

    assert out["listed"]["osv-ref"]["enabled"] is True and out["listed"]["wiki-ref"]["enabled"] is True
    assert out["providers"] == {
        "cve": "CveReferenceProvider", "ghsa": "GhsaReferenceProvider", "osv": "OsvReferenceProvider",
        "wiki": "WikiReferenceProvider",
    }
    assert out["calls"][0] == {"url": "https://api.osv.dev/v1/vulns/CVE-2024-3651", "timeout": 7}
    message = out["message"]
    assert "--- Attached Context ---" in message
    assert "OSV: CVE-2024-3651 — idna DoS" in message
    assert "- PyPI idna: < 3.7 (fixed in 3.7) [CVE-2024-3651]" in message
    assert "(details truncated to 300 characters)" in message


def test_osv_ref_autocomplete_is_empty_and_offline(tmp_path: Path):
    home, work = _dirs(tmp_path)
    _write_home(home, {"osv-ref": ""})
    out = _probe(OSV_PROBE, home, work)

    assert out["complete"]["items"] == []
    assert [c["url"] for c in out["calls"]] == ["https://api.osv.dev/v1/vulns/CVE-2024-3651"]


# -- gh-ref --------------------------------------------------------------------------------------

def test_hermes_loads_gh_ref_next_to_the_others_and_applies_its_settings(tmp_path: Path):
    home, work = _dirs(tmp_path)
    settings = "max_chars: 300\nmax_comments: 2\ntimeout_seconds: 7\n"
    _write_home(home, {"gh-ref": settings, "osv-ref": "", "wiki-ref": ""})
    out = _probe(GH_PROBE, home, work)

    assert out["listed"]["enabled"] is True
    assert out["providers"] == {
        "cve": "CveReferenceProvider", "ghsa": "GhsaReferenceProvider", "osv": "OsvReferenceProvider",
        "wiki": "WikiReferenceProvider", "gh": "GitHubReferenceProvider",
    }
    # 3 comments, 2 wanted: the last page holds one, so the page before it is read too.
    assert out["calls"][0]["url"] == "https://api.github.com/repos/o/r/issues/7"
    assert sorted(c["url"] for c in out["calls"][1:]) == [
        "https://api.github.com/repos/o/r/issues/7/comments?per_page=2&page=1",
        "https://api.github.com/repos/o/r/issues/7/comments?per_page=2&page=2",
    ]
    assert out["calls"][0]["timeout"] == 7
    message = out["message"]
    assert "--- Attached Context ---" in message
    assert "GitHub issue o/r#7: Crash on start" in message
    assert "(description truncated to 300 characters)" in message
    assert "Comments (the last 2 of 3, oldest first):\n--- @u1 · 2026-01-01\nc1\n--- @u2 · 2026-01-01\nc2" in message
    assert "@u0" not in message
    assert out["complete"]["items"] == []


# -- pep-ref -------------------------------------------------------------------------------------

def test_hermes_loads_pep_ref_applies_its_settings_and_autocompletes(tmp_path: Path):
    home, work = _dirs(tmp_path)
    _write_home(home, {"pep-ref": "max_chars: 300\ntimeout_seconds: 7\n", "wiki-ref": ""})
    out = _probe(PEP_PROBE, home, work)

    assert out["listed"]["enabled"] is True
    assert out["providers"] == {"wiki": "WikiReferenceProvider", "pep": "PepReferenceProvider"}
    # The index is read once: for the reference, then again from the cache for autocomplete.
    assert out["calls"] == [
        {"url": "https://peps.python.org/api/peps.json", "timeout": 7},
        {"url": "https://peps.python.org/pep-0572/", "timeout": out["calls"][1]["timeout"]},
    ]
    assert 6 < out["calls"][1]["timeout"] <= 7  # what is left of the reference's 7 s
    message = out["message"]
    assert "--- Attached Context ---" in message
    assert "PEP 572 — Assignment Expressions\nhttps://peps.python.org/pep-0572/#abstract" in message
    assert "(section truncated to 300 characters)" in message
    items = out["complete"]["items"]
    assert [i["text"] for i in items] == ["@pep:570", "@pep:572"]
    assert items[1]["display"] == "PEP 572 — Assignment Expressions" and items[1]["meta"] == "Final"


# -- rfc-ref -------------------------------------------------------------------------------------

def test_hermes_loads_rfc_ref_next_to_pep_ref_and_applies_its_settings(tmp_path: Path):
    home, work = _dirs(tmp_path)
    _write_home(home, {"rfc-ref": "timeout_seconds: 7\nmax_chars: 300\n", "pep-ref": ""})
    out = _probe(RFC_PROBE, home, work)

    assert out["listed"]["enabled"] is True
    assert out["providers"] == {"pep": "PepReferenceProvider", "rfc": "RfcReferenceProvider"}
    assert sorted((c["url"], c["timeout"]) for c in out["calls"][:2]) == [
        ("https://www.rfc-editor.org/rfc/rfc26.json", 7), ("https://www.rfc-editor.org/rfc/rfc9110.json", 7),
    ]
    assert [c["url"] for c in out["calls"][2:]] == [
        "https://www.rfc-editor.org/rfc/rfc9110.json", "https://www.rfc-editor.org/rfc/rfc9110.txt",
    ]
    assert "Section: 9. Methods" in out["section"] and "(section truncated to 300 characters)" in out["section"]
    message = out["message"]
    assert "--- Attached Context ---" in message
    assert "RFC 9110: HTTP Semantics\nhttps://www.rfc-editor.org/rfc/rfc9110.html" in message
    assert "@rfc:26: plugin expansion error: the RFC Editor has no RFC 26" in message
    assert out["complete"]["items"] == []


# -- doi-ref -------------------------------------------------------------------------------------

def test_hermes_loads_doi_ref_next_to_rfc_ref_and_applies_its_settings(tmp_path: Path):
    home, work = _dirs(tmp_path)
    _write_home(home, {"doi-ref": "timeout_seconds: 7\nmax_chars: 300\n", "rfc-ref": ""})
    out = _probe(DOI_PROBE, home, work)

    assert out["listed"]["enabled"] is True
    assert out["providers"] == {"doi": "DoiReferenceProvider", "rfc": "RfcReferenceProvider"}
    assert sorted(c["url"] for c in out["calls"]) == [
        "https://api.crossref.org/works/10.1038%2Fnature14539",
        "https://api.crossref.org/works/10.9999%2Fnope",
        "https://api.datacite.org/dois/10.9999%2Fnope",
    ]
    assert {c["timeout"] for c in out["calls"] if "crossref" in c["url"]} == {7}
    message = out["message"]
    assert "--- Attached Context ---" in message
    assert "DOI 10.1038/nature14539: Deep learning\nhttps://doi.org/10.1038/nature14539" in message
    assert "(abstract truncated to 300 characters)" in message
    assert "@doi:10.9999/nope: plugin expansion error: neither Crossref nor DataCite has DOI 10.9999/nope" in message
    assert out["complete"]["items"] == []
