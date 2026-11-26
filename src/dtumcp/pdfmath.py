"""PDF pages: fonts, TeX glyph maps, formulas written out and figures."""

import json
import os
import re
import subprocess
import tempfile
import unicodedata
import zlib
from xml.etree import ElementTree as ET
from collections import Counter
from html import unescape
from pathlib import Path

from .text import PIECES, _pdf_text


def _numbered_pages(text: str, marks: dict | None = None, rebuilt: dict | None = None) -> list[str]:
    """pdftotext's output, a page each, headed [Page n] and with what
    _page_marks found on it, as [Page n, has images, drawing, math, unreadable
    symbols]. pdftotext ends every page with a form feed, which says nothing to
    a reader. Only the last one goes: the ones before it may close blank pages,
    which still count."""
    pages = text.removesuffix("\f").split("\f")
    for n, page in (rebuilt or {}).items():  # math pages with their formulas written out
        if 0 < int(n) <= len(pages):
            pages[int(n) - 1] = page
    on = {}
    for kind, label in (("images", "images"), ("drawing", "drawing"), ("math", "math"),
                        ("symbols", "unreadable symbols")):
        for n in (marks or {}).get(kind, ()):
            on.setdefault(n, []).append(label)
    for n, hidden in (marks or {}).get("hidden", ()):  # _hidden_text: left out, said so
        if 0 < n <= len(pages):
            # pdftotext -layout pads words with runs of spaces and may wrap the
            # sentence, so its words match with any white space between them;
            # a line that held only them goes whole.
            words = r"\s+".join(map(re.escape, hidden.split()))
            page, whole = re.subn(rf"(?m)^[ \t]*{words}[ \t]*(?:\n|\Z)", "", pages[n - 1])
            pages[n - 1], inline = re.subn(words, "", page)
            label = "hidden text left out" if whole or inline else "hidden text it could not take out"
            if label not in on.get(n, []):
                on.setdefault(n, []).append(label)
    return [
        f"[Page {n}{', has ' + ', '.join(on[n]) if n in on else ''}]\n{page.strip(chr(10))}"
        for n, page in enumerate(pages, 1)
    ]


def _poppler(*args, timeout: int = 120) -> str:
    """A poppler tool's output, or empty when it is missing, fails or hangs."""
    try:
        done = subprocess.run(
            [str(a) for a in args], capture_output=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return done.stdout if done.returncode == 0 else ""


def _page_marks(path: Path) -> dict:
    """What a PDF's pages hold that its text loses, as page numbers under
    images, drawing, math and symbols (text in a font with no usable character
    map, see _unmapped_fonts). Worked out when a course downloads and kept in
    its record, since the drawing pass takes seconds on a long PDF. A poppler
    tool that is missing leaves its kind out. The math pages' formulas are
    rebuilt on the way (_write_math_text), from the same poppler output.
    None found when the analysis fails: one odd PDF must not fail a course."""
    try:
        return _marks(path)
    except Exception:  # best effort: what is lost is page headings, not the file
        return {}


def _marks(path: Path) -> dict:
    xml = _poppler("pdftohtml", "-xml", "-i", "-stdout", "-q", path)
    math, pages = _math_pages(xml)
    with tempfile.TemporaryDirectory() as tmp:
        svg = Path(tmp) / "pages.svg"
        _poppler("pdftocairo", "-svg", path, svg)
        drawing, rules, width, delimiters = _drawing_pages(svg) if svg.exists() else (set(), [], 0.0, [])
    images = _picture_pages(_poppler("pdfimages", "-list", path, timeout=60), pages)
    words = _word_boxes(_poppler("pdftotext", "-bbox", path, "-")) if math else []
    try:
        _write_math_text(path, xml, math, rules, width, delimiters, words)
    except OSError:  # no rebuilt math beside the PDF; its marks still count
        pass
    try:
        symbols = _font_pages(xml, _unmapped_fonts(path.read_bytes()))
    except OSError:
        symbols = set()
    marks = {kind: sorted(found) for kind, found in
             (("images", images), ("drawing", drawing), ("math", math), ("symbols", symbols)) if found}
    if hidden := _hidden_text(xml):
        marks["hidden"] = hidden
    return marks


def _hidden_text(xml: str) -> list:
    """Text set at size 0, which no reader sees, as [page, text] pairs from
    pdftohtml's XML. Left out of what the tools give, as a course's trap
    for AI help or a planted instruction would otherwise read as part of
    the page."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    return [[int(page.get("number")), text]
            for page in root.iter("page") for t in page.iter("text")
            # a sentence, not a stray digit: taken out wherever it occurs
            if t.get("height") == "0" and len(text := "".join(t.itertext()).strip()) >= 20]


# TeX's fonts as pdfTeX embeds them draw the right shapes but tell text tools
# the wrong characters. Each font carries a ToUnicode map from its codes to
# Unicode, and pdfTeX's leaves out what Unicode has no plain letter for: the
# big-operator font's sum is code X, the symbol font's calligraphic N is N,
# the \big delimiters are control characters. pdftotext then writes the code
# itself. _tex_maps completes those maps, by font and code.
OMX = {  # TeX's big operators and delimiters, CMEX
    **dict(zip(map(chr, range(32)), "()[]⌊⌋⌈⌉{}⟨⟩|‖/\\()()[]⌊⌋⌈⌉{}⟨⟩/\\")),
    **dict(zip(" !\"#$%&'()*+,-./", "()[]⌊⌋⌈⌉{}⟨⟩/\\/\\")),
    **dict(zip("0123456789:;<=>?@ABCDE", "⎛⎞⎡⎤⎣⎦⎢⎥⎧⎫⎩⎭⎨⎬⎪⏐⎝⎠⎜⎟⟨⟩")),
    **dict(zip("FGHIJKLMNOPQRSTUVWXYZ[\\]^_`a", "⨆⨆∮∮⨀⨀⨁⨁⨂⨂∑∏∫⋃⋂⨄⋀⋁∑∏∫⋃⋂⨄⋀⋁∐∐")),
    **dict(zip("hijklmnopqrst", "[]⌊⌋⌈⌉{}√√√√⎷")),
}
CAPITALS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
TEX_GLYPHS = (  # by font name: what its codes stand for
    (re.compile(r"CMEX\d|LMMathExtension"), OMX),  # Latin Modern's math fonts code alike
    (re.compile(r"CMB?SY\d|LMMathSymbols"), dict(zip(CAPITALS, "𝒜ℬ𝒞𝒟ℰℱ𝒢ℋℐ𝒥𝒦ℒℳ𝒩𝒪𝒫𝒬ℛ𝒮𝒯𝒰𝒱𝒲𝒳𝒴𝒵"))),
    (re.compile(r"MSBM\d"), dict(zip(CAPITALS, "𝔸𝔹ℂ𝔻𝔼𝔽𝔾ℍ𝕀𝕁𝕂𝕃𝕄ℕ𝕆ℙℚℝ𝕊𝕋𝕌𝕍𝕎𝕏𝕐ℤ"))),
)
# Word and PowerPoint's Cambria Math maps tell the same kind of lie about its
# n-ary products and sums: a displayed ∏ is the Sinhala sign ෑ, an inline one
# ς, a displayed ∑ ෍ and an inline one σ. _tex_maps swaps them back.
# shortcut: an upright σ or ς set in Cambria Math reads as ∑ or ∏; Office sets
# letters in math italic (𝜎), so upright ones were not seen in the downloads.
CAMBRIA = re.compile(r"CambriaMath")
OFFICE_NARY = {"ෑ": "∏", "ς": "∏", "෍": "∑", "σ": "∑"}


PDF_OBJECT = re.compile(rb"(?<!\d)(\d+)\s+\d+\s+obj\b")
HEX = re.compile(rb"<([0-9A-Fa-f]*)>")


def _pdf_objects(data: bytes) -> dict:
    """A PDF's objects by number, each as the bytes between obj and endobj,
    those packed in object streams included. The last definition of a number
    is the live one."""
    objects = {}
    for m in PDF_OBJECT.finditer(data):
        end = data.find(b"endobj", m.end())
        start = data.find(b"stream", m.end(), end)
        length = start > 0 and re.search(rb"/Length\s+(\d+)(?!\s+\d+\s+R)", data[m.end():start])
        if length:  # a stream's bytes may hold "endobj"
            end = data.find(b"endobj", start + int(length.group(1)))
        objects[int(m.group(1))] = data[m.end():end]
    for body in list(objects.values()):
        if not re.search(rb"/Type\s*/ObjStm", body) or (raw := _pdf_stream(body)) is None:
            continue
        try:
            first = int(re.search(rb"/First\s+(\d+)", body).group(1))
            head = list(map(int, raw[:first].split()))
        except (AttributeError, ValueError):
            continue
        places = [(head[i], first + head[i + 1]) for i in range(0, len(head) - 1, 2)]
        for (num, at), (_, nxt) in zip(places, places[1:] + [(0, len(raw))]):
            objects.setdefault(num, raw[at:nxt])
    return objects


def _pdf_stream(body: bytes) -> bytes | None:
    """An object's stream, inflated; None for one in another filter."""
    at = body.find(b"stream")
    if at < 0:
        return None
    raw = body[at + 6:body.rfind(b"endstream")].removeprefix(b"\r").removeprefix(b"\n")
    if b"/Filter" not in body[:at]:
        return raw
    if not re.search(rb"/Filter\s*\[?\s*/FlateDecode\s*\]?", body[:at]):
        return None
    try:
        return zlib.decompressobj().decompress(raw)
    except zlib.error:
        return None


def _utf16(hex_digits: bytes) -> str:
    try:
        return bytes.fromhex(hex_digits.decode()).decode("utf-16-be", "replace")
    except ValueError:  # an odd number of digits: a broken map, read as broken text is
        return "\ufffd"


def _cmap_entries(cmap: bytes) -> dict:
    """A ToUnicode map's one-byte codes and what they stand for."""
    entries = {}
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", cmap, re.S):
        for code, uni in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            entries[int(code, 16)] = _utf16(uni)
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", cmap, re.S):
        for lo, hi, to in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])", block):
            lo, hi = int(lo, 16), int(hi, 16)
            if to.startswith(b"["):
                entries.update((lo + i, _utf16(u)) for i, u in enumerate(HEX.findall(to)))
            elif len(start := _utf16(to[1:-1])) == 1:
                entries.update((code, chr(ord(start) + code - lo)) for code in range(lo, hi + 1))
    return entries


