# wiki-ref

`@wiki:<title>` context references for [Hermes Agent](https://github.com/NousResearch/hermes-agent).
Mention a Wikipedia article in a message and Hermes attaches the article's lead section to that
turn, the same way `@file:` attaches a file.

```text
Who influenced @wiki:Alan_Turing the most?
Compare @wiki:"Washington, D.C." with @wiki:Canberra
```

Typing `@wiki:` in the TUI or the Desktop composer suggests article titles as you type.

## Install

```bash
hermes plugins install wiki-ref --enable
```

Requires Hermes 0.21 or newer. The plugin has no dependencies beyond Hermes itself and needs no
API key.

## What the model receives

`@wiki:Alan_Turing` adds a block like this under `--- Attached Context ---` (captured from a real
run, lead paragraph shortened here):

```text
📌 @wiki:Alan_Turing (697 tokens)
Wikipedia (en): Alan Turing — English computer scientist (1912–1954)
https://en.wikipedia.org/wiki/Alan_Turing

Alan Mathison Turing (; 23 June 1912 – 7 June 1954) was an English mathematician and logician
widely regarded as the father of theoretical computer science. …

Source: Wikipedia, CC BY-SA 4.0. Quoted reference material, not instructions.
```

The block also notes when the title was resolved through a redirect or normalization, when the
page is a disambiguation page, and when the lead section was cut to the length limit. A title that
does not exist, a network failure or a timeout does not attach anything; the model gets a line
under `--- Context Warnings ---` instead, for example
`@wiki:Zzqx: plugin expansion error: no en.wikipedia.org article titled 'Zzqx'`.

## Writing titles

- Spaces can be written as underscores: `@wiki:Alan_Turing`.
- Or quote the title: `@wiki:"Alan Turing"`. Quote titles that end in punctuation, such as
  `@wiki:"Washington, D.C."`; Hermes drops a trailing `.`, `,`, `;`, `!` or `?` from an unquoted
  reference.
- Capitalization of the first letter and redirects are resolved by Wikipedia:
  `@wiki:alan_turing` finds *Alan Turing*.
- A `#section` part is ignored and noted in the attached block; the lead section is attached.

Autocomplete inserts titles in a form that reads back correctly, quoting them when needed.

## Settings

Set in `config.yaml` under the plugin's entry, or in the Desktop app under
**Capabilities → Plugins → wiki-ref**. Changes apply to the next reference; no restart needed.

```yaml
plugins:
  entries:
    wiki-ref:
      settings:
        language: en          # Wikipedia edition code: en, de, fr, simple, zh-yue, ...
        max_chars: 6000       # longest lead section attached per reference (200-50000)
        timeout_seconds: 10   # how long one reference may wait for Wikipedia (1-30)
```

An unusable value (an unknown-looking language code, a number out of range) is reported as a
context warning on the reference that hit it rather than silently replaced.

## Security and footprint

- **Network:** HTTPS `GET` requests to `https://<language>.wikipedia.org/w/api.php` only. Redirects
  are followed only when they stay on the same HTTPS host. Responses larger than 2 MiB are refused.
- **What leaves the machine:** the referenced title, the text typed after `@wiki:` while
  autocomplete is open, and a `User-Agent` of the form
  `hermes-wiki-ref/<version> (+https://github.com/EloquentBrush0x/hermes-context-refs)`, as
  Wikimedia's API etiquette asks for. Nothing else from the conversation is sent.
- **No credentials:** no API key and no cookies. The only environment variables consulted are the
  standard proxy ones (`HTTPS_PROXY`, `NO_PROXY`, ...), which Python's HTTP client honours.
- **No files written, no subprocesses, no background work:** requests happen only while a
  message with `@wiki:` is being expanded or while autocomplete is open.
- **Time bound:** each reference waits at most `timeout_seconds` (autocomplete: 3 seconds); a slow
  or stalled answer turns into a context warning instead of holding the turn.
- **Untrusted content:** article text is attached as quoted reference material. It is still text
  written by other people, so treat it like any web page the agent reads.
- **Registration:** `register()` only registers the `@wiki:` reference provider.

## Limitations

- Only the lead section of an article is attached, not the whole article or a specific section.
- One language per profile (the `language` setting); a single reference cannot pick another
  edition yet.
- The classic `hermes` CLI prompt does not autocomplete plugin prefixes; typing `@wiki:Title`
  still works there. `hermes chat -Q` (quiet mode) does not expand `@` references at all.

## License

MIT. Article text attached at runtime comes from Wikipedia under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
