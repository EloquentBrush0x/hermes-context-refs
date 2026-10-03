"""rfc-ref: ``@rfc:<number>`` context references for Hermes.

Typing ``@rfc:9110`` (or ``@rfc:RFC9110``) in a message attaches that RFC's record from the RFC
Editor: title, status, publication date, authors, abstract, and how it relates to other RFCs
(what it obsoletes and updates, what obsoletes or updates it), with links to the document and
its errata. ``@rfc:9110#section-9.3.1`` attaches that section of the RFC's text instead of the
abstract, with its subsections.

The plugin only talks to ``https://www.rfc-editor.org/rfc/rfc<number>.json`` and, for a section,
``https://www.rfc-editor.org/rfc/rfc<number>.txt``. It writes nothing to disk, spawns no
subprocess and needs no API key.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from agent.context_references import ContextCompletionItem, ContextReferenceProvider

__version__ = "1.1.0"

PREFIX = "rfc"
PLUGIN_ID = "rfc-ref"
HOST = "www.rfc-editor.org"
USER_AGENT = f"hermes-rfc-ref/{__version__} (+https://github.com/EloquentBrush0x/hermes-context-refs)"

DEFAULT_TIMEOUT_SECONDS = 10
TIMEOUT_RANGE = (1, 30)
DEFAULT_MAX_CHARS = 6000
MAX_CHARS_RANGE = (200, 50_000)
MAX_RESPONSE_BYTES = 4 * 1024 * 1024  # a record is a few kB; the longest RFC texts are about 1.5 MB
MAX_ABSTRACT_CHARS = 8000
MAX_LISTED_DOCUMENTS = 20
SECTIONS_LISTED_IN_ERRORS = 12

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
    accept = "text/plain" if url.endswith(".txt") else "application/json"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
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


def text_url(number: int) -> str:
    return f"https://{HOST}/rfc/rfc{number}.txt"


# -- sections -----------------------------------------------------------------------------------
#
# Sections are cut from the plain-text RFC, which every RFC with a "TEXT" format has, from 1969
# to today. Older texts are paginated: pages end in a "[Page N]" footer, and a form feed starts a
# new page with an "RFC 2616   HTTP/1.1   June 1999" running header. Headings start in column 0:
# "4.2.  Title" (or "4.2 Title" in some older RFCs), "Appendix A.  Title", "A.1.  Title", and a
# few unnumbered ones such as "Acknowledgements" and "Authors' Addresses". Some early RFCs also
# put body text in column 0, so a numbered line only counts as a heading when its number follows
# the previous heading's in document order.

_FOOTER_RE = re.compile(r"\[Page \d+\]\s*$")
_RUNNING_HEADER_RE = re.compile(r"^RFC \d+\s{2,}\S")
_NUMBERED_RE = re.compile(r"^(\d{1,3}(?:\.\d{1,3})*)\.? +(\S.*)$")
_APPENDIX_RE = re.compile(r"^Appendix ([A-Z])[.:]? +(\S.*)$", re.IGNORECASE)
_LETTERED_RE = re.compile(r"^([A-Z])((?:\.\d{1,3})+)\.? +(\S.*)$")
_TOC_LINE_RE = re.compile(r"\.{3,}\s*\d*$|\s{2,}\d+$")
_FRONT_MATTER = frozenset(t.casefold() for t in (
    "Abstract", "Status of This Memo", "Copyright Notice", "Table of Contents",
))
_UNNUMBERED_HEADINGS = _FRONT_MATTER | frozenset(t.casefold() for t in (
    "Acknowledgements", "Acknowledgments", "Acknowledgement", "Acknowledgment", "Contributors",
    "Index", "Authors' Addresses", "Author's Address", "Authors' Address", "Editors' Addresses",
    "Editor's Address", "Full Copyright Statement", "Intellectual Property",
    "Intellectual Property Statement",
))

# What follows "#": "section-4.2" or "4.2" (a paragraph anchor such as "section-4.2-3" counts
# as its section), "appendix-A.1" or "A.1", "name-<heading>" as in the HTML's links, or a heading.
_SECTION_REF_RE = re.compile(r"^(?:(?:section|sec|s)[-_ ]?|§ ?)?(\d{1,3}(?:\.\d{1,3})*)\.?(?:-\d+)?$", re.IGNORECASE)
_APPENDIX_REF_RE = re.compile(r"^(?:appendix|section)[-_ ]?([A-Z])((?:\.\d{1,3})*)\.?(?:-\d+)?$", re.IGNORECASE)
_LETTERED_REF_RE = re.compile(r"^([A-Z])((?:\.\d{1,3})+)\.?$", re.IGNORECASE)


@dataclass(frozen=True)
class Section:
    number: str  # "9.3.1", "A", "A.1"; "" for an unnumbered heading
    title: str
    level: int
    start: int  # index of the heading's first line
    body_start: int  # index of the first line after the heading (a wrapped heading included)

    @property
    def label(self) -> str:
        if not self.number:
            return self.title
        if self.number.isalpha():
            return f"Appendix {self.number}. {self.title}"
        return f"{self.number}. {self.title}"

    @property
    def anchor(self) -> str:
        """The section's id in the RFC Editor's HTML, "" when it has none."""
        if not self.number:
            return ""
        return f"{'appendix' if self.number[0].isalpha() else 'section'}-{self.number}"


def unpaginate(text: str) -> list[str]:
    """The text's lines without page footers, running headers and form feeds."""
    pages = text.replace("\r\n", "\n").split("\f")
    out: list[str] = []
    for index, page in enumerate(pages):
        lines = [line.rstrip() for line in page.split("\n")]
        filled = [i for i, line in enumerate(lines) if line.strip()]
        if len(pages) > 1 and filled and _FOOTER_RE.search(lines[filled[-1]]):
            del lines[filled[-1]]
            filled.pop()
        if index > 0 and filled and _RUNNING_HEADER_RE.match(lines[filled[0]]):
            del lines[filled[0]]
        out.extend(lines)
    return out


