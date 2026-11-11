# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp[cli]>=2", "httpx"]
# ///
"""MCP server for DTU Learn (D2L Brightspace) course material.

Auth: run `uv run --script server.py login`. That lists the Firefox
profiles it finds, opens DTU Learn in the one you pick, waits for you
to sign in, lifts the session cookies out of that profile's cookie database and
saves them to ~/.config/dtumcp/cookie.

DTU_COOKIE still overrides the saved file if you would rather paste a Cookie
header from devtools yourself.
"""

import configparser
import inspect
import json
import logging
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import webbrowser
import zipfile
from datetime import datetime, timedelta, timezone
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache, partial
from itertools import count
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import anyio
import httpx
from mcp.server.mcpserver import Context, Image, MCPServer
from mcp.server.transport_security import TransportSecuritySettings

BASE = os.environ.get("DTU_BASE", "https://learn.inside.dtu.dk")
LP, LE = "1.63", "1.97"
DOWNLOADS = Path(os.environ.get("DTU_DOWNLOADS", "~/Downloads/dtulearn")).expanduser()
COOKIE_FILE = Path(os.environ.get("DTU_COOKIE_FILE", "~/.config/dtumcp/cookie")).expanduser()

mcp = MCPServer("dtu-learn")
logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request


# Chromium browsers (Chrome, Brave, Edge) encrypt their cookies with a key in
# the desktop keyring, and LibreWolf never writes session cookies to disk, so
# only Firefox is listed here.
BROWSERS = {
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


def _pick_profile() -> tuple[str, list[str], Path]:
    found = _profiles()
    if not found:
        raise RuntimeError(
            "No Firefox profile found. Set DTU_FIREFOX_PROFILE to "
            "your profile directory."
        )
    if len(found) == 1:
        return found[0]
    print("Browsers found:")
    for i, (label, _, path) in enumerate(found, 1):
        print(f"  {i}. {label}  [{path}]")
    while True:
        choice = input("Sign in with which one? [1]: ").strip() or "1"
        if choice.isdigit() and 1 <= int(choice) <= len(found):
            return found[int(choice) - 1]
        print(f"Enter a number from 1 to {len(found)}.")


TEXT_LIMIT = 100_000  # ponytail: ~25k tokens; raise it if a real course file is cut

# The zip members that hold the words, per OOXML flavour.
OOXML_MEMBERS = {
    "wordprocessingml": ("word/document.xml",),
    "presentationml": ("ppt/slides/slide",),
    "spreadsheetml": ("xl/sharedStrings.xml", "xl/worksheets/sheet"),
}


def _ooxml_text(path: Path, ctype: str) -> str | None:
    """A .docx, .pptx or .xlsx is a zip of XML, so stdlib zipfile plus some tag
    stripping reads one without a dependency. Returns None for anything that is
    not OOXML, so the caller falls back to handing over the file."""
    members = next((v for k, v in OOXML_MEMBERS.items() if k in ctype), None)
    if members is None:
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            names = sorted(n for n in archive.namelist() if n.startswith(members))
            xml = "\n".join(archive.read(n).decode("utf-8", "replace") for n in names)
    except (zipfile.BadZipFile, OSError):
        return None
    # Paragraph, slide-paragraph and table-row ends are where the lines break.
    xml = re.sub(r"</(w:p|a:p|w:tr|row)>", "\n", xml)
    text = re.sub(r"[ \t]+", " ", unescape(re.sub(r"<[^>]+>", " ", xml)))
    return re.sub(r"[ \t]*\n[ \t\n]*", "\n", text).strip() or None


def _pdf_text(path: Path) -> str | None:
    """Extract a PDF's text with poppler's pdftotext, which is already installed
    on most Linux systems and reads layout better than the pure-Python parsers.
    Returns None when it is missing or the PDF holds no text layer, so the
    caller can fall back to handing over the file itself."""
    try:
        done = subprocess.run(
            ["pdftotext", "-layout", "-q", str(path), "-"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0 or not done.stdout.strip():
        return None
    return done.stdout


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


def login() -> None:
    """Open DTU Learn, wait for you to sign in, then save the session cookies."""
    label, command, profile = _pick_profile()
    url = f"{BASE}/d2l/home"
    print(f"Opening {url} in {label}. Sign in there, then return to this terminal.")
    if command and shutil.which(command[0]):
        # Naming the profile makes the browser open the URL in that profile,
        # handing it to the running window if there is one.
        subprocess.Popen(
            [*command, "--profile", str(profile), url],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    else:
        webbrowser.open(url)
    input("Press Enter once you are logged in: ")

    cookie = _cookies_from_firefox(profile)
    if "d2lSessionVal" not in cookie:
        raise SystemExit(
            f"No DTU Learn session cookie in {label}. Check that you signed in "
            "there and that the login finished. The browser rewrites its "
            "session-restore file every ~15 seconds, so if the login just "
            "completed, wait a moment and run login again."
        )
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.parent.chmod(0o700)
    COOKIE_FILE.write_text(cookie)
    COOKIE_FILE.chmod(0o600)
    print(f"Saved session to {COOKIE_FILE}\n")
    print(list_courses())


def _client() -> httpx.Client:
    cookie = os.environ.get("DTU_COOKIE", "").strip()
    if not cookie and COOKIE_FILE.exists():
        cookie = COOKIE_FILE.read_text().strip()
    if not cookie:
        raise RuntimeError(
            "No DTU Learn session. Run: uv run --script server.py login"
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
            "run 'uv run --script server.py login' again — or this is one of the "
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


@lru_cache(maxsize=None)
def _resolve(org_unit_id: int) -> int:
    """Course-scoped calls need the org unit id that list_courses prints in
    brackets, but the DTU course number is the memorable one and sits right
    there in the course name, so accept either.

    ponytail: ids and course numbers are told apart by width -- DTU org unit
    ids are six digits, course numbers at most five. If that ever stops holding,
    look the value up in the enrollment list both ways instead.
    """
    if org_unit_id >= 100000:
        return org_unit_id

    matches = [
        ou
        for ou in _enrolled()[0]
        if (m := re.match(r"(\d{4,5})\b", ou["Name"]))
        and int(m.group(1)) == org_unit_id
    ]
    if len(matches) == 1:
        return matches[0]["Id"]
    if matches:
        # The same course number is offered again in later terms, so there is no
        # safe default here; name the options and let the caller choose.
        listing = "\n".join(
            f"  [{ou['Id']}] {ou['Name']}"
            for ou in sorted(matches, key=_term, reverse=True)
        )
        raise RuntimeError(
            f"Course {org_unit_id:05d} has several offerings. Use one of these ids:\n{listing}"
        )
    raise RuntimeError(
        f"{org_unit_id} is not one of your course ids or course numbers. "
        "Run list_courses to see them."
    )


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


class _TextExtractor(HTMLParser):
    """Strip a D2L HTML page down to readable text."""

    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def _html_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = re.sub(r"[ \t]+", " ", " ".join(parser.parts))
    # Joining with spaces leaves one on each side of the inserted line breaks.
    return re.sub(r"[ \t]*\n[ \t\n]*", "\n", text).strip()


def _toc_index(modules) -> tuple:
    """Index a table of contents by topic id and by module id. get_file needs
    to know what kind of thing a topic is before fetching it, and the
    single-topic endpoint does not answer consistently across courses."""
    topics, units = {}, {}

    def walk(modules):
        for m in modules or []:
            units[m["ModuleId"]] = m
            for t in m.get("Topics") or []:
                topics[t["TopicId"]] = t
            walk(m.get("Modules"))

    walk(modules)
    return topics, units


def _copies() -> list[Path]:
    """The course records download_course leaves in each course folder,
    oldest first, so a course saved under two names resolves to the newer."""
    return sorted(DOWNLOADS.glob("*/_course-*.json"), key=lambda p: p.stat().st_mtime)


def _copy(ou: int) -> dict | None:
    """A course's offline copy as download_course saved it, or None: its
    table of contents, announcements and the files it saved, with root set
    to the folder holding them."""
    found = [p for p in _copies() if p.name == f"_course-{ou}.json"]
    if not found:
        return None
    return json.loads(found[-1].read_text(encoding="utf-8")) | {"root": found[-1].parent}


def _course(ou: int, ctx: Context | None = None) -> dict:
    """A course's offline copy, downloading the course first if it has none.
    Course material is read from the copy once it exists, so the first read
    of a course is slow and every later one is local."""
    # ponytail: two first reads of one course at once both download it; the
    # second overwrites the first with the same files. Lock per course if
    # that ever costs more than the wait.
    copy = _copy(ou)
    if copy is None:
        _download(ou, report=_reporter(ctx))
        copy = _copy(ou)
    return copy


def _saved_note(copy: dict) -> str:
    return (f"(From the offline copy saved {copy['saved']}. download_course "
            "brings it up to date.)")


def _enrolled() -> tuple[list, str]:
    """The org units of my courses, and if DTU Learn could not be asked, why.
    With the session expired or no network, the courses saved offline stand
    in, so what was downloaded can still be found and read."""
    try:
        items = _paged(f"/d2l/api/lp/{LP}/enrollments/myenrollments/", orgUnitTypeId=3)
        return [it["OrgUnit"] for it in items], ""
    except (RuntimeError, httpx.HTTPError) as e:
        saved = {}
        for p in _copies():
            copy = json.loads(p.read_text(encoding="utf-8"))
            saved[copy["ou"]] = {"Id": copy["ou"], "Name": copy["name"]}
        if not saved:
            raise
        return list(saved.values()), str(e)


def _description(node) -> str:
    """Modules and topics carry a Description in both a plain and an HTML
    rendition. Text is already clean; fall back to stripping the HTML for the
    ones where D2L only filled that in."""
    desc = node.get("Description") or {}
    return (desc.get("Text") or "").strip() or _html_text(desc.get("Html") or "")


def _urn_kind(url: str) -> str:
    """A ContentService topic points at Brightspace's media service with a URN,
    d2l:brightspace:content:<region>:<tenant>:<kind>:<id>/<revision>, whose
    <kind> says what the item actually is."""
    parts = url.split(":")
    if url.startswith("d2l:brightspace:content:") and len(parts) > 5:
        return parts[5]
    return "media item"


def _flatten(modules, depth=0):
    """TOC modules nest arbitrarily; flatten to one line per module and topic.

    A course copied from last year keeps topics whose file did not come with
    it. D2L flags them and answers 404 for their content, and they sit beside
    this year's topic of nearly the same title, so they are left out and only
    counted on their unit's line, where nobody can pick one by mistake.
    """
    out = []
    for m in modules or []:
        topics = m.get("Topics") or []
        info = " (info)" if _description(m) else ""
        broken = sum(1 for t in topics if t.get("IsBroken"))
        left_out = f" [{broken} broken topic{'s' * (broken > 1)} not listed]" if broken else ""
        out.append("  " * depth + f"# [{m['ModuleId']}] {m.get('Title', '?')}{info}{left_out}")
        for t in topics:
            if t.get("IsBroken"):
                continue
            kind = t.get("TypeIdentifier", "?")
            # Link topics point either inside D2L (a relative quicklink, no use
            # to anyone outside it) or out to the course's own website, which is
            # public. Only the latter is worth printing.
            url = t.get("Url") or ""
            external = f" {url}" if url.startswith("http") else ""
            if kind == "ContentService":
                kind = _urn_kind(url)  # "video" reads better than "ContentService"
            out.append(
                "  " * (depth + 1)
                + f"- [{t['TopicId']}] {t.get('Title', '?')} ({kind}){external}"
            )
        out.extend(_flatten(m.get("Modules"), depth + 1))
    return out


SEASONS = {  # ordered by where the term falls in the calendar year
    "winter": 0, "january": 0, "jan": 0,
    "spring": 1,
    "summer": 2, "june": 2, "jun": 2,
    "fall": 3, "autumn": 3,
}
SEASON_NAMES = {0: "Winter", 1: "Spring", 2: "Summer", 3: "Fall"}
CODE_SEASONS = {"v": 0, "f": 1, "s": 2, "e": 3}  # vinter, forår, sommer, efterår
TERM_IN_NAME = re.compile(
    r"\b(winter|january|jan|spring|summer|june|jun|fall|autumn)\s*'?(\d{4}|\d{2})\b",
    re.I,
)


def _term(org_unit) -> tuple[int, int]:
    """Sortable (year, season) for a course. DTU writes the term into the course
    name -- "Fall 2026", "Jan 24", "Fall22-Spring23" -- which is authoritative;
    the Code is only a fallback for names that carry no term at all."""
    match = TERM_IN_NAME.search(org_unit["Name"])
    if match:
        year = int(match.group(2))
        return (year + 2000 if year < 100 else year), SEASONS[match.group(1).lower()]

    match = re.match(r"DTU_([efsv])(\d\d)_", org_unit.get("Code") or "")
    if match:
        season = CODE_SEASONS[match.group(1)]
        year = 2000 + int(match.group(2))
        # The code carries the academic year, and that year's January falls in
        # the next calendar one: DTU_v25_27016 is named "Winter 2026".
        return (year + 1 if season == 0 else year), season

    return 0, -1  # sorts last, under "No term"


@mcp.tool()
def list_courses() -> str:
    """List the DTU Learn courses you are enrolled in, grouped by term, newest first.

    When DTU Learn cannot be reached, lists the courses saved offline instead."""
    units, why = _enrolled()
    groups = {}
    for ou in units:
        groups.setdefault(_term(ou), []).append(f"[{ou['Id']}] {ou['Name']}")

    out = [f"DTU Learn could not be reached: {why}\nThese are the courses saved offline.\n"] if why else []
    for term in sorted(groups, reverse=True):
        year, season = term
        out.append(f"## {SEASON_NAMES[season]} {year}" if year else "## No term")
        out.extend(sorted(groups[term]))
        out.append("")
    return "\n".join(out).strip() or "No course enrollments found."


@mcp.tool()
def course_contents(org_unit_id: int, ctx: Context | None = None) -> str:
    """Show a course's content tree. Takes an org unit id or a DTU course number.

    The topic ids it prints are what get_file takes. Topics copied from an
    earlier year without their files are left out and counted on their
    unit's line. Course material is read from the offline copy
    download_course saves; a course with none is downloaded first, which
    takes a minute or two.
    """
    copy = _course(_resolve(org_unit_id), ctx)
    lines = _flatten(copy["modules"])
    return "\n".join([_saved_note(copy), "", *lines]) if lines else "Course has no content."


@mcp.tool()
def unit_info(org_unit_id: int, unit_id: int, ctx: Context | None = None) -> str:
    """Show a unit's own text: its schedule, instructions and notes, plus its topics.

    Unit ids are the bracketed numbers on the `#` lines of course_contents. Only
    units marked `(info)` there have any text of their own.
    """
    unit = _toc_index(_course(_resolve(org_unit_id), ctx)["modules"])[1].get(unit_id)
    if unit is None:
        raise RuntimeError(
            f"No unit {unit_id} in that course. Run course_contents; unit ids are "
            "the bracketed numbers on the lines beginning with #."
        )
    out = [f"# {unit.get('Title', '?')}", ""]
    out.append(_description(unit) or "(This unit has no text of its own.)")
    topics = _flatten([unit])[1:]  # drop the heading _flatten repeats
    if topics:
        out += ["", "Contents:", *topics]
    return "\n".join(out)


def _topic_fetch(ou: int, topic, client=None):
    """One topic's file response and the name D2L gives it. get_file wants the
    text out of it and download_course wants the bytes on disk, but the fetch,
    its errors and the filename are the same for both."""
    topic_id = topic["TopicId"]
    c = client or _client()
    try:
        r = c.get(f"/d2l/api/le/{LE}/{ou}/content/topics/{topic_id}/file")
    finally:
        if client is None:
            c.close()
    if r.status_code in (401, 403):
        raise RuntimeError(
            "Session rejected (HTTP %d). Run 'uv run --script server.py login' again."
            % r.status_code
        )
    if r.status_code == 404:
        why = (
            "is marked broken in the course: it was copied from an earlier "
            "offering without its file, and nobody but the teacher can fix that"
            if topic.get("IsBroken")
            else f"is a {topic.get('TypeIdentifier')} topic with no file behind it"
        )
        raise RuntimeError(f"'{topic.get('Title', topic_id)}' {why}.")
    r.raise_for_status()

    return r, _filename(r, f"topic-{topic_id}")


def _filename(r: httpx.Response, fallback: str) -> str:
    disp = r.headers.get("content-disposition", "")
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disp)
    # D2L percent-encodes the filename, so a space arrives as %20.
    return unquote(match.group(1)) if match else fallback


@mcp.tool()
def get_file(org_unit_id: int, topic_id: int, ctx: Context | None = None) -> str:
    """Fetch one content topic: a file's text, an HTML page's text, or a link's target.

    Topic ids come from course_contents. PDFs come back as extracted text with
    the saved file's path; other binaries are saved and only the path returned.
    Read from the course's offline copy, like course_contents.
    """
    ou = _resolve(org_unit_id)
    copy = _course(ou, ctx)
    topic = _toc_index(copy["modules"])[0].get(topic_id)
    if topic is None:
        raise RuntimeError(
            f"No topic {topic_id} in that course. Run course_contents to see the "
            "topic ids it does have."
        )
    title = topic.get("Title", topic_id)
    kind = topic.get("TypeIdentifier")
    url = topic.get("Url") or ""

    if kind == "Link":
        # An external link is a public page for the caller to fetch; a D2L
        # quicklink is a relative path that only means anything in a browser.
        target = url if url.startswith("http") else f"{BASE}{url}"
        return f"'{title}' is a link, not a file. It points at {target}"

    if kind == "ContentService":
        return (
            f"'{title}' is a {_urn_kind(url)} in Brightspace's media service, so there "
            f"is nothing to extract. Watch it at "
            f"{BASE}/d2l/le/lessons/{ou}/topics/{topic_id}"
        )

    saved = copy["files"].get(str(topic_id))
    if saved:
        return _saved_text(copy["root"] / saved, title)
    # Not in the copy: its download failed, so try DTU Learn once more.
    r, name = _topic_fetch(ou, topic)
    return _file_text(r.content, r.headers.get("content-type", ""), name, title)


def _numbered_pages(text: str) -> str:
    """pdftotext's output with each page headed [Page n]. It ends every page
    with a form feed, which says nothing to a reader. Only the last one goes:
    the ones before it may close blank pages, which still count."""
    pages = text.removesuffix("\f").split("\f")
    return "\n\n".join(f"[Page {n}]\n{page.strip(chr(10))}" for n, page in enumerate(pages, 1))


PAGE_LIMIT = 10  # ponytail: ~2k tokens a page; raise it if real questions need more at once


@mcp.tool()
def get_pdf_pages(
    org_unit_id: int, topic_id: int, first_page: int, last_page: int = 0,
    ctx: Context | None = None,
) -> list[str | Image]:
    """Show pages of a PDF topic as images, for what get_file's text loses.

    Figures, diagrams and mathematical notation do not survive text
    extraction. Read the text with get_file first; it numbers the pages as
    [Page n]. Then ask for only the pages that matter: an image costs about
    2k tokens against roughly 100 for the same page as text. last_page
    defaults to first_page; at most 10 pages at a time.
    """
    last_page = last_page or first_page
    if not 1 <= first_page <= last_page or last_page - first_page >= PAGE_LIMIT:
        raise RuntimeError(
            f"Ask for pages first_page to last_page, counted from 1, at most "
            f"{PAGE_LIMIT} at a time."
        )
    ou = _resolve(org_unit_id)
    copy = _course(ou, ctx)
    topic = _toc_index(copy["modules"])[0].get(topic_id)
    if topic is None:
        raise RuntimeError(
            f"No topic {topic_id} in that course. Run course_contents to see the "
            "topic ids it does have."
        )
    title = topic.get("Title", topic_id)
    if topic.get("TypeIdentifier") != "File":
        raise RuntimeError(f"'{title}' is not a file, so it has no pages. get_file says what it is.")
    saved = copy["files"].get(str(topic_id))
    if saved:
        path = copy["root"] / saved
    else:  # not in the copy: its download failed, so try DTU Learn once more
        r, name = _topic_fetch(ou, topic)
        DOWNLOADS.mkdir(parents=True, exist_ok=True)
        path = DOWNLOADS / name
        path.write_bytes(r.content)
    if path.suffix.lower() != ".pdf":
        raise RuntimeError(f"'{title}' is {path.name}, not a PDF. get_file has its text.")

    with tempfile.TemporaryDirectory() as tmp:
        # 1568 pixels on the long side is as much as a model takes in before
        # scaling an image down, so anything sharper is paid for and lost.
        try:
            subprocess.run(
                ["pdftoppm", "-png", "-scale-to", "1568", "-f", str(first_page),
                 "-l", str(last_page), str(path), f"{tmp}/page"],
                check=True, capture_output=True, timeout=120,
            )
        except FileNotFoundError:
            raise RuntimeError("Showing PDF pages needs pdftoppm, from poppler-utils.")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"pdftoppm could not read {path.name}: {e.stderr.decode().strip()}")
        # page-07.png: zero-padded to the width of the page count, so they sort.
        images = sorted(Path(tmp).glob("page-*.png"))
        if not images:
            raise RuntimeError(f"'{title}' has fewer than {first_page} pages.")
        out = []
        for image in images:
            out += [f"Page {int(image.stem.split('-')[-1])} of '{title}':",
                    Image(data=image.read_bytes(), format="png")]
    return out


def _blocks(result) -> list[dict]:
    """A tool's result as MCP content blocks, which is also the shape
    LearnHarness tools return: text, and images as base64 PNG."""
    return [
        item.to_image_content().model_dump(by_alias=True, exclude_none=True)
        if isinstance(item, Image) else {"type": "text", "text": item}
        for item in (result if isinstance(result, list) else [result])
    ]


def _decode(content: bytes, ctype: str) -> str:
    """Bytes as text, in the charset the content type names, else UTF-8."""
    charset = re.search(r"charset=([\w-]+)", ctype)
    try:
        return content.decode(charset.group(1) if charset else "utf-8", "replace")
    except LookupError:  # a charset Python does not know
        return content.decode("utf-8", "replace")


def _saved_text(path: Path, title) -> str:
    """_file_text for a file in an offline copy. The copy keeps no content
    type, so the extension stands in. Anything but a page, a PDF or an Office
    file is left to the bytes, as D2L's unknown type is: guess_type calls a
    MATLAB .m file a Mathematica package."""
    ctype = mimetypes.guess_type(path.name)[0] or ""
    if not any(kind in ctype for kind in ("html", "pdf", "officedocument")):
        ctype = "d2l/unknowntype"
    return _file_text(path.read_bytes(), ctype, path.name, title, path)


def _file_text(content: bytes, ctype: str, name: str, title, path: Path | None = None) -> str:
    """Turn a file into text for the caller, saving it to disk when the bytes
    are not text themselves. Shared by content topics and announcement
    attachments, fetched or read from an offline copy; path is where a copy
    already keeps the file."""
    if "html" in ctype:
        return f"[{title}]\n\n{_html_text(_decode(content, ctype))}"
    if ctype.startswith("text/") or "json" in ctype:
        return _decode(content, ctype)
    if ctype == "d2l/unknowntype":
        # D2L labels every extension it does not know this way, and many of them
        # are plain text: source code, PRISM models, LaTeX. Let the bytes decide.
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            pass

    if path is None:
        DOWNLOADS.mkdir(parents=True, exist_ok=True)
        path = DOWNLOADS / name
        path.write_bytes(content)

    if "pdf" in ctype or name.lower().endswith(".pdf"):
        text = _pdf_text(path)
        if text:
            # Reading the PDF itself renders every page as an image, which costs
            # roughly ten times the tokens of its text. Extraction drops figures
            # and mangles heavy mathematical notation, so say where the original
            # is rather than pretending the text is the whole document, and
            # number the pages so the ones that matter can be looked at.
            text = _numbered_pages(text)
            cut = "\n\n[Text truncated here.]" if len(text) > TEXT_LIMIT else ""
            return (
                f"[Text of {name}. The PDF itself is at {path}. Figures and "
                f"mathematical notation do not survive extraction: open it, or "
                f"for a course topic see those pages with get_pdf_pages.]"
                f"\n\n{text[:TEXT_LIMIT]}{cut}"
            )

    office = _ooxml_text(path, ctype)
    if office:
        note = "" if "spreadsheetml" not in ctype else (
            " Cell values only; the row and column layout is not preserved."
        )
        cut = "\n\n[Text truncated here.]" if len(office) > TEXT_LIMIT else ""
        return f"[Text of {name}.{note}]\n\n{office[:TEXT_LIMIT]}{cut}"

    # Saved, but no text could be got out of it. The path is on the machine
    # running this server, which is not necessarily where the caller can read.
    return (
        f"No text could be extracted from {name} ({ctype}, {len(content)} bytes). "
        f"It is saved at {path} on the machine running this server; open it there."
    )


def _safe(name: str) -> str:
    """A title becomes a directory or file name, so drop what a filesystem
    would choke on and keep the rest recognisable."""
    # Strip after cutting, or a long course name ends in ", " once cut at 80.
    return re.sub(r'[\\/:*?"<>|]+', "_", name)[:80].strip(" .,") or "untitled"


def _file_topics(modules, parents=()):
    """Every File topic in the tree, with the unit titles standing above it."""
    for module in modules or []:
        path = parents + (_safe(module.get("Title", "unit")),)
        for topic in module.get("Topics") or []:
            # Broken topics have no file to fetch; skipping them here keeps a
            # copied course from reporting fifty failures nobody can act on.
            if topic.get("TypeIdentifier") == "File" and not topic.get("IsBroken"):
                yield path, topic
        yield from _file_topics(module.get("Modules"), path)


@lru_cache(maxsize=None)
def _course_name(ou: int) -> str:
    """The course's own name, for the folder to be called. lp/courses/{ou} is
    forbidden to students, so read it off the enrollment list instead."""
    for it in _paged(f"/d2l/api/lp/{LP}/enrollments/myenrollments/", orgUnitTypeId=3):
        if it["OrgUnit"]["Id"] == ou:
            return it["OrgUnit"]["Name"]
    return str(ou)


def _save_topic(client, ou: int, root: Path, parents: tuple, topic) -> tuple:
    try:
        r, name = _topic_fetch(ou, topic, client)
    except (RuntimeError, httpx.HTTPError) as e:
        return None, f"{topic.get('Title', topic['TopicId'])}: {e}"
    # Named after the topic, which is what the course tree calls it, with the
    # extension off the server's filename. Two runs then overwrite rather than
    # pile up copies.
    dest = root.joinpath(*parents, _safe(topic.get("Title", "")) + Path(name).suffix)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return dest, None


def _save_attachment(client, ou: int, root: Path, news_id: int, att) -> tuple:
    name = att["FileName"]
    dest = root / "Announcements" / f"{news_id}-{_safe(Path(name).stem)}{Path(name).suffix}"
    try:
        r = client.get(f"/d2l/api/le/{LE}/{ou}/news/{news_id}/attachments/{att['FileId']}")
        r.raise_for_status()
    except httpx.HTTPError as e:
        return None, f"{name}: {e}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return dest, None


# A src or href in teacher HTML, and the course file area D2L serves files from.
REFERENCE = re.compile(r'(\b(?:src|href)=")([^"]*)"')


QUICKLINK = "/d2l/common/dialogs/quickLink/quickLink.d2l"


def _d2l_file(value: str) -> str | None:
    """The DTU Learn file a src or href points at, as an absolute URL, or None.
    A short link to a course file (quickLink.d2l?type=coursefile) counts:
    DTU Learn redirects it to the file. Short links to anything else lead to
    pages, not files. Relative values are left alone: in index.html they are
    already local."""
    url = unescape(value)
    if url.startswith(("/content/", QUICKLINK)):
        url = BASE + url
    if url.startswith(BASE + "/content/"):
        return url
    if url.startswith(BASE + QUICKLINK) and re.search(r"[?&]type=coursefile\b", url, re.I):
        return url
    return None


def _linked_dest(root: Path, url: str) -> Path:
    """Where a file a page links to is kept: its D2L path, under _linked/."""
    parts = [unquote(p) for p in urlparse(url).path.split("/")[2:] if p] or ["untitled"]
    name = Path(parts.pop())
    return root.joinpath("_linked", *map(_safe, parts), _safe(name.stem) + name.suffix)


def _save_linked(client, root: Path, url: str) -> tuple:
    try:
        r = client.get(url)  # only ever a BASE URL, so the cookie stays on D2L
    except httpx.HTTPError:
        return url, None
    # An expired session redirects to the login page, which is not the file.
    if not r.is_success or not r.url.path.startswith("/content/"):
        return url, None
    # Kept by where it ended up: a short link names no file of its own.
    dest = _linked_dest(root, str(r.url))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return url, dest


def _relink(html: str, page: Path, linked: dict) -> str:
    """Point a page's DTU Learn pictures and file links at the copies in linked."""
    def swap(m):
        dest = linked.get(_d2l_file(m.group(2)))
        if dest is None:
            return m.group(0)
        return m.group(1) + quote(Path(os.path.relpath(dest, page.parent)).as_posix()) + '"'
    return REFERENCE.sub(swap, html)


# Spreadsheets are left out: their cells are mostly numbers from data tables,
# which in one course came to 235 MB of search text against 8 MB for
# everything else. They are still found by title.
OOXML_KINDS = {".docx": "wordprocessingml", ".pptx": "presentationml"}


def _search_pages(path: Path) -> list[str]:
    """A saved file's text for the search box: one string per page of a PDF,
    one for anything else. Empty when no text can be got out of it."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pages = (_pdf_text(path) or "").split("\f")  # pdftotext ends each page with \f
    elif suffix in OOXML_KINDS:
        pages = [_ooxml_text(path, OOXML_KINDS[suffix]) or ""]
    elif suffix in (".html", ".htm"):
        pages = [_html_text(path.read_bytes().decode("utf-8", "replace"))]
    else:
        return []
    pages = [" ".join(p.split()) for p in pages]
    return pages if any(pages) else []


def _search_entry(title: str, href: str, tag: str, pages=(), where: str = "") -> dict:
    """One search result: its text joined into one string, and for a PDF the
    offset each page starts at, so a match can link to its page. where names
    the units above it, since many courses repeat titles like "Description"."""
    starts, text = [], ""
    for page in pages:
        starts.append(len(text))
        text += page + " "
    entry = {"t": title, "h": href, "k": tag, "x": text.rstrip()}
    if len(starts) > 1:
        entry["p"] = starts
    if where:
        entry["u"] = where
    return entry


def _index_html(name: str, ou: int, modules, news, saved: dict, texts: dict) -> tuple:
    """The page that stands in for the course site offline, and the entries its
    search box looks through. saved maps a topic id, or a (news id, file id)
    pair for an attachment, to its path relative to the page; texts maps the
    same keys to the file's pages of text. Anything not saved links back to
    DTU Learn, which needs a connection."""
    docs = []
    # Teacher HTML points at /content/... on D2L, which would mean
    # file:///content/... here. Make it absolute; _relink then swaps in
    # the local copies of whatever it could fetch.
    def rich(field):  # D2L stores rich text in a plain and an HTML rendition
        field = field or {}
        html = field.get("Html") or f"<p>{escape(field.get('Text') or '')}</p>"
        return re.sub(r'\b(src|href)="/(?!/)', rf'\1="{BASE}/', html)

    def item(href, label, tag, online=False, key=None, where=""):
        docs.append(_search_entry(label, href, tag, texts.get(key, ()), where))
        cls = ' class="tag online"' if online else ' class="tag"'
        return (f'<li><a href="{escape(href)}">{escape(label)}</a>'
                f"<span{cls}>{escape(tag)}</span></li>")

    def topic(t, where):
        tid, url, kind = t["TopicId"], t.get("Url") or "", t.get("TypeIdentifier", "?")
        title = t.get("Title", "?")
        if tid in saved:
            tag = Path(unquote(saved[tid])).suffix[1:].upper() or "file"
            return item(saved[tid], title, tag, key=tid, where=where)
        if kind == "ContentService":
            kind = _urn_kind(url)
        if t.get("IsBroken"):
            kind += ", broken"
        href = url if url.startswith("http") else f"{BASE}/d2l/le/lessons/{ou}/topics/{tid}"
        return item(href, title, f"{kind} · online", online=True, where=where)

    def tree(modules):
        """The sidebar: every unit, nested and folded, linking to its section."""
        for m in modules or []:
            a = f'<a href="#u{m["ModuleId"]}">{escape(m.get("Title", "?"))}</a>'
            if m.get("Modules"):
                yield f"<li><details><summary>{a}</summary><ul>"
                yield from tree(m["Modules"])
                yield "</ul></details></li>"
            else:
                yield f"<li>{a}</li>"

    def units(modules, depth=2, trail=()):
        """Each unit's section. trail holds the (id, title) of the units above
        it, shown as links over the heading so a deep unit says where it sits."""
        for m in modules or []:
            h, title = min(depth, 6), m.get("Title", "?")
            parents = " › ".join(t for _, t in trail)
            docs.append(_search_entry(title, f'#u{m["ModuleId"]}', "unit",
                                      [" ".join(_description(m).split())], parents))
            yield f'<section id="u{m["ModuleId"]}">'
            if trail:
                yield '<p class="crumbs">' + " › ".join(
                    f'<a href="#u{i}">{escape(t)}</a>' for i, t in trail) + "</p>"
            yield f"<h{h}>{escape(title)}</h{h}>"
            if _description(m):
                yield f'<div class="text">{rich(m.get("Description"))}</div>'
            here = " › ".join([*(t for _, t in trail), title])
            if m.get("Topics"):
                yield '<ul class="topics">' + "".join(topic(t, here) for t in m["Topics"]) + "</ul>"
            yield from units(m.get("Modules"), depth + 1, (*trail, (m["ModuleId"], title)))
            yield "</section>"

    def announcements():
        for n in news:
            body = n.get("Body") or {}
            text = (body.get("Text") or "").strip() or _html_text(body.get("Html") or "")
            docs.append(_search_entry(n.get("Title", "?"), f'#n{n["Id"]}', "announcement", [" ".join(text.split())]))
            yield (f'<article id="n{n["Id"]}"><h2>{escape(n.get("Title", "?"))}</h2>'
                   f'<time>{(n.get("StartDate") or "")[:10]}</time>'
                   f'<div class="text">{rich(n.get("Body"))}</div>')
            files = n.get("Attachments") or []
            if files:
                yield '<ul class="topics">' + "".join(
                    item(saved[(n["Id"], a["FileId"])], a["FileName"], "attachment", key=(n["Id"], a["FileId"]))
                    if (n["Id"], a["FileId"]) in saved
                    else f'<li>{escape(a["FileName"])}<span class="tag online">download failed</span></li>'
                    for a in files
                ) + "</ul>"
            yield "</article>"

    html = "\n".join([
        '<!doctype html><html lang="en"><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(name)}</title><style>{INDEX_CSS}</style>",
        f'<header><a class="back" href="../index.html">← All courses</a><h1>{escape(name)}</h1>',
        f'<p>Saved from DTU Learn on {datetime.now():%Y-%m-%d}</p>',
        '<div class="tabs" role="tablist">',
        '<input type="radio" name="tab" id="t-content" checked><label for="t-content">Content</label>',
        f'<input type="radio" name="tab" id="t-news"><label for="t-news">Announcements ({len(news)})</label>',
        '<input type="search" id="q" placeholder="Search this course, inside files too (press /)" aria-label="Search this course">',
        "</div></header>",
        f'<div id="p-content" class="panel"><nav aria-label="Units"><ul>{"".join(tree(modules))}</ul></nav>',
        '<main>', *units(modules), "</main></div>",
        '<div id="p-news" class="panel"><main>',
        *(announcements() if news else ["<p>No announcements.</p>"]),
        "</main></div>",
        '<main id="results" aria-live="polite"></main>',
        f'<script src="_search.js"></script><script>{INDEX_JS}</script>',
    ])
    return html, docs


def _landing_html(courses) -> str:
    """The page above the course folders: every downloaded course, grouped by
    term with the newest first, and a search box over all of them. courses
    holds (name, folder as a URL path, saved date)."""
    groups = {}
    for name, folder, saved in courses:
        groups.setdefault(_term({"Name": name}), []).append(
            (name, f'<li><a href="{escape(folder)}/index.html">{escape(name)}</a>'
                   f'<span class="tag">saved {escape(saved)}</span></li>')
        )
    body = []
    for year, season in sorted(groups, reverse=True):
        body.append(f"<h2>{SEASON_NAMES[season]} {year}</h2>" if year else "<h2>No term</h2>")
        body.append('<ul class="topics">' + "".join(li for _, li in sorted(groups[year, season])) + "</ul>")

    def js(value):  # a string literal that cannot close the <script> it sits in
        return json.dumps(value).replace("</", "<\\/")

    # Every course's _search.js sets window.SEARCH. Loaded one after another,
    # each is taken in turn, its links made relative to this page and its
    # results labelled with the course, before the next one replaces it.
    # ponytail: all courses load up front, ~10 MB each for the big ones; load
    # on first keystroke if the landing page ever opens slowly.
    loads = [
        f'<script src="{escape(folder)}/_search.js"></script><script>take({js(folder)}, {js(name)})</script>'
        for name, folder, _ in courses
    ]
    return "\n".join([
        '<!doctype html><html lang="en"><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>DTU Learn courses</title><style>{INDEX_CSS}</style>",
        "<header><h1>DTU Learn courses</h1><p>Saved for offline reading. "
        "Run download_course again to add a course or bring one up to date.</p>",
        '<div class="tabs"><input type="search" id="q" placeholder="Search every course (press /)" '
        'aria-label="Search every course"></div></header>',
        '<main id="courses">', *(body or ["<p>No courses downloaded yet.</p>"]), "</main>",
        '<main id="results" aria-live="polite"></main>',
        f"<script>{LANDING_JS}</script>", *loads,
        f"<script>window.SEARCH = all;{INDEX_JS}</script>",
    ])


def _write_landing() -> None:
    """Rebuild the landing page from the course folders on disk, so it lists
    every course downloaded so far, not only the one just fetched."""
    courses = []
    for page in DOWNLOADS.glob("*/index.html"):
        found = re.search(r"<title>(.*?)</title>", page.read_text(encoding="utf-8", errors="replace"))
        saved = datetime.fromtimestamp(page.stat().st_mtime)
        courses.append((
            unescape(found.group(1)) if found else page.parent.name,
            quote(page.parent.name),
            f"{saved:%Y-%m-%d}",
        ))
    (DOWNLOADS / "index.html").write_text(_landing_html(courses), encoding="utf-8")


# The tabs are radio buttons, so they work with no script and from the keyboard.
# System colours (Canvas, CanvasText, LinkText) follow the reader's dark mode.
INDEX_CSS = """
:root{color-scheme:light dark;--line:color-mix(in srgb,CanvasText 14%,transparent);
--muted:color-mix(in srgb,CanvasText 62%,transparent)}
*{box-sizing:border-box}
body{margin:0;background:Canvas;color:CanvasText;font:16px/1.5 system-ui,sans-serif}
header{padding:1rem 1rem 0;border-bottom:1px solid var(--line)}
h1{margin:0;font-size:1.5rem}
header p{margin:.2rem 0 .6rem;color:var(--muted);font-size:.9rem}
.tabs{display:flex;flex-wrap:wrap;align-items:center;gap:.25rem}
#q{flex:0 1 22rem;margin:0 0 .4rem auto;padding:.4rem .6rem;font:inherit;color:inherit;
background:Canvas;border:1px solid var(--line);border-radius:.4rem}
#results{display:none}
body.searching #results{display:block}
body.searching .panel,body.searching #courses{display:none!important}
.hits li{flex-wrap:wrap}
.hits p{flex-basis:100%;margin:.15rem 0 0;color:var(--muted);font-size:.9rem}
.hits p.where{margin:0;font-size:.8rem}
.tabs input[type=radio]{position:absolute;opacity:0}
.tabs label{padding:.5rem .9rem;cursor:pointer;color:var(--muted);border-bottom:3px solid transparent}
.tabs input:checked+label{color:CanvasText;font-weight:600;border-color:LinkText}
.tabs input:focus-visible+label{outline:2px solid LinkText;outline-offset:-2px}
.panel{display:none}
body:has(#t-content:checked) #p-content{display:grid;grid-template-columns:18rem minmax(0,1fr)}
body:has(#t-news:checked) #p-news{display:block}
nav{position:sticky;top:0;align-self:start;max-height:100vh;overflow:auto;
padding:1rem;border-right:1px solid var(--line);font-size:.92rem}
nav ul{list-style:none;margin:0 0 0 .3rem;padding-left:.8rem;border-left:1px solid var(--line)}
nav>ul{margin:0;padding-left:0;border:0}
nav li{margin:.15rem 0}
nav a{color:inherit;text-decoration:none}
nav ul:has(details)>li>a{display:block;padding-left:1.1em}
nav a:hover{color:LinkText;text-decoration:underline}
summary{cursor:pointer}
main{padding:1rem;max-width:60rem;min-width:0}
section{scroll-margin-top:1rem;margin-bottom:1.5rem}
main>section+section{border-top:1px solid var(--line);padding-top:1.5rem}
section section{margin:1.25rem 0 0;padding-left:1rem;
border-left:3px solid color-mix(in srgb,LinkText 35%,transparent)}
.crumbs{margin:0;font-size:.85rem;color:var(--muted)}
.crumbs a{color:inherit}
.back{font-size:.9rem;color:var(--muted);text-decoration:none}
.back:hover{color:LinkText}
h2,h3,h4,h5,h6{margin:0 0 .5rem}
.text{overflow-x:auto}
.text img{max-width:100%;height:auto}
.topics{list-style:none;margin:.5rem 0;padding:0}
.topics li{display:flex;gap:.5rem;align-items:baseline;padding:.35rem 0;border-bottom:1px solid var(--line)}
.tag{margin-left:auto;flex:none;font-size:.75rem;padding:0 .45rem;border:1px solid var(--line);
border-radius:.3rem;color:var(--muted)}
.tag.online{border-style:dashed}
article{padding:1rem 0;border-bottom:1px solid var(--line)}
article h2{font-size:1.15rem;margin:0}
time{color:var(--muted);font-size:.9rem}
@media (max-width:45rem){
body:has(#t-content:checked) #p-content{grid-template-columns:1fr}
nav{position:static;max-height:none;border-right:0;border-bottom:1px solid var(--line)}
#q{flex-basis:100%;margin-left:0}}
"""

# Every search word has to start a word in a result's title, the units above
# it, or its text, so "rsa" finds "RSA" but not "adversary". Results whose
# title and units hold every word come first, the rest stay in course order.
# A PDF match links to its page.
# ponytail: a linear regex scan, a few ms over a course's ~10 MB of text.
# An inverted index only if a course ever makes typing lag.
INDEX_JS = r"""
// lt is the title and the units above it, so "milk description" finds the
// Description topic inside the Milk case study.
const docs = (window.SEARCH || []).map(d => ({...d, lt: (d.t + " " + (d.u || "")).toLowerCase(), lx: d.x.toLowerCase()}));
const q = document.getElementById("q"), out = document.getElementById("results");
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};
function run() {
  const words = q.value.toLowerCase().split(/\s+/).filter(Boolean);
  document.body.classList.toggle("searching", words.length > 0);
  out.replaceChildren();
  if (!words.length) return;
  // \p{L}\p{N} rather than \b, which only knows ASCII and would miss "økonomi".
  const start = "(?<![\\p{L}\\p{N}])";
  const esc = w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const res = words.map(w => new RegExp(start + esc(w), "u"));
  const inTitle = d => res.every(r => r.test(d.lt));
  const hits = docs.filter(d => res.every(r => r.test(d.lt) || r.test(d.lx)))
    .sort((a, b) => inTitle(b) - inTitle(a));
  out.append(el("p", "", hits.length ? `${hits.length} results` : "No results."));
  const mark = new RegExp("(" + start + "(?:" + words.map(esc).join("|") + "))", "giu");
  const list = el("ul", "topics hits");
  for (const d of hits.slice(0, 200)) {
    const at = Math.min(...res.map(r => d.lx.search(r)).filter(i => i >= 0));
    let href = d.h, tag = d.k;
    if (d.p && isFinite(at)) {
      const page = d.p.filter(start => start <= at).length;
      href += "#page=" + page;
      tag += " · p. " + page;
    }
    const li = el("li"), a = el("a", "", d.t);
    a.href = href;
    li.append(a, el("span", "tag", tag));
    if (d.u) li.append(el("p", "where", d.u));
    if (isFinite(at)) {
      const from = Math.max(0, at - 70);
      const snippet = el("p");
      d.x.slice(from, at + 150).split(mark).forEach((part, i) =>
        snippet.append(i % 2 ? el("mark", "", part) : part));
      if (from) snippet.prepend("…");
      li.append(snippet);
    }
    list.append(li);
  }
  out.append(list);
}
q.addEventListener("input", run);
// A unit or an announcement lives in a tab: open that tab before jumping to it.
out.addEventListener("click", e => {
  const href = e.target.closest("a")?.getAttribute("href") || "";
  if (!href.startsWith("#")) return;
  document.getElementById(href[1] === "n" ? "t-news" : "t-content").checked = true;
  q.value = "";
  run();
});
// The landing page's results link to course/index.html#n123. An announcement
// sits in a hidden tab, where the browser cannot scroll to it: open its tab.
function openTarget() {
  const id = location.hash.slice(1), target = document.getElementById(id);
  const tab = document.getElementById(/^n\d+$/.test(id) ? "t-news" : /^u\d+$/.test(id) ? "t-content" : "");
  if (target && tab) {
    tab.checked = true;
    target.scrollIntoView();
  }
}
addEventListener("hashchange", openTarget);
openTarget();
// "/" jumps to the search box from anywhere, in place of the browser's own
// use of it (Quick Find in Firefox), unless you are typing in a text field.
addEventListener("keydown", e => {
  if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.matches?.("input:not([type=radio]), textarea, select, [contenteditable]")) return;
  e.preventDefault();
  q.focus();
  q.select();
}, true);
run();  // the browser may restore the box's text on reload
"""

# Collects each course's search entries on the landing page; see _landing_html.
# Links become relative to the landing page, and the course name leads the
# trail, so "crypto rsa" keeps to the cryptography course.
LANDING_JS = r"""
const all = [];
function take(folder, name) {
  for (const d of window.SEARCH || []) {
    const href = /^[a-z]+:/.test(d.h) ? d.h : folder + "/" + (d.h.startsWith("#") ? "index.html" : "") + d.h;
    all.push({...d, h: href, u: d.u ? name + " › " + d.u : name});
  }
  window.SEARCH = undefined;  // a course without _search.js must not repeat the last one
}
"""


@mcp.tool()
def download_course(org_unit_id: int, ctx: Context | None = None) -> str:
    """Save a course for offline use: its files, unit text and announcements.

    Files land in ~/Downloads/dtulearn/<course>/<unit>/, on the machine running
    this server, with an index.html beside them that lays the course out like
    its site. Pictures and files the pages link to are fetched as well. Links
    and videos are not files; the index points at them online.
    course_contents, unit_info, get_file and the announcement tools read
    this copy, so run it again to bring them up to date.
    Nothing comes back as text -- for that, ask for the one topic with get_file.
    """
    row = _download(_resolve(org_unit_id), report=_reporter(ctx))
    _status(keep=True)  # a blank line between the ✓ line and the table
    return _summary([row])


@mcp.tool()
def download_term(term: str = "", ctx: Context | None = None) -> str:
    """Download every course of one term for offline use, as download_course does.

    term is written like the headings of list_courses: "Fall 2026", "Jan 25".
    Left out, it is the newest term you are enrolled in. A course that fails
    is reported and the rest still download.
    """
    want = None
    if term:
        want = _term({"Name": term})
        if want == (0, -1):
            raise RuntimeError(
                f"No term in {term!r}. Write it like the headings of list_courses, "
                "for example 'Fall 2026', 'Spring 2025' or 'Jan 25'."
            )
    units = _term_units(want)
    if not units:
        raise RuntimeError(f"You have no courses in {term or 'any term'}. Run list_courses to see your terms.")
    rows, report = [], _reporter(ctx)
    for i, ou in enumerate(units, 1):
        prefix = f"[{i}/{len(units)}] "
        try:
            rows.append(_download(ou["Id"], prefix, report))
        except (RuntimeError, httpx.HTTPError) as e:
            rows.append((_code(ou["Name"]), None, [("could not download", [str(e)])]))
            _status(("31", "  ✗ "), ("2", prefix), ("", ou["Name"]), keep=True)
    _status(keep=True)  # a blank line between the ✓ lines and the table
    return _summary(rows)


COLUMNS = ("files", "pictures", "announcements", "links", "videos", "broken")


def _code(name: str) -> str:
    """The course number a course name starts with, to label its summary row."""
    found = re.match(r"\d{5}\b", name)
    return found.group() if found else name[:20]


def _summary(rows: list) -> str:
    """What the download tools end with: a row of counts per course, then
    what went wrong, then where to browse. rows are _download's results;
    a course that failed outright has None for counts."""
    width = max(len(code) for code, _, _ in rows)
    table = [" " * width + "".join(f"  {c}" for c in COLUMNS)]
    table += [
        code.ljust(width) + "".join(f"  {counts[c]:>{len(c)}}" for c in COLUMNS)
        for code, counts, _ in rows if counts
    ]
    out = [f"  {line}" for line in table] if len(table) > 1 else []
    problems = [
        line
        for code, _, found in rows for head, details in found
        for line in [f"  ✗ {code}: {head}", *(f"      {d}" for d in details)]
    ]
    if problems:
        out += ["", *problems]
    out += ["", f"  Open {DOWNLOADS / 'index.html'} to see every course."]
    return "\n".join(out).lstrip("\n")


def _status(*parts: tuple[str, str], keep: bool = False) -> None:
    """Rewrite the live status line on stderr, or leave it behind with keep.
    parts are (SGR colour code, text) pairs, cut to the terminal's width.
    Only on a terminal: under an MCP client stderr goes to a log, where a line
    per file would only fill it."""
    if not sys.stderr.isatty():
        return
    room, line = shutil.get_terminal_size().columns - 1, ""
    for colour, text in parts:
        text = text[:room]
        room -= len(text)
        line += f"\033[{colour}m{text}\033[0m" if colour and not os.environ.get("NO_COLOR") else text
    print("\r\033[K" + line, end="\n" if keep else "", file=sys.stderr, flush=True)


def _reporter(ctx: Context | None):
    """Send each status line to an MCP client as a progress notification too.
    A no-op from the CLI, and in the SDK when the client asked for none."""
    if ctx is None:
        return lambda text: None
    # The spec wants progress to rise with every notification. The counts
    # restart each step, so they ride in the message and this only ticks.
    ticks = count(1)
    # Tools run in an anyio worker thread; the notification goes out on the
    # event loop.
    return lambda text: anyio.from_thread.run(partial(ctx.report_progress, next(ticks), None, text))


def _each(pool, fn, items, phase: str, say) -> list:
    """pool.map that reports "phase 12/245" through say as results come in."""
    items, out = list(items), []
    say(phase, 0, len(items))
    for result in pool.map(fn, items):
        out.append(result)
        say(phase, len(out), len(items))
    return out


def _download(ou: int, prefix: str = "", report=lambda text: None) -> tuple:
    """download_course's work, for one org unit id. prefix leads the status
    line, so a term download can say which course of how many it is on.
    report gets the same line as plain text, for _reporter. Returns a row
    for _summary: the course number, its counts and its problems."""
    name = _course_name(ou)

    def say(what, done=0, total=0):
        bar, tally = [], f" {done}/{total}" if total else ""
        if total:
            filled = 12 * done // total
            bar = [("36", "█" * filled), ("2", "░" * (12 - filled) + " ")]
        # Counts first: a long course name is what gets cut.
        _status(("2", "    " + prefix), *bar, ("1", what + tally), ("2", " · "), ("", name))
        report(f"{prefix}{what}{tally} · {name}")

    say("reading the course")
    modules = _get(f"/d2l/api/le/{LE}/{ou}/content/toc").get("Modules")
    news = _get(f"/d2l/api/le/{LE}/{ou}/news/")
    jobs = list(_file_topics(modules))
    atts = [(n["Id"], a) for n in news for a in n.get("Attachments") or []]

    root = DOWNLOADS / _safe(name)
    root.mkdir(parents=True, exist_ok=True)
    # ponytail: eight at a time; a course is a few hundred files at most, so
    # nothing here needs a rate limiter or a resumable queue.
    with _client() as c, ThreadPoolExecutor(max_workers=8) as pool:
        tasks = [partial(_save_topic, c, ou, root, *job) for job in jobs]
        tasks += [partial(_save_attachment, c, ou, root, *job) for job in atts]
        results = _each(pool, lambda task: task(), tasks, "files", say)

        keys = [t["TopicId"] for _, t in jobs] + [(nid, a["FileId"]) for nid, a in atts]
        index = {
            key: quote(p.relative_to(root).as_posix())
            for key, (p, _) in zip(keys, results) if p
        }
        found = _each(pool, lambda r: _search_pages(r[0]) if r[0] else [], results, "search text", say)
        texts = dict(zip(keys, found))
        html, docs = _index_html(name, ou, modules, news, index, texts)
        # The index and every saved HTML page can show pictures and link to
        # files in D2L's file area. Fetch each of those once, then point the
        # pages at the copies.
        pages = {root / "index.html": html}
        for p, _ in results:
            if p and p.suffix.lower() in (".html", ".htm"):
                try:
                    pages[p] = p.read_bytes().decode("utf-8")
                except UnicodeDecodeError:
                    pass  # not ours to mangle; it keeps its online links
        refs = {
            url for html in pages.values() for m in REFERENCE.finditer(html)
            if (url := _d2l_file(m.group(2)))
        }
        linked = dict(_each(pool, lambda url: _save_linked(c, root, url), refs,
                            "pictures and linked files", say))
        linked = {url: dest for url, dest in linked.items() if dest}
        # Linked files have no topic, so they are searchable by their file name.
        docs += [
            _search_entry(dest.name, quote(dest.relative_to(root).as_posix()),
                          dest.suffix[1:].upper() or "file", found)
            for dest, found in zip(linked.values(), _each(
                pool, _search_pages, linked.values(), "search text of linked files", say))
            if found
        ]
    say("writing the pages")
    for page, html in pages.items():
        page.write_text(_relink(html, page, linked), encoding="utf-8")
    # A script file rather than JSON: a page opened from disk may load a
    # <script src> but not fetch() a file beside it.
    (root / "_search.js").write_text(
        f"window.SEARCH = {json.dumps(docs, ensure_ascii=False)};\n", encoding="utf-8"
    )
    # What the reading tools answer from once the course is saved: the tree
    # and announcements as D2L sent them, and where each saved file went,
    # keyed by topic id or by "news id/file id". Written last and swapped in
    # whole, so a copy exists only once everything above has been saved.
    record = {
        "ou": ou, "name": name, "saved": f"{datetime.now():%Y-%m-%d %H:%M}",
        "modules": modules, "news": news,
        "files": {
            "/".join(map(str, key)) if isinstance(key, tuple) else str(key):
                p.relative_to(root).as_posix()
            for key, (p, _) in zip(keys, results) if p
        },
        # Files the course's pages link to, by the link as the page has it:
        # a /content/ address, or a short link to a course file.
        "linked": {url: dest.relative_to(root).as_posix() for url, dest in linked.items()},
        # Videos are downloaded only on demand, by download_video; a refresh
        # keeps the ones still on disk.
        "videos": {
            topic: path for topic, path in ((_copy(ou) or {}).get("videos") or {}).items()
            if (root / path).exists()
        },
    }
    part = root / f"_course-{ou}.json.part"
    part.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    part.replace(root / f"_course-{ou}.json")
    _write_landing()
    # The course's bar collapses into one line that stays.
    _status(("32", "  ✓ "), ("2", prefix), ("", name), keep=True)

    def topics(modules):
        for m in modules or []:
            yield from m.get("Topics") or []
            yield from topics(m.get("Modules"))

    kinds = Counter("broken" if t.get("IsBroken") else t.get("TypeIdentifier") for t in topics(modules))
    counts = {
        "files": sum(1 for p, _ in results if p),
        "pictures": len(linked),
        "announcements": len(news),
        # Videos are ContentService topics; any other kind is a link of some sort.
        "videos": kinds["ContentService"],
        "broken": kinds["broken"],
        "links": sum(n for kind, n in kinds.items() if kind not in ("File", "ContentService", "broken")),
    }
    problems = []
    failed = [err for p, err in results if err]
    if failed:
        problems.append((f"{len(failed)} files failed", failed))
    if len(refs) > len(linked):
        problems.append((f"{len(refs) - len(linked)} pictures or linked files could not be "
                         "fetched; the pages link to them online", []))
    return _code(name), counts, problems


@mcp.tool()
def list_assignments(org_unit_id: int) -> str:
    """List assignment folders in a course with their due and closing dates."""
    lines = []
    for f in _get(f"/d2l/api/le/{LE}/{_resolve(org_unit_id)}/dropbox/folders/"):
        due = (f.get("DueDate") or "no due date")[:16]
        closes = ((f.get("Availability") or {}).get("EndDate") or "")[:16]
        # The TotalFiles / TotalUsersWithSubmissions counters on this endpoint are
        # instructor-only; D2L returns -1 to students, so submission status is not
        # reported here rather than guessed from them.
        lines.append(
            f"[{f['Id']}] {f['Name']} — due {due}"
            + (f" — closes {closes}" if closes and closes[:10] != due[:10] else "")
        )
    return "\n".join(lines) or "No assignments."


@mcp.tool()
def list_announcements(org_unit_id: int, ctx: Context | None = None) -> str:
    """List course announcements (news items), newest first.

    Each one's heading carries its id, and any attached files are listed under
    it with their file ids; get_announcement_attachment takes both. Read from
    the course's offline copy, like course_contents.
    """
    copy = _course(_resolve(org_unit_id), ctx)
    lines = [_saved_note(copy)]
    for n in copy["news"]:
        lines.append(
            f"## [{n['Id']}] {n['Title']} ({(n.get('StartDate') or '')[:10]})\n"
            f"{n['Body']['Text']}"
        )
        lines[-1] += "".join(
            f"\nAttachment [{a['FileId']}] {a['FileName']} ({a.get('Size', '?')} bytes)"
            for a in n.get("Attachments") or []
        )
    return "\n\n".join(lines) if len(lines) > 1 else "No announcements."


@mcp.tool()
def get_announcement_attachment(
    org_unit_id: int, news_id: int, file_id: int, ctx: Context | None = None
) -> str:
    """Fetch a file attached to an announcement: its text, or where it was saved.

    The ids come from list_announcements: news_id from the heading, file_id
    from the Attachment line. Handled like get_file handles a content file.
    """
    ou = _resolve(org_unit_id)
    copy = _course(ou, ctx)
    saved = copy["files"].get(f"{news_id}/{file_id}")
    if saved:
        return _saved_text(copy["root"] / saved, Path(saved).name)
    with _client() as c:
        r = c.get(f"/d2l/api/le/{LE}/{ou}/news/{news_id}/attachments/{file_id}")
    if r.status_code in (401, 403):
        raise RuntimeError(
            "Session rejected (HTTP %d). Run 'uv run --script server.py login' again."
            % r.status_code
        )
    if r.status_code == 404:
        raise RuntimeError(
            f"No attachment {file_id} on announcement {news_id}. Run "
            "list_announcements for the ids it does have."
        )
    r.raise_for_status()
    name = _filename(r, f"attachment-{file_id}")
    return _file_text(r.content, r.headers.get("content-type", ""), name, name)


def _term_units(want: tuple | None = None) -> list:
    """The org units of the courses I am enrolled in for one (year, season)
    term, by default the newest. A calendar of every course I ever took is not
    what "my calendar" means, nor a download of them what "this term" does."""
    units = [
        it["OrgUnit"]
        for it in _paged(f"/d2l/api/lp/{LP}/enrollments/myenrollments/", orgUnitTypeId=3)
    ]
    if want is None:
        want = max((_term(ou) for ou in units), default=(0, -1))
    return [ou for ou in units if _term(ou) == want]


def _my_events(ids: list[int], start: datetime, end: datetime) -> list:
    path = f"/d2l/api/le/{LE}/calendar/events/myEvents/"
    window = {
        d: t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        for d, t in (("startDateTime", start), ("endDateTime", end))
    }
    csv = ",".join(str(i) for i in ids)
    try:
        return _get(path, **window, orgUnitIdsCSV=csv)["Objects"]
    except RuntimeError:
        # One course whose calendar is closed to students answers 403 for the
        # whole batch, so ask course by course and leave that one out. If every
        # course refuses it is the session that is gone, so let the error stand.
        events, refused = [], 0
        for ou in ids:
            try:
                events += _get(path, **window, orgUnitIdsCSV=str(ou))["Objects"]
            except RuntimeError:
                refused += 1
        if refused == len(ids):
            raise
        return events


def _local(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()


def _calendar_lines(events: list) -> list[str]:
    """Group events by local day. A lecture spans a time range; a deadline or a
    release date is a single instant, so it prints one time instead of two."""
    lines, day = [], None
    for e in sorted(events, key=lambda e: e["StartDateTime"]):
        start, end = _local(e["StartDateTime"]), _local(e["EndDateTime"])
        if start.date() != day:
            day = start.date()
            lines.append(f"\n## {start:%a %d %b %Y}")

        when = "all day" if e["IsAllDayEvent"] else f"{start:%H:%M}"
        if not e["IsAllDayEvent"] and end != start:
            when += f"-{end:%H:%M}"
        entity = (e.get("AssociatedEntity") or {}).get("AssociatedEntityType") or ""
        due = "due: " if "Dropbox" in entity or "Quiz" in entity else ""
        number = re.match(r"\d{4,5}\b", e["OrgUnitName"] or "")
        course = number.group() if number else (e["OrgUnitName"] or "")
        where = (e.get("LocationName") or "").strip()
        if len(where) > 60:  # a session booked into six rooms lists them all
            where = where[:57] + "..."
        lines.append(f"{when}  {course}  {due}{e['Title']}" + (f" — {where}" if where else ""))
    return lines


@mcp.tool()
def calendar(days: int = 7, org_unit_id: int = 0) -> str:
    """Show what is scheduled from today to `days` ahead, in local time: lectures,
    exercise sessions, hand-in deadlines and material release dates.

    Covers every course of the current term, or one course when org_unit_id is
    given (an org unit id or a DTU course number)."""
    ids = [_resolve(org_unit_id)] if org_unit_id else [ou["Id"] for ou in _term_units()]
    if not ids:
        return "No course enrollments found."
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    events = _my_events(ids, start, start + timedelta(days=days))
    if not events:
        return f"Nothing scheduled in the next {days} days."
    lines = _calendar_lines(events)
    if len(events) >= 100:
        # D2L stops at 100 events and reports no continuation to page through.
        lines.append("\n(100 events — the API caps it there. Ask for fewer days.)")
    return "\n".join(lines).strip()


def _topic_parents(modules, topic_id: int, parents=()) -> tuple | None:
    """The unit folders a topic is saved under, as download_course names them."""
    for module in modules or []:
        path = parents + (_safe(module.get("Title", "unit")),)
        if any(t["TopicId"] == topic_id for t in module.get("Topics") or []):
            return path
        found = _topic_parents(module.get("Modules"), topic_id, path)
        if found:
            return found
    return None


def _video_link(ou: int, topic, disposition: str) -> str:
    """A short-lived signed link to a media-service video's file.

    Videos live in Brightspace's media service, not the course's file area,
    and the session does not reach it. The player gets in the way done here:
    the topic's page carries an anti-forgery token, which buys a short-lived
    bearer token from DTU Learn, which buys a signed link from the media
    service. The service only answers with the topic named as context; the
    same request without it is refused (403)."""
    parts = (topic.get("Url") or "").split(":")
    if parts[:3] != ["d2l", "brightspace", "content"] or len(parts) < 7:
        raise RuntimeError(f"'{topic.get('Title')}' is not in Brightspace's media service.")
    region, tenant = parts[3], parts[4]
    content, _, revision = parts[6].partition("/")
    with _client() as c:
        page = c.get(f"/d2l/le/lessons/{ou}/topics/{topic['TopicId']}", headers={"Accept": "text/html"})
        xsrf = re.search(r"XSRF\.Token','([^']+)'", page.text)
        if not xsrf:
            raise RuntimeError(
                "DTU Learn handed out no page token, so the session has probably "
                "expired. Run 'uv run --script server.py login' again."
            )
        r = c.post("/d2l/lp/auth/oauth2/token", data={"scope": "*:*:*"},
                   headers={"X-Csrf-Token": xsrf.group(1)})
        r.raise_for_status()
        token = r.json()["access_token"]
    r = httpx.get(
        f"https://api.{region}.content-service.brightspace.com/api/{tenant}/content/{content}"
        f"/revisions/{revision or 'latest'}/resources/transcodes/signed-url",
        params={"disposition": disposition, "contextType": "topic", "contextId": f"{topic['TopicId']}:{ou}"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if r.status_code in (401, 403):
        raise RuntimeError(f"The media service refused '{topic.get('Title')}' (HTTP {r.status_code}).")
    r.raise_for_status()
    try:
        answer = r.json()
    except ValueError:
        return r.text.strip().strip('"')
    if isinstance(answer, dict):
        answer = answer.get("value") or answer.get("url") or answer.get("Url") or answer.get("signedUrl")
    if not isinstance(answer, str) or not answer.startswith("https://"):
        raise RuntimeError("The media service answered without a link.")
    return answer


def _video_topic(org_unit_id: int, topic_id: int) -> tuple:
    ou = _resolve(org_unit_id)
    copy = _course(ou)
    topic = _toc_index(copy["modules"])[0].get(topic_id)
    if topic is None or topic.get("TypeIdentifier") != "ContentService":
        raise RuntimeError(f"Topic {topic_id} is not a video in that course. Run course_contents.")
    return ou, copy, topic


def video_link(org_unit_id: int, topic_id: int) -> str:
    """Print a short-lived link to play a video topic, for the LearnHarness view.

    Not an MCP tool: the link is a credential for as long as it lasts.
    """
    ou, _, topic = _video_topic(org_unit_id, topic_id)
    return _video_link(ou, topic, "inline")


def download_video(org_unit_id: int, topic_id: int) -> str:
    """Download a video topic into its course's offline copy, next to its unit's files.

    Videos are left out of download_course, being large; this fetches one on
    demand. The course's record notes where it went, and a refresh keeps it.
    """
    ou, copy, topic = _video_topic(org_unit_id, topic_id)
    root = copy["root"]
    dest = root.joinpath(*(_topic_parents(copy["modules"], topic_id) or ()), _safe(topic.get("Title", "video")) + ".mp4")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    # DTU_PROGRESS asks for a line per percent on stderr, "progress <bytes>
    # <total>", for the LearnHarness plugin to show; a terminal gets the
    # status line instead.
    report, shown = os.environ.get("DTU_PROGRESS"), -1
    with httpx.stream("GET", _video_link(ou, topic, "attachment"), timeout=60, follow_redirects=True) as r:
        r.raise_for_status()
        total, done = int(r.headers.get("content-length") or 0), 0
        with part.open("wb") as out:
            for chunk in r.iter_bytes(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if total:
                    _status(("1", f"  {topic.get('Title')} "), ("2", f"{done / 1e6:.0f}/{total / 1e6:.0f} MB"))
                    if report and done * 100 // total != shown:
                        shown = done * 100 // total
                        print(f"progress {done} {total}", file=sys.stderr, flush=True)
    part.replace(dest)
    _status(("32", "  ✓ "), ("", topic.get("Title", "")), keep=True)
    # Note it in the course's record, swapped in whole like download_course's.
    record_path = root / f"_course-{ou}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record.setdefault("videos", {})[str(topic_id)] = dest.relative_to(root).as_posix()
    tmp = record_path.with_name(record_path.name + ".part")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    tmp.replace(record_path)
    return f"Saved {dest} ({dest.stat().st_size / 1e6:.0f} MB)."


def _rich(field) -> dict:
    """D2L rich text, a plain and an HTML rendition, as the view takes it."""
    field = field or {}
    return {"text": field.get("Text") or "", "html": field.get("Html") or ""}


# Group categories a student signs up to themselves, by D2L's EnrollmentStyle:
# people per group, number of groups, or both, with self-enrolment.
SELF_ENROLMENT = {3, 4, 5}


def _group_rows(categories: list, groups: list, me: int, classlist: list) -> list:
    """A course's group categories as its Groups tab shows them, each with
    the learner's own group and its members, if they are in one. groups holds
    each category's groups, in the same order; their Enrollments are user
    ids, which the class list names."""
    people = {int(u["Identifier"]): u for u in classlist}
    rows = []
    for category, members_of in zip(categories, groups):
        mine = next((g for g in members_of if me in (g.get("Enrollments") or [])), None)
        rows.append({
            "name": category["Name"],
            "description": _rich(category.get("Description")),
            "groups": len(members_of),
            "size": category.get("MaxUsersPerGroup"),
            "signup": category.get("EnrollmentStyle") in SELF_ENROLMENT,
            "opens": category.get("SelfEnrollmentStartDate"),
            "closes": category.get("SelfEnrollmentExpiryDate"),
            "mine": mine and {
                "name": mine["Name"],
                "description": _rich(mine.get("Description")),
                "members": [
                    {"name": people[i].get("DisplayName") or f"{people[i].get('FirstName', '')} {people[i].get('LastName', '')}".strip(),
                     "email": people[i].get("Email") or "", "role": people[i].get("ClasslistRoleDisplayName") or "",
                     "me": i == me}
                    for i in mine["Enrollments"] if i in people
                ],
            },
        })
    return rows


def course_tab(org_unit_id: int, tab: str) -> str:
    """Print one tab of a course's page in the LearnHarness view as JSON.

    tab is assignments, quizzes, groups or calendar, read live from DTU Learn.
    Not an MCP tool: the agents have list_assignments and calendar, and this
    carries what a page shows rather than what an answer needs.
    """
    ou = _resolve(org_unit_id)
    if tab == "assignments":
        items = [
            {
                "id": f["Id"], "name": f["Name"], "due": f.get("DueDate"),
                "start": (f.get("Availability") or {}).get("StartDate"),
                "end": (f.get("Availability") or {}).get("EndDate"),
                "instructions": _rich(f.get("CustomInstructions")),
                "files": [a.get("FileName") for a in f.get("Attachments") or []],
                "links": [{"name": link.get("LinkName") or link.get("Href"), "url": link.get("Href")}
                          for link in f.get("LinkAttachments") or []],
                # A group assignment names the group category it is handed in by.
                "group": bool(f.get("GroupTypeId")),
                "points": (f.get("Assessment") or {}).get("ScoreDenominator"),
                "url": f"{BASE}/d2l/lms/dropbox/user/folder_submit_files.d2l?db={f['Id']}&ou={ou}",
            }
            for f in _get(f"/d2l/api/le/{LE}/{ou}/dropbox/folders/") if not f.get("IsHidden")
        ]
        return json.dumps({"items": items}, ensure_ascii=False)
    if tab == "quizzes":
        page = _get(f"/d2l/api/le/{LE}/{ou}/quizzes/")
        quizzes = page.get("Objects") or []
        while page.get("Next"):
            page = _get(page["Next"])
            quizzes += page.get("Objects") or []
        items = []
        for q in quizzes:
            if not q.get("IsActive"):
                continue  # hidden from students on DTU Learn too
            attempts = q.get("AttemptsAllowed") or {}
            limit = q.get("SubmissionTimeLimit") or {}
            description = q.get("Description") or {}
            items.append({
                "id": q["QuizId"], "name": q["Name"],
                "start": q.get("StartDate"), "due": q.get("DueDate"), "end": q.get("EndDate"),
                "attempts": None if attempts.get("IsUnlimited") else attempts.get("NumberOfAttemptsAllowed"),
                "minutes": limit.get("TimeLimitValue") if limit.get("IsEnforced") else None,
                "description": _rich(description.get("Text") if description.get("IsDisplayed", True) else None),
                "url": f"{BASE}/d2l/lms/quizzing/user/quiz_summary.d2l?qi={q['QuizId']}&ou={ou}",
            })
        return json.dumps({"items": items}, ensure_ascii=False)
    if tab == "groups":
        me = int(_get(f"/d2l/api/lp/{LP}/users/whoami")["Identifier"])
        categories = _get(f"/d2l/api/lp/{LP}/{ou}/groupcategories/")
        with ThreadPoolExecutor(max_workers=8) as pool:
            groups = list(pool.map(
                lambda c: _get(f"/d2l/api/lp/{LP}/{ou}/groupcategories/{c['GroupCategoryId']}/groups/"),
                categories,
            ))
        # Names only for the learner's own groups, so the class list only then.
        mine = any(me in (g.get("Enrollments") or []) for gs in groups for g in gs)
        classlist = _get(f"/d2l/api/le/{LE}/{ou}/classlist/") if mine else []
        return json.dumps({"items": _group_rows(categories, groups, me, classlist)}, ensure_ascii=False)
    if tab == "calendar":
        # From today, as far ahead as a term runs.
        start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        events = _my_events([ou], start, start + timedelta(days=150))
        items = []
        for e in sorted(events, key=lambda e: e["StartDateTime"]):
            entity = (e.get("AssociatedEntity") or {}).get("AssociatedEntityType") or ""
            # Event descriptions are HTML. A booked lecture's only says that
            # TimeEdit made it, which is no news on every line of the week.
            html = (e.get("Description") or "").strip()
            if "room bookings in TimeEdit" in html:
                html = ""
            items.append({
                "title": e["Title"], "start": e["StartDateTime"], "end": e["EndDateTime"],
                "allDay": e["IsAllDayEvent"], "where": (e.get("LocationName") or "").strip(),
                "due": "Dropbox" in entity or "Quiz" in entity,
                "description": {"text": "", "html": html},
            })
        # D2L stops at 100 events and reports no continuation to page through.
        return json.dumps({"items": items, "capped": len(events) >= 100}, ensure_ascii=False)
    raise RuntimeError(f"No tab {tab!r}: assignments, quizzes, groups or calendar.")


COMMANDS = {
    f.__name__: f
    for f in (
        login,
        list_courses,
        course_contents,
        unit_info,
        get_file,
        get_pdf_pages,
        video_link,
        download_video,
        course_tab,
        list_assignments,
        list_announcements,
        get_announcement_attachment,
        calendar,
        download_course,
        download_term,
    )
}


def _usage() -> str:
    """Build the help text from the same docstrings the MCP tools advertise."""
    lines = ["Usage: uv run --script server.py [command] [arguments]", "", "Commands:"]
    for name, fn in COMMANDS.items():
        params = " ".join(f"<{p}>" for p in inspect.signature(fn).parameters if p != "ctx")
        lines.append(f"  {name} {params}".rstrip())
        lines.append(f"      {inspect.getdoc(fn).splitlines()[0]}")
    lines += [
        "  tools",
        "      Print the tools as JSON, for clients that speak no MCP (LearnHarness)",
        "  file_text <path>",
        "      Print a saved file's text, as get_file does a topic's",
        "  --json <command> [arguments]",
        "      Print a command's result as MCP content blocks, images included",
        "",
        "With no command, runs as an MCP server on stdin/stdout, which is how",
        "Claude Code, Cursor and other MCP clients start it.",
        "",
        "Environment:",
        f"  DTU_COOKIE           Cookie header to use instead of {COOKIE_FILE}",
        f"  DTU_COOKIE_FILE      Where the session is stored [{COOKIE_FILE}]",
        f"  DTU_DOWNLOADS        Where get_file saves binaries [{DOWNLOADS}]",
        f"  DTU_BASE             Brightspace instance [{BASE}]",
        "  DTU_FIREFOX_PROFILE  Profile directory to read cookies from; skips the scan",
        "  DTU_MCP_PATH         Serve over HTTP at this path instead of stdio.",
        "                       Needs DTU_ALLOWED_HOST. Exposes your session to",
        "                       anyone who has the URL, so keep the path secret.",
        "  DTU_ALLOWED_HOST     Public hostname allowed to reach the HTTP server",
        f"  DTU_PORT             Port for the HTTP server [{os.environ.get('DTU_PORT', 8000)}]",
        "",
        "Examples:",
        "  uv run --script server.py login",
        "  uv run --script server.py list_courses",
        "  uv run --script server.py course_contents 326350",
        '  uv run --script server.py download_term "Fall 2026"',
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    # A command name as the first argument runs that one thing and prints the
    # result, for pasting into claude.ai or any other chat. No server, no
    # network exposure.
    # --json prints the result as MCP content blocks, images included, for
    # the LearnHarness plugin. Plain output names an image instead.
    as_json = sys.argv[1:2] == ["--json"]
    if as_json:
        del sys.argv[1]
    if len(sys.argv) > 1:
        name = sys.argv[1]
        if name in ("-h", "--help", "help"):
            print(_usage())
            raise SystemExit(0)
        if name == "file_text" and len(sys.argv) == 3:
            # A saved file's text, as get_file gives a topic's: for files a
            # course links to, which have no topic. Command line only; the
            # LearnHarness plugin checks the path is one a course saved.
            path = Path(sys.argv[2])
            print(_saved_text(path, path.name))
            raise SystemExit(0)
        if name == "tools":
            # Names, descriptions and argument schemas as MCP clients see
            # them, so the LearnHarness plugin offers the same tools.
            tools = anyio.run(mcp.list_tools)
            print(json.dumps([
                {"name": t.name, "description": t.description, "parameters": t.input_schema}
                for t in tools
            ], indent=1))
            raise SystemExit(0)
        if name not in COMMANDS:
            raise SystemExit(f"Unknown command {name!r}.\n\n{_usage()}")
        args = [int(a) if a.lstrip("-").isdigit() else a for a in sys.argv[2:]]
        try:
            # Let inspect report a wrong argument count instead of a traceback.
            inspect.signature(COMMANDS[name]).bind(*args)
        except TypeError as e:
            raise SystemExit(f"{name}: {e}\n\n{_usage()}")
        try:
            result = COMMANDS[name](*args)
        except RuntimeError as e:
            # Expired sessions, unknown course ids and the like are ordinary
            # outcomes here; a traceback only buries the message.
            _status()  # or the message lands on the end of a download's status line
            raise SystemExit(str(e))
        except httpx.TransportError as e:
            # No network, or DTU Learn down: also an ordinary outcome.
            _status()
            raise SystemExit(f"Could not reach DTU Learn or Brightspace: {e}")
        if result is not None:
            blocks = _blocks(result)
            print(json.dumps(blocks) if as_json else "\n".join(
                b.get("text") or f"[{b['mimeType']} image, {len(b['data']) * 3 // 4096} KB]"
                for b in blocks
            ))
        raise SystemExit(0)

    # Setting DTU_MCP_PATH switches on the remote transport that claude.ai
    # connectors require. That path is the only thing standing between the
    # public internet and your DTU session, so make it a long random string
    # and treat the resulting URL as a password.
    path = os.environ.get("DTU_MCP_PATH")
    if not path:
        mcp.run()
    elif not os.environ.get("DTU_ALLOWED_HOST"):
        raise SystemExit(
            "DTU_MCP_PATH is set but DTU_ALLOWED_HOST is not. Set it to the public "
            "hostname of your tunnel (for example abc-def.trycloudflare.com) so DNS "
            "rebinding protection stays on."
        )
    else:
        mcp.run(
            transport="streamable-http",
            host="127.0.0.1",
            port=int(os.environ.get("DTU_PORT", 8000)),
            streamable_http_path=path,
            transport_security=TransportSecuritySettings(
                allowed_hosts=[os.environ["DTU_ALLOWED_HOST"]],
                allowed_origins=["https://claude.ai"],
            ),
        )
