"""The HTTP client for DTU Learn and Brightspace's API."""

import os
from urllib.parse import urlparse

import httpx

from .app import BASE, COOKIE_FILE, RUN


def _client() -> httpx.Client:
    cookie = os.environ.get("DTU_COOKIE", "").strip()
    if not cookie and COOKIE_FILE.exists():
        cookie = COOKIE_FILE.read_text().strip()
    if not cookie:
        raise RuntimeError(
            f"No DTU Learn session. Run: {RUN} login"
        )
    # In the cookie jar, not a Cookie header: httpx drops that header on every
    # redirect, so a short link to a course file, which redirects to the
    # file, arrived signed out and was sent to the login page. Scoped to
    # DTU Learn's host, so a redirect elsewhere (DTU's sign-in) never gets it.
    jar = httpx.Cookies()
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name:
            jar.set(name, value, domain=urlparse(BASE).hostname)
    return httpx.Client(
        base_url=BASE,
        cookies=jar,
        headers={"Accept": "application/json"},
        timeout=30,
        follow_redirects=True,
    )


def _get(path: str, **params):
    with _client() as c:
        r = c.get(path, params=params)
    if r.status_code in (401, 403):
        raise RuntimeError(
            "DTU Learn refused that (HTTP %d). Either the session has expired — "
            f"run '{RUN} login' again — or this is one of the "
            "things it keeps from students, such as an old course's calendar."
            % r.status_code
        )
    if r.status_code == 404:
        raise RuntimeError(
            f"DTU Learn has nothing at {path}. If that was a course id, check it "
            "against the ids list_courses prints in brackets."
        )
    r.raise_for_status()
    return r.json()


def _number(value: int | str) -> int:
    """An org unit id or course number as a number. A course number written
    as text keeps its leading zero, "02231", which JSON has no integer for."""
    try:
        return int(value)
    except (TypeError, ValueError):
        raise RuntimeError(f"{value!r} is not an org unit id or a course number, such as 02231.") from None


def _paged(path: str, **params):
    """D2L paginates with an opaque Bookmark; walk it until HasMoreItems is false."""
    items, bookmark = [], None
    while True:
        page = _get(path, **params, **({"bookmark": bookmark} if bookmark else {}))
        items.extend(page["Items"])
        info = page.get("PagingInfo") or {}
        if not info.get("HasMoreItems"):
            return items
        bookmark = info["Bookmark"]
