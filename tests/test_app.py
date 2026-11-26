"""Tests for dtumcp.app."""
import httpx

from _package import mod


def test_status_error():
    # An error answer from DTU Learn says what it is, and a 5xx that the
    # trouble is DTU Learn's; the URL goes without its query (a signed link).
    def error(code):
        request = httpx.Request("GET", "https://learn.inside.dtu.dk/d2l/api/le/1.0/5/news/?token=secret")
        return httpx.HTTPStatusError("x", request=request, response=httpx.Response(code, request=request))

    got = mod._status_error(error(503))
    assert got == ("DTU Learn answered 503 Service Unavailable for https://learn.inside.dtu.dk/d2l/api/le/1.0/5/news/: "
                   "DTU Learn is down or overloaded, not this server. Try again in a few minutes."), got
    assert mod._status_error(error(404)).endswith("404 Not Found for https://learn.inside.dtu.dk/d2l/api/le/1.0/5/news/.")
