"""Load the plugins through Hermes's own plugin manager in a fresh process and exercise them."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from conftest import OSV_DIR, WIKI_DIR

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


def _write_home(home: Path, plugins: dict[str, str]) -> None:
    """Install each ``{plugin_dir_name: settings_yaml}`` into ``home`` and enable it."""
    entries = ""
    for name, settings in plugins.items():
        source = {"wiki-ref": WIKI_DIR, "osv-ref": OSV_DIR}[name]
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
