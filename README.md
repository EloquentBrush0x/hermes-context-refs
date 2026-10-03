# hermes-context-refs

Small [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugins that add `@<prefix>:`
context references, the plugin extension of Hermes's built-in `@file:` / `@url:` references.
Each plugin lives in its own directory and is installed on its own.

| Plugin | Reference | Source |
|---|---|---|
| [wiki-ref](wiki-ref/) | `@wiki:<title>`: lead section of a Wikipedia article; `@wiki:<title>#<section>`: one section | Wikipedia API, no key |
| [osv-ref](osv-ref/) | `@cve:<id>`, `@ghsa:<id>`, `@osv:<id>`: a vulnerability record with affected packages and fixed versions | OSV.dev API, no key |
| [gh-ref](gh-ref/) | `@gh:owner/repo#123`: a GitHub issue or pull request with its latest comments | GitHub REST API, anonymous |
| [pep-ref](pep-ref/) | `@pep:<number>`: a PEP's status, authors and first section; `@pep:<number>#<section>`: one section | peps.python.org, no key |
| [rfc-ref](rfc-ref/) | `@rfc:<number>`: an RFC's status, abstract and what obsoletes or updates it; `@rfc:<number>#<section>`: one section | RFC Editor, no key |

## Development

The tests run against a Hermes checkout, the same way the plugins run inside Hermes:

```bash
git clone https://github.com/NousResearch/hermes-agent ../hermes-agent
(cd ../hermes-agent && uv sync --frozen --group dev)
PYTHONPATH=../hermes-agent uv run --project ../hermes-agent --no-sync python -m pytest tests
```

- `tests/test_wiki_ref.py`, `tests/test_osv_ref.py`, `tests/test_gh_ref.py`, `tests/test_pep_ref.py`,
  `tests/test_rfc_ref.py`: each plugin's providers against Hermes's real reference parser and
  expander. `tests/fixtures/` holds pages, index entries and records taken from the real sites.
- `tests/test_hermes_integration.py`: loads the plugins through Hermes's plugin manager and checks
  settings and `complete.path` autocomplete.
- `tests/test_e2e_chat.py`: real `hermes chat` turns against local fake model, Wikipedia, OSV,
  GitHub, peps.python.org and RFC Editor servers; asserts what reaches the model.
- `tests/test_live.py`: the real Wikipedia, OSV, GitHub, PEP and RFC Editor sites, only with `CONTEXT_REFS_LIVE=1`
  (GitHub tests skip when the anonymous rate limit of the runner's IP address is used up).

CI runs the suite on Linux, macOS and Windows against Hermes 0.21.0 and Hermes main, runs
`hermes plugins validate` on each plugin, and repeats daily (with the live tests) to catch upstream
changes.

`docs/card.png` (wiki-ref), `docs/osv-ref-card.png` (osv-ref), `docs/gh-ref-card.png` (gh-ref),
`docs/pep-ref-card.png` (pep-ref) and `docs/rfc-ref-card.png` (rfc-ref) are the catalog cards.
`docs/make_card.py`, `docs/make_osv_card.py`, `docs/make_gh_card.py`, `docs/make_pep_card.py` and
`docs/make_rfc_card.py` render them from each plugin's real output and
refuse a layout that the docs page hero would crop. They live outside the plugin directories, so
installs do not carry them.

## License

MIT
