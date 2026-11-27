"""Saved files: their text, where they go and the files a course links to."""

import filecmp
import mimetypes
import os
import re
import shutil
from functools import lru_cache
from html import unescape
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import httpx
from mcp.server.mcpserver import Image

from .app import BASE, DOWNLOADS, LE, LP
from .text import TEXT_LIMIT, _html_text, _ooxml_text, _pdf_text, _sheet_text
from .client import _paged
from .courses import _topic_fetch
from .pdfmath import MATH_NOTATION, _math_text, _numbered_pages, _page_marks, _save


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


# Python's own table has no Office types: without the system's (/etc/mime.types,
# the Windows registry) a saved workbook would read as an unknown file.
for _suffix, _kind in ((".docx", "wordprocessingml.document"), (".xlsx", "spreadsheetml.sheet"),
                       (".pptx", "presentationml.presentation")):
    mimetypes.add_type(f"application/vnd.openxmlformats-officedocument.{_kind}", _suffix)


def _saved_text(path: Path, title, marks: dict | None = None, pages: tuple = (1, 0), link=None,
                first_row: int = 1, find: str = "") -> str:
    """_file_text for a file in an offline copy. The copy keeps no content
    type, so the extension stands in. Anything but a page, a PDF or an Office
    file is left to the bytes, as D2L's unknown type is: guess_type calls a
    MATLAB .m file a Mathematica package."""
    ctype = mimetypes.guess_type(path.name)[0] or ""
    if not any(kind in ctype for kind in ("html", "pdf", "officedocument")):
        ctype = "d2l/unknowntype"
    return _file_text(path.read_bytes(), ctype, path.name, title, path, marks, pages, link, first_row, find)


def _capped(text: str, path: Path | None) -> str:
    """A text file's text up to TEXT_LIMIT. A Maple worksheet (.mw) is XML of
    several megabytes, mostly its plots, which no caller can take whole."""
    if len(text) <= TEXT_LIMIT:
        return text
    where = f" The whole file is at {path}." if path else ""
    return f"{text[:TEXT_LIMIT]}\n\n[Text cut here, after {TEXT_LIMIT:,} of its {len(text):,} characters.{where}]"


