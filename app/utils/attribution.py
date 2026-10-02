"""Ad attribution: remember utm_* / ttclid from the landing URL in a 30-day cookie, read it back at signup."""
from __future__ import annotations

import json
from urllib.parse import quote, unquote

from fastapi import Request, Response

COOKIE = "tf_attr"
KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "ttclid")
MAX_VALUE = 120


def capture(request: Request, response: Response) -> None:
    """Call on landing-page views. Only overwrites when the URL carries tracking params."""
    found = {k: request.query_params[k][:MAX_VALUE] for k in KEYS if request.query_params.get(k)}
    if not found:
        return
    ref = request.headers.get("referer", "")[:200]
    if ref:
        found["referrer"] = ref
    response.set_cookie(COOKIE, quote(json.dumps(found)), max_age=60 * 60 * 24 * 30,
                        httponly=True, samesite="lax", secure=request.url.scheme == "https")


def read(request: Request) -> dict | None:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        data = json.loads(unquote(raw))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return {k: str(v)[:200] for k, v in data.items() if k in KEYS or k == "referrer"} or None
