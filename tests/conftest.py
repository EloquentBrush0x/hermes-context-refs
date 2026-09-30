"""Shared helpers. Hermes itself must be importable (run pytest with a Hermes checkout on PYTHONPATH)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WIKI_DIR = REPO_ROOT / "wiki-ref"
OSV_DIR = REPO_ROOT / "osv-ref"
GH_DIR = REPO_ROOT / "gh-ref"
PEP_DIR = REPO_ROOT / "pep-ref"
RFC_DIR = REPO_ROOT / "rfc-ref"
TESTS_DIR = Path(__file__).resolve().parent
FIXTURES = TESTS_DIR / "fixtures"

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def load_plugin_module(plugin_dir: Path):
    """Import <plugin>/__init__.py the way Hermes does: a package rooted at the plugin directory."""
    name = plugin_dir.name.replace("-", "_") + "_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, plugin_dir / "__init__.py", submodule_search_locations=[str(plugin_dir)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def wiki():
    return load_plugin_module(WIKI_DIR)


@pytest.fixture
def osv():
    return load_plugin_module(OSV_DIR)


@pytest.fixture
def gh():
    return load_plugin_module(GH_DIR)


@pytest.fixture
def pep():
    return load_plugin_module(PEP_DIR)


@pytest.fixture
def rfc():
    return load_plugin_module(RFC_DIR)


@pytest.fixture(autouse=True)
def _clean_reference_registry():
    from agent.context_references import _context_reference_providers

    saved = dict(_context_reference_providers)
    _context_reference_providers.clear()
    yield
    _context_reference_providers.clear()
    _context_reference_providers.update(saved)


class RecordingTransport:
    """Stands in for a plugin's ``http_get_json``: records every call and answers from ``responder``."""

    def __init__(self, responder):
        self.calls: list[tuple] = []
        self._responder = responder

    def __call__(self, *args):
        self.calls.append(args)
        return self._responder(*args)
