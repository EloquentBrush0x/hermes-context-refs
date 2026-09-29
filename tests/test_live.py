"""Against the real Wikipedia and OSV APIs. Opt-in: CONTEXT_REFS_LIVE=1 (CI's daily run sets it)."""

from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CONTEXT_REFS_LIVE") != "1", reason="set CONTEXT_REFS_LIVE=1 to call Wikipedia and OSV"
)


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


def test_live_cve_gets_package_data_from_its_aliases(osv):
    text = run(osv.CveReferenceProvider().expand("cve-2024-3651"))
    lines = text.splitlines()
    assert lines[0].startswith("OSV: CVE-2024-3651")
    assert lines[1] == "https://osv.dev/vulnerability/CVE-2024-3651"
    assert "GHSA-jjg7-2v4v-x38h" in lines[2]
    assert "- PyPI idna: " in text and "(fixed in 3.7)" in text


def test_live_ghsa_and_other_databases(osv):
    assert "crates.io gix-transport" in run(osv.GhsaReferenceProvider().expand("GHSA-RRJW-J4M2-MF34"))
    assert run(osv.OsvReferenceProvider().expand("PYSEC-2024-60")).startswith("OSV: PYSEC-2024-60")


def test_live_missing_record(osv):
    with pytest.raises(osv.OsvRecordMissing, match="OSV has no record CVE-2099-0001"):
        run(osv.CveReferenceProvider().expand("CVE-2099-0001"))