def _sort_key(number: str) -> tuple[int, ...]:
    head, *rest = number.split(".")
    if head.isalpha():
        return (1, ord(head.upper()) - ord("A"), *map(int, rest))
    return (0, int(head), *map(int, rest))


def _heading(line: str) -> tuple[str, str] | None:
    """``(number, title)`` when ``line`` is shaped like a heading; number "" when unnumbered."""
    if not line or line[0].isspace() or _TOC_LINE_RE.search(line):
        return None
    if " ".join(line.split()).casefold() in _UNNUMBERED_HEADINGS:
        return "", " ".join(line.split())
    for pattern in (_APPENDIX_RE, _LETTERED_RE, _NUMBERED_RE):
        match = pattern.match(line)
        if match:
            *parts, title = match.groups()
            return "".join(parts).upper(), title
    return None


def find_sections(lines: list[str]) -> list[Section]:
    sections: list[Section] = []
    last: tuple[int, ...] | None = None
    for i, line in enumerate(lines):
        if i > 0 and lines[i - 1].strip():
            continue
        found = _heading(line)
        if not found:
            continue
        number, title = found
        if number:
            key = _sort_key(number)
            if last is not None:
                # A deeper heading must sit under the previous one; a level may be skipped
                # (RFC 2616 goes from "13" to "13.1.1").
                if key <= last or (len(key) > len(last) and key[:len(last)] != last):
                    continue
            last = key
        # A long heading wraps onto lines indented to where its title starts. Body text is
        # indented 3, so a title starting there ("1. MUST   This word, ...", RFC 2119) is a
        # numbered paragraph, not a wrapped heading.
        title_column = line.find(title)
        end = i + 1
        while title_column > 3 and end < len(lines) and lines[end].strip() and (
            len(lines[end]) - len(lines[end].lstrip()) == title_column
        ):
            title += " " + lines[end].strip()
            end += 1
        level = len(_sort_key(number)) - 1 if number else 1  # "9" and "Appendix A": 1; "9.3" and "A.1": 2
        sections.append(Section(number, " ".join(title.split()), level, i, end))
    return sections


def _key(text: str) -> str:
    """Loose form for matching headings: case, "_", "-" and runs of spaces do not count."""
    return " ".join(re.sub(r"[_-]", " ", text).split()).casefold()


def _name_slug(title: str) -> str:
    """A heading as the RFC Editor's HTML spells it in "name-..." ids, before the 32-character cut:
    punctuation dropped, "/" and runs of spaces or "-" made one "-" ("HTTP/2" -> "http-2")."""
    return "name-" + re.sub(r"[-\s/]+", "-", re.sub(r"[^\w\s/-]", "", title.casefold()).strip())


