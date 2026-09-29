"""A real `hermes chat` turn: the referenced article must reach the model in the user message."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from conftest import PLUGIN_DIR, TESTS_DIR
from fakes import MODEL_ID, REPLY, FakeServer

_SCRUBBED_ENV_PREFIXES = ("OPENAI", "ANTHROPIC", "OPENROUTER", "GIT_", "HERMES_")


def _home(home: Path, origin: str, settings: str = "") -> None:
    shutil.copytree(PLUGIN_DIR, home / "plugins" / "wiki-ref", ignore=shutil.ignore_patterns("__pycache__"))
    (home / "config.yaml").write_text(
        "model:\n"
        "  provider: custom\n"
        f"  base_url: {origin}/v1\n"
        f"  default: {MODEL_ID}\n"
        "  context_length: 128000\n"
        "agent:\n"
        "  api_max_retries: 1\n"
        "plugins:\n"
        "  enabled: [wiki-ref]\n" + settings,
        encoding="utf-8",
    )
    (home / ".env").write_text("OPENAI_API_KEY=sk-fake-e2e\n", encoding="utf-8")


def _chat(home: Path, work: Path, origin: str, query: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith(_SCRUBBED_ENV_PREFIXES)}
    env["HERMES_HOME"] = str(home)
    env["WIKI_REF_TEST_ORIGIN"] = origin
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
