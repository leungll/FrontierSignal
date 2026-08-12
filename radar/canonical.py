"""URL canonicalization — dedupe layer 1, and the cheapest one."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING_PARAMS = {
    "ref", "referrer", "source", "fbclid", "gclid", "mc_cid", "mc_eid",
    "igshid", "s", "spm", "share", "__twitter_impression",
}

ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})")


def canonicalize(url: str) -> str:
    """Collapse tracking params, www, trailing slashes, and arXiv abs/pdf/version
    variants down to one stable key."""
    u = urlparse(url.strip())
    host = u.netloc.lower().removeprefix("www.")

    # arXiv: abs/2601.12345v3 and pdf/2601.12345v1 are the same paper.
    if "arxiv.org" in host:
        m = ARXIV_ID_RE.search(u.path)
        if m:
            return f"arxiv:{m.group(1)}"

    qs = [
        (k, v)
        for k, v in parse_qsl(u.query)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    path = u.path.rstrip("/") or "/"
    return urlunparse(("https", host, path, "", urlencode(sorted(qs)), ""))