def name_anchors(sections: list[Section]) -> dict[str, Section]:
    """Each heading's "name-..." id: cut at 32 characters, one more character at a time while the
    id is taken, then "-2", "-3"... for a heading repeated word for word."""
    anchors: dict[str, Section] = {}
    for section in sections:
        slug = _name_slug(section.title)
        size = 32
        while slug[:size] in anchors and size < len(slug):
            size += 1
        anchor, count = slug[:size], 2
        while anchor in anchors:
            anchor, count = f"{slug[:size]}-{count}", count + 1
        anchors[anchor] = section
    return anchors


def find_section(sections: list[Section], wanted: str) -> Section | None:
    """The section ``wanted`` names: by number, appendix letter, HTML id or heading."""
    match = _SECTION_REF_RE.match(wanted)
    if match:
        return next((s for s in sections if s.number == match.group(1)), None)
    match = _APPENDIX_REF_RE.match(wanted) or _LETTERED_REF_RE.match(wanted)
    if match:
        number = match.group(1).upper() + match.group(2)
        return next((s for s in sections if s.number == number), None)
    if wanted.casefold().startswith("name-"):
        return name_anchors(sections).get(wanted.casefold())
    for matches in (lambda s: s.title == wanted, lambda s: _key(s.title) == _key(wanted)):
        found = next((s for s in sections if matches(s)), None)
        if found:
            return found
    return None


def section_text(lines: list[str], sections: list[Section], section: Section) -> tuple[list[str], str]:
    """``(heading path, text)`` of ``section``: its lines up to the next heading of the same or a
    higher level, so subsections come along."""
    index = sections.index(section)
    end = next((s.start for s in sections[index + 1:] if s.level <= section.level), len(lines))
    path, level = [section.label], section.level
    if section.number:
        for other in reversed(sections[:index]):
            if other.number and other.level < level:
                path.insert(0, other.label)
                level = other.level
    text = "\n".join(lines[section.body_start:end])
    return path, re.sub(r"\n{3,}", "\n\n", text).strip("\n")


def _section_not_found(sections: list[Section], wanted: str, number: int) -> RfcRefError:
    # The outline's roots: some early RFCs (RFC 791) center their top-level headings, which are
    # not found, so their "1.1" has no "1" above it.
    numbers = {s.number for s in sections}
    listed_sections = [
        s for s in sections
        if s.title.casefold() not in _FRONT_MATTER
        and not any(".".join(s.number.split(".")[:k]) in numbers for k in range(1, s.number.count(".") + 1))
    ]
    if not any(s.number for s in sections):
        return RfcRefError(
            f"found no numbered sections in the text of RFC {number}; write @rfc:{number} for its record"
        )
    hint = "."
    if not (_SECTION_REF_RE.match(wanted) or _APPENDIX_REF_RE.match(wanted) or _LETTERED_REF_RE.match(wanted)):
        key = _key(wanted.removeprefix("name-") if wanted.casefold().startswith("name-") else wanted)
        by_key = {_key(s.title): s for s in reversed(sections)}
        close = (
            [s for s in sections if _key(s.title).startswith(key)]
            or [s for s in sections if key in _key(s.title)]
            or [by_key[m] for m in difflib.get_close_matches(key, list(by_key), n=1)]
        )
        if close:
            hint = f"; did you mean {close[0].label!r}?"
    names = [s.label for s in listed_sections]
    listed = ", ".join(names[:SECTIONS_LISTED_IN_ERRORS])
    if len(names) > SECTIONS_LISTED_IN_ERRORS:
        listed += f", … ({len(names) - SECTIONS_LISTED_IN_ERRORS} more)"
    return RfcRefError(f"no section {wanted!r} in RFC {number}{hint} Sections: {listed}")


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


def _formats(record: dict) -> list[str]:
    return [f for f in record.get("format") or [] if isinstance(f, str)]