def _file_text(content: bytes, ctype: str, name: str, title, path: Path | None = None,
               marks: dict | None = None, pages: tuple = (1, 0), link=None, first_row: int = 1,
               find: str = "") -> str:
    """Turn a file into text for the caller, saving it to disk when the bytes
    are not text themselves. Shared by content topics and announcement
    attachments, fetched or read from an offline copy; path is where a copy
    already keeps the file, and marks what _page_marks found in it when the
    copy was saved; a PDF without them is looked at now. pages is the first
    and last page of a PDF to give (0: to its end), whole pages up to
    TEXT_LIMIT; a workbook's pages are its sheets, from first_row of the
    first, or only the rows with every word of find (_sheet_text)."""
    if "html" in ctype:
        return f"[{title}]\n\n{_html_text(_decode(content, ctype), link)}"
    if ctype.startswith("text/") or "json" in ctype:
        return _capped(_decode(content, ctype), path)
    if ctype == "d2l/unknowntype":
        # D2L labels every extension it does not know this way, and many of them
        # are plain text: source code, PRISM models, LaTeX. Let the bytes decide.
        try:
            return _capped(content.decode("utf-8"), path)
        except UnicodeDecodeError:
            pass

    if path is None:
        DOWNLOADS.mkdir(parents=True, exist_ok=True)
        path = DOWNLOADS / name
        _save(path, content)

    if "pdf" in ctype or name.lower().endswith(".pdf"):
        text = _pdf_text(path)
        if text:
            # Reading the PDF itself renders every page as an image, which costs
            # roughly ten times the tokens of its text. Extraction drops figures
            # and mangles heavy mathematical notation, so say where the original
            # is rather than pretending the text is the whole document, and
            # number the pages so the ones that matter can be looked at.
            marks = _page_marks(path) if marks is None else marks
            rebuilt = _math_text(path)
            blocks = _numbered_pages(text, marks, rebuilt)
            first, last = pages[0], min(pages[1] or len(blocks), len(blocks))
            if not 1 <= first <= last:
                raise RuntimeError(f"{name} has pages 1 to {len(blocks)}: ask for first_page "
                                   f"to last_page within them.")
            # Whole pages, so a page is never half read; a page longer than the
            # limit on its own is cut, and says so.
            shown, size = [], 0
            for n, block in enumerate(blocks[first - 1:last], first):
                if len(block) > TEXT_LIMIT:  # pdftotext -layout pads columns with spaces; most such pages fit squeezed
                    block = re.sub(r" {3,}", "  ", block)
                if len(block) > TEXT_LIMIT:
                    block = (block[:TEXT_LIMIT] + f"\n\n[Page {n} is cut here, after {TEXT_LIMIT:,} "
                             f"characters. get_pdf_pages shows it whole.]")
                if shown and size + len(block) > TEXT_LIMIT:
                    break
                shown.append(block)
                size += len(block) + 2
            end = first + len(shown) - 1
            span = "" if (first, end) == (1, len(blocks)) else f", pages {first} to {end} of {len(blocks)}"
            cut = (f"\n\n[Text stops after page {end} of {len(blocks)}. Ask for first_page={end + 1} "
                   f"for the rest.]" if end < last else "")
            text = "\n\n".join(shown)
            return (
                f"[Text of {name}{span}. The PDF itself is at {path}. Figures do "
                f"not survive extraction, and mathematical notation "
                f"{'only roughly' if rebuilt else 'does not either'}: open it, or "
                f"see those pages with get_pdf_pages. A page "
                f"heading says when the page has images, a drawing, math or "
                f"unreadable symbols (a font its text cannot name, so look at "
                f"the page for those); "
                f"a figure of level and upright lines alone (a grid, a "
                f"table) is not marked.{MATH_NOTATION if rebuilt else ''}]"
                f"\n\n{text}{cut}"
            )
        # Say why, or a missing pdftotext reads as a PDF made of images.
        why = ("pdftotext, from poppler, is not installed on the machine running this server"
               if not shutil.which("pdftotext") else
               "it has no text layer, as a scanned PDF has none; get_pdf_pages shows its pages as images")
        return f"No text could be extracted from {name}: {why}. It is saved at {path}."

    if "spreadsheetml" in ctype and (sheets := _sheet_text(path, name, pages, first_row, find)) is not None:
        return (f"{sheets}\n\nThe workbook itself is at {path}. To search or sum a long sheet, open it with "
                "pandas or openpyxl if you can run code.")
    office = _ooxml_text(path, ctype)
    if office:
        cut = "\n\n[Text truncated here.]" if len(office) > TEXT_LIMIT else ""
        return f"[Text of {name}.]\n\n{office[:TEXT_LIMIT]}{cut}"

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
    name = re.sub(r'[\x00-\x1f\\/:*?"<>|]+', "_", name)[:80].strip(" .,") or "untitled"
    # Names Windows keeps for devices, with or without an extension: "Aux"
    # cannot be a file or folder there. Changed everywhere, so a course saves
    # under the same names on every system.
    return re.sub(r"^(CON|PRN|AUX|NUL|COM\d|LPT\d)(?=\.|$)", r"\1_", name, flags=re.I)


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


def _store(dest: Path, content: bytes) -> str | None:
    """Write a downloaded file, and say what went wrong if the disk refused
    it, so one file that cannot be written fails alone, not its course."""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _save(dest, content)
    except OSError as e:  # open in another program, a path too long for Windows
        return f"could not be saved at {dest}: {e.strerror or e}"
    return None


