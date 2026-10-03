"""A real `hermes chat` turn: the referenced article or advisory must reach the model in the user message."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from conftest import GH_DIR, OSV_DIR, PEP_DIR, RFC_DIR, TESTS_DIR, WIKI_DIR
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


def test_a_chat_turn_sends_one_section_to_the_model(tmp_path: Path):
    home, work = tmp_path / "home", tmp_path / "work"
    work.mkdir()
    with FakeServer() as fake:
        _home(home, fake.origin)
        proc = _chat(home, work, fake.origin, "Summarize @wiki:Berlin#History in one line.")

        detail = f"exit {proc.returncode}\nstdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        assert proc.returncode == 0, detail
        [lookup] = fake.wiki_requests
        assert lookup["params"]["titles"] == "Berlin" and lookup["params"]["exsectionformat"] == "wiki", lookup
        assert "exintro" not in lookup["params"], lookup
        [first_turn, *_] = [m for m in fake.user_messages() if "@wiki:Berlin#History" in m]
        assert "--- Attached Context ---" in first_turn, first_turn
        assert "https://en.wikipedia.org/wiki/Berlin#History\nSection: History" in first_turn, first_turn
        assert "formerly settled by Slavs" in first_turn, first_turn
        assert "marshy woodlands" not in first_turn, first_turn


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
        # Hermes itself may call api.github.com in the same process (0.21.0's background update
        # check), and the test routing sends that here too; count the plugin's requests only.
        plugin = [r for r in fake.github_requests if r["user_agent"].startswith("hermes-gh-ref/")]
        assert [r["path"] for r in plugin] == [
            "/repos/NousResearch/hermes-agent/issues/26193", "/repos/NousResearch/hermes-agent/issues/26193/comments",
        ], fake.github_requests
        assert plugin[1]["query"] == "per_page=10&page=1"
        assert {r["user_agent"].split(" ")[0] for r in plugin} == {"hermes-gh-ref/1.0.0"}
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


def test_a_chat_turn_sends_a_pep_section_to_the_model(tmp_path: Path):
    home, work = tmp_path / "home", tmp_path / "work"
    work.mkdir()
    with FakeServer() as fake:
        _home(home, fake.origin, plugins=(PEP_DIR,))
        proc = _chat(home, work, fake.origin, "Apply @pep:572#site.py to my code. One line.")

        detail = f"exit {proc.returncode}\nstdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        assert proc.returncode == 0, detail
        assert REPLY in proc.stdout, detail
        assert [r["path"] for r in fake.pep_requests] == ["/api/peps.json", "/pep-0572/"], fake.pep_requests
        assert {r["user_agent"].split(" ")[0] for r in fake.pep_requests} == {"hermes-pep-ref/1.0.0"}
        [first_turn, *_] = [m for m in fake.user_messages() if "@pep:572#site.py" in m]
        assert "--- Attached Context ---" in first_turn, first_turn
        assert "PEP 572 — Assignment Expressions\nhttps://peps.python.org/pep-0572/#site-py" in first_turn
        assert "Section: Examples › Examples from the Python standard library › site.py" in first_turn
        assert "if env_base := os.environ.get(" in first_turn, first_turn


def test_an_unknown_pep_reaches_the_model_as_a_warning(tmp_path: Path):
    home, work = tmp_path / "home", tmp_path / "work"
    work.mkdir()
    with FakeServer() as fake:
        _home(home, fake.origin, plugins=(PEP_DIR,))
        proc = _chat(home, work, fake.origin, "What does @pep:9999 say?")

        detail = f"exit {proc.returncode}\nstdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        assert proc.returncode == 0, detail
        assert [r["path"] for r in fake.pep_requests] == ["/api/peps.json"], fake.pep_requests
        [first_turn, *_] = [m for m in fake.user_messages() if "@pep:9999" in m]
        assert "--- Context Warnings ---" in first_turn, first_turn
        assert "no PEP 9999 on peps.python.org" in first_turn, first_turn


def test_a_chat_turn_sends_the_rfc_record_to_the_model(tmp_path: Path):
    home, work = tmp_path / "home", tmp_path / "work"
    work.mkdir()
    with FakeServer() as fake:
        _home(home, fake.origin, plugins=(RFC_DIR,))
        proc = _chat(home, work, fake.origin, "Is @rfc:RFC9110 current? One line.")

        detail = f"exit {proc.returncode}\nstdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        assert proc.returncode == 0, detail
        assert REPLY in proc.stdout, detail
        assert [r["path"] for r in fake.rfc_requests] == ["/rfc/rfc9110.json"], fake.rfc_requests
        assert {r["user_agent"].split(" ")[0] for r in fake.rfc_requests} == {"hermes-rfc-ref/1.1.0"}
        [first_turn, *_] = [m for m in fake.user_messages() if "@rfc:RFC9110" in m]
        assert "--- Attached Context ---" in first_turn, first_turn
        assert "RFC 9110: HTTP Semantics\nhttps://www.rfc-editor.org/rfc/rfc9110.html" in first_turn
        assert "Obsoletes: RFC 2818, RFC 7230" in first_turn
        assert "Abstract:\nThe Hypertext Transfer Protocol (HTTP) is a stateless" in first_turn


def test_a_chat_turn_sends_an_rfc_section_to_the_model(tmp_path: Path):
    home, work = tmp_path / "home", tmp_path / "work"
    work.mkdir()
    with FakeServer() as fake:
        _home(home, fake.origin, plugins=(RFC_DIR,))
        proc = _chat(home, work, fake.origin, "Can a GET have a body per @rfc:9110#section-9.3.1? One line.")

        detail = f"exit {proc.returncode}\nstdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        assert proc.returncode == 0, detail
        assert REPLY in proc.stdout, detail
        assert [r["path"] for r in fake.rfc_requests] == ["/rfc/rfc9110.json", "/rfc/rfc9110.txt"], fake.rfc_requests
        [first_turn, *_] = [m for m in fake.user_messages() if "@rfc:9110#section-9.3.1" in m]
        assert "--- Attached Context ---" in first_turn, first_turn
        assert "https://www.rfc-editor.org/rfc/rfc9110.html#section-9.3.1" in first_turn, first_turn
        assert "Section: 9. Methods › 9.3. Method Definitions › 9.3.1. GET" in first_turn, first_turn
        assert "The GET method requests transfer of a current selected representation" in first_turn
        assert "Abstract:" not in first_turn and "HEAD method" not in first_turn


def test_an_unissued_rfc_reaches_the_model_as_a_warning(tmp_path: Path):
    home, work = tmp_path / "home", tmp_path / "work"
    work.mkdir()
    with FakeServer() as fake:
        _home(home, fake.origin, plugins=(RFC_DIR,))
        proc = _chat(home, work, fake.origin, "Summarize @rfc:26 please.")

        detail = f"exit {proc.returncode}\nstdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        assert proc.returncode == 0, detail
        assert [r["path"] for r in fake.rfc_requests] == ["/rfc/rfc26.json"], fake.rfc_requests
        [first_turn, *_] = [m for m in fake.user_messages() if "@rfc:26" in m]
        assert "--- Context Warnings ---" in first_turn, first_turn
        assert "the RFC Editor has no RFC 26" in first_turn, first_turn
