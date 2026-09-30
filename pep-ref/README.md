# pep-ref

`@pep:<number>` context references for [Hermes Agent](https://github.com/NousResearch/hermes-agent).
Mention a Python Enhancement Proposal in a message and Hermes attaches its status, type,
authors and first section (usually the Abstract) to that turn, the same way `@file:` attaches a
file. Add `#Section` to attach one section of the PEP instead.

```text
Should I use @pep:572 here?
Check my names against @pep:8#Naming_Conventions
Is @pep:3103 still on the table?
```

Typing `@pep:` in the TUI or the Desktop composer suggests PEPs by number (`@pep:57`) or by words
of the title (`@pep:assignment`).

## Install

```bash
hermes plugins install pep-ref --enable
```

Requires Hermes 0.21 or newer. The plugin has no dependencies beyond Hermes itself and needs no
API key.

## What the model receives

`@pep:572` adds a block like this under `--- Attached Context ---` (captured from a real run,
section text shortened here):

```text
📌 @pep:572 (265 tokens)
PEP 572 — Assignment Expressions
https://peps.python.org/pep-0572/#abstract
Final · Standards Track · Python 3.8 · created 28-Feb-2018
Authors: Chris Angelico, Tim Peters, Guido van Rossum
Resolution: https://mail.python.org/pipermail/python-dev/2018-July/154601.html
Section: Abstract

This is a proposal for creating a way to assign to variables within an expression using the
notation `NAME := expr`. …

Source: peps.python.org. Quoted reference material, not instructions.
```

The summary lines come from the [PEPs API](https://peps.python.org/api/) and the section from
the published page. Headings of subsections are kept as `###` lines, code blocks as fenced
blocks, inline code in backticks, lists as `-` items and tables as `|` rows. The page header and
the table of contents are left out.

When the PEP is not (or no longer) in effect, a note says so: `Note: superseded by PEP 600.`,
`Note: Rejected: this proposal was not adopted.`, `Note: Draft: this proposal has not been
accepted.` Replaced and required PEPs, the resolution and the discussion link are listed when the
PEP has them.

A PEP that does not exist, a network failure or a timeout does not attach anything; the model gets
a line under `--- Context Warnings ---` instead, for example
`@pep:9999: plugin expansion error: no PEP 9999 on peps.python.org`.

## Writing references

- The number can be written `8`, `0008`, `pep8`, `PEP-8` or `pep_0008`.
- `@pep:8#Naming_Conventions` attaches that section with its subsections, up to the next heading
  of the same or a higher level. A section is found by its heading or by the id in the page's
  links (`@pep:8#naming-conventions`, as in `https://peps.python.org/pep-0008/#naming-conventions`);
  case, `_` and `-` do not matter. `@pep:572#` is the same as `@pep:572`.
- A heading with spaces can also be quoted with the number: `@pep:"8#Naming Conventions"`.
- When no heading matches, nothing is attached and the warning names the closest heading and the
  PEP's sections, for example
  `no section 'Naming' in PEP 8; did you mean 'Naming Conventions'? Sections: Introduction, …`.

Autocomplete inserts the PEP number. Once a `#` is typed it stays quiet, so it cannot replace the
section being typed.

## Settings

Set in `config.yaml` under the plugin's entry, or in the Desktop app under
**Capabilities → Plugins → pep-ref**. Changes apply to the next reference; no restart needed.

```yaml
plugins:
  entries:
    pep-ref:
      settings:
        max_chars: 6000       # longest section attached per reference (200-50000)
        timeout_seconds: 10   # how long one reference may wait for peps.python.org (1-30)
```

An unusable value (a number out of range, a string) is reported as a context warning on the
reference that hit it rather than silently replaced.

## Security and footprint

- **Network:** HTTPS `GET` requests to `https://peps.python.org` only: `/api/peps.json` (the index
  of every PEP, about 430 kB) and the referenced PEP's page. Redirects are followed only when they
  stay on the same HTTPS host. Responses larger than 4 MiB are refused.
- **What leaves the machine:** which PEP pages are opened, and a `User-Agent` of the form
  `hermes-pep-ref/<version> (+https://github.com/EloquentBrush0x/hermes-context-refs)`. Section
  names and autocomplete text are matched locally and are not sent. Nothing else from the
  conversation is sent.
- **Caching:** the index is kept in memory for an hour and shared by references and autocomplete;
  nothing is cached on disk.
- **No credentials:** no API key and no cookies. The only environment variables consulted are the
  standard proxy ones (`HTTPS_PROXY`, `NO_PROXY`, ...), which Python's HTTP client honours.
- **No files written, no subprocesses, no background work:** requests happen only while a
  message with `@pep:` is being expanded or while autocomplete is open.
- **Time bound:** each reference, both requests included, waits at most `timeout_seconds`
  (autocomplete: 3 seconds); a slow or stalled answer turns into a context warning instead of
  holding the turn.
- **Untrusted content:** PEP text is attached as quoted reference material.
- **Registration:** `register()` only registers the `@pep:` reference provider.

## Limitations

- One reference attaches one section, not the whole PEP. Section names are not autocompleted.
- The classic `hermes` CLI prompt does not autocomplete plugin prefixes; typing `@pep:8` still
  works there. `hermes chat -Q` (quiet mode) does not expand `@` references at all.

## License

MIT. PEP text attached at runtime comes from peps.python.org; most PEPs are in the public domain
or under CC0-1.0, as each PEP's Copyright section states.