def _save_topic(client, ou: int, root: Path, parents: tuple, topic, own_id: bool = False) -> tuple:
    try:
        r, name = _topic_fetch(ou, topic, client)
    except (RuntimeError, httpx.HTTPError) as e:
        return None, f"{topic.get('Title', topic['TopicId'])}: {e}"
    # Named after the topic, which is what the course tree calls it, with the
    # extension off the server's filename. Two runs then overwrite rather than
    # pile up copies. own_id adds the topic id, for a title another topic of
    # the unit shares, which would otherwise overwrite this one's file.
    stem = _safe(topic.get("Title", "")) + (f" ({topic['TopicId']})" if own_id else "")
    dest = root.joinpath(*parents, stem + Path(name).suffix)
    if problem := _store(dest, r.content):
        return None, f"{topic.get('Title', topic['TopicId'])}: {problem}"
    return dest, None


def _save_attachment(client, ou: int, root: Path, news_id: int, att) -> tuple:
    name = att["FileName"]
    dest = root / "Announcements" / f"{news_id}-{_safe(Path(name).stem)}{Path(name).suffix}"
    try:
        r = client.get(f"/d2l/api/le/{LE}/{ou}/news/{news_id}/attachments/{att['FileId']}")
        r.raise_for_status()
    except httpx.HTTPError as e:
        return None, f"{name}: {e}"
    if problem := _store(dest, r.content):
        return None, f"{name}: {problem}"
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
    if _store(dest, r.content):
        return url, None  # as when it cannot be fetched: the page keeps its online link
    return url, dest


def _dedupe_linked(linked: dict, saved: list) -> dict:
    """linked with each file that is a byte-for-byte copy of a saved topic file
    pointed at that file instead, and the copy deleted. A page may link to a
    file the course also lists as a topic, which would otherwise be kept,
    searched and found twice, the second time without a topic id to read it by."""
    by_size = {}
    for path in saved:
        by_size.setdefault(path.stat().st_size, []).append(path)
    same = {}
    for dest in set(linked.values()):
        size = dest.stat().st_size
        # Empty files are all alike, and none is the same file as another. A
        # link a reprocess finds already pointed at its topic file is no copy of it.
        match = size and next((p for p in by_size.get(size, ())
                               if not p.samefile(dest) and filecmp.cmp(p, dest, shallow=False)), None)
        if match:
            dest.unlink()
            same[dest] = match
    return {url: same.get(dest, dest) for url, dest in linked.items()}


def _link_note(copy: dict, page: Path | None = None):
    """For _html_text: how to open a saved file a link points at, as
    "saved file: topic_id 7" or 'saved file: linked="_linked/..."', or None.
    The link is a DTU Learn address, as unit text has it, or one relative to
    page, as the saved pages have them (_relink)."""
    root, linked = copy["root"].resolve(), copy.get("linked") or {}
    topics = {saved: key for key, saved in (copy.get("files") or {}).items() if "/" not in key}
    kept = set(linked.values())

    def note(href):
        saved = linked.get(_d2l_file(href) or "")
        if saved is None and page is not None and href and not urlparse(href).scheme and href[0] not in "/#":
            try:
                saved = (page.parent / unquote(href.split("#")[0])).resolve().relative_to(root).as_posix()
            except ValueError:
                return None
        if saved in topics:
            return f"saved file: topic_id {topics[saved]}"
        return f'saved file: linked="{saved}"' if saved in kept else None

    return note


def _linked_file(copy: dict, linked: str) -> Path:
    """The saved copy of a file the course's pages link to, by the linked="..."
    path search_courses and page text give, or by the link itself. Only files
    the copy lists: the path is never joined on unchecked."""
    links, want = copy.get("linked") or {}, linked.strip()
    saved = want if want in set(links.values()) else links.get(_d2l_file(want) or "")
    if saved is None:
        raise RuntimeError(f"No linked file {linked!r} in that course. search_courses and the "
                           'text of its pages give them as linked="_linked/...".')
    return copy["root"] / saved


def _relink(html: str, page: Path, linked: dict) -> str:
    """Point a page's DTU Learn pictures and file links at the copies in linked."""
    def swap(m):
        dest = linked.get(_d2l_file(m.group(2)))
        if dest is None:
            return m.group(0)
        return m.group(1) + quote(Path(os.path.relpath(dest, page.parent)).as_posix()) + '"'
    return REFERENCE.sub(swap, html)
