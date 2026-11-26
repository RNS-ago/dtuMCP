"""Signing in: finding a browser profile and lifting DTU Learn's session cookies."""

import configparser
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import webbrowser
from functools import partial
from pathlib import Path
from urllib.parse import urlparse

from websockets.sync.client import connect as websocket

from .app import BASE, COOKIE_FILE
from .tools import list_courses


# The Firefox installs whose cookie databases login reads. LibreWolf never
# writes session cookies to disk, so it is not one; Chrome is asked over its
# DevTools instead (see CHROMES), and any other browser can still be used
# through the default browser entry, by pasting its cookies.
BROWSERS = {
    # %APPDATA% is unset off Windows, so this never matches there.
    "Firefox (Windows)": (
        [os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Mozilla Firefox", "firefox.exe")],
        os.path.join(os.environ.get("APPDATA", "~/AppData/Roaming"), "Mozilla", "Firefox"),
    ),
    "Firefox": (["firefox"], "~/.mozilla/firefox"),
    "Firefox (snap)": (["firefox"], "~/snap/firefox/common/.mozilla/firefox"),
    "Firefox (flatpak)": (
        ["flatpak", "run", "org.mozilla.firefox"],
        "~/.var/app/org.mozilla.firefox/.mozilla/firefox",
    ),
}


def _profiles() -> list[tuple[str, list[str], Path]]:
    """Every browser profile on this machine that holds a cookie database, as
    (label, launch command, profile directory). Each install's own default
    comes first, so the first entry is the best guess."""
    override = os.environ.get("DTU_FIREFOX_PROFILE")
    if override:
        return [("DTU_FIREFOX_PROFILE", [], Path(override).expanduser())]
    found = []
    for browser, (command, root) in BROWSERS.items():
        root = Path(root).expanduser()
        ini = configparser.ConfigParser()
        ini.read(root / "profiles.ini")
        # The [InstallXXXX] section names the profile the browser actually
        # launches. The Default=1 flag under a [ProfileN] section is often a
        # stale older profile, so it only ranks second.
        launched = {ini[s]["Default"] for s in ini.sections()
                    if s.startswith("Install") and ini[s].get("Default")}
        ranked = []
        for s in ini.sections():
            if not s.startswith("Profile") or "Path" not in ini[s]:
                continue
            path = ini[s]["Path"]
            if not (root / path / "cookies.sqlite").exists():
                continue
            rank = 0 if path in launched else 1 if ini[s].get("Default") == "1" else 2
            name = ini[s].get("Name", path) + (" (default)" if rank == 0 else "")
            ranked.append((rank, f"{browser}: {name}", command, root / path))
        found += [e[1:] for e in sorted(ranked, key=lambda e: e[0])]
    return found


# Chrome encrypts its cookie database, on Windows with a key only Chrome
# itself may use, and by default keeps session cookies such as d2lSessionVal
# in memory only. So login asks the running browser over the DevTools
# protocol, which Chrome serves only for a profile other than your usual one:
# DTU Learn gets a profile of its own, kept for next time.
CHROME_PROFILE = COOKIE_FILE.parent / "chrome"
CHROMES = ["google-chrome", "google-chrome-stable", *(
    os.path.join(os.environ[root], "Google", "Chrome", "Application", "chrome.exe")
    for root in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA") if root in os.environ
)]


def _pick_browser() -> tuple:
    """The browser to sign in with: Firefox profiles, then Chrome, then the
    default browser. Returns its label, the command that opens a URL in it
    ([] for the default browser) and what reads its session cookies after."""
    found = [
        (label, [*command, "--profile", str(path)] if command else [],
         partial(_cookies_from_firefox, path), path)
        for label, command, path in _profiles()
    ]
    if not os.environ.get("DTU_FIREFOX_PROFILE"):
        chrome = next(filter(None, map(shutil.which, CHROMES)), None)
        if chrome:
            found.append(("Chrome, in a window of its own", [
                chrome, f"--user-data-dir={CHROME_PROFILE}", "--remote-debugging-port=0",
                "--no-first-run", "--no-default-browser-check",
            ], _cookies_from_chrome, CHROME_PROFILE))
        found.append(("Default browser (you copy the cookies over)", [], _pasted_cookies, None))
    if len(found) == 1:
        return found[0][:3]
    print("Browsers found:")
    for i, (label, _, _, path) in enumerate(found, 1):
        print(f"  {i}. {label}" + (f"  [{path}]" if path else ""))
    while True:
        choice = input("Sign in with which one? [1]: ").strip() or "1"
        if choice.isdigit() and 1 <= int(choice) <= len(found):
            return found[int(choice) - 1][:3]
        print(f"Enter a number from 1 to {len(found)}.")


def _mozlz4(path: Path) -> bytes:
    """Decode Firefox's mozlz4 container: an 8-byte magic, a little-endian
    uncompressed size, then one LZ4 block. Decoding the block inline keeps this
    dependency-free."""
    raw = path.read_bytes()
    if raw[:8] != b"mozLz40\0":
        raise RuntimeError(f"{path} is not a mozlz4 file.")
    src, out, i = raw[12:], bytearray(), 0
    while i < len(src):
        token = src[i]
        i += 1
        literal = token >> 4
        if literal == 15:  # lengths of 15 continue in 0xFF-terminated bytes
            while True:
                extra = src[i]
                i += 1
                literal += extra
                if extra != 255:
                    break
        out += src[i : i + literal]
        i += literal
        if i >= len(src) - 1:  # the final block is literals only
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        match = token & 0x0F
        if match == 15:
            while True:
                extra = src[i]
                i += 1
                match += extra
                if extra != 255:
                    break
        start = len(out) - offset
        for j in range(match + 4):  # matches may overlap, so copy byte by byte
            out.append(out[start + j])
    return bytes(out)


def _session_cookies(profile: Path, host: str) -> dict[str, str]:
    """Session cookies have no expiry, so Firefox never writes them to
    cookies.sqlite. They live in memory, mirrored into the session-restore file
    so that they survive a restart. D2L's d2lSessionVal is one of these."""
    store = profile / "sessionstore-backups" / "recovery.jsonlz4"
    if not store.exists():
        return {}
    data = json.loads(_mozlz4(store))
    return {
        c["name"]: c["value"]
        for c in data.get("cookies", [])
        if c.get("host") in (host, f".{host}")
    }


def _cookies_from_firefox(profile: Path) -> str:
    host = urlparse(BASE).hostname
    db = profile / "cookies.sqlite"
    if not db.exists():
        raise RuntimeError(f"No cookie database at {db}.")
    with tempfile.TemporaryDirectory() as tmp:
        # Firefox holds a lock on the live database, so read a copy of it
        # instead. The -wal file carries writes not yet folded into the main
        # file; without it a cookie from this session can be missing.
        for part in db.parent.glob("cookies.sqlite*"):
            shutil.copy(part, tmp)
        con = sqlite3.connect(Path(tmp) / "cookies.sqlite")
        jar = dict(
            con.execute(
                "SELECT name, value FROM moz_cookies WHERE host = ? OR host = ?",
                (host, f".{host}"),
            )
        )
        con.close()
    jar.update(_session_cookies(profile, host))
    return "; ".join(f"{name}={value}" for name, value in jar.items())


def _cookies_from_chrome() -> str:
    """Ask the Chrome that login started for DTU Learn's cookies, over the
    DevTools protocol (see CHROME_PROFILE), then close it."""
    try:
        port, path = (CHROME_PROFILE / "DevToolsActivePort").read_text().split()[:2]
    except (OSError, ValueError):
        raise SystemExit("Chrome did not start with DTU Learn's profile. Run login again.")
    host = urlparse(BASE).hostname
    try:
        with websocket(f"ws://127.0.0.1:{port}{path}", max_size=None) as ws:
            ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
            while (reply := json.loads(ws.recv())).get("id") != 1:
                pass  # an event, not the answer
            # The window was only for signing in; the profile keeps the sign-in.
            ws.send(json.dumps({"id": 2, "method": "Browser.close"}))
    except OSError:
        raise SystemExit("Chrome was closed before its cookies could be read. Run login again and leave its window open.")
    return "; ".join(
        f"{c['name']}={c['value']}" for c in reply["result"]["cookies"]
        if c["domain"] in (host, f".{host}")
    )


def _pasted_cookies() -> str:
    """The session cookies, copied by hand out of the browser's developer
    tools: only Firefox's can be read off the disk."""
    print(
        "\nCopy the session cookies over: open the browser's developer tools "
        "(F12), then Storage (Firefox) or Application (Chrome, Edge) → Cookies "
        f"→ {BASE}, and paste each value here."
    )
    jar = {name: input(f"{name}: ").strip() for name in ("d2lSessionVal", "d2lSecureSessionVal")}
    if not all(jar.values()):
        raise SystemExit("Both cookies are needed. Run login again once you have them.")
    return "; ".join(f"{name}={value}" for name, value in jar.items())


def login() -> None:
    """Open DTU Learn, wait for you to sign in, then save the session cookies."""
    label, command, read = _pick_browser()
    # Before the browser starts: Chrome's profile for DTU Learn goes in here too.
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.parent.chmod(0o700)
    url = f"{BASE}/d2l/home"
    print(f"Opening {url} in {label}. Sign in there, then return to this terminal.")
    if command and shutil.which(command[0]):
        # Naming the profile makes the browser open the URL in that profile,
        # handing it to the running window if there is one.
        subprocess.Popen(
            [*command, url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    else:
        webbrowser.open(url)
    input("Press Enter once you are logged in: ")
    cookie = read()
    if "d2lSessionVal" not in cookie:
        raise SystemExit(
            f"No DTU Learn session cookie in {label}. Check that you signed in "
            "there and that the login finished. Firefox rewrites its "
            "session-restore file every ~15 seconds, so if the login just "
            "completed, wait a moment and run login again."
        )
    COOKIE_FILE.write_text(cookie)
    COOKIE_FILE.chmod(0o600)
    print(f"Saved session to {COOKIE_FILE}\n")
    print(list_courses())
