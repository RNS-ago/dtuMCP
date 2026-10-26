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
from functools import lru_cache
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from mcp.server.mcpserver import MCPServer
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
    return httpx.Client(
        base_url=BASE,
        headers={"Cookie": cookie, "Accept": "application/json"},
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

    items = _paged(f"/d2l/api/lp/{LP}/enrollments/myenrollments/", orgUnitTypeId=3)
    matches = [
        it["OrgUnit"]
        for it in items
        if (m := re.match(r"(\d{4,5})\b", it["OrgUnit"]["Name"]))
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


@lru_cache(maxsize=None)
def _toc_index(ou: int) -> tuple:
    """Index one fetch of the table of contents by topic id and by module id.
    get_file needs to know what kind of thing a topic is before fetching it, and
    the single-topic endpoint does not answer consistently across courses."""
    topics, units = {}, {}

    def walk(modules):
        for m in modules or []:
            units[m["ModuleId"]] = m
            for t in m.get("Topics") or []:
                topics[t["TopicId"]] = t
            walk(m.get("Modules"))

    walk(_get(f"/d2l/api/le/{LE}/{ou}/content/toc").get("Modules"))
    return topics, units


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
    """TOC modules nest arbitrarily; flatten to one line per module and topic."""
    out = []
    for m in modules or []:
        info = " (info)" if _description(m) else ""
        out.append("  " * depth + f"# [{m['ModuleId']}] {m.get('Title', '?')}{info}")
        for t in m.get("Topics") or []:
            kind = t.get("TypeIdentifier", "?")
            # Link topics point either inside D2L (a relative quicklink, no use
            # to anyone outside it) or out to the course's own website, which is
            # public. Only the latter is worth printing.
            url = t.get("Url") or ""
            external = f" {url}" if url.startswith("http") else ""
            if kind == "ContentService":
                kind = _urn_kind(url)  # "video" reads better than "ContentService"
            # A course copied from last year keeps topics whose file did not
            # come with it. D2L flags them and answers 404 for their content.
            if t.get("IsBroken"):
                kind += ", broken"
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
    """List the DTU Learn courses you are enrolled in, grouped by term, newest first."""
    items = _paged(f"/d2l/api/lp/{LP}/enrollments/myenrollments/", orgUnitTypeId=3)
    groups = {}
    for it in items:
        ou = it["OrgUnit"]
        groups.setdefault(_term(ou), []).append(f"[{ou['Id']}] {ou['Name']}")

    out = []
    for term in sorted(groups, reverse=True):
        year, season = term
        out.append(f"## {SEASON_NAMES[season]} {year}" if year else "## No term")
        out.extend(sorted(groups[term]))
        out.append("")
    return "\n".join(out).strip() or "No course enrollments found."


@mcp.tool()
def course_contents(org_unit_id: int) -> str:
    """Show a course's content tree. Takes an org unit id or a DTU course number.

    The topic ids it prints are what get_file takes."""
    toc = _get(f"/d2l/api/le/{LE}/{_resolve(org_unit_id)}/content/toc")
    return "\n".join(_flatten(toc.get("Modules"))) or "Course has no content."


@mcp.tool()
def unit_info(org_unit_id: int, unit_id: int) -> str:
    """Show a unit's own text: its schedule, instructions and notes, plus its topics.

    Unit ids are the bracketed numbers on the `#` lines of course_contents. Only
    units marked `(info)` there have any text of their own.
    """
    unit = _toc_index(_resolve(org_unit_id))[1].get(unit_id)
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
def get_file(org_unit_id: int, topic_id: int) -> str:
    """Fetch one content topic: a file's text, an HTML page's text, or a link's target.

    Topic ids come from course_contents. PDFs come back as extracted text with
    the saved file's path; other binaries are saved and only the path returned.
    """
    ou = _resolve(org_unit_id)
    topic = _toc_index(ou)[0].get(topic_id)
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

    r, name = _topic_fetch(ou, topic)
    return _file_text(r, name, title)


def _file_text(r: httpx.Response, name: str, title) -> str:
    """Turn a fetched file into text for the caller, saving it to disk when the
    bytes are not text themselves. Shared by content topics and announcement
    attachments, which arrive the same way."""
    ctype = r.headers.get("content-type", "")

    if "html" in ctype:
        return f"[{title}]\n\n{_html_text(r.text)}"
    if ctype.startswith("text/") or "json" in ctype:
        return r.text
    if ctype == "d2l/unknowntype":
        # D2L labels every extension it does not know this way, and many of them
        # are plain text: source code, PRISM models, LaTeX. Let the bytes decide.
        try:
            return r.content.decode("utf-8")
        except UnicodeDecodeError:
            pass

    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    path = DOWNLOADS / name
    path.write_bytes(r.content)

    if "pdf" in ctype or name.lower().endswith(".pdf"):
        text = _pdf_text(path)
        if text:
            # Reading the PDF itself renders every page as an image, which costs
            # roughly ten times the tokens of its text. Extraction drops figures
            # and mangles heavy mathematical notation, so say where the original
            # is rather than pretending the text is the whole document.
            cut = "\n\n[Text truncated here.]" if len(text) > TEXT_LIMIT else ""
            return (
                f"[Text of {name}. The PDF itself is at {path} -- open it for "
                f"figures or mathematical notation, which do not survive "
                f"extraction.]\n\n{text[:TEXT_LIMIT]}{cut}"
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
        f"No text could be extracted from {name} ({ctype}, {len(r.content)} bytes). "
        f"It is saved at {path} on the machine running this server; open it there."
    )


def _safe(name: str) -> str:
    """A title becomes a directory or file name, so drop what a filesystem
    would choke on and keep the rest recognisable."""
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip(". ")[:80] or "untitled"


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


@mcp.tool()
def download_course(org_unit_id: int) -> str:
    """Download every file in a course to disk, mirroring its unit tree.

    Files land in ~/Downloads/dtulearn/<course>/<unit>/, on the machine running
    this server. Links and videos are not files and are only counted. Nothing
    comes back as text -- for that, ask for the one topic with get_file.
    """
    ou = _resolve(org_unit_id)
    modules = _get(f"/d2l/api/le/{LE}/{ou}/content/toc").get("Modules")
    jobs = list(_file_topics(modules))
    if not jobs:
        return "That course has no files to download."

    root = DOWNLOADS / _safe(_course_name(ou))
    # ponytail: eight at a time; a course is a few hundred files at most, so
    # nothing here needs a rate limiter or a resumable queue.
    with _client() as c, ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda job: _save_topic(c, ou, root, *job), jobs))

    saved = [p for p, err in results if p]
    failed = [err for p, err in results if err]
    kinds = Counter(
        "broken" if t.get("IsBroken") else t.get("TypeIdentifier")
        for t in _toc_index(ou)[0].values()
    )
    skipped = ", ".join(
        f"{n} {kind}" for kind, n in sorted(kinds.items()) if kind != "File"
    )
    out = [f"Saved {len(saved)} files under {root}"]
    if skipped:
        out.append(f"Nothing to download for: {skipped}.")
    if failed:
        out.append(f"{len(failed)} failed:")
        out += [f"  - {f}" for f in failed]
    return "\n".join(out)


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
def list_announcements(org_unit_id: int) -> str:
    """List course announcements (news items), newest first.

    Each one's heading carries its id, and any attached files are listed under
    it with their file ids; get_announcement_attachment takes both.
    """
    lines = []
    for n in _get(f"/d2l/api/le/{LE}/{_resolve(org_unit_id)}/news/"):
        lines.append(
            f"## [{n['Id']}] {n['Title']} ({(n.get('StartDate') or '')[:10]})\n"
            f"{n['Body']['Text']}"
        )
        lines[-1] += "".join(
            f"\nAttachment [{a['FileId']}] {a['FileName']} ({a.get('Size', '?')} bytes)"
            for a in n.get("Attachments") or []
        )
    return "\n\n".join(lines) or "No announcements."