def _cmap(entries: dict, width: int = 1) -> bytes:
    """A ToUnicode map of codes width bytes long (2 for a CID font's)."""
    lines = [b"/CIDInit /ProcSet findresource begin", b"12 dict begin", b"begincmap",
             b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
             b"/CMapName /Adobe-Identity-UCS def", b"/CMapType 2 def",
             b"1 begincodespacerange", b"<%s> <%s>" % (b"00" * width, b"FF" * width), b"endcodespacerange"]
    items = sorted(entries.items())
    for i in range(0, len(items), 100):  # a bfchar block holds at most 100
        lines.append(b"%d beginbfchar" % len(items[i:i + 100]))
        lines += [b"<%0*X> <%s>" % (2 * width, code, uni.encode("utf-16-be").hex().upper().encode())
                  for code, uni in items[i:i + 100]]
        lines.append(b"endbfchar")
    lines += [b"endcmap", b"CMapName currentdict /CMap defineresource pop", b"end", b"end"]
    return b"\n".join(lines) + b"\n"


def _tex_maps(data: bytes) -> bytes:
    """A PDF with the ToUnicode maps of TeX's CMEX, CMSY and MSBM fonts
    completed from TEX_GLYPHS and PIECES, and Cambria Math's ∏ and ∑ put
    right (OFFICE_NARY), added as an incremental update: the
    new maps go after the file's last byte, under the same object numbers, so
    nothing drawn changes and the original bytes stay as they were. The PDF
    unchanged when it has no such map to complete, is encrypted or signed (an
    update would show as tampering), or cannot be read this way. A completed
    PDF comes back unchanged, so a second pass adds nothing."""
    try:
        at = int(re.findall(rb"startxref\s+(\d+)", data[-2048:])[-1])
    except IndexError:
        return data
    end = data.find(b"stream", at) if data[at:at + 4] != b"xref" else data.find(b">>", data.find(b"trailer", at))
    trailer = data[at:end + 2]
    if b"/Encrypt" in trailer or b"/ByteRange" in data or not (size := re.search(rb"/Size\s+(\d+)", trailer)):
        return data
    objects, kinds = _pdf_objects(data), {}
    for body in objects.values():
        name = re.search(rb"/Type\s*/Font.*?/BaseFont\s*/([^\s/<>\[\]()]+)|/BaseFont\s*/([^\s/<>\[\]()]+).*?/Type\s*/Font", body, re.S)
        ref = re.search(rb"/ToUnicode\s+(\d+)\s+\d+\s+R", body)
        if name and ref:
            font = (name.group(1) or name.group(2)).decode("latin-1")
            for pattern, table in TEX_GLYPHS + ((CAMBRIA, OFFICE_NARY),):
                if pattern.search(font):
                    kinds.setdefault(int(ref.group(1)), set()).add(id(table))
                    break
    tables = {id(table): table for _, table in TEX_GLYPHS}
    maps = {}
    for num, (kind, *more) in ((n, tuple(k)) for n, k in kinds.items()):
        if more or num not in objects or (cmap := _pdf_stream(objects[num])) is None:
            continue  # shared by fonts of different kinds, or unreadable
        entries = _cmap_entries(cmap)
        if kind == id(OFFICE_NARY):  # by what the map says, not by code
            done = {code: OFFICE_NARY.get(uni, uni) for code, uni in entries.items()}
        else:
            done = dict(entries)
            for code, symbol in ((ord(c), s) for c, s in tables[kind].items()):
                if entries.get(code, chr(code)) == chr(code) or entries[code] in PIECES:
                    done[code] = symbol  # missing, the code's own letter, or a private-use piece
            done.update([(code, PIECES[uni]) for code, uni in done.items() if uni in PIECES])
        if done != entries:
            space = re.search(rb"begincodespacerange\s*<([0-9A-Fa-f]+)>", cmap)
            maps[num] = _cmap(done, len(space.group(1)) // 2 if space else 1)
    if not maps:
        return data
    update, places = bytearray(b"\n"), {}
    for num, cmap in sorted(maps.items()):
        packed = zlib.compress(cmap)
        places[num] = len(data) + len(update)
        update += b"%d 0 obj\n<< /Length %d /Filter /FlateDecode >>\nstream\n%s\nendstream\nendobj\n" % (
            num, len(packed), packed)
    xref = len(data) + len(update)
    update += b"xref\n0 1\n0000000000 65535 f \n"
    update += b"".join(b"%d 1\n%010d 00000 n \n" % (num, place) for num, place in sorted(places.items()))
    keep = b" ".join(m.group(0) for key in (rb"/Root\s+\d+\s+\d+\s+R", rb"/Info\s+\d+\s+\d+\s+R", rb"/ID\s*\[[^\]]*\]")
                     if (m := re.search(key, trailer)))
    update += b"trailer\n<< /Size %s %s /Prev %d >>\nstartxref\n%d\n%%%%EOF\n" % (size.group(1), keep, at, xref)
    return data + bytes(update)


def _unmapped_fonts(data: bytes) -> set[str]:
    """The fonts of a PDF whose ToUnicode map names under half of the
    characters they have glyphs for, as PowerPoint for the Mac embeds Cambria
    Math: text tools then write their codes, so L^Σ_{CPA−0} reads L^8_{6!7$0}.
    Its glyphs carry no names to recover them by. Simple fonts only, by name,
    and only those with no /Encoding: with one, text tools fall back on it and
    read a text font right."""
    objects, found = _pdf_objects(data), set()
    for body in objects.values():
        name = re.search(rb"/BaseFont\s*/([^\s/<>\[\]()]+)", body)
        ref = re.search(rb"/ToUnicode\s+(\d+)\s+\d+\s+R", body)
        first = re.search(rb"/FirstChar\s+(\d+)", body)
        widths = re.search(rb"/Widths\s*(\[[^\]]*\]|(\d+)\s+\d+\s+R)", body)
        if not (name and ref and first and widths and b"/Font" in body) or b"/Encoding" in body:
            continue
        listed = objects.get(int(widths.group(2)), b"") if widths.group(2) else widths.group(1)
        codes = {int(first.group(1)) + i for i, w in enumerate(re.findall(rb"-?\d+(?:\.\d+)?", listed))
                 if float(w) > 0} - {32}
        cmap = _pdf_stream(objects.get(int(ref.group(1)), b""))
        if len(codes) >= 4 and cmap is not None and 2 * len(codes & set(_cmap_entries(cmap))) < len(codes):
            found.add(name.group(1).decode("latin-1"))
    return found


def _font_pages(xml: str, families: set) -> set[int]:
    """The pages of pdftohtml's XML with text set in any of these fonts."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return set()
    ids = {f.get("id") for f in root.iter("fontspec") if f.get("family") in families}
    return {int(page.get("number")) for page in root.findall("page")
            if any(t.get("font") in ids and "".join(t.itertext()).strip() for t in page.iter("text"))}


def _save(dest: Path, content: bytes) -> None:
    """Write a downloaded file. A PDF set in TeX's fonts gets its symbols'
    maps completed (_tex_maps), kept only when poppler reads it with as many
    pages as before. Done to every PDF as it is saved, so two downloads of one
    file stay byte for byte alike for _dedupe_linked."""
    dest.write_bytes(content)
    if dest.suffix.lower() != ".pdf":
        return
    try:
        patched = _tex_maps(content)
    except Exception:  # best effort: the file as sent is kept
        return
    if patched is content:
        return
    pages = re.compile(r"^Pages:\s+(\d+)", re.M)
    # A name of its own, so two writers of one file never share a .part.
    fd, name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".part", dir=dest.parent)
    part = Path(name)
    with os.fdopen(fd, "wb") as f:
        f.write(patched)
    before = pages.search(_poppler("pdfinfo", dest))
    after = pages.search(_poppler("pdfinfo", part))
    try:
        if before and after and before.group(1) == after.group(1):
            part.replace(dest)  # refused on Windows while the PDF is open: the file as sent stays
    except OSError:
        pass
    finally:
        part.unlink(missing_ok=True)


# TeX's math fonts (Computer Modern, AMS, Fraktur, script) and any font named
# for math, which covers Cambria Math, STIX, New Computer Modern (Typst's) and
# Latin Modern.
TEX_EXTENSION = re.compile(r"CMEX|LMMathExtension")  # TeX's big operators and delimiters
MATH_FONT = re.compile(r"CMMI|CMSY|CMEX|CMBSY|MSAM|MSBM|EUFM|EUSM|RSFS|Math", re.I)


def _math_pages(xml: str) -> tuple[set[int], int]:
    """The pages of pdftohtml's XML that hold math, and how many pages it has.
    A page holds math when 20 or more of its characters are set in math fonts,
    or when it has a displayed formula, which _displayed tells. A lone $n$ in a
    sentence or a list bullet, which LaTeX takes from a math font, is neither."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return set(), 0
    math = {f.get("id") for f in root.iter("fontspec") if MATH_FONT.search(f.get("family") or "")}
    pages = root.findall("page")
    return {
        int(page.get("number")) for page in pages
        if sum(len("".join("".join(t.itertext()).split()))
               for t in page.iter("text") if t.get("font") in math) >= 20
        or _displayed(page, math)
    }, len(pages)


def _displayed(page, math: set) -> bool:
    """Whether a pdftohtml page has a displayed formula: a line with text in a
    math font that starts a tenth of the page's width or more right of the
    page's left margin, as a centred formula does and body text, a list item
    or its next line does not. The top and bottom 8% are left out: a slide's
    running head can name a section in a math font."""
    try:
        width, height = int(page.get("width")), int(page.get("height"))
        runs = sorted((int(t.get("top")), int(t.get("height")), int(t.get("left")),
                       t.get("font") in math and bool("".join(t.itertext()).strip()))
                      for t in page.iter("text"))
    except (TypeError, ValueError):
        return False
    lines = []  # [top, height, left, has math]
    for top, h, left, is_math in runs:
        # A sub- or superscript sits within its line's height.
        if not lines or top > lines[-1][0] + max(lines[-1][1], h) * 0.6:
            lines.append([top, h, left, is_math])
        else:
            lines[-1][2] = min(lines[-1][2], left)
            lines[-1][3] = lines[-1][3] or is_math
    lines = [line for line in lines if 0.08 * height <= line[0] <= 0.92 * height]
    if not lines:
        return False
    margin = min(left for _, _, left, _ in lines)
    return any(is_math and left >= margin + 0.1 * width for _, _, left, is_math in lines)


BIG_OPERATORS = set("∑∏∫⋃⋂⨁⨂⨀⨄⋀⋁∐∮⨆")
PRIMES = ("′", "′′", "″", "‴")  # set beside their letter, never as scripts
# Pieces of tall delimiters, which pdftohtml gives boxes of the wrong height,
# as it does every glyph of TeX's big-symbol font.
DELIMITER_PIECES = set("⎛⎜⎝⎞⎟⎠⎡⎢⎣⎤⎥⎦⎧⎨⎩⎪⎫⎬⎭⎮⏐")
ACCENTS = (("¯", "̄"), ("˜", "̃"), ("ˆ", "̂"), ("˙", "̇"))
MATH_TEXT = ".math.json"  # beside a PDF: its math pages' rebuilt text
MATH_NOTATION = (" On pages with math, formulas are written out from their layout: x_{i} is a "
                 "subscript, x^{2} a superscript, (a)/(b) a fraction, ∑_{i=1}^{n} a sum with its limits.")


def _script(text: str, kind: str) -> str:
    text = text.strip()
    return f"{kind}{text}" if len(text) == 1 else f"{kind}{{{text}}}"


def _nest(runs: list) -> str:
    """Runs of one formula part, in left order, as text: those smaller than
    the part's largest are its scripts, _{..} below its middle and ^{..}
    above, nested the same way inside, so e^{−1/(2ϵ^2)}. A prime stays put."""
    if not runs:
        return ""
    top = max(r["size"] for r in runs)
    base = [r for r in runs if r["size"] >= 0.85 * top] or runs
    middle = sum(r["mid"] for r in base) / len(base)
    out, group, kind = "", [], None
    for r in runs + [None]:
        small = r is not None and r["size"] < 0.85 * top and r["text"].strip() not in PRIMES
        here = ("_" if r["mid"] > middle else "^") if small else None
        if group and (here != kind or not small):
            out += _script(_nest(group), kind)
            group = []
        if r is None:
            break
        if small:
            group.append(r)
            kind = here
        else:
            out += r["text"]
    return re.sub(r"\s+", " ", out).strip()


def _rebuild_page(page, fonts: dict, rules: list, scale: float, delimiters: list = (), words: list = ()) -> str:
    """A page of pdftohtml's XML as text with its formulas written out, where
    pdftotext puts each script, limit and fraction part on a line of its own:
    a subscript as x_{i}, a superscript as x^{2} (_nest), a fraction as
    (a)/(b) when a rule (from the SVG, in points; scale turns them into
    pdftohtml's pixels) has text right above and right below it, and a big
    operator's limits as ∑_{lower}^{upper}. Brackets Word and PowerPoint
    draw as outlines (delimiters, from the SVG in points as the rules are)
    go back into the formula they are beside. pdftotext -bbox's words, in
    pixels as (left, top, right, bottom, text), place what falls inside one
    of pdftohtml's runs. Lines keep their place across the page, as
    pdftotext -layout's do."""
    runs = []
    for t in page.iter("text"):
        text = "".join(t.itertext())
        try:
            top, left, width, height = (int(t.get(k)) for k in ("top", "left", "width", "height"))
        except (TypeError, ValueError):
            continue
        if not text.strip() or height == 0:  # size 0: no reader sees it (_hidden_text)
            continue
        size, family = fonts.get(t.get("font"), (0, ""))
        if re.search(r"CMSY|LMMathSymbols", family):  # TeX's ↦ is CMSY's \mapstochar, read as 7, and →
            text = text.replace("7→", "↦")
        big = text.strip() in BIG_OPERATORS
        runs.append(dict(text=text, top=top, left=left, right=left + width, w=width, h=height,
                         # CMEX's tall brackets and operators hang well below their short box
                         mid=top + (0.75 * size if TEX_EXTENSION.search(family) and (big or text.strip() in "()[]{}⟨⟩⌊⌋⌈⌉")
                                    else height / 2), size=size, big=big,
                         math=bool(MATH_FONT.search(family)),
                         piece=not big and (set(text.strip()) <= DELIMITER_PIECES or bool(TEX_EXTENSION.search(family)))))
    # A / set over a sign strikes it out, as TeX writes ∉: ∈ with / on it.
    for r in [r for r in runs if r["text"].strip() == "/"]:
        sign = next((b for b in runs if b is not r and b["text"].strip() in "∈∋=≡<>≤≥⊂⊃⊆⊇∼≃≅≈|∃" and b["text"].strip()
                     and b["left"] - 3 <= r["left"] and r["right"] <= b["right"] + 3
                     and b["top"] < r["top"] + r["h"] and r["top"] < b["top"] + b["h"]), None)
        if sign:
            sign["text"] = sign["text"].replace(sign["text"].strip(),
                                                unicodedata.normalize("NFC", sign["text"].strip() + "̸"))
            runs.remove(r)
    if not runs:
        return ""

    def boxes(r):  # the run's words, where pdftotext -bbox's spell out its text
        found = sorted((w for w in words if r["top"] <= (w[1] + w[3]) / 2 <= r["top"] + r["h"]
                        and r["left"] - 3 <= w[0] and w[2] <= r["right"] + 3), key=lambda w: w[0])
        return found if "".join(w[4] for w in found) == "".join(r["text"].split()) else None

    sizes = Counter()
    for r in runs:
        sizes[r["size"]] += len(r["text"])
    body = sizes.most_common(1)[0][0]
    # A big operator pdftohtml ran into the text before it (= 1 − ∏) stands
    # alone, about as wide as the type is tall, so its limits find it; one in
    # a script stays in it.
    for r in [r for r in runs if len(r["text"].strip()) > 1 and r["text"].rstrip()[-1] in BIG_OPERATORS
              and r["size"] >= 0.85 * body]:
        text, wide = r["text"].rstrip(), min(r["w"], r["size"])
        runs.remove(r)
        runs += [dict(r, text=text[:-1].rstrip(), right=r["right"] - wide, w=r["w"] - wide),
                 dict(r, text=text[-1], left=r["right"] - wide, w=wide, big=True, piece=False,
                      spaced=text[-2].isspace())]
    touch = max(3, 0.15 * body)  # the most a script sits right of its base, or into an italic one
    # TeX's √ hangs below its box, which sits up to half a line high: it
    # goes with the text it covers, right after it.
    for r in (r for r in runs if r["text"].strip() == "√"):
        under = [b for b in runs if b is not r and -touch <= b["left"] - r["right"] <= touch
                 and r["top"] <= b["top"] < r["top"] + r["h"]]
        if under:
            shift = min(under, key=lambda b: b["left"])["mid"] - r["mid"]
            r["top"], r["mid"] = r["top"] + shift, r["mid"] + shift
    for r in runs:
        # Small beside the page's text, or beside the larger text it follows,
        # overlapping it up or down: a slide's smaller
        # or larger formulas' scripts, an operator's limits set beside it.
        r["small"] = not r["big"] and (r["size"] < 0.85 * body or any(
            r["size"] < 0.85 * b["size"] and -touch <= r["left"] - b["right"] <= touch
            and b["top"] < r["top"] + r["h"] and r["top"] < b["top"] + b["h"] for b in runs))
    # Body text's line height, the most common: a slide's title, twice as
    # tall, or a tall symbol would join lines.
    line_h = Counter(r["h"] for r in runs if r["size"] == body and not r["big"] and not r["piece"]).most_common(1)
    line_h = line_h[0][0] if line_h else 16

    # Brackets drawn as outlines (_delimiters), each beside a formula's text:
    # one inside a run, which pdftohtml spans across the gap it leaves, goes
    # in between the run's words it falls between (boxes), else at the space
    # nearest where it falls; the rest join the lines as pieces do.
    for char, x0, y0, x1, y1 in ((c, *(v * scale for v in box)) for c, *box in delimiters):
        if not y1 - y0 <= 3 * line_h:
            continue
        centre = (x0 + x1) / 2
        beside = [r for r in runs if y0 <= r["mid"] <= y1 and not r["piece"]
                  and r["left"] - body <= centre <= r["right"] + body]
        if not any(r["math"] for r in beside):
            continue
        inside = next((r for r in beside if r["left"] < centre < r["right"]), None)
        if inside:
            text = inside["text"]
            if (found := boxes(inside)):
                letters, at = sum(len(w[4]) for w in found if (w[0] + w[2]) / 2 < centre), 0
                while letters and at < len(text):  # just after the last letter before it
                    letters -= not text[at].isspace()
                    at += 1
            else:
                at = round((centre - inside["left"]) / inside["w"] * len(text))
                at = min((i for i, c in enumerate(text) if c == " "), key=lambda i: abs(i - at), default=at)
            if char in "([{":  # an opening bracket hugs what follows
                at += len(text[at:]) - len(text[at:].lstrip())
            inside["text"] = text[:at] + char + text[at:]
        else:
            # Sized as the text it is next to, it goes where that goes, never as a script.
            beside = min(beside, key=lambda r: min(abs(r["left"] - x1), abs(r["right"] - x0)))
            runs.append(dict(text=char, top=y0, left=x0, right=x1, w=x1 - x0, h=y1 - y0, mid=(y0 + y1) / 2,
                             size=beside["size"], big=False, math=True, piece=not beside["small"],
                             small=beside["small"], beside=beside, bracket=True))
    for r in runs:  # smaller text right after a drawn bracket is its script: {0,1}^λ
        r["small"] = r["small"] or not r["big"] and not r.get("bracket") and any(
            b.get("bracket") and r["size"] < 0.85 * b["size"] and -touch <= r["left"] - b["right"] <= touch
            and b["top"] < r["top"] + r["h"] and r["top"] < b["top"] + b["h"] for b in runs)

    # Small text touching full-size text beside it, overlapping it: its script.
    scripts = lambda r: r["small"] and any(
        not (b["small"] or b["big"] or b["piece"]) and -touch <= r["left"] - b["right"] <= touch
        and b["top"] < r["top"] + r["h"] and r["top"] < b["top"] + b["h"] for b in runs)
    # Fractions first, shorter rules first, so an inner one is a single run by
    # the time its outer rule is looked at. Text on one side only is an
    # overline, an underline or a root; text with gaps as wide as a table's
    # columns is a table; a bar over a tenth as thick as its text is tall is a
    # piece of TeX's \underbrace, not a fraction's (Office's are a sixteenth).
    # Three or more long rules spanning the same width are a table's lines;
    # short ones, stacked fractions or bars over letters in a column.
    spans = Counter((round(x0), round(x1)) for x0, x1, *_ in rules)
    for x0, x1, y, thick in sorted(((x0 * scale, x1 * scale, y * scale, (rest or [0])[0] * scale)
                                    for x0, x1, y, *rest in rules
                                    if spans[round(x0), round(x1)] < 3 or (x1 - x0) * scale <= 6 * body),
                                   key=lambda r: r[1] - r[0]):
        # A bar from where a √ ends is the root's, over what it covers.
        if any(r["text"].strip() == "√" and abs(r["right"] - x0) <= touch
               and r["top"] - line_h <= y <= r["top"] + r["h"] for r in runs):
            continue
        inside = [r for r in runs if not r.get("taken") and x0 - 3 <= r["left"] <= x1 and r["right"] <= x1 + 0.6 * body]
        # A short bar through the top of the box of the text under it, above
        # its letters, is a bar over them, as TeX's x̄_1 for not x_1; a
        # fraction's bar sits above its denominator's box, or under a
        # numerator centred on it.
        over = [r for r in inside if r["top"] + 1 <= y <= r["top"] + 0.4 * r["h"] and r["right"] <= x1 + 2]
        if over and x1 - x0 <= 6 * body and sum(r["w"] for r in over) >= 0.5 * (x1 - x0) and not any(
                x0 <= (a["left"] + a["right"]) / 2 <= x1 and y - 0.6 * line_h <= a["top"] + a["h"] <= y + 2
                and a["top"] < y for a in inside if not any(a is r for r in over) and not scripts(a)):
            for r in over:
                mark = "̄" if len(r["text"].strip()) == 1 else "̅"
                r["text"] = "".join(c if c.isspace() else c + mark for c in r["text"])
            continue
        above = [r for r in inside if y - 1.6 * line_h <= r["top"] + r["h"] <= y + 2 and r["top"] < y]
        below = [r for r in inside if y - 2 <= r["top"] <= y + 1.2 * line_h]
        if not above or not below:
            continue
        near_above = max(r["top"] + r["h"] for r in above)
        near_below = min(r["top"] for r in below)
        num = sorted((r for r in above if r["top"] + r["h"] >= near_above - 0.6 * line_h), key=lambda r: r["left"])
        den = sorted((r for r in below if r["top"] <= near_below + 0.6 * line_h), key=lambda r: r["left"])
        # Both parts are centred on the bar: text at one end of a rule, as a
        # sum's total under its last row or a □ ending a proof, is not one.
        centred = lambda part: abs((part[0]["left"] + part[-1]["right"]) / 2 - (x0 + x1) / 2) <= 0.25 * (x1 - x0) + 3
        if {id(r) for r in num} & {id(r) for r in den} or any(
                b["left"] - a["right"] > 1.5 * body for part in (num, den) for a, b in zip(part, part[1:])) or (
                thick > 0.1 * max(r["size"] for r in num + den)) or not (centred(num) and centred(den)):
            continue
        wrap = lambda part: part if len(part) == 1 or re.fullmatch(r"\d+(\.\d+)?", part) else f"({part})"
        for r in num + den:
            r["taken"] = True
        size = max(r["size"] for r in num + den)
        left, right = min(r["left"] for r in num + den), max(r["right"] for r in num + den)
        runs.append(dict(text=f"{wrap(_nest(num))}/{wrap(_nest(den))}", top=min(r["top"] for r in num),
                         left=left, right=right, w=right - left, mid=y, size=size,
                         h=max(r["top"] + r["h"] for r in den) - min(r["top"] for r in num),
                         small=size < 0.85 * body, big=False, piece=False, fraction=True))

    # A displayed operator's limits: small text centred right over or under it,
    # unless it is the script of text it touches, as the n of X_n above a ∑.
    for op in (r for r in runs if r["big"]):
        centre = (op["left"] + op["right"]) / 2
        op["over"], op["under"] = [], []
        for r in runs:
            if not r["small"] or r.get("taken") or abs((r["left"] + r["right"]) / 2 - centre) > max(op["w"], r["w"]) * 0.6:
                continue
            if scripts(r):
                continue
            if op["top"] - 1.6 * line_h <= r["top"] + r["h"] <= op["top"] + 4:
                op["over"].append(r)
                r["taken"] = True
            elif op["top"] + 0.5 * line_h <= r["top"] <= op["top"] + 2.6 * line_h:
                op["under"].append(r)
                r["taken"] = True
        # A limit wider than the operator, X:∥X∥=n under a ∑, goes on in small
        # text beside it, TeX's thin spaces apart, with its own scripts.
        for side in (op["over"], op["under"]):
            grew = True
            while grew:
                grew = False
                for r in runs:
                    gap = max(touch, 0.5 * r["size"])
                    if r["small"] and not r.get("taken") and not r.get("fraction") and any(
                            s["top"] < r["top"] + r["h"] and r["top"] < s["top"] + s["h"]
                            and (-touch <= r["left"] - s["right"] <= gap or -touch <= s["left"] - r["right"] <= gap)
                            for s in side):
                        side.append(r)
                        r["taken"] = grew = True

    # Lines from ordinary full-size text; operators and delimiters, whose boxes
    # are wrong, join the nearest one, a stacked piece only when it is close.
    lines = []
    for r in sorted((r for r in runs if not (r["small"] or r["big"] or r["piece"] or r.get("taken"))), key=lambda r: r["mid"]):
        if lines and abs(r["mid"] - lines[-1]["mid"]) <= 0.75 * line_h:
            lines[-1]["runs"].append(r)
            lines[-1]["mid"] = sum(x["mid"] for x in lines[-1]["runs"]) / len(lines[-1]["runs"])
        else:
            lines.append({"mid": r["mid"], "runs": [r]})
    for r in (r for r in runs if (r["big"] or r["piece"]) and not r.get("taken")):
        near = next((line for line in lines if any(x is r.get("beside") for x in line["runs"])), None) or min(
            lines, key=lambda line: abs(line["mid"] - r["mid"]), default=None)
        whole = r["big"] or not set(r["text"].strip()) <= DELIMITER_PIECES
        gap = abs(near["mid"] - r["mid"]) if near else 1e9
        if gap <= 1.5 * line_h and (whole or gap <= 0.6 * line_h):
            near["runs"].append(r)
        else:
            lines.append({"mid": r["mid"], "runs": [r]})
    lines.sort(key=lambda line: line["mid"])
    # Scripts: small text touching text on the nearest line with text near it
    # across, not one in another column. Other small text (a label, a
    # footnote) makes lines of its own.
    for r in sorted((r for r in runs if r["small"] and not r.get("taken")), key=lambda r: r["left"]):
        column_lines = [line for line in lines if any(b["left"] - 2 * body <= r["left"] <= b["right"] + 2 * body
                                                      for b in line["runs"])]
        near = min(column_lines or lines, key=lambda line: abs(line["mid"] - r["mid"]), default=None)
        # A limit set beside its operator, as Word and PowerPoint set inline
        # ones, goes with the operator's line, though tall ones put it nearer
        # the line above.
        op = next((b for b in runs if b["big"] and -touch <= r["left"] - b["right"] <= touch
                   and abs(b["mid"] - r["mid"]) <= 1.5 * line_h), None)
        near = next((line for line in lines if any(x is op for x in line["runs"])), near)
        if (home := next((line for line in lines if any(x is r.get("beside") for x in line["runs"])), None)):
            home["runs"].append(r)  # a drawn bracket goes with the text it is beside
            continue
        # An opening bracket carries no script: text touching one starts inside
        # it, as inside one in a script, n(n−1)/2 in {0,1}^{n(n−1)/2}, the
        # script goes on. A script after a tall bracket sits higher or lower than one after a letter.
        # A script it follows overlaps it up or down, where a diagram's label
        # below a formula does not. Larger text it follows says which it is,
        # since a line can hold two columns' rows, unless it sits level with
        # it, as TeX's ℕ_0 does, or its box is taller than its type, as a big
        # symbol's is; TeX's symbol boxes are too rough to ask more. Office
        # leaves a wider gap after a ∑ or ∏ set in a script and its limits,
        # before the summand (the i of e^{∑_{i=1}^{q−1} i}).
        limit = lambda b: b.get("kind") and any(
            x["text"].rstrip()[-1:] in BIG_OPERATORS and -touch <= b["left"] - x["right"] <= touch
            for x in near["runs"])
        root = max((b for b in near["runs"] if not (b.get("kind") or b["big"] or b.get("fraction"))
                    and b["right"] <= r["left"] + touch),
                   key=lambda b: b["right"], default=None) if near else None
        # A prime sits as far out after an italic capital (TeX's V′).
        wide = 2 * touch if r["text"].strip() in PRIMES else touch
        base = [b for b in near["runs"] if -touch <= r["left"] - b["right"] <= (2 * touch if limit(b) else wide)
                and (not b.get("kind") or b["top"] < r["top"] + r["h"] and r["top"] < b["top"] + b["h"])
                and (b.get("kind") or not b["text"].rstrip().endswith(tuple("([{⟨⌊⌈")))
                # smaller than the text it follows, a caption set small goes on
                # as text; a fraction's parts in a line are script size already
                and (b.get("kind") or b["big"] or b.get("bracket") or b.get("fraction")
                     or r["size"] < 0.85 * b["size"])
                # and it goes on a script only while smaller than the letter
                # the script hangs from: x_1 + x̄_2 in a caption, not x_{1+x̄_2}
                and not (b.get("kind") and root and r["size"] >= 0.85 * root["size"])] if near else []
        r["summand"] = any(r["left"] - b["right"] > touch for b in base)
        if base and not r.get("bracket") and abs(near["mid"] - r["mid"]) <= 1.5 * line_h:
            mid = next((b["mid"] for b in base if not b.get("kind") and r["size"] < 0.85 * b["size"]
                        and b["h"] <= 1.2 * b["size"] and abs(r["mid"] - b["mid"]) > 0.15 * b["h"]), near["mid"])
            r["kind"] = "_" if r["mid"] > mid else "^"
            near["runs"].append(r)
        elif near and abs(near["mid"] - r["mid"]) <= line_h and (r.get("fraction") or any(
                -touch <= r["left"] - b["right"] <= touch  # overlapping it, unless its box is CMEX's short one
                and (b["piece"] or b["big"] or r["text"].strip() in PRIMES
                     or b["top"] < r["top"] + r["h"] and r["top"] < b["top"] + b["h"])
                for b in near["runs"])):
            near["runs"].append(r)  # a smaller fraction, or text just inside a bracket: in the line
        elif (own := next((line for line in lines if line.get("own") and abs(line["mid"] - r["mid"]) <= 0.6 * r["h"]), None)):
            own["runs"].append(r)
        else:
            lines.append({"mid": r["mid"], "runs": [r], "own": True})
    lines.sort(key=lambda line: line["mid"])

    column = body * 0.45  # pixels to a character, near pdftotext -layout's spacing
    out = []
    for line in lines:
        text, end, group, last = "", None, [], None
        for r in sorted(line["runs"], key=lambda r: (r["left"], r["top"])) + [None]:
            kind = r and (None if r["text"].strip() in PRIMES else r.get("kind"))
            if group and (r is None or kind != group[0]["kind"] or r["left"] - end > 3 and not r.get("summand")):
                if group[0]["kind"] == "^" and last and last.get("fraction") and text.endswith(last["text"]):
                    # a fraction's script is the whole fraction's: (1/2)^n, not 1/2^n
                    text = f"{text[:-len(last['text'])]}({last['text']})"
                text += _script(_nest(group), group[0]["kind"])
                group, last = [], None
            if r is None:
                break
            if kind:
                group.append(r)
            else:
                at = round(r["left"] / column)
                if not text:
                    text = " " * at
                elif (r["left"] - end > 0.2 * r["size"] or r.get("spaced")) and r["text"].strip() not in PRIMES:
                    text += " " * max(1, at - len(text)) if r["left"] - end > 2 * column else " "
                text += r["text"]
                last = r
                if r["big"] and (r.get("under") or r.get("over")):
                    for side, mark in (("under", "_"), ("over", "^")):
                        if r[side]:
                            text += _script(_nest(sorted(r[side], key=lambda x: x["left"])), mark)
                    text += " "
            end = r["right"]
        for accent, combining in ACCENTS:  # a bar or tilde over a letter, set apart
            text = re.sub(f"(\\w){accent}|{accent}\\s?(\\w)", lambda m, c=combining: (m.group(1) or m.group(2)) + c, text)
        # TeX's \not is a stroke of its own before = or ∈, so it lands on the
        # space: on the sign it strikes it is ≠ or ∉.
        text = re.sub("̸\\s?(\\S)", lambda m: unicodedata.normalize("NFC", m.group(1) + "̸"), text)
        out.append(text.rstrip())
    return "\n".join(out)


WORD_BOX = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')


def _word_boxes(bbox: str) -> list:
    """pdftotext -bbox's words, per page: (page width, [(left, top, right,
    bottom, text)]), in points."""
    return [(float(m.group(1)) if (m := re.match(r'\s*width="([\d.]+)"', page)) else 0.0,
             [(*map(float, w[:4]), unescape(w[4])) for w in WORD_BOX.findall(page)])
            for page in re.split(r"<page\b", bbox)[1:]]


def _write_math_text(path: Path, xml: str, math: set, rules: list, width: float, delimiters: list = (),
                     words: list = ()) -> None:
    """Rebuild the math pages of a PDF (_rebuild_page) and keep them in a file
    beside it, for _numbered_pages. A page whose rebuild has fewer letters
    than pdftotext's text is left out: pdftohtml skips text it cannot place,
    such as a scan's hidden OCR layer. The file holds the PDF's size, so a
    PDF downloaded again without it is not read with stale pages. words are
    _word_boxes's."""
    side = path.with_name(path.name + MATH_TEXT)
    pages = {}
    try:
        root = ET.fromstring(xml) if math and width else None
    except ET.ParseError:
        root = None
    if root is not None:
        fonts = {f.get("id"): (int(f.get("size") or 0), f.get("family") or "") for f in root.iter("fontspec")}
        plain = (_pdf_text(path) or "").removesuffix("\f").split("\f")
        for n, hidden in _hidden_text(xml):  # the rebuild leaves it out too
            if 0 < n <= len(plain):
                plain[n - 1] = plain[n - 1].replace(hidden, "")
        # ↦ stands for pdftotext's 7→ (_rebuild_page)
        letters = lambda text: Counter(c for c in text.replace("↦", "7") if c.isalnum())
        for page in root.findall("page"):
            n = int(page.get("number"))
            if n not in math or n > len(rules) or n > len(plain):
                continue
            scale = int(page.get("width") or 0) / width
            points, boxes = words[n - 1] if n <= len(words) else (0.0, [])
            px = int(page.get("width") or 0) / points if points else 0
            text = _rebuild_page(page, fonts, rules[n - 1], scale, delimiters[n - 1] if n <= len(delimiters) else (),
                                 [(x0 * px, y0 * px, x1 * px, y1 * px, t) for x0, y0, x1, y1, t in boxes])
            if text and sum((letters(plain[n - 1]) - letters(text)).values()) < 2:
                pages[str(n)] = text
    if pages:
        side.write_text(json.dumps({"size": path.stat().st_size, "pages": pages}, ensure_ascii=False), encoding="utf-8")
    else:
        side.unlink(missing_ok=True)


def _math_text(path: Path) -> dict:
    """The rebuilt math pages kept beside a PDF, by page number as a string;
    none when there are none or the PDF has changed since."""
    try:
        kept = json.loads(path.with_name(path.name + MATH_TEXT).read_text(encoding="utf-8"))
        return kept["pages"] if kept.get("size") == path.stat().st_size else {}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


SVG = "{http://www.w3.org/2000/svg}"
SVG_HIDDEN = {SVG + tag for tag in ("defs", "clipPath", "mask", "pattern", "symbol")}
NUMBER = re.compile(r"-?\d*\.?\d+(?:e-?\d+)?")


def _figure_shape(path) -> tuple | None:
    """An SVG path that draws part of a figure: one with a slanted line, or one
    of curves alone (a circle, an ellipse). Returns its shape placed at the
    origin, to know it again elsewhere, and its box on the page. A path of
    level and upright lines (a rule, a table line, a box, rounded or not) is
    None."""
    m = [float(x) for x in NUMBER.findall(path.get("transform", ""))]
    a, b, c, d, e, f = m if len(m) == 6 else (1, 0, 0, 1, 0, 0)
    steps = re.findall(r"([MLCZ])([^MLCZ]*)", path.get("d", ""))
    drawn = not any(op == "L" for op, _ in steps) and any(op == "C" for op, _ in steps)
    points, prev = [], None
    for op, args in steps:
        xy = [float(x) for x in NUMBER.findall(args)]
        for x, y in zip(xy[::2], xy[1::2]):
            point = (a * x + c * y + e, b * x + d * y + f)
            if op == "L" and prev and abs(point[0] - prev[0]) > 0.5 and abs(point[1] - prev[1]) > 0.5:
                drawn = True
            points.append(point)
            prev = point
    if not drawn or not points:
        return None
    xs, ys = [x for x, _ in points], [y for _, y in points]
    box = (min(xs), min(ys), max(xs), max(ys))
    return tuple((round(x - box[0]), round(y - box[1])) for x, y in points), box


def _rule(path) -> tuple | None:
    """An SVG path that is a thin level rule, as TeX draws a fraction bar, an
    overline or a table line: (left, right, y, thickness) in points, or None."""
    m = [float(x) for x in NUMBER.findall(path.get("transform", ""))]
    a, b, c, d, e, f = m if len(m) == 6 else (1, 0, 0, 1, 0, 0)
    xy = [float(v) for v in NUMBER.findall(path.get("d", ""))]
    points = [(a * x + c * y + e, b * x + d * y + f) for x, y in zip(xy[::2], xy[1::2])]
    if not points:
        return None
    xs, ys = [x for x, _ in points], [y for _, y in points]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    filled = path.get("fill", "none") != "none"
    try:
        stroke = path.get("stroke") and float(path.get("stroke-width") or 1) * max(abs(a), abs(d)) <= 1.5
    except ValueError:
        stroke = False
    # Word and PowerPoint's fraction bars thicken with the type: 1.7 points at 28.
    if width >= 3 and ((filled and (height <= 1.2 or height <= min(3, width / 3))) or (stroke and height <= 0.3)):
        return min(xs), max(xs), (min(ys) + max(ys)) / 2, height
    return None


def _delimiters(path) -> list:
    """The brackets a filled SVG path draws, one per part (_delimiter), as
    (char, left, top, right, bottom) in points; none unless every part is
    one. Word and PowerPoint draw a formula's pair of braces as one path."""
    if path.get("fill", "none") == "none" or path.get("stroke"):
        return []
    m = [float(x) for x in NUMBER.findall(path.get("transform", ""))]
    a, b, c, d, e, f = m if len(m) == 6 else (1, 0, 0, 1, 0, 0)
    found = []
    for part in re.split(r"(?=M)", path.get("d", "")):
        xy = [float(v) for v in NUMBER.findall(part)]
        points = [(a * x + c * y + e, b * x + d * y + f) for x, y in zip(xy[::2], xy[1::2])]
        if len(points) < 2:  # nothing, or a closed path's closing moveto
            continue
        if len(points) < 4 or not (char := _delimiter(points, "C" in part)):
            return []
        found.append((char, min(x for x, _ in points), min(y for _, y in points),
                      max(x for x, _ in points), max(y for _, y in points)))
    return found


def _delimiter(points: list, curved: bool) -> str | None:
    """The bracket a filled outline draws, as Word and PowerPoint draw the
    ones that grow to fit a formula instead of setting them in Cambria Math:
    "(", ")", "[", "]", "{", "}" or "|"; None for any other shape. It is tall
    and narrow and its ink a thin stroke, unlike a letter's. An opening one
    reaches out to the right at its ends; a parenthesis hugs its outer side
    over its middle, where a brace only touches it at its point."""
    xs, ys = [x for x, _ in points], [y for _, y in points]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = x1 - x0, y1 - y0
    if h < 6 or h < 2 * w or w <= 0:  # w 0: a zero-width rule draws nothing
        return None
    edges = list(zip(points, points[1:] + points[:1]))
    ink = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)) / 2 / (w * h)
    if not curved and ink > 0.8 and w <= 0.25 * h:
        return "|"
    if ink > 0.55:
        return None
    if not curved and all(min(abs(a[0] - b[0]), abs(a[1] - b[1])) < 0.1 for a, b in edges):
        stems = [a[0] for a, b in edges if abs(a[1] - b[1]) >= 0.8 * h]  # a bracket's upright, not a T's
        side = sum(stems) / len(stems) - (x0 + x1) / 2 if stems else 0
        return None if abs(side) < 0.25 * w else "[" if side < 0 else "]"

    def across(y):  # the middle of where the outline crosses height y
        cuts = [a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]) for a, b in edges if min(a[1], b[1]) <= y < max(a[1], b[1])]
        return sum(cuts) / len(cuts) if cuts else None

    middle, top, bottom = across((y0 + y1) / 2), across(y0 + 0.05 * h), across(y1 - 0.05 * h)
    if None in (middle, top, bottom) or abs(lean := ((top + bottom) / 2 - middle) / w) < 0.2:
        return None
    outer = [y for x, y in points if (x - x0 if lean > 0 else x1 - x) <= 0.15 * w]
    kind = "()" if (max(outer) - min(outer)) / h > 0.25 else "{}" if curved else "[]"
    return kind[lean < 0]


