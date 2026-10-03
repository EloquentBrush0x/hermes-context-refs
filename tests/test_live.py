"""Against the real Wikipedia, OSV, GitHub, PEP and RFC Editor sites.

Opt-in: CONTEXT_REFS_LIVE=1 (CI's daily run sets it).
"""

from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CONTEXT_REFS_LIVE") != "1",
    reason="set CONTEXT_REFS_LIVE=1 to call Wikipedia, OSV, GitHub, peps.python.org and the RFC Editor",
)


def run(coro):
    return asyncio.run(coro)


def test_live_article_lead_section(wiki):
    text = run(wiki.WikiReferenceProvider().expand("alan_turing"))
    lines = text.splitlines()
    assert lines[0].startswith("Wikipedia (en): Alan Turing")
    assert lines[1] == "https://en.wikipedia.org/wiki/Alan_Turing"
    assert "(resolved from 'alan turing')" in text
    assert "(;" not in text and "( ;" not in text  # the lead opens "Alan Mathison Turing (; 23 June 1912" raw
    assert "Turing" in text.split("\n\n", 1)[1]


def test_live_other_edition_and_disambiguation(wiki):
    provider = wiki.WikiReferenceProvider(get_config=lambda key, default=None: {"language": "de"}.get(key, default))
    assert run(provider.expand("Berlin")).startswith("Wikipedia (de): Berlin")
    assert "disambiguation page" in run(wiki.WikiReferenceProvider().expand("Mercury"))


def test_live_missing_article(wiki):
    with pytest.raises(wiki.WikiRefError, match="no en.wikipedia.org article titled"):
        run(wiki.WikiReferenceProvider().expand("Zzqx_no_such_article_for_wiki_ref"))


def test_live_section_and_unknown_section(wiki):
    lines = run(wiki.WikiReferenceProvider().expand("Berlin#History")).splitlines()
    assert lines[1:3] == ["https://en.wikipedia.org/wiki/Berlin#History", "Section: History"]
    assert any(line.startswith("=== ") for line in lines)  # its subsections come along
    with pytest.raises(wiki.WikiRefError, match="did you mean 'History'"):
        run(wiki.WikiReferenceProvider().expand("Berlin#Histroy"))


def test_live_language_prefix_and_article_address(wiki):
    """Wikipedia's own interwiki answer picks the edition; "Re:Zero" stays an English title."""
    by_prefix = run(wiki.WikiReferenceProvider().expand("de:Berlin#Geschichte")).splitlines()
    assert by_prefix[1:3] == ["https://de.wikipedia.org/wiki/Berlin#Geschichte", "Section: Geschichte"]
    assert "(resolved from 'de:Berlin')" in by_prefix
    by_address = run(wiki.WikiReferenceProvider().expand("https://en.m.wikipedia.org/wiki/Berlin#1900%E2%80%931945"))
    assert by_address.splitlines()[2] == "Section: History › 1900–1945"
    assert run(wiki.WikiReferenceProvider().expand("Re:Zero")).startswith("Wikipedia (en): Re:Zero")
    with pytest.raises(wiki.WikiRefError, match="links to en.wiktionary.org"):
        run(wiki.WikiReferenceProvider().expand("wikt:cat"))


@pytest.mark.parametrize("title", ["Berlin", "Alan Turing", "Python (programming language)"])
def test_live_extract_headings_have_the_expected_shape(wiki, title):
    """Section cutting relies on TextExtracts marking every heading as its own "== Name ==" line."""
    data = wiki.http_get_json(wiki.api_url("en"), {
        "action": "query", "format": "json", "formatversion": "2", "prop": "extracts",
        "explaintext": "1", "exsectionformat": "wiki", "titles": title,
    }, 10)
    extract = data["query"]["pages"][0]["extract"]
    marked = [line for line in extract.splitlines() if line.startswith("==")]
    assert marked and all(wiki._HEADING_RE.fullmatch(line) for line in marked), marked


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


def _gh(gh, target):
    try:
        return run(gh.GitHubReferenceProvider().expand(target))
    except gh.GhRefError as exc:
        if "anonymous API limit" in str(exc) or "secondary rate limit" in str(exc):
            pytest.skip(f"GitHub rate limit on this IP: {exc}")
        raise


