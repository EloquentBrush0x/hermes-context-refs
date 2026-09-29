"""A real `hermes chat` turn: the referenced article or advisory must reach the model in the user message."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from conftest import GH_DIR, OSV_DIR, TESTS_DIR, WIKI_DIR
from fakes import MODEL_ID, REPLY, FakeServer

_SCRUBBED_ENV_PREFIXES = ("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_", "HERMES_")


def _home(home: Path, origin: str, settings: str = "", plugins: tuple[Path, ...] = (WIKI_DIR,)) -> None:
    for plugin in plugins:
        shutil.copytree(plugin, home / "plugins" / plugin.name, ignore=shutil.ignore_patterns("__pycache__"))
    (home / "config.yaml").write_text(
        "model:\n"
        "  provider: custom\n"
        f"  base_url: {origin}/v1\n"
        f"  default: {MODEL_ID}\n"
        "  context_length: 128000\n"
        "agent:\n"
        "  api_max_retries: 1\n"
        "plugins:\n"
        f"  enabled: [{', '.join(p.name for p in plugins)}]\n" + settings,
        encoding="utf-8",
    )
    (home / ".env").write_text("OPENAI_API_KEY=sk-fake-e2e\n", encoding="utf-8")


def _chat(home: Path, work: Path, origin: str, query: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(_SCRUBBED_ENV_PREFIXES)}
    env["HERMES_HOME"] = str(home)
    env["CONTEXT_REFS_TEST_ORIGIN"] = origin
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(TESTS_DIR / "e2e_site"), os.environ.get("PYTHONPATH")]))
    env["NO_COLOR"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "chat", "-q", query, "--oneshot"],
        cwd=work, env=env, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
    )


def test_a_chat_turn_sends_the_article_to_the_model(tmp_path: Path):
    home, work = tmp_path / "home", tmp_path / "work"
    work.mkdir()
    with FakeServer() as fake:
        _home(home, fake.origin)
        proc = _chat(home, work, fake.origin, "Who was @wiki:Alan_Turing? Answer in one line.")

        detail = f"exit {proc.returncode}\nstdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        assert proc.returncode == 0, detail
        assert REPLY in proc.stdout, detail
        [lookup] = fake.wiki_requests
        assert lookup["language"] == "en" and lookup["params"]["titles"] == "Alan Turing", lookup
        assert lookup["user_agent"].startswith("hermes-wiki-ref/"), lookup
        [first_turn, *_] = [m for m in fake.user_messages() if "@wiki:Alan_Turing" in m]
        assert "--- Attached Context ---" in first_turn, first_turn
        assert "Wikipedia (en): Alan Turing — English computer scientist (1912–1954)" in first_turn
        assert "Alan Mathison Turing was an English mathematician and computer scientist." in first_turn


def test_a_missing_article_reaches_the_model_as_a_warning(tmp_path: Path):
    home, work = tmp_path / "home", tmp_path / "work"
    work.mkdir()
    settings = "  entries:\n    wiki-ref:\n      settings:\n        language: de\n"
    with FakeServer() as fake:
        _home(home, fake.origin, settings)
        proc = _chat(home, work, fake.origin, "Erkläre @wiki:Gibt_es_nicht kurz.")

        detail = f"exit {proc.returncode}\nstdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        assert proc.returncode == 0, detail
        assert [r["language"] for r in fake.wiki_requests] == ["de"], fake.wiki_requests
        [first_turn, *_] = [m for m in fake.user_messages() if "@wiki:Gibt_es_nicht" in m]
        assert "--- Context Warnings ---" in first_turn, first_turn
        assert "no de.wikipedia.org article titled 'Gibt es nicht'" in first_turn, first_turn


def test_a_chat_turn_sends_the_advisory_to_the_model(tmp_path: Path):
    home, work = tmp_path / "home", tmp_path / "work"
    work.mkdir()
    with FakeServer() as fake:
        _home(home, fake.origin, plugins=(OSV_DIR, WIKI_DIR))
        proc = _chat(home, work, fake.origin, "Does @cve:cve-2024-3651 affect idna 3.6? Answer in one line.")

        detail = f"exit {proc.returncode}\nstdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        assert proc.returncode == 0, detail
        assert REPLY in proc.stdout, detail
        ids = [r["id"] for r in fake.osv_requests]
        assert ids[0] == "CVE-2024-3651" and sorted(ids[1:]) == ["GHSA-jjg7-2v4v-x38h", "PYSEC-2024-60"], ids
        assert {r["user_agent"].split(" ")[0] for r in fake.osv_requests} == {"hermes-osv-ref/1.0.0"}
        [first_turn, *_] = [m for m in fake.user_messages() if "@cve:cve-2024-3651" in m]
        assert "--- Attached Context ---" in first_turn, first_turn
        assert "OSV: CVE-2024-3651 — Denial of Service via Quadratic Complexity in kjd/idna" in first_turn
        assert "- PyPI idna: < 3.7 (fixed in 3.7) [GHSA-jjg7-2v4v-x38h]" in first_turn
        # PYSEC-2024-60 is not among the canned records: the model is told, the rest still arrives.
        assert "Aliases without an OSV record: PYSEC-2024-60" in first_turn


def test_an_unknown_advisory_reaches_the_model_as_a_warning(tmp_path: Path):
    home, work = tmp_path / "home", tmp_path / "work"
    work.mkdir()
    with FakeServer() as fake:
        _home(home, fake.origin, plugins=(OSV_DIR,))
        proc = _chat(home, work, fake.origin, "Explain @ghsa:GHSA-aaaa-bbbb-cccc briefly.")

        detail = f"exit {proc.returncode}\nstdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        assert proc.returncode == 0, detail
        assert [r["id"] for r in fake.osv_requests] == ["GHSA-aaaa-bbbb-cccc"], fake.osv_requests
        [first_turn, *_] = [m for m in fake.user_messages() if "@ghsa:GHSA-aaaa-bbbb-cccc" in m]
        assert "--- Context Warnings ---" in first_turn, first_turn
        assert "OSV has no record GHSA-aaaa-bbbb-cccc" in first_turn, first_turn


def test_a_chat_turn_sends_the_issue_to_the_model(tmp_path: Path):
    home, work = tmp_path / "home", tmp_path / "work"
    work.mkdir()
    with FakeServer() as fake:
        _home(home, fake.origin, plugins=(GH_DIR,))
        proc = _chat(home, work, fake.origin, "What shipped for @gh:NousResearch/hermes-agent#26193? One line.")

        detail = f"exit {proc.returncode}\nstdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        assert proc.returncode == 0, detail
        assert REPLY in proc.stdout, detail
        assert [r["path"] for r in fake.github_requests] == [
            "/repos/NousResearch/hermes-agent/issues/26193", "/repos/NousResearch/hermes-agent/issues/26193/comments",
        ], fake.github_requests
        assert fake.github_requests[1]["query"] == "per_page=10&page=1"
        assert {r["user_agent"].split(" ")[0] for r in fake.github_requests} == {"hermes-gh-ref/1.0.0"}
        [first_turn, *_] = [m for m in fake.user_messages() if "@gh:NousResearch/hermes-agent#26193" in m]
        assert "--- Attached Context ---" in first_turn, first_turn
        assert "GitHub issue NousResearch/hermes-agent#26193: feat(plugins)" in first_turn
        assert "template hint" not in first_turn
        assert "--- @teknium1 · 2026-08-13\nShipped in #84937." in first_turn


def test_an_unknown_issue_reaches_the_model_as_a_warning(tmp_path: Path):
    home, work = tmp_path / "home", tmp_path / "work"
    work.mkdir()
    with FakeServer() as fake:
        _home(home, fake.origin, plugins=(GH_DIR,))
        proc = _chat(home, work, fake.origin, "Explain @gh:o/r#404 briefly.")

        detail = f"exit {proc.returncode}\nstdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        assert proc.returncode == 0, detail
        [first_turn, *_] = [m for m in fake.user_messages() if "@gh:o/r#404" in m]
        assert "--- Context Warnings ---" in first_turn, first_turn
        assert "no issue or pull request o/r#404 is visible without signing in" in first_turn, first_turn