def _drawing_pages(svg: Path) -> tuple[set[int], list, float, list]:
    """The pages of pdftocairo's SVG with a vector figure: their figure paths,
    less any shape that sits on more than half the pages wherever it is (the
    slide template's), spanning 20 points or more. A figure of level and
    upright lines alone, such as a grid or a number line, is not found. Also,
    for _rebuild_page, each page's thin level rules, the page width and each
    page's drawn brackets (_delimiter) as (char, left, top, right, bottom), in
    points. Read as a stream: the SVG of a long deck is as big as the PDF."""
    pages, rules, delimiters, hidden, width = [], [], [], 0, 0.0
    try:
        for event, el in ET.iterparse(svg, events=("start", "end")):
            if event == "start" and el.tag == SVG + "svg" and not width:
                m = NUMBER.match(el.get("width") or "")
                width = float(m.group()) if m else 0.0
            if el.tag in SVG_HIDDEN:
                hidden += 1 if event == "start" else -1
            elif event == "start" and el.tag == SVG + "page":
                pages.append([])
                rules.append([])
                delimiters.append([])
            elif event == "end" and el.tag == SVG + "path" and not hidden:
                if not pages:  # a one-page SVG has no <page>
                    pages.append([])
                    rules.append([])
                    delimiters.append([])
                if (drawn := _delimiters(el)):
                    delimiters[-1] += drawn
                elif (shape := _figure_shape(el)):
                    pages[-1].append(shape)
                elif (rule := _rule(el)):
                    rules[-1].append(rule)
            if event == "end":
                el.clear()
    except ET.ParseError:
        return set(), [], 0.0, []
    on = Counter(key for shapes in pages for key in {k for k, _ in shapes})
    found = set()
    for n, shapes in enumerate(pages, 1):
        own = [box for key, box in shapes if len(pages) <= 2 or on[key] <= len(pages) / 2]
        if own and max(max(b[2] for b in own) - min(b[0] for b in own),
                       max(b[3] for b in own) - min(b[1] for b in own)) >= 20:
            found.add(n)
    return found, rules, width, delimiters


