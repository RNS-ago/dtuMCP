"""Tests for dtumcp.auth."""
import json
import os
import tempfile
from pathlib import Path

from _package import mod


def test_profiles():
    INI = """[Install4F96D1932A9F858E]
Default=t8zsz31d.default-release
Locked=1

[Profile2]
Name=never-used
IsRelative=1
Path=zzzzzzzz.never-used

[Profile1]
Name=default
IsRelative=1
Path=prxr6jl6.default
Default=1

[Profile0]
Name=default-release
IsRelative=1
Path=t8zsz31d.default-release
"""

    with tempfile.TemporaryDirectory() as tmp:
        firefox = Path(tmp) / ".mozilla" / "firefox"
        for profile in ("t8zsz31d.default-release", "prxr6jl6.default"):
            (firefox / profile).mkdir(parents=True)
            (firefox / profile / "cookies.sqlite").touch()
        (firefox / "profiles.ini").write_text(INI)
        os.environ["HOME"] = tmp
        os.environ.pop("DTU_FIREFOX_PROFILE", None)

        # The Install section's profile leads, then Default=1; a profile with no
        # cookie database is left out.
        assert [(label, path) for label, _, path in mod._profiles()] == [
            ("Firefox: default-release (default)", firefox / "t8zsz31d.default-release"),
            ("Firefox: default", firefox / "prxr6jl6.default"),
        ], mod._profiles()

        # The override skips the scan.
        os.environ["DTU_FIREFOX_PROFILE"] = "/somewhere/else"
        assert mod._profiles() == [("DTU_FIREFOX_PROFILE", [], Path("/somewhere/else"))]
        os.environ.pop("DTU_FIREFOX_PROFILE")

        # No profiles.ini anywhere means nothing found, not a crash.
        (firefox / "profiles.ini").unlink()
        assert mod._profiles() == []
        # With no Firefox or Chrome, login goes straight to the default browser.
        assert mod._pick_browser()[2] is mod._pasted_cookies

        # Windows keeps profiles under %APPDATA%, which is ~/AppData/Roaming.
        windows = Path(tmp) / "AppData" / "Roaming" / "Mozilla" / "Firefox"
        (windows / "t8zsz31d.default-release").mkdir(parents=True)
        (windows / "t8zsz31d.default-release" / "cookies.sqlite").touch()
        (windows / "profiles.ini").write_text(INI)
        assert [(label, path) for label, _, path in mod._profiles()] == [
            ("Firefox (Windows): default-release (default)", windows / "t8zsz31d.default-release"),
        ], mod._profiles()


def test_cookies_from_chrome():
    # Asked over DevTools at the port Chrome wrote down: the answer comes after
    # any event, only DTU Learn's cookies are kept, and the window is closed.
    with tempfile.TemporaryDirectory() as tmp:
        mod.CHROME_PROFILE = Path(tmp)
        (Path(tmp) / "DevToolsActivePort").write_text("41234\n/devtools/browser/abc\n")
        sent, replies = [], iter([{"method": "Target.targetCreated"}, {"id": 1, "result": {"cookies": [
            {"name": "d2lSessionVal", "value": "s", "domain": "learn.inside.dtu.dk"},
            {"name": "d2lSecureSessionVal", "value": "t", "domain": ".learn.inside.dtu.dk"},
            {"name": "ESTSAUTH", "value": "x", "domain": ".login.microsoftonline.com"},
        ]}}])

        class Socket:
            def __init__(self, url, **_):
                sent.append(url)
            __enter__ = lambda self: self
            __exit__ = lambda self, *exc: None
            send = lambda self, text: sent.append(json.loads(text)["method"])
            recv = lambda self: json.dumps(next(replies))

        mod.websocket = Socket
        assert mod._cookies_from_chrome() == "d2lSessionVal=s; d2lSecureSessionVal=t"
        assert sent == ["ws://127.0.0.1:41234/devtools/browser/abc", "Storage.getCookies", "Browser.close"], sent
