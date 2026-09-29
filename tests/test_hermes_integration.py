"""Load wiki-ref through Hermes's own plugin manager in a fresh process and exercise it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from conftest import PLUGIN_DIR

PROBE = textwrap.dedent('''
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
    with open(os.environ["WIKI_REF_PROBE_OUT"], "w", encoding="utf-8") as fh:
        json.dump(out, fh, default=str)
''')


def _write_home(home: Path, settings: str) -> None:
    shutil.copytree(PLUGIN_DIR, home / "plugins" / "wiki-ref", ignore=shutil.ignore_patterns("__pycache__"))
    (home / "config.yaml").write_text(
        "plugins:\n"
        "  enabled: [wiki-ref]\n"
        "  entries:\n"
        "    wiki-ref:\n"
        "      settings:\n" + textwrap.indent(settings, "        "),
        encoding="utf-8",
    )


def _probe(home: Path, cwd: Path, **extra_env: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OPENAI", "ANTHROPIC", "OPENROUTER", "HERMES_"))}
    env.update(extra_env)
    env["HERMES_HOME"] = str(home)
    env["WIKI_REF_PROBE_OUT"] = str(cwd / "probe.json")
    proc = subprocess.run(
        [sys.executable, "-c", PROBE], cwd=cwd, env=env, capture_output=True, text=True, timeout=240,
    )
    assert proc.returncode == 0 and (cwd / "probe.json").is_file(), (
        f"probe failed ({proc.returncode})\nstdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-3000:]}"
    )
    return json.loads((cwd / "probe.json").read_text(encoding="utf-8-sig"))


def test_hermes_loads_the_plugin_and_applies_its_settings(tmp_path: Path):
    home, work = tmp_path / "home", tmp_path / "work"
    work.mkdir()
    _write_home(home, "language: de\nmax_chars: 500\n")
    out = _probe(home, work)

    assert out["provider"] == "WikiReferenceProvider"
    assert out["listed"]["enabled"] is True
    query_calls = [c for c in out["calls"] if c["params"].get("action") == "query"]
    assert [c["url"] for c in query_calls] == ["https://de.wikipedia.org/w/api.php"]
    assert query_calls[0]["params"]["titles"] == "Berlin"
    assert "--- Attached Context ---" in out["message"]
    assert "Wikipedia (de): Berlin" in out["message"]
    assert "Berlin ist die Hauptstadt." in out["message"]


def test_a_settings_change_applies_without_a_restart(tmp_path: Path):
    home, work = tmp_path / "home", tmp_path / "work"
    work.mkdir()
    _write_home(home, "language: de\n")
    out = _probe(home, work, WIKI_REF_PROBE_RELANGUAGE="1")

    query_urls = [c["url"] for c in out["calls"] if c["params"].get("action") == "query"]
    assert query_urls == ["https://de.wikipedia.org/w/api.php", "https://fr.wikipedia.org/w/api.php"]


def test_hermes_autocomplete_offers_article_titles(tmp_path: Path):
    home, work = tmp_path / "home", tmp_path / "work"
    work.mkdir()
    _write_home(home, "language: de\n")
    out = _probe(home, work)

    items = out["complete"]["items"]
    assert [i["text"] for i in items] == ["@wiki:Berlin", "@wiki:Berlin_Wall"]
    assert [i["display"] for i in items] == ["Berlin", "Berlin Wall"]
    assert {i["meta"] for i in items} == {"Wikipedia (de)"}
    search_calls = [c for c in out["calls"] if c["params"].get("action") == "opensearch"]
    assert search_calls and search_calls[0]["params"]["search"] == "Berl"
