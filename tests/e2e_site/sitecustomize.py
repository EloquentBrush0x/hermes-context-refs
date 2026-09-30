"""Test-only: send a Hermes subprocess's Wikipedia, OSV, GitHub and PEP requests to the local fake server.

Placed on PYTHONPATH by tests/test_e2e_chat.py. Active only when CONTEXT_REFS_TEST_ORIGIN is
set; the original host is passed along in the X-Test-Host header.
"""

import os
import urllib.parse
import urllib.request

_ORIGIN = os.environ.get("CONTEXT_REFS_TEST_ORIGIN")
_HOSTS = ("api.osv.dev", "api.github.com", "peps.python.org")

if _ORIGIN:
    _real_open = urllib.request.OpenerDirector.open

    def _routed_open(self, fullurl, *args, **kwargs):
        if isinstance(fullurl, urllib.request.Request):
            url = urllib.parse.urlsplit(fullurl.full_url)
            host = url.hostname or ""
            if url.scheme == "https" and (host.endswith(".wikipedia.org") or host in _HOSTS):
                routed = urllib.request.Request(
                    f"{_ORIGIN}{url.path}?{url.query}", headers=dict(fullurl.header_items())
                )
                routed.add_header("X-Test-Host", host)
                fullurl = routed
        return _real_open(self, fullurl, *args, **kwargs)

    urllib.request.OpenerDirector.open = _routed_open
