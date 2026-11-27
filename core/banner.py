"""Inject the 'dummy content' banner into HTML responses at serve time.

The banner is added by the server, never written into the user's file.
Non-HTML content (txt, json, and so on) is returned unchanged.
"""

from __future__ import annotations

import re

BANNER_TEXT = "תוכן דמה"

_BANNER_HTML = (
    '<div id="dummysite-banner" dir="rtl" style="'
    "position:fixed;bottom:0;left:0;right:0;z-index:2147483647;"
    "background:#b00020;color:#fff;font:600 14px sans-serif;"
    'text-align:center;padding:6px;box-shadow:0 -2px 6px rgba(0,0,0,.3)">'
    f"{BANNER_TEXT}</div>"
)

_BODY_OPEN = re.compile(r"<body\b[^>]*>", re.IGNORECASE)


def is_html(content_type: str) -> bool:
    return content_type.split(";")[0].strip().lower() == "text/html"


def inject(body: bytes, content_type: str) -> bytes:
    """Return the body with the banner added, when the content is HTML."""
    if not is_html(content_type):
        return body
    text = body.decode("utf-8", errors="replace")
    match = _BODY_OPEN.search(text)
    if match:
        pos = match.end()
        text = text[:pos] + _BANNER_HTML + text[pos:]
    else:
        text = _BANNER_HTML + text
    return text.encode("utf-8")
