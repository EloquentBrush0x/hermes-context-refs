# hermes-context-refs

Small [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugins that add `@<prefix>:`
context references, the plugin extension of Hermes's built-in `@file:` / `@url:` references.
Each plugin lives in its own directory and is installed on its own.

| Plugin | Reference | Source |
|---|---|---|
| [wiki-ref](wiki-ref/) | `@wiki:<title>`: lead section of a Wikipedia article | Wikipedia API, no key |

## Development

The tests run against a Hermes checkout, the same way the plugin runs inside Hermes:

```bash
git clone https://github.com/NousResearch/hermes-agent ../hermes-agent
(cd ../hermes-agent && uv sync --frozen --group dev)
PYTHONPATH=../hermes-agent uv run --project ../hermes-agent --no-sync python -m pytest tests
```

- `tests/test_wiki_ref.py`: the provider against Hermes's real reference parser and expander.
- `tests/test_hermes_integration.py`: loads the plugin through Hermes's plugin manager and checks
  settings and `complete.path` autocomplete.
- `tests/test_e2e_chat.py`: a real `hermes chat` turn against local fake model and Wikipedia
  servers; asserts what reaches the model.
- `tests/test_live.py`: the real Wikipedia API, only with `WIKI_REF_LIVE=1`.

CI runs the suite on Linux, macOS and Windows against Hermes 0.21.0 and Hermes main, runs
`hermes plugins validate`, and repeats daily (with the live tests) to catch upstream changes.

## License

MIT
