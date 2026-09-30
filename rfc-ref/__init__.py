"""rfc-ref: ``@rfc:<number>`` context references for Hermes.

Typing ``@rfc:9110`` (or ``@rfc:RFC9110``) in a message attaches that RFC's record from the RFC
Editor: title, status, publication date, authors, abstract, and how it relates to other RFCs
(what it obsoletes and updates, what obsoletes or updates it), with links to the document and
its errata.

The plugin only talks to ``https://www.rfc-editor.org/rfc/rfc<number>.json``. It writes nothing
to disk, spawns no subprocess and needs no API key.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from agent.context_references import ContextCompletionItem, ContextReferenceProvider

__version__ = "1.0.0"

PREFIX = "rfc"
PLUGIN_ID = "rfc-ref"
HOST = "www.rfc-editor.org"
USER_AGENT = f"hermes-rfc-ref/{__version__} (+https://github.com/EloquentBrush0x/hermes-context-refs)"

DEFAULT_TIMEOUT_SECONDS = 10
TIMEOUT_RANGE = (1, 30)
MAX_RESPONSE_BYTES = 1024 * 1024  # a record is a few kB
MAX_ABSTRACT_CHARS = 8000
MAX_LISTED_DOCUMENTS = 20

# "9110", "RFC9110", "rfc-9110", "RFC 9110" (quoted); "#..." after the number names a section.
_NUMBER_RE = re.compile(r"^(?:rfc[-_ ]?)?0*(\d{1,5})$", re.IGNORECASE)
_DOC_ID_RE = re.compile(r"^([A-Z]+)0*(\d+)$")

Transport = Callable[[str, float], "tuple[int, bytes]"]


class RfcRefError(Exception):
    """A reference that cannot be expanded; Hermes shows the message as a context warning."""


class _SameHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only when it stays on the same https host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if new.scheme != "https" or new.hostname != old.hostname:
            raise urllib.error.HTTPError(
                newurl, code, f"refused redirect from {old.hostname} to {new.hostname}", headers, fp
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SameHostRedirectHandler)
# Requests run here rather than on the event loop's default executor: Hermes expands
# references inside asyncio.run(), whose shutdown joins the default executor, so a
# slow request there would hold the turn past our timeout.
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rfc-ref")


def http_get(url: str, timeout: float) -> tuple[int, bytes]:
    """GET ``url`` and return ``(status, body)``; error statuses are returned, not raised.

    ``timeout`` bounds each socket operation and, checked after every read, the whole
    exchange, so a server trickling bytes cannot keep the request alive.
    """
    deadline = time.monotonic() + timeout
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        response = _OPENER.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.fp is not None:
            exc.close()
        return exc.code, b""
    chunks, size = [], 0
    with response:
        while chunk := response.read1(64 * 1024):
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ValueError(f"response larger than {MAX_RESPONSE_BYTES} bytes")
            if time.monotonic() > deadline:
                raise TimeoutError(f"no complete answer within {timeout:g}s")
            chunks.append(chunk)
        return response.status, b"".join(chunks)


def parse_target(target: str) -> tuple[int, str]:
    """Split a reference target into ``(number, section)``."""
    value, _, section = target.strip().partition("#")
    match = _NUMBER_RE.match(value.strip())
    if not match or int(match.group(1)) == 0:
        raise RfcRefError(f"{value.strip()!r} is not an RFC number; write @rfc:9110 or @rfc:RFC9110")
    return int(match.group(1)), " ".join(section.split())


def record_url(number: int) -> str:
    return f"https://{HOST}/rfc/rfc{number}.json"


# -- rendering ----------------------------------------------------------------------------------

def _one_line(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _document(doc_id: str) -> str:
    """"RFC2818" -> "RFC 2818", "BCP0014" -> "BCP 14"; anything else unchanged."""
    match = _DOC_ID_RE.match(doc_id.strip())
    return f"{match.group(1)} {int(match.group(2))}" if match else doc_id.strip()


def _documents(value: Any) -> str:
    ids = [_document(v) for v in value or [] if isinstance(v, str) and v.strip()] if isinstance(value, list) else []
    shown = ", ".join(ids[:MAX_LISTED_DOCUMENTS])
    if len(ids) > MAX_LISTED_DOCUMENTS:
        shown += f" (+{len(ids) - MAX_LISTED_DOCUMENTS} more)"
    return shown


def _abstract(value: Any) -> str:
    """The record's abstract with its paragraphs kept and each paragraph on one line."""
    if not isinstance(value, str):
        return ""
    paragraphs = (" ".join(p.split()) for p in re.split(r"\n\s*\n", value))
    return "\n\n".join(p for p in paragraphs if p)


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    cut = text[:max_chars]
    space = cut.rfind(" ")
    if space > max_chars // 2:
        cut = cut[:space]
    return cut.rstrip() + " […]", True


