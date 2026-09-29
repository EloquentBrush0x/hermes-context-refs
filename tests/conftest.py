"""Shared helpers. Hermes itself must be importable (run pytest with a Hermes checkout on PYTHONPATH)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "wiki-ref"
TESTS_DIR = Path(__file__).resolve().parent

if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))


def load_plugin_module():
    """Import wiki-ref/__init__.py the way Hermes does: a package rooted at the plugin directory."""
    name = "wiki_ref_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def wiki():
    return load_plugin_module()


@pytest.fixture(autouse=True)
def _clean_reference_registry():
    from agent.context_references import _context_reference_providers

    saved = dict(_context_reference_providers)
    _context_reference_providers.clear()
    yield
    _context_reference_providers.clear()
    _context_reference_providers.update(saved)


class RecordingTransport:
    """Stands in for ``http_get_json``: records every call and answers from ``responder``."""

    def __init__(self, responder):
        self.calls: list[tuple[str, dict, float]] = []
        self._responder = responder

    def __call__(self, url: str, params: dict, timeout: float):
        self.calls.append((url, dict(params), timeout))
        return self._responder(url, params, timeout)