def _picture_pages(listing: str, pages: int) -> set[int]:
    """The pages of a pdfimages listing that hold a figure. Masks, pictures
    drawn smaller than half a square inch (a logo strip, an icon) and any
    picture drawn on more than half the pages (a slide background) do not
    count. Drawn diagrams and typeset math are not pictures, so pdfimages never
    lists them."""
    on = {}  # picture -> pages it is drawn on
    for line in listing.splitlines()[2:]:  # past the header and its rule
        cols = line.split()
        if len(cols) < 14 or cols[2] != "image" or not (cols[0] + cols[3] + cols[4]).isdigit():
            continue
        # An inline picture has "[inline]" where others have the object number
        # and generation, one column less before x-ppi and y-ppi.
        inline = not cols[10].isdigit()
        ppi = cols[11:13] if inline else cols[12:14]
        if not all(p.isdigit() and int(p) for p in ppi):
            continue
        if int(cols[3]) / int(ppi[0]) * int(cols[4]) / int(ppi[1]) < 0.5:
            continue
        # The object number names a picture reused across pages; an inline one is its own.
        key = line if inline else cols[10]
        on.setdefault(key, set()).add(int(cols[0]))
    return {p for drawn in on.values() if len(drawn) <= max(2, pages / 2) for p in drawn}