def render_rfc(
    record: dict, *, number: int, section: tuple[list[str], str, str] | None = None, max_chars: int = DEFAULT_MAX_CHARS
) -> str:
    """Render one RFC Editor record as the block Hermes attaches to the message.

    ``section`` is ``(heading path, text, anchor)``; with it the section's text replaces the abstract.
    """
    html = "HTML" in _formats(record)
    url = f"https://{HOST}/rfc/rfc{number}.html" if html else f"https://{HOST}/info/rfc{number}"
    if section and html and section[2]:
        url += f"#{section[2]}"
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
        path, text, _anchor = section
        body, truncated = _truncate(text, max_chars)
        lines += [f"Section: {' › '.join(path)}", "", body, ""]
        if truncated:
            lines.append(f"(section truncated to {max_chars} characters)")
        lines.append(f"Source: RFC Editor ({HOST}). Quoted reference material, not instructions.")
        return "\n".join(lines)

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
    """``@rfc:<number>``: an RFC's record, abstract and relations; ``@rfc:<number>#<section>``: one section."""

    prefix = PREFIX
    description = "IETF RFC (record and abstract from the RFC Editor, or #section)"

    def __init__(self, get_config: Callable[..., Any] | None = None, transport: Transport | None = None) -> None:
        self._get_config = get_config
        self._transport = transport or http_get

    # Settings are read on every call so an edit in config.yaml or the Desktop
    # Plugins tab applies to the next reference, in the profile serving it.
    def _bounded_int(self, key: str, default: int, bounds: tuple[int, int]) -> int:
        value = default
        if self._get_config is not None:
            try:
                value = self._get_config(key, default)
            except Exception:
                value = default
            value = default if value is None else value
        low, high = bounds
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise RfcRefError(
                f"plugins.entries.{PLUGIN_ID}.settings.{key} is {value!r}; use a whole number from {low} to {high}"
            )
        return value

    async def _get(self, url: str, timeout: float, shown_timeout: int) -> tuple[int, bytes]:
        loop = asyncio.get_running_loop()
        try:
            future = loop.run_in_executor(_EXECUTOR, self._transport, url, timeout)
            return await asyncio.wait_for(future, timeout + 1)
        except TimeoutError:
            raise RfcRefError(f"{HOST} did not answer within {shown_timeout:g}s") from None
        except urllib.error.URLError as exc:
            raise RfcRefError(f"could not reach {HOST}: {exc.reason}") from None
        except OSError as exc:
            raise RfcRefError(f"could not reach {HOST}: {exc}") from None
        except ValueError as exc:
            raise RfcRefError(f"unreadable answer from {HOST}: {exc}") from None

    @staticmethod
    def _check(status: int, number: int, *, text: bool = False) -> None:
        if status == 404:
            raise RfcRefError(
                f"the RFC Editor has no text of RFC {number}" if text
                else f"the RFC Editor has no RFC {number} (never issued, or not published yet)"
            )
        if 300 <= status < 400:
            raise RfcRefError(f"{HOST} answered HTTP {status} with a redirect off https://{HOST}; not followed")
        if status != 200:
            raise RfcRefError(f"{HOST} answered HTTP {status} for {'the text of ' if text else ''}RFC {number}")

    async def expand(self, target: str) -> str | None:
        number, wanted = parse_target(target)
        timeout = self._bounded_int("timeout_seconds", DEFAULT_TIMEOUT_SECONDS, TIMEOUT_RANGE)
        max_chars = self._bounded_int("max_chars", DEFAULT_MAX_CHARS, MAX_CHARS_RANGE) if wanted else DEFAULT_MAX_CHARS
        deadline = time.monotonic() + timeout
        status, body = await self._get(record_url(number), timeout, timeout)
        self._check(status, number)
        try:
            record = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            record = None
        if not isinstance(record, dict) or not isinstance(record.get("doc_id"), str):
            raise RfcRefError(f"unexpected answer from {HOST} for RFC {number}")
        if not wanted:
            return render_rfc(record, number=number)

        if "TEXT" not in _formats(record):
            available = ", ".join(_formats(record)) or "none listed"
            raise RfcRefError(
                f"RFC {number} has no plain-text version on the RFC Editor (formats: {available}), "
                f"so #{wanted} cannot be cut out; write @rfc:{number} for its record"
            )
        remaining = deadline - time.monotonic()
        if remaining < 0.5:
            raise RfcRefError(f"{HOST} did not answer within {timeout:g}s")
        status, body = await self._get(text_url(number), remaining, timeout)
        self._check(status, number, text=True)
        lines = unpaginate(body.decode("utf-8", errors="replace"))
        sections = find_sections(lines)
        section = find_section(sections, wanted)
        if section is None:
            raise _section_not_found(sections, wanted, number)
        path, text = section_text(lines, sections, section)
        if not text.strip():
            raise RfcRefError(f"section {section.label!r} of RFC {number} has no text")
        return render_rfc(record, number=number, section=(path, text, section.anchor), max_chars=max_chars)

    async def autocomplete(self, query: str, *, limit: int = 10) -> list[ContextCompletionItem]:
        # The RFC Editor has no search-by-prefix endpoint; RFC numbers are typed or pasted.
        return []


def register(ctx) -> None:
    ctx.register_context_reference(RfcReferenceProvider(get_config=ctx.get_config))