def test_live_github_issue_with_comments(gh):
    text = _gh(gh, "NousResearch/hermes-agent#26193")
    lines = text.splitlines()
    assert lines[0].startswith("GitHub issue NousResearch/hermes-agent#26193: ")
    assert lines[1] == "https://github.com/NousResearch/hermes-agent/issues/26193"
    assert lines[2].startswith("State: closed")
    assert "Comments (" in text and "#84937" in text


def test_live_github_renamed_repository_pull_request(gh):
    text = _gh(gh, "https://github.com/Byron/gitoxide/pull/1032")
    assert text.splitlines()[0].startswith("GitHub pull request GitoxideLabs/gitoxide#1032: ")
    assert "(resolved from Byron/gitoxide#1032" in text
    assert "State: merged 2023-09-24" in text and "Branch: " in text


def test_live_github_missing_issue(gh):
    with pytest.raises(gh.GhRefError, match="no issue or pull request NousResearch/hermes-agent#999999999"):
        _gh(gh, "NousResearch/hermes-agent#999999999")


def test_live_pep_summary_section_and_autocomplete(pep):
    provider = pep.PepReferenceProvider()
    lines = run(provider.expand("pep-0572")).splitlines()
    assert lines[:2] == ["PEP 572 — Assignment Expressions", "https://peps.python.org/pep-0572/#abstract"]
    assert "Section: Abstract" in lines and any("`NAME := expr`" in line for line in lines)
    section = run(provider.expand("8#naming_conventions"))
    assert "Section: Naming Conventions" in section and "### " in section  # with its subsections
    with pytest.raises(pep.PepRefError, match="did you mean 'Naming Conventions'"):
        run(provider.expand("8#Naming"))
    assert "572" in [item.text for item in run(provider.autocomplete("assignment expressions"))]


@pytest.mark.parametrize("number", [8, 20, 484, 572])
def test_live_pep_pages_have_the_structure_the_parser_relies_on(pep, number):
    """A #pep-content section holding titled <section> elements; the page header and contents are left out."""
    status, body = pep.http_get(pep.page_url(number), 15)
    assert status == 200
    page = pep.parse_page(body.decode("utf-8"))
    assert len(page.sections) >= 2 and all(s.id and s.title for s in page.sections)
    assert page.text.startswith("## ") and "Table of Contents" not in page.text


def test_live_rfc_record_obsoleted_rfc_and_missing_number(rfc):
    provider = rfc.RfcReferenceProvider()
    lines = run(provider.expand("RFC9110")).splitlines()
    assert lines[:2] == ["RFC 9110: HTTP Semantics", "https://www.rfc-editor.org/rfc/rfc9110.html"]
    assert "Abstract:" in lines and lines[lines.index("Abstract:") + 1].startswith("The Hypertext Transfer Protocol")
    assert any(line.startswith("Note: obsoleted by RFC 7230") for line in run(provider.expand("2616")).splitlines())
    with pytest.raises(rfc.RfcRefError, match="the RFC Editor has no RFC 26"):
        run(provider.expand("26"))


def test_live_rfc_sections_from_current_and_paginated_texts(rfc):
    provider = rfc.RfcReferenceProvider()
    lines = run(provider.expand("9110#section-9.3.1")).splitlines()
    assert lines[1] == "https://www.rfc-editor.org/rfc/rfc9110.html#section-9.3.1"
    assert "Section: 9. Methods › 9.3. Method Definitions › 9.3.1. GET" in lines
    assert any(line.strip().startswith("The GET method requests transfer") for line in lines)
    text = run(provider.expand("2616#13.1.1"))
    assert "Section: 13. Caching in HTTP › 13.1.1. Cache Correctness" in text
    assert "[Page" not in text and "\f" not in text
    assert "Appendix B. Protocol Data Structures and Constant Values › B.4. Cipher Suites" in run(
        provider.expand("8446#appendix-B.4")
    )
    assert "#section-9.3.1" in run(provider.expand("9110#name-get")).splitlines()[1]
    with pytest.raises(rfc.RfcRefError, match="RFC 8 has no plain-text version"):
        run(provider.expand("8#1"))
