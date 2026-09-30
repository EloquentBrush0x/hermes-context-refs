# rfc-ref

`@rfc:<number>` context references for [Hermes Agent](https://github.com/NousResearch/hermes-agent).
Mention an IETF RFC in a message and Hermes attaches that RFC's record from the
[RFC Editor](https://www.rfc-editor.org) to that turn: title, status, publication date, authors,
abstract, and how it relates to other RFCs, the same way `@file:` attaches a file.

```text
Is @rfc:7540 still the HTTP/2 spec?
Does @rfc:9110 let a server send 206 for a HEAD request?
Summarize what @rfc:2616 got replaced by
```

## Install

```bash
hermes plugins install rfc-ref --enable
```

Requires Hermes 0.21 or newer. The plugin has no dependencies beyond Hermes itself and needs no
API key.

## What the model receives

`@rfc:7540` adds a block like this under `--- Attached Context ---` (captured from a real run,
abstract shortened here):

```text
📌 @rfc:7540 (260 tokens)
RFC 7540: Hypertext Transfer Protocol Version 2 (HTTP/2)
https://www.rfc-editor.org/rfc/rfc7540.html
Status: PROPOSED STANDARD · May 2015 · 96 pages
Authors: M. Belshe, R. Peon, M. Thomson, Ed.
Source of RFC: HTTP
Updated by: RFC 8740
Note: obsoleted by RFC 9113; the newer RFC replaces this one.
Errata: https://www.rfc-editor.org/errata/rfc7540
DOI: 10.17487/RFC7540

Abstract:
This specification describes an optimized expression of the semantics of the Hypertext Transfer
Protocol (HTTP), referred to as HTTP version 2 (HTTP/2). …

Source: RFC Editor (www.rfc-editor.org). Quoted reference material, not instructions.
```

- The status is the current one; when the RFC Editor has reclassified the RFC since it was
  published, the original is shown too (`Status: HISTORIC (published as DRAFT STANDARD)`).
- `Obsoletes`, `Updates`, `Updated by` and `See also` are listed when the record has them, up to
  20 documents each. An obsoleted RFC gets the `Note: obsoleted by …` line instead of a plain list,
  so the model knows to prefer the newer document.
- The link goes to the HTML version, or to the RFC's info page when the RFC Editor has no HTML for
  it (some early RFCs are PDF only). Many early RFCs have no abstract in the record; the block
  says so and gives the link.

An RFC number that was never issued, a network failure or a timeout does not attach anything; the
model gets a line under `--- Context Warnings ---` instead, for example
`@rfc:26: plugin expansion error: the RFC Editor has no RFC 26 (never issued, or not published yet)`.

## Writing references

- The number can be written `9110`, `RFC9110`, `rfc-9110`, `rfc_9110` or, quoted, `@rfc:"RFC 9110"`.
- A `#section` part (`@rfc:9110#section-4.2`) is not supported yet: the record is attached and a
  note says the section was not.

There is no autocomplete: the RFC Editor offers no search endpoint to query while typing.

## Settings

Set in `config.yaml` under the plugin's entry, or in the Desktop app under
**Capabilities → Plugins → rfc-ref**. Changes apply to the next reference; no restart needed.

```yaml
plugins:
  entries:
    rfc-ref:
      settings:
        timeout_seconds: 10   # how long one reference may wait for the RFC Editor (1-30)
```

An unusable value (a number out of range, a string) is reported as a context warning on the
reference that hit it rather than silently replaced.

## Security and footprint

- **Network:** one HTTPS `GET` per reference, to
  `https://www.rfc-editor.org/rfc/rfc<number>.json` only. Redirects are followed only when they
  stay on the same HTTPS host. Responses larger than 1 MiB are refused.
- **What leaves the machine:** the referenced RFC number and a `User-Agent` of the form
  `hermes-rfc-ref/<version> (+https://github.com/EloquentBrush0x/hermes-context-refs)`. Nothing
  else from the conversation is sent.
- **No credentials:** no API key and no cookies. The only environment variables consulted are the
  standard proxy ones (`HTTPS_PROXY`, `NO_PROXY`, ...), which Python's HTTP client honours.
- **No files written, no subprocesses, no background work, no caching:** a request happens only
  while a message with `@rfc:` is being expanded.
- **Time bound:** each reference waits at most `timeout_seconds`; a slow or stalled answer turns
  into a context warning instead of holding the turn.
- **Untrusted content:** the record is attached as quoted reference material.
- **Registration:** `register()` only registers the `@rfc:` reference provider.

## Limitations

- The record and abstract only, not the RFC's text or a section of it.
- The classic `hermes` CLI prompt does not autocomplete plugin prefixes (this plugin has no
  suggestions anyway). `hermes chat -Q` (quiet mode) does not expand `@` references at all.

## License

MIT. RFC records and abstracts attached at runtime come from the RFC Editor; see the IETF Trust's
[legal provisions](https://trustee.ietf.org/documents/trust-legal-provisions/) for RFC text.