def render_rfc(record: dict, *, number: int, section: str = "") -> str:
    """Render one RFC Editor record as the block Hermes attaches to the message."""
    formats = [f for f in record.get("format") or [] if isinstance(f, str)]
    url = f"https://{HOST}/rfc/rfc{number}.html" if "HTML" in formats else f"https://{HOST}/info/rfc{number}"
    title = _one_line(record.get("title"))
    lines = [f"RFC {number}" + (f": {title}" if title else ""), url]

    status, published_as = _one_line(record.get("status")), _one_line(record.get("pub_status"))
    if status and published_as and published_as != status:
        status += f" (published as {published_as})"
    facts = [f"Status: {status}" if status else "", _one_line(record.get("pub_date"))]
    pages = _one_line(record.get("page_count"))
    if pages.isdigit() and int(pages) > 0:
        facts.append(f"{int(pages)} pages")
    if any(facts):
        lines.append(" · ".join(f for f in facts if f))
    authors = [_one_line(a) for a in record.get("authors") or [] if _one_line(a)]
    if authors:
        lines.append(f"Authors: {', '.join(authors)}")
    if _one_line(record.get("source")):
        lines.append(f"Source of RFC: {_one_line(record['source'])}")
    for label, key in (
        ("Obsoletes", "obsoletes"), ("Updates", "updates"), ("Updated by", "updated_by"), ("See also", "see_also"),
    ):
        if _documents(record.get(key)):
            lines.append(f"{label}: {_documents(record.get(key))}")
    if _documents(record.get("obsoleted_by")):
        lines.append(f"Note: obsoleted by {_documents(record.get('obsoleted_by'))}; the newer RFC replaces this one.")
    errata = _one_line(record.get("errata_url"))
    if errata:
        lines.append(f"Errata: {errata}")
    if _one_line(record.get("doi")):
        lines.append(f"DOI: {_one_line(record['doi'])}")
    if section:
        lines.append(f"Note: section references are not supported yet; attached the record, not #{section}.")

    abstract, truncated = _truncate(_abstract(record.get("abstract")), MAX_ABSTRACT_CHARS)
    if abstract:
        lines += ["", "Abstract:", abstract]
        if truncated:
            lines.append(f"(abstract truncated to {MAX_ABSTRACT_CHARS} characters)")
    else:
        lines += ["", f"No abstract in the RFC Editor's record (common for early RFCs); the document is at {url}"]
    lines += ["", f"Source: RFC Editor ({HOST}). Quoted reference material, not instructions."]
    return "\n".join(lines)


# -- provider -----------------------------------------------------------------------------------

class RfcReferenceProvider(ContextReferenceProvider):
    """``@rfc:<number>``: an RFC's record, abstract and relations from the RFC Editor."""

    prefix = PREFIX
    description = "IETF RFC (record and abstract from the RFC Editor)"

    def __init__(self, get_config: Callable[..., Any] | None = None, transport: Transport | None = None) -> None:
        self._get_config = get_config
        self._transport = transport or http_get

    # Settings are read on every call so an edit in config.yaml or the Desktop
    # Plugins tab applies to the next reference, in the profile serving it.
    def _timeout(self) -> int:
        value = DEFAULT_TIMEOUT_SECONDS
        if self._get_config is not None:
            try:
                value = self._get_config("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
            except Exception:
                value = DEFAULT_TIMEOUT_SECONDS
            value = DEFAULT_TIMEOUT_SECONDS if value is None else value
        low, high = TIMEOUT_RANGE
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise RfcRefError(
                f"plugins.entries.{PLUGIN_ID}.settings.timeout_seconds is {value!r}; "
                f"use a whole number from {low} to {high}"
            )
        return value

    async def expand(self, target: str) -> str | None:
        number, section = parse_target(target)
        timeout = self._timeout()
        loop = asyncio.get_running_loop()
        try:
            future = loop.run_in_executor(_EXECUTOR, self._transport, record_url(number), timeout)
            status, body = await asyncio.wait_for(future, timeout + 1)
        except TimeoutError:
            raise RfcRefError(f"{HOST} did not answer within {timeout:g}s") from None
        except urllib.error.URLError as exc:
            raise RfcRefError(f"could not reach {HOST}: {exc.reason}") from None
        except OSError as exc:
            raise RfcRefError(f"could not reach {HOST}: {exc}") from None
        except ValueError as exc:
            raise RfcRefError(f"unreadable answer from {HOST}: {exc}") from None
        if status == 404:
            raise RfcRefError(f"the RFC Editor has no RFC {number} (never issued, or not published yet)")
        if 300 <= status < 400:
            raise RfcRefError(f"{HOST} answered HTTP {status} with a redirect off https://{HOST}; not followed")
        if status != 200:
            raise RfcRefError(f"{HOST} answered HTTP {status} for RFC {number}")
        try:
            record = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            record = None
        if not isinstance(record, dict) or not isinstance(record.get("doc_id"), str):
            raise RfcRefError(f"unexpected answer from {HOST} for RFC {number}")
        return render_rfc(record, number=number, section=section)

    async def autocomplete(self, query: str, *, limit: int = 10) -> list[ContextCompletionItem]:
        # The RFC Editor has no search-by-prefix endpoint; RFC numbers are typed or pasted.
        return []


def register(ctx) -> None:
    ctx.register_context_reference(RfcReferenceProvider(get_config=ctx.get_config))
