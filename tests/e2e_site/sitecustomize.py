"""Test-only: send a Hermes subprocess's Wikipedia requests to the local fake server.

Placed on PYTHONPATH by tests/test_e2e_chat.py. Active only when WIKI_REF_TEST_ORIGIN is
set; the original Wikipedia language is passed along in the X-Test-Wiki-Language header.
"""

import os
import urllib.parse
import urllib.request

_ORIGIN = os.environ.get("WIKI_REF_TEST_ORIGIN")

if _ORIGIN:
    _real_open = urllib.request.OpenerDirector.open

    def _routed_open(self, fullurl, *args, **kwargs):
        if isinstance(fullurl, urllib.request.Request):
            url = urllib.parse.urlsplit(fullurl.full_url)
            host = url.hostname or ""
            if url.scheme == "https" and host.endswith(".wikipedia.org"):
                routed = urllib.request.Request(
                    f"{_ORIGIN}{url.path}?{url.query}", headers=dict(fullurl.header_items())
                )
                routed.add_header("X-Test-Wiki-Language", host[: -len(".wikipedia.org")])
                fullurl = routed
        return _real_open(self, fullurl, *args, **kwargs)

    urllib.request.OpenerDirector.open = _routed_open
