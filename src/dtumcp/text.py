"""Plain text out of HTML, Word, PowerPoint and PDF files."""

import json
import posixpath
import re
import subprocess
import zipfile
from datetime import datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET


TEXT_LIMIT = 100_000  # ponytail: ~25k tokens; raise it if a real course file is cut

# The zip members that hold the words, per OOXML flavour.
OOXML_MEMBERS = {
    "wordprocessingml": ("word/document.xml",),
    "presentationml": ("ppt/slides/slide",),
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


# Excel's own number formats that show a date or a time.
DATE_FORMATS = {*range(14, 23), *range(27, 37), *range(45, 48), *range(50, 59)}


class _Workbook:
    """An .xlsx read a sheet at a time, its cells as text by address: a
    stdlib zipfile and streamed XML, so even a sheet of 25,000 rows is read
    without a dependency and only when asked for. Transitional or Strict
    Open XML alike: the parts are found through the package's relations, and
    the tags in the namespace the workbook itself is written in. Raises
    zipfile.BadZipFile, KeyError or ET.ParseError for a file that is not one."""

    def __init__(self, path: Path):
        self.zip = zipfile.ZipFile(path)
        main = "xl/workbook.xml"
        if "_rels/.rels" in self.zip.namelist():
            main = {kind: m for _, kind, m in self._related("_rels/.rels", "")}.get("officeDocument", main)
        book = ET.fromstring(self.zip.read(main))
        # Transitional's spreadsheetml/2006/main, or Strict's ooxml/spreadsheetml/main.
        ns = self.ns = book.tag[:book.tag.find("}") + 1]
        folder, name = posixpath.split(main)
        related = self._related(posixpath.join(folder, "_rels", name + ".rels"), folder)
        rels, parts = {i: m for i, _, m in related}, {kind: m for _, kind, m in related}
        self.parts = {kind: parts.get(kind, posixpath.join(folder, kind + ".xml")) for kind in ("sharedStrings", "styles")}
        pr = book.find(ns + "workbookPr")
        self.epoch = datetime(1904, 1, 1) if pr is not None and pr.get("date1904") in ("1", "true") \
            else datetime(1899, 12, 30)  # Excel counts from 1900 as if it were a leap year
        # (name, zip member, hidden) per sheet, in the workbook's order.
        self.sheets = []
        for sheet in book.iter(ns + "sheet"):
            rid = next((v for k, v in sheet.attrib.items() if k.endswith("}id")), "")  # r:id, in either namespace
            self.sheets.append((sheet.get("name") or "?", rels.get(rid, ""), sheet.get("state") in ("hidden", "veryHidden")))
        self._strings = self._dates = None

    def _related(self, rels: str, folder: str) -> list[tuple[str, str, str]]:
        """What a relations part points at, as (id, the last word of its type,
        such as officeDocument, sharedStrings or styles, zip member); a
        relative target is from folder."""
        found = []
        for r in ET.fromstring(self.zip.read(rels)):
            target = r.get("Target") or ""
            member = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
            found.append((r.get("Id"), (r.get("Type") or "").rsplit("/", 1)[-1], member))
        return found

    def strings(self) -> list[str]:
        """The shared strings a text cell points at by number."""
        if self._strings is None:
            self._strings, ns = [], self.ns
            if self.parts["sharedStrings"] in self.zip.namelist():
                for _, el in ET.iterparse(self.zip.open(self.parts["sharedStrings"])):
                    if el.tag == ns + "si":  # its runs' text, less phonetic guides
                        guides = {t for r in el.iter(ns + "rPh") for t in r.iter(ns + "t")}
                        self._strings.append("".join(t.text or "" for t in el.iter(ns + "t") if t not in guides))
                        el.clear()
        return self._strings

    def dates(self) -> set[int]:
        """The cell styles (s=) whose number format shows a date or time."""
        if self._dates is None:
            self._dates = set()
            if self.parts["styles"] in self.zip.namelist():
                styles = ET.fromstring(self.zip.read(self.parts["styles"]))
                custom = set()
                for fmt in styles.iter(self.ns + "numFmt"):
                    # A date's code has d, m, y, h or s outside quotes, [colours] and \escapes.
                    code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", fmt.get("formatCode") or "")
                    if re.search(r"[dmyhs]", code, re.I) and code.lower() != "general":
                        custom.add(int(fmt.get("numFmtId", -1)))
                xfs = styles.find(self.ns + "cellXfs")
                for i, xf in enumerate(xfs if xfs is not None else []):
                    if int(xf.get("numFmtId", 0)) in DATE_FORMATS | custom:
                        self._dates.add(i)
        return self._dates

    def size(self, n: int) -> str:
        """Sheet n's used range as Excel states it (A1:O25427), read off its start."""
        try:
            for _, el in ET.iterparse(self.zip.open(self.sheets[n - 1][1]), events=("start",)):
                if el.tag == self.ns + "dimension":
                    return el.get("ref") or ""
                if el.tag == self.ns + "sheetData":
                    return ""
        except (KeyError, ET.ParseError):
            pass
        return ""

    def rows(self, n: int, formulas: bool = True, addresses: bool = False):
        """Sheet n's rows that hold anything, as (row number, line): the row
        number, then the cells by column, text quoted, numbers as stored,
        dates as dates, and a formula's result followed by = and the formula,
        unless formulas is False. A formula Excel shares down a column names
        the cell it is written out in. With addresses, each cell has its own
        (A12) and the line no row number, so a search snippet cut from the
        middle of a row still says which row it is."""
        strings, dates, shared = self.strings(), self.dates(), {}
        try:
            source = self.zip.open(self.sheets[n - 1][1])
        except KeyError:
            return
        number = 0
        for _, el in ET.iterparse(source):
            if el.tag != self.ns + "row":
                continue
            number = int(el.get("r") or number + 1)
            cells, column = [], 0
            for c in el.iter(self.ns + "c"):
                ref = c.get("r") or ""
                letters = re.match(r"[A-Z]*", ref).group() or _column(column + 1)
                column = _column_number(letters)
                text = self._value(c, strings, dates)
                if formulas and (f := c.find(self.ns + "f")) is not None:
                    if f.get("t") == "shared" and f.get("ref"):
                        shared[f.get("si")] = ref
                    formula = _one_line(f.text) or (f"as in {shared[f.get('si')]}" if f.get("si") in shared else "")
                    if formula:
                        text = f"{text} = {formula}" if text else f"= {formula}"
                if text:
                    cells.append(f"{letters}{number if addresses else ''} {text}")
            el.clear()
            if cells:
                yield number, ("" if addresses else f"{number}: ") + " · ".join(cells)

    def _value(self, c, strings, dates) -> str:
        kind, v = c.get("t"), c.find(self.ns + "v")
        if kind == "inlineStr":
            return _quoted("".join(t.text or "" for t in c.iter(self.ns + "t")))
        if v is None or v.text is None:
            return ""
        if kind == "s":
            i = int(v.text)
            return _quoted(strings[i] if 0 <= i < len(strings) else "")
        if kind in ("str", "e"):  # a formula's text result, or an error such as #DIV/0!
            return _quoted(v.text) if kind == "str" else v.text
        if kind == "b":
            return "TRUE" if v.text == "1" else "FALSE"
        if kind == "d":  # a date written out, as Strict writes every date
            try:
                when = datetime.fromisoformat(v.text.removesuffix("Z"))
            except ValueError:
                return v.text
            return when.strftime("%Y-%m-%d") if when.time() == datetime.min.time() else when.strftime("%Y-%m-%d %H:%M")
        try:
            x = float(v.text)
        except ValueError:
            return v.text
        if int(c.get("s") or 0) in dates and 0 <= x < 2_958_466:  # up to 9999-12-31
            when = self.epoch + timedelta(days=x)
            return (when.strftime("%H:%M:%S") if x < 1 else when.strftime("%Y-%m-%d")
                    if x == int(x) else when.strftime("%Y-%m-%d %H:%M"))
        return str(int(x)) if x.is_integer() and abs(x) < 1e15 else repr(x)


def _one_line(text: str | None) -> str:
    """A cell's text or formula on one line: a break typed in the cell (Alt+Enter)
    would split its row, or, quoted, stick a \\n to the next word, and a search
    or find for that word would miss it."""
    return re.sub(r"\s*[\r\n]+\s*|\t", " ", text or "")


def _quoted(text: str) -> str:
    """A text cell as a row line shows it: in quotes, on one line."""
    return json.dumps(_one_line(text), ensure_ascii=False)


def _column(n: int) -> str:
    """Column n's letters: 1 is A, 27 is AA."""
    letters = ""
    while n:
        n, r = divmod(n - 1, 26)
        letters = chr(65 + r) + letters
    return letters


def _column_number(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def _sheet_text(path: Path, name: str, pages: tuple = (1, 0), first_row: int = 1, find: str = "") -> str | None:
    """An .xlsx's text for the caller, a sheet for a page: the list of its
    sheets, then sheets first to last (0: its last) a row a line, from
    first_row of the first, whole rows up to TEXT_LIMIT, then a note saying
    where to go on from. With find, only the rows holding every word of it
    (_sheet_find). None when it cannot be read as a workbook."""
    try:
        book = _Workbook(path)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError):
        return None
    count = len(book.sheets)
    if not count:
        return None
    first, last = pages[0], min(pages[1] or count, count)
    if not 1 <= first <= last:
        raise RuntimeError(f"{name} has sheets 1 to {count}: ask for first_page to last_page within them.")
    listing = [f"  {n}. {json.dumps(sheet, ensure_ascii=False)} {book.size(n) or 'empty'}"
               + (" (hidden)" if hidden else "") for n, (sheet, _, hidden) in enumerate(book.sheets, 1)]
    try:
        out, cut = (_sheet_find(book, first, last, first_row, find) if find.split()
                    else _sheet_rows(book, first, last, first_row))
    except (ET.ParseError, ValueError, KeyError) as e:
        out, cut = [], f"[The workbook could not be read: {e}.]"
    if not find.split() and not cut and last < count:
        cut = f"[Sheets {last + 1} to {count} follow: ask first_page={last + 1}.]"
    head = (f"[{name}: {count} sheet{'s' * (count != 1)}, read as pages (first_page, last_page). Each line is a row: "
            "its number, then each cell with anything in it by column letter. Text is in quotes, numbers as stored, "
            "dates as YYYY-MM-DD, and a formula's cell gives its result, then = and the formula ("
            "\"as in D2\" for one copied from D2 with its references moved along).]")
    if find.split():
        head += (f" Only the rows with every word of {json.dumps(find, ensure_ascii=False)} (at a word's start, "
                 "ignoring case), after "
                 f"each such sheet's first {LABEL_ROWS} rows, which usually hold its column labels.")
    return "\n".join([head, "Sheets:", *listing, "", *out, *([""] + [cut] if cut else [])])


def _sheet_rows(book: _Workbook, first: int, last: int, first_row: int) -> tuple[list, str]:
    """_sheet_text's lines for sheets first to last, and the note after them.
    The first sheet is shown from first_row, cut at the limit; a later one
    only whole, so one long data sheet does not crowd out the small sheets
    after it. Those too long are named instead."""
    out, size, cut, left_out = [], 0, "", []
    for n in range(first, last + 1):
        sheet, _, _ = book.sheets[n - 1]
        head = f"[Sheet {n}: {json.dumps(sheet, ensure_ascii=False)}]"
        lines, length = [], 0
        for row, line in book.rows(n):
            if n == first and row < first_row:
                continue
            # (The first sheet's first row is shown even when it alone is too long.)
            if size + length + len(line) > TEXT_LIMIT and (lines or n != first):
                if n == first:
                    cut = f"[Sheet {n} goes on: ask first_page={n}, first_row={row} for the rest.]"
                else:
                    left_out.append(n)
                    lines = None
                break
            lines.append(line)
            length += len(line) + 1
        if lines is None:
            continue
        out += [head, *lines] if lines else [head, "(No cells with anything in them"
                                             + (f" from row {first_row}" if n == first and first_row > 1 else "")
                                             + "; a sheet may hold only charts or pictures.)"]
        size += length
        if cut:
            break
    if left_out:
        cut = (f"[Not shown, too long for what was left of this answer: sheet{'s' * (len(left_out) > 1)} "
               f"{', '.join(map(str, left_out))}. Ask first_page={left_out[0]} for "
               f"{'it' if len(left_out) == 1 else 'the first'}.]" + ("\n" + cut if cut else ""))
    return out, cut


LABEL_ROWS = 5  # a sheet's first rows, shown with the rows find matches in it, for its column labels


def _sheet_find(book: _Workbook, first: int, last: int, first_row: int, find: str) -> tuple[list, str]:
    """_sheet_text's lines when it is given find: per sheet first to last,
    how many of its rows hold every word of find, its first LABEL_ROWS rows,
    then those rows, up to TEXT_LIMIT, from first_row of the first. A word is
    looked for at the start of a word in the cells' text, numbers and
    formulas, not in the row numbers and column letters around them. Every row of a sheet is read,
    so the count is whole even when the rows shown stop."""
    # Each word at the start of a word, as search matches them: "oxide" is
    # not found in "dioxide".
    words = [re.compile(r"(?<!\w)" + re.escape(w)) for w in find.lower().split()]
    out, size, cut, missed = [], 0, "", []
    for n in range(first, last + 1):
        sheet, _, _ = book.sheets[n - 1]
        labels, hits, used, matched, total = [], [], 0, 0, 0
        for row, line in book.rows(n):
            total += 1
            cells = re.sub(r"(?:^| · )[A-Z]+ ", " ", line.split(": ", 1)[1]).lower()  # less row and column labels
            hit = (n != first or row >= first_row) and all(w.search(cells) for w in words)
            matched += hit
            if total <= LABEL_ROWS:
                labels.append(line)
                used += len(line) + 1
            elif hit and not cut:
                if size + used + len(line) > TEXT_LIMIT and (hits or out):
                    cut = (f"[More rows match in sheet {n}: ask first_page={n}, first_row={row}, "
                           f"find={json.dumps(find, ensure_ascii=False)} for the rest.]")
                else:
                    hits.append(line)
                    used += len(line) + 1
        if not matched:
            missed.append(n)
            continue
        out += [f"[Sheet {n}: {json.dumps(sheet, ensure_ascii=False)}: {matched:,} of its {total:,} rows match]",
                *labels, *hits]
        size += used
        if cut:
            break
    if missed:
        note = f"(No row of sheet{'s' * (len(missed) > 1)} {', '.join(map(str, missed))} has every word.)"
        out = out + [note] if out else [note]
    return out, cut


def _sheet_pages(path: Path) -> list[str]:
    """An .xlsx's search text, a page per sheet: its name and cell values, no
    formulas, up to TEXT_LIMIT a sheet. A course's data sheet runs to 25,000
    rows (12 MB as text), and the search text goes into the offline
    index.html too; past the limit such a sheet is read by row with get_file.
    Empty when the file cannot be read."""
    try:
        book, pages = _Workbook(path), []
        for n, (sheet, _, _) in enumerate(book.sheets, 1):
            lines, size = [sheet], len(sheet)
            for _, line in book.rows(n, formulas=False, addresses=True):
                if size + len(line) > TEXT_LIMIT:
                    break
                lines.append(line)
                size += len(line) + 1
            pages.append("\n".join(lines))
        return pages
    except (zipfile.BadZipFile, KeyError, ET.ParseError, ValueError, OSError):
        return []


def _pdf_text(path: Path) -> str | None:
    """Extract a PDF's text with poppler's pdftotext, which is already installed
    on most Linux systems and reads layout better than the pure-Python parsers.
    Returns None when it is missing or the PDF holds no text layer, so the
    caller can fall back to handing over the file itself."""
    try:
        done = subprocess.run(
            # -enc: Windows builds of poppler write Latin-1 unless told
            ["pdftotext", "-enc", "UTF-8", "-layout", "-q", str(path), "-"],
            capture_output=True,
            encoding="utf-8", errors="replace",  # text=True would guess cp1252 on Windows
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
