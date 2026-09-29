"""Against the real Wikipedia API. Opt-in: WIKI_REF_LIVE=1 (CI's daily run sets it)."""

from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("WIKI_REF_LIVE") != "1", reason="set WIKI_REF_LIVE=1 to call Wikipedia")


def run(coro):
    return asyncio.run(coro)


def test_live_article_lead_section(wiki):
    text = run(wiki.WikiReferenceProvider().expand("alan_turing"))
    lines = text.splitlines()
    assert lines[0].startswith("Wikipedia (en): Alan Turing")
    assert lines[1] == "https://en.wikipedia.org/wiki/Alan_Turing"
    assert "(resolved from 'alan turing')" in text
    assert "Turing" in text.split("\n\n", 1)[1]


def test_live_other_edition_and_disambiguation(wiki):
    provider = wiki.WikiReferenceProvider(get_config=lambda key, default=None: {"language": "de"}.get(key, default))
    assert run(provider.expand("Berlin")).startswith("Wikipedia (de): Berlin")
    assert "disambiguation page" in run(wiki.WikiReferenceProvider().expand("Mercury"))


def test_live_missing_article(wiki):
    with pytest.raises(wiki.WikiRefError, match="no en.wikipedia.org article titled"):
        run(wiki.WikiReferenceProvider().expand("Zzqx_no_such_article_for_wiki_ref"))


def test_live_autocomplete(wiki):
    items = run(wiki.WikiReferenceProvider().autocomplete("Alan Tur"))
    assert "Alan Turing" in [item.display for item in items]
    assert "Alan_Turing" in [item.text for item in items]
