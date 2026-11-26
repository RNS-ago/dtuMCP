"""Plain text out of HTML, Word, PowerPoint and PDF files."""

import re
import subprocess
import zipfile
from html import unescape
from html.parser import HTMLParser
from pathlib import Path


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
            encoding="utf-8",  # what pdftotext writes; text=True would guess cp1252 on Windows
            timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0 or not done.stdout.strip():
        return None
    return done.stdout.translate(str.maketrans(PIECES))


class _TextExtractor(HTMLParser):
    """Strip a D2L HTML page down to readable text. link, given a link's
    href, names how to open what it points at, written after its words."""

    def __init__(self, link=None):
        super().__init__()
        self.parts, self.skip, self.link, self.note = [], 0, link, None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")
        elif tag == "a" and self.link:
            self.note = self.link(dict(attrs).get("href") or "")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        elif tag == "a" and self.note:
            self.parts.append(f"[{self.note}]")
            self.note = None

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def _html_text(html: str, link=None) -> str:
    parser = _TextExtractor(link)
    parser.feed(html)
    text = re.sub(r"[ \t]+", " ", " ".join(parser.parts))
    # Joining with spaces leaves one on each side of the inserted line breaks.
    return re.sub(r"[ \t]*\n[ \t\n]*", "\n", text).strip()


# The pieces of tall parentheses, brackets, braces and arrows, for which
# pdfTeX's maps give Adobe's private-use codes.
PIECES = dict(zip(""
                  "", "⏐⎯⎛⎜⎝⎡⎢⎣⎧⎨⎩⎪⎮⎞⎟⎠⎤⎥⎦⎫⎬⎭"))