@mcp.tool()
def get_announcement_attachment(org_unit_id: int, news_id: int, file_id: int) -> str:
    """Fetch a file attached to an announcement: its text, or where it was saved.

    The ids come from list_announcements: news_id from the heading, file_id
    from the Attachment line. Handled like get_file handles a content file.
    """
    ou = _resolve(org_unit_id)
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
    return _file_text(r, name, name)


def _current_term_ids() -> list[int]:
    """Org unit ids of the courses in the newest term I am enrolled in. A
    calendar of every course I ever took is not what "my calendar" means."""
    units = [
        it["OrgUnit"]
        for it in _paged(f"/d2l/api/lp/{LP}/enrollments/myenrollments/", orgUnitTypeId=3)
    ]
    newest = max((_term(ou) for ou in units), default=(0, -1))
    return [ou["Id"] for ou in units if _term(ou) == newest]


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
    ids = [_resolve(org_unit_id)] if org_unit_id else _current_term_ids()
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


COMMANDS = {
    f.__name__: f
    for f in (
        login,
        list_courses,
        course_contents,
        unit_info,
        get_file,
        list_assignments,
        list_announcements,
        calendar,
        download_course,
    )
}


def _usage() -> str:
    """Build the help text from the same docstrings the MCP tools advertise."""
    lines = ["Usage: uv run --script server.py [command] [arguments]", "", "Commands:"]
    for name, fn in COMMANDS.items():
        params = " ".join(f"<{p}>" for p in inspect.signature(fn).parameters)
        lines.append(f"  {name} {params}".rstrip())
        lines.append(f"      {inspect.getdoc(fn).splitlines()[0]}")
    lines += [
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
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    # A command name as the first argument runs that one thing and prints the
    # result, for pasting into claude.ai or any other chat. No server, no
    # network exposure.
    if len(sys.argv) > 1:
        name = sys.argv[1]
        if name in ("-h", "--help", "help"):
            print(_usage())
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
            raise SystemExit(str(e))
        if result is not None:
            print(result)
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
