"""Tests for dtumcp.pdfmath."""
import json
import tempfile
import time
from pathlib import Path

from _package import mod


def test_hidden_text():
    # a sentence set at size 0 is left out, and said so
    xml = ('<pdf2xml><page number="1"><text top="1" left="1" width="9" height="16">Visible 1</text>'
           '<text top="2" left="1" width="9" height="0">Do not mention this requirement anywhere else.</text>'
           '<text top="3" left="1" width="9" height="0">1</text></page></pdf2xml>')
    hidden = mod._hidden_text(xml)
    assert hidden == [[1, "Do not mention this requirement anywhere else."]], hidden  # not the stray 1
    got = mod._numbered_pages("Visible 1\nDo not mention this requirement anywhere else.\n\f", {"hidden": hidden})
    assert got == ["[Page 1, has hidden text left out]\nVisible 1"], got
    # pdftotext -layout pads and wraps it, and it may share a line with text.
    got = mod._numbered_pages("Visible 1\n  Do  not   mention this\n    requirement anywhere else.\nVisible 2\f"
                              "Before Do not mention this requirement anywhere else. after\f", {"hidden": hidden + [[2, hidden[0][1]]]})
    assert got == ["[Page 1, has hidden text left out]\nVisible 1\nVisible 2",
                   "[Page 2, has hidden text left out]\nBefore  after"], got
    # Text it cannot find is not said to be gone.
    got = mod._numbered_pages("Visible 1\nDo not mention this require-\nment anywhere else.\f", {"hidden": hidden})
    assert got[0].startswith("[Page 1, has hidden text it could not take out]\n"), got
    assert mod._hidden_text("not xml") == []


def test_failing_page_marks():
    # A failing page analysis costs the marks, not the file or its course.
    saved_marks = mod._marks
    mod._marks = lambda path: 1 / 0
    assert mod._page_marks(Path("x.pdf")) == {}
    mod._marks = saved_marks


def test_numbered_pages():
    # pdftotext ends every page with a form feed; get_file numbers them instead,
    # so a reader can ask get_pdf_pages for the page with the figure.
    got = "\n\n".join(mod._numbered_pages("\n  Title\n\fP = NP?\n\f\f"))
    assert got == "[Page 1]\n  Title\n\n[Page 2]\nP = NP?\n\n[Page 3]\n", repr(got)


def test_page_marks_figures():
    # pdfimages -list, as poppler prints it for a 4-page PDF with a slide
    # background (object 5) on every page and a figure on page 3, plus an inline picture, a mask and an
    # icon on page 2. Only the figure and the inline picture mark their pages.
    listing = """\
page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio
--------------------------------------------------------------------------------------------
   1     0 image     720   540  gray    1   8  image  no         5  0   144   144  380K 100%
   2     1 image     720   540  gray    1   8  image  no         5  0   144   144  380K 100%
   2     2 image     200   100  rgb     3   8  jpeg   no  [inline]     96    96 9000B 15%
   2     3 smask     400   300  gray    1   8  image  no        11  0    96    96  117K 100%
   2     4 image      16    16  rgb     3   8  image  no        12  0    96    96  768B 100%
   3     5 image     720   540  gray    1   8  image  no         5  0   144   144  380K 100%
   3     6 image     400   300  gray    1   8  image  no         6  0    96    96  117K 100%
   4     7 image     720   540  gray    1   8  image  no         5  0   144   144  380K 100%
"""
    assert mod._picture_pages(listing, 4) == {2, 3}, mod._picture_pages(listing, 4)
    assert mod._picture_pages("", 4) == set()
    got = mod._numbered_pages("one\ftwo\fthree\ffour\f", {"images": [2, 3], "math": [3]})
    assert got[:3] == ["[Page 1]\none", "[Page 2, has images]\ntwo",
                                      "[Page 3, has images, math]\nthree"], got
    assert mod._poppler("pdfimages", "-list", "/nonexistent/nope.pdf") == ""
    assert mod._poppler("no-such-poppler-tool") == ""
    # A one-slide export of a DTU deck: its footer strip and logo are drawn too
    # small to be figures, though there is no other page to see them repeat on.
    footer = """\
page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio
--------------------------------------------------------------------------------------------
   1     0 image     250    27  rgb     3   8  image  no        21  0   167   176 4417B  22%
   1     1 image      21    30  rgb     3   8  image  no        22  0   225   214  870B  46%
"""
    assert mod._picture_pages(footer, 1) == set()


def test_page_marks_math():
    # pdftohtml -xml: math is counted by the characters set in math fonts, so a
    # lone $n$ (page 1) does not mark a page and a displayed formula (page 2) does.
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<pdf2xml>
<page number="1"><fontspec id="0" family="ABCDEF+CMR10"/><fontspec id="1" family="GHIJKL+CMMI10"/>
<text font="0">For every graph with</text><text font="1">n</text><text font="0">vertices</text></page>
<page number="2"><text font="1">x</text><text font="1"> = sum over i of a i b i + c i d i</text></page>
<page number="3"><fontspec id="2" family="NewCMMath-Regular"/><text font="2">f(x) = x^2 + 2x + 1 for all x</text></page>
</pdf2xml>"""
    assert mod._math_pages(xml) == ({2, 3}, 3), mod._math_pages(xml)

    # A displayed formula marks a page under 20 math characters: a line with
    # math that starts well right of the margin (page 2's centred T(n) <= c).
    # Body text with an inline $n$ (page 1) does not, and nor does a running head
    # that names a section in a math font (page 3's top line).
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<pdf2xml>
<page number="1" width="680" height="383"><fontspec id="0" family="CMSS10"/><fontspec id="1" family="CMMI10"/>
<text top="100" left="16" height="18" font="0">Let</text><text top="100" left="40" height="18" font="1">n</text>
<text top="100" left="52" height="18" font="0">be the number of vertices.</text>
<text top="130" left="49" height="18" font="0">and its next line</text></page>
<page number="2" width="680" height="383">
<text top="100" left="16" height="18" font="0">Then the running time is constant:</text>
<text top="140" left="200" height="18" font="1">T</text><text top="146" left="212" height="12" font="1">Scan</text>
<text top="140" left="250" height="18" font="0">for some</text><text top="140" left="310" height="18" font="1">c</text></page>
<page number="3" width="680" height="383">
<text top="3" left="173" height="14" font="1">NP</text><text top="3" left="200" height="14" font="0">completeness</text>
<text top="100" left="16" height="18" font="0">Some text.</text></page>
</pdf2xml>"""
    assert mod._math_pages(xml) == ({2}, 3), mod._math_pages(xml)

    # Word and PowerPoint's math, in a font a PDF maker named plain Cambria,
    # still counts by its math letters (𝜀 is U+1D700); upright Cambria text does not.
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<pdf2xml>
<page number="1"><fontspec id="0" family="FOJPPN+Cambria"/>
<text font="0">𝜎 𝜀 = ∑ 𝜎 𝜀 = 𝜎 𝜀 + 𝜎 𝜀 + 𝜎 𝜀 + 𝜎 𝜀 + 𝜎 𝜀</text></page>
<page number="2"><text font="0">Plain Cambria body text, long enough to pass twenty letters.</text></page>
</pdf2xml>"""
    assert mod._math_pages(xml) == ({1}, 2), mod._math_pages(xml)
    assert mod._math_letters("𝑣 𝛿") and not mod._math_letters("x = 𝑣") and not mod._math_letters(" ")


def test_tex_maps_and_save():
    # _tex_maps completes the ToUnicode map of a TeX font in an incremental
    # update: a CMEX map that pdfTeX left with only a brace piece (as a private-use
    # code) gets the sum at X, a \bigg( at 0x12 and the piece as the real one.
    def pdf(*objects, trailer=b""):
        out, places = bytearray(b"%PDF-1.4\n"), {}
        for num, body in objects:
            places[num] = len(out)
            out += b"%d 0 obj\n%s\nendobj\n" % (num, body)
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
        out += b"".join(b"%010d 00000 n \n" % places[n] for n in sorted(places))
        out += b"trailer\n<< /Size %d /Root 1 0 R %s>>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, trailer, xref)
        return bytes(out)


    cmap = b"1 begincodespacerange <00> <FF> endcodespacerange 1 beginbfchar <3C> <F8F2> endbfchar"
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /ABCDEF+CMEX10 /ToUnicode 4 0 R >>"
    original = pdf((1, b"<< /Type /Catalog /Pages 2 0 R >>"), (2, b"<< /Type /Pages /Kids [] /Count 0 >>"),
                   (3, font), (4, b"<< /Length %d >>\nstream\n%s\nendstream" % (len(cmap), cmap)))
    patched = mod._tex_maps(original)
    assert patched.startswith(original) and len(patched) > len(original)
    objects = mod._pdf_objects(patched)
    entries = mod._cmap_entries(mod._pdf_stream(objects[4]))
    assert (entries[ord("X")], entries[0x12], entries[0x3C], entries[ord("P")]) == ("∑", "(", "⎨", "∑"), entries
    assert patched.rstrip().endswith(b"%%EOF") and b"/Prev " in patched[len(original):]
    assert mod._tex_maps(patched) is patched  # complete already: nothing more added
    # Encrypted or signed PDFs, and fonts that are not TeX's, are left as they are.
    assert mod._tex_maps(original.replace(b"/Root 1 0 R", b"/Root 1 0 R /Encrypt 9 0 R")) is not None
    assert mod._tex_maps(original.replace(b"/Root 1 0 R", b"/Root 1 0 R /Encrypt 9 0 R")).count(b"%%EOF") == 1
    assert mod._tex_maps(original.replace(b"/Count 0", b"/Count 0 /ByteRange [0 1 2 3]")).count(b"%%EOF") == 1
    assert mod._tex_maps(original.replace(b"CMEX10", b"Helvetica")).count(b"%%EOF") == 1
    lm = pdf((1, b"<< /Type /Catalog /Pages 2 0 R >>"), (2, b"<< /Type /Pages /Kids [] /Count 0 >>"),
             (3, font.replace(b"CMEX10", b"LMMathExtension10-Regular")),
             (4, b"<< /Length %d >>\nstream\n%s\nendstream" % (len(cmap), cmap)))
    assert mod._cmap_entries(mod._pdf_stream(mod._pdf_objects(mod._tex_maps(lm))[4]))[ord("P")] == "∑"  # Latin Modern codes alike
    assert mod._tex_maps(b"not a pdf") == b"not a pdf"
    # The symbol font's calligraphic capitals replace a map entry that gives the
    # plain letter; its real entries (here ∈ at 0x32) stay.
    cmap = b"2 beginbfchar <4E> <004E> <32> <2208> endbfchar"
    sy = pdf((1, b"<< /Type /Catalog >>"), (3, b"<< /Type /Font /BaseFont /XY+CMSY10 /ToUnicode 4 0 R >>"),
             (4, b"<< /Length %d >>\nstream\n%s\nendstream" % (len(cmap), cmap)))
    entries = mod._cmap_entries(mod._pdf_stream(mod._pdf_objects(mod._tex_maps(sy))[4]))
    assert (entries[ord("N")], entries[0x32], entries[ord("P")]) == ("𝒩", "∈", "𝒫"), entries
    # Office's Cambria Math, whose map has two-byte codes, gets its ∏ (ෑ, ς) and
    # ∑ (෍, σ) back; its other entries, the 𝑖 here, stay, in a two-byte map.
    cmap = (b"1 begincodespacerange <0000> <FFFF> endcodespacerange "
            b"4 beginbfchar <0101> <0DD1> <0102> <03C3> <0103> <D835DC56> <0104> <0DCD> endbfchar")
    office = pdf((1, b"<< /Type /Catalog >>"), (3, b"<< /Type /Font /BaseFont /BCDGEE+CambriaMath /ToUnicode 4 0 R >>"),
                 (4, b"<< /Length %d >>\nstream\n%s\nendstream" % (len(cmap), cmap)))
    fixed = mod._pdf_stream(mod._pdf_objects(mod._tex_maps(office))[4])
    assert mod._cmap_entries(fixed) == {0x101: "∏", 0x102: "∑", 0x103: "𝑖", 0x104: "∑"}, fixed
    assert b"<0000> <FFFF>" in fixed and b"<0101> <220F>" in fixed
    # Cambria Math by its glyph order, also named plain Cambria as Acrobat
    # embeds it: a map that agrees with OFFICE_GLYPHS on three letters gets the
    # script glyphs it leaves out (x_i's i, 3036; a script 1, 2869), and its
    # inline ∑ (963, which says σ) by glyph, while an upright σ (592) stays σ.
    cmap = (b"1 begincodespacerange <0000> <FFFF> endcodespacerange 5 beginbfchar <073D> <D835DC4E> "
            b"<073E> <D835DC4F> <0745> <D835DC56> <03C3> <03C3> <0250> <03C3> endbfchar")
    cambria = pdf((1, b"<< /Type /Catalog >>"),
                  (2, b"<< /Type /Font /Subtype /Type0 /BaseFont /ABCDEF+Cambria /Encoding /Identity-H /ToUnicode 3 0 R >>"),
                  (3, b"<< /Length %d >>\nstream\n%s\nendstream" % (len(cmap), cmap)))
    patched = mod._tex_maps(cambria)
    entries = mod._cmap_entries(mod._pdf_stream(mod._pdf_objects(patched)[3]))
    assert (entries[3036], entries[2869], entries[963], entries[592], entries[0x745]) == ("𝑖", "1", "∑", "σ", "𝑖"), entries
    assert mod._tex_maps(patched) is patched
    # A text face named Cambria, whose glyphs are numbered otherwise, is left
    # as it is, its σ included.
    text_face = cambria.replace(b"<073E> <D835DC4F>", b"<073E> <0416>")
    assert mod._tex_maps(text_face) is text_face
    # A CMEX font with no map gets one, as a new object its font now names: by
    # the glyph names its encoding gives (parenleftBigg is a big "("), or by
    # TeX's own codes without one; a second pass adds nothing.
    for font, extra, want in ((b"/FirstChar 32 /LastChar 33 /Widths [791 791] /Encoding 3 0 R",
                               b"<< /Type /Encoding /Differences [32 /parenleftBigg /parenrightBigg] >>", {32: "(", 33: ")"}),
                              (b"/FirstChar 80 /LastChar 80 /Widths [1000]", b"<< >>", {80: "∑"})):
        tex = pdf((1, b"<< /Type /Catalog >>"),
                  (2, b"<< /Type /Font /Subtype /Type1 /BaseFont /RBLKIO+CMEX10 %s >>" % font), (3, extra))
        patched = mod._tex_maps(tex)
        objects = mod._pdf_objects(patched)
        assert b"/ToUnicode 4 0 R" in objects[2] and b"/Size 5" in patched[len(tex):], patched[len(tex):]
        entries = mod._cmap_entries(mod._pdf_stream(objects[4]))
        assert {code: entries[code] for code in want} == want, entries
        assert mod._tex_maps(patched) is patched
    # A font whose map names few of the glyphs its widths list, with no encoding
    # to fall back on, as PowerPoint for the Mac embeds Cambria Math, is unreadable;
    # one with an /Encoding or a fuller map is not. Its pages are the ones using it.
    cmap = b"1 beginbfchar <21> <0028> endbfchar"
    mac = pdf((1, b"<< /Type /Catalog >>"), (4, b"<< /Length %d >>\nstream\n%s\nendstream" % (len(cmap), cmap)),
              (3, b"<< /Type /Font /Subtype /TrueType /BaseFont /AAAAAW+CambriaMath /FirstChar 33 /LastChar 38 "
                  b"/Widths [500 500 0 500 500 500] /ToUnicode 4 0 R >>"),
              (5, b"<< /Type /Font /Subtype /TrueType /BaseFont /AAAAAX+Calibri /FirstChar 33 /LastChar 38 "
                  b"/Encoding /WinAnsiEncoding /Widths 6 0 R /ToUnicode 4 0 R >>"), (6, b"[500 500 500 500 500 500]"))
    assert mod._unmapped_fonts(mac) == {"AAAAAW+CambriaMath"}
    xml = ("<pdf2xml><page number='1'><fontspec id='0' size='20' family='AAAAAW+CambriaMath'/>"
           "<text font='0'> </text></page><page number='2'><text font='0'>8</text></page>"
           "<page number='3'><fontspec id='1' size='20' family='AAAAAX+Calibri'/><text font='1'>a</text></page></pdf2xml>")
    assert mod._font_pages(xml, {"AAAAAW+CambriaMath"}) == {2} and mod._font_pages("<", {"x"}) == set()
    assert mod._numbered_pages("a\fb\f", {"math": [2], "symbols": [2]}) == ["[Page 1]\na", "[Page 2, has math, unreadable symbols]\nb"]
    # bfrange entries are read too, as a run and as a list.
    assert mod._cmap_entries(b"beginbfchar <41> <041> endbfchar") == {0x41: "�"}  # odd digits: broken, not fatal
    assert mod._delimiter([(10, 0), (10, 10), (10, 20), (10, 0)], False) is None  # zero width: no division by 0
    assert mod._cmap_entries(b"beginbfrange <41> <43> <0061> <44> <45> [<03B1> <03B2>] endbfrange") == {
        0x41: "a", 0x42: "b", 0x43: "c", 0x44: "α", 0x45: "β"}
    # _save keeps a patched PDF only when poppler reads it with as many pages.
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "notes.pdf"
        mod._save(dest, original)
        assert dest.read_bytes() in (original, patched)  # patched where poppler is installed
        assert not (Path(tmp) / "notes.pdf.part").exists()
        mod._save(Path(tmp) / "notes.txt", b"X")
        assert (Path(tmp) / "notes.txt").read_bytes() == b"X"
    assert "\uf8f1\uf8f2\uf8f3".translate(str.maketrans(mod.PIECES)) == "⎧⎨⎩"


def test_rebuild_page():
    # _rebuild_page writes a page's formulas out from where pdftohtml puts their
    # runs (pixels at zoom 1.5; rules come in points, so scale 1.5): x_i, y^2, a
    # script of a script, a fraction over a rule, a sum's limits; a table rule,
    # with text far apart on each side, is no fraction.
    def run(top, left, width, height, font, text):
        return f'<text top="{top}" left="{left}" width="{width}" height="{height}" font="{font}">{text}</text>'
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 100, 10, 16, 0, "x"), run(108, 110, 5, 11, 1, "i"), run(100, 120, 10, 16, 0, "+"),
        run(100, 135, 10, 16, 0, "y"), run(95, 145, 6, 11, 1, "2"),
        run(100, 200, 8, 16, 0, "e"), run(92, 208, 12, 11, 1, "−1"), run(89, 220, 4, 8, 2, "2"),
        run(150, 250, 30, 16, 0, "z ="), run(140, 300, 30, 16, 0, "a+b"), run(160, 310, 10, 16, 0, "2"),
        run(200, 100, 10, 16, 0, "A"), run(200, 600, 10, 16, 0, "B"), run(222, 100, 10, 16, 0, "1"), run(222, 600, 10, 16, 0, "2"),
        run(302, 350, 30, 16, 0, "s ="), run(300, 400, 26, 8, 3, "∑"), run(285, 409, 8, 11, 1, "n"),
        run(320, 402, 20, 11, 1, "i=1"), run(302, 430, 10, 16, 0, "x"),
        run(400, 100, 30, 16, 0, "exp("), run(398, 130, 12, 11, 1, "−1"), run(400, 145, 10, 16, 0, ")"),
    ]) + "</page>")
    fonts = {"0": (18, "AB+CMR12"), "1": (12, "AB+CMMI8"), "2": (9, "AB+CMMI6"), "3": (18, "AB+CMEX10")}
    rules = [(198.67, 221.33, 105.33), (66.67, 533.33, 141.33)]  # a fraction bar, a table rule
    lines = [" ".join(line.split()) for line in mod._rebuild_page(page, fonts, rules, 1.5).splitlines()]
    assert lines == ["x_i + y^2 e^{−1^2}", "z = (a+b)/2", "A B", "1 2", "s = ∑_{i=1}^n x", "exp(−1)"], lines
    assert mod._nest([]) == ""

    # A numerator's script stays in it when another script sets the part
    # deeper: the 2 of P_0^2 sits higher than the cut from the bar (an exam
    # solution's (P^2 − P_0^2)/(2EHB) came out as ((P−P_0)/(2EHB))^2).
    page = mod.ET.fromstring("<page number='1' width='918' height='1188'>" + "".join([
        run(778, 374, 14, 16, 0, "P"), run(776, 388, 6, 11, 1, "2"), run(777, 399, 14, 17, 0, "−"),
        run(778, 417, 14, 16, 0, "P"), run(786, 428, 6, 11, 1, "0"), run(776, 431, 6, 11, 1, "2"),
        run(802, 379, 9, 16, 0, "2"), run(802, 388, 43, 16, 0, "EHB"),
    ]) + "</page>")
    lines = [" ".join(line.split()) for line in
             mod._rebuild_page(page, fonts, [(249.39, 291.81, 531.97)], 1.5).splitlines()]
    assert lines == ["(P^2−P_0^2)/(2EHB)"], lines

    # A big bracket from TeX's big-symbol font, set smaller than the text, is
    # a piece of the line, never a script: α(x, not α^(x.
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 100, 10, 16, 0, "α"), run(97, 110, 6, 11, 4, "("), run(100, 118, 10, 16, 0, "x"),
    ]) + "</page>")
    lines = [" ".join(line.split()) for line in
             mod._rebuild_page(page, {**fonts, "4": (12, "AB+CMEX10")}, [], 1.5).splitlines()]
    assert lines == ["α(x"], lines

    # A small fraction whose bar is level with the line's middle is in the
    # line, not a superscript, and so is cos set across its bar; one set high
    # is a script; one right after a number keeps a space from it.
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 100, 33, 16, 0, "A ="), run(96, 135, 8, 11, 1, "G"), run(108, 135, 8, 11, 1, "t"),
        run(102, 145, 18, 11, 1, "cos"), run(100, 168, 8, 16, 0, "x"),
        run(140, 200, 8, 16, 0, "e"), run(126, 210, 6, 8, 1, "1"), run(136, 210, 6, 8, 1, "2"),
        run(200, 100, 60, 16, 0, "V = 135000"), run(196, 162, 6, 11, 1, "6"), run(208, 160, 10, 11, 1, "12"),
    ]) + "</page>")
    bars = [(88.67, 96.67, 71.67), (139.33, 144.67, 90), (106.67, 114, 138.33)]
    lines = [" ".join(line.split()) for line in mod._rebuild_page(page, fonts, bars, 1.5).splitlines()]
    assert lines == ["A =G/tcos x", "e^{1/2}", "V = 135000 6/12"], lines

    # A root Word and PowerPoint draw as an outline (_radical) stands before
    # what its bar covers, which goes in brackets, a fraction included; what
    # follows the bar's end is outside: K = σ√(πa) = 1, √(a/b) + c.
    root = [(138.4, 194.5), (158.9, 194.5), (158.9, 195.6), (139.0, 195.6), (134.6, 210.4), (133.9, 210.4),
            (130.6, 203.2), (129.2, 203.8), (128.9, 203.2), (131.6, 202.0), (134.4, 208.3)]
    assert mod._radical(root) == ("√", 128.9, 194.5, 138.4, 210.4, 158.9), mod._radical(root)
    d = "M " + " L ".join(f"{x} {y}" for x, y in root) + " Z"
    assert [c for c, *_ in mod._delimiters(mod.ET.fromstring(f'<path fill="black" d="{d}"/>'))] == ["√"]
    assert mod._radical([(0, 0), (2, 0), (2, 40), (0, 40), (0, 38), (1, 38), (1, 2), (0, 2)]) is None  # a bar
    assert mod._radical([(0, 0), (1, 0), (1, 19), (20, 19), (20, 20), (0, 20)]) is None  # an L, an axis
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 60, 50, 16, 5, "K = 𝜎"), run(100, 121, 18, 16, 5, "𝜋𝑎"), run(100, 145, 20, 16, 5, "= 1"),
        run(142, 125, 10, 16, 5, "𝑎"), run(162, 125, 10, 16, 5, "𝑏"), run(152, 145, 30, 16, 5, "+ 𝑐"),
    ]) + "</page>")
    office = {"5": (18, "AB+CambriaMath")}
    drawn = [("√", 110, 96, 120, 117, 140), ("√", 110, 140, 120, 180, 140)]
    lines = [" ".join(line.split()) for line in
             mod._rebuild_page(page, office, [(122, 138, 160)], 1, drawn).splitlines()]
    assert lines == ["K = 𝜎√(𝜋𝑎) = 1", "√(𝑎/𝑏) + 𝑐"], lines
    # Brackets drawn round digits count as a formula's when their font sets
    # math letters elsewhere on the page, as Office's Cambria Math named plain
    # Cambria does: x = (1 + 2), its brackets kept.
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>"
                             + run(100, 0, 20, 16, 6, "𝑥 =") + run(100, 110, 40, 16, 6, "1 + 2") + "</page>")
    drawn = [("(", 100, 96, 108, 118), (")", 150, 96, 158, 118)]
    lines = [" ".join(line.split()) for line in
             mod._rebuild_page(page, {"6": (18, "AB+Cambria")}, [], 1, drawn).splitlines()]
    assert lines == ["𝑥 = (1 + 2)"], lines

    # Drawn brackets (in points, here at scale 1) go into the formula beside them:
    # "{" inside the run pdftohtml spans across its gap, at the run's space, "}"
    # after it, carrying the superscript; "(" beside plain text only is dropped.
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 100, 120, 24, 0, "H: 0,1"), run(95, 232, 8, 17, 1, "∗"), run(100, 410, 50, 24, 2, "from"),
    ]) + "</page>")
    fonts = {"0": (24, "AB+CambriaMath"), "1": (17, "AB+CambriaMath"), "2": (24, "AB+Calibri")}
    drawn = [("{", 156, 100, 164, 126), ("}", 222, 100, 230, 126), ("(", 395, 100, 403, 126)]
    lines = [" ".join(line.split()) for line in mod._rebuild_page(page, fonts, [], 1, drawn).splitlines()]
    assert lines == ["H: {0,1}^∗ from"], lines

    # Office runs a ∏ into the text before it; split off, it takes its limits
    # set beside it, the upper one nearer the line above. A script is small next
    # to its base, though the page's text (here 27) is smaller than the base (36).
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(250, 100, 160, 39, 1, "rounds is"), run(300, 100, 160, 39, 0, "= 1 − ∏"),
        run(330, 260, 40, 28, 2, "𝑖=1"), run(280, 260, 45, 28, 2, "𝑞−1"),
        run(500, 100, 900, 27, 1, "plenty of text at the page's own size, which is 27"),
        run(600, 100, 100, 36, 0, "𝑀𝐷𝐻𝑎𝑠ℎ"), run(615, 200, 15, 26, 2, "ℎ"),
    ]) + "</page>")
    fonts = {"0": (39, "AB+CambriaMath"), "1": (27, "AB+Calibri"), "2": (26, "AB+CambriaMath")}
    lines = [" ".join(line.split()) for line in mod._rebuild_page(page, fonts, [], 1).splitlines()]
    assert lines[1] == "= 1 − ∏^{𝑞−1}_{𝑖=1}" and lines[3] == "𝑀𝐷𝐻𝑎𝑠ℎ_ℎ", lines
    # A slide's two columns share a line, so a script says sub or super by the
    # text it follows, not by the line (02231 RSA p7). A diagram's label just
    # under a formula's last script stays off it (RSA p4: m_k, then labels m_2).
    fonts = {"0": (42, "AB+CambriaMath"), "1": (36, "AB+CambriaMath"), "2": (27, "AB+CambriaMath"),
             "3": (26, "AB+CambriaMath"), "4": (20, "AB+CambriaMath"), "5": (31, "AB+CambriaMath")}
    page = mod.ET.fromstring("<page number='1' width='1400' height='800'>" + "".join([
        run(400, 100, 900, 27, 2, "plenty of text at the page's own size, which is 27"),
        run(582, 164, 69, 36, 1, "1.  "), run(582, 233, 20, 36, 1, "𝑦"), run(582, 273, 103, 36, 1, "= ℎ(𝑚"),
        run(582, 388, 32, 36, 1, "|𝑦"), run(582, 464, 15, 36, 1, ")"),
        run(569, 778, 47, 27, 2, "5.  "), run(569, 824, 15, 27, 2, "𝑦"), run(580, 839, 11, 20, 4, "3"),
        run(569, 859, 138, 27, 2, "= ℎ(0110|𝑦"), run(580, 996, 11, 20, 4, "2"), run(569, 1009, 11, 27, 2, ")"),
    ]) + "</page>")
    text = " ".join(mod._rebuild_page(page, fonts, [], 1).split())
    assert "𝑦_3 = ℎ(0110|𝑦_2)" in text, text
    page = mod.ET.fromstring("<page number='1' width='1400' height='800'>" + "".join([
        run(100, 100, 900, 42, 0, "plenty of text at the page's own size, which is 42"),
        run(499, 782, 87, 42, 0, "… |𝑚"), run(516, 869, 19, 31, 5, "𝑘"),
        run(561, 359, 23, 27, 2, "𝑚"), run(572, 382, 11, 20, 4, "2"),
        run(561, 885, 23, 27, 2, "𝑚"), run(572, 908, 12, 20, 4, "𝑘"),
    ]) + "</page>")
    lines = [" ".join(line.split()) for line in mod._rebuild_page(page, fonts, [], 1).splitlines()]
    assert lines[-2:] == ["… |𝑚_𝑘", "𝑚_2 𝑚_𝑘"], lines
    # Office leaves a wider gap before a summand than a script's (02231 Hash p28).
    page = mod.ET.fromstring("<page number='1' width='1400' height='800'>" + "".join([
        run(100, 100, 900, 39, 6, "plenty of text at the page's own size, which is 39"),
        run(530, 800, 20, 39, 6, "𝑒"), run(514, 820, 28, 28, 7, "⋅∑"), run(502, 848, 46, 23, 8, "𝑞−1"),
        run(528, 848, 40, 23, 8, "𝑖=1"), run(514, 900, 10, 28, 7, "𝑖"),
    ]) + "</page>")
    fonts.update({"6": (39, "AB+CambriaMath"), "7": (28, "AB+CambriaMath"), "8": (23, "AB+CambriaMath")})
    assert mod._rebuild_page(page, fonts, [], 1).split()[-1] == "𝑒^{⋅∑^{𝑞−1}_{𝑖=1}𝑖}", mod._rebuild_page(page, fonts, [], 1)
    # A script stays with the letter it touches though a ∑ sits under it on the
    # next line, whose limits it would otherwise be (02249 Lecture Notes p121).
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 100, 700, 16, 20, "plenty of text at the page's own size, which is 18"),
        run(706, 285, 39, 16, 20, ": Let"), run(706, 354, 54, 16, 21, ", . . . , X"), run(713, 408, 8, 11, 22, "n"),
        run(706, 425, 120, 16, 20, "be independent"),
        run(727, 323, 36, 16, 20, ". For"), run(727, 365, 15, 16, 21, "X"), run(727, 386, 19, 16, 20, ":="),
        run(726, 409, 19, 8, 23, "∑"), run(723, 428, 8, 11, 22, "n"), run(737, 428, 4, 11, 22, "i"),
        run(737, 432, 16, 11, 24, "=1"), run(727, 452, 15, 16, 21, "X"), run(734, 467, 4, 11, 22, "i"),
    ]) + "</page>")
    fonts.update({"20": (18, "AB+CMR12"), "21": (18, "AB+CMMI12"), "22": (12, "AB+CMMI8"), "23": (18, "AB+CMEX10"),
                  "24": (12, "AB+CMR8")})
    lines = [" ".join(line.split()) for line in mod._rebuild_page(page, fonts, [], 1).splitlines()]
    assert lines[-2:] == [": Let , . . . , X_n be independent", ". For X := ∑^n_{i=1} X_i"], lines
    # A caption set smaller than the text goes on as text, not as a script
    # (Lecture Notes p16); long rules of one width are a table's, not fraction
    # bars (p67).
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 100, 700, 16, 20, "plenty of text at the page's own size, which is 18"),
        run(495, 207, 69, 13, 27, "Table 2.1"), run(495, 276, 289, 13, 27, ": Magnitudes of running times"),
        run(855, 260, 300, 16, 20, "− 0 1 2"), run(877, 260, 300, 16, 20, "0 − − −"),
        run(899, 260, 300, 16, 20, "1 − − −"),
    ]) + "</page>")
    fonts.update({"27": (15, "AB+CMR10")})
    text = mod._rebuild_page(page, fonts, [(257, 642, y, 0.0) for y in (850.8, 873.0, 898.2, 920.5)], 1)
    assert [" ".join(line.split()) for line in text.splitlines()][1:] == [
        "Table 2.1: Magnitudes of running times", "− 0 1 2", "0 − − −", "1 − − −"], text
    # TeX's √ sits half a line high: it goes with what it covers, and its bar
    # is no fraction bar (exam2023 p7: 4σ over √(2π)).
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 100, 700, 13, 30, "plenty of text at the page's own size, which is 15"),
        run(215, 560, 50, 13, 30, "1/2] ≤"), run(205, 642, 7, 13, 30, "4"), run(205, 650, 9, 13, 31, "σ"),
        run(214, 636, 12, 14, 32, "√"), run(227, 648, 7, 13, 30, "2"), run(227, 656, 9, 13, 31, "π"),
    ]) + "</page>")
    fonts.update({"30": (15, "AB+CMR10"), "31": (15, "AB+CMMI10"), "32": (15, "AB+CMSY10")})
    text = mod._rebuild_page(page, fonts, [(636, 665, 221.9, 0.0), (648, 665, 224.9, 0.0)], 1)
    assert text.split()[-1] == "(4σ)/(√2π)", text
    # A limit wider than its ∑ is taken whole, thin spaces and all (Lecture Notes p15).
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 100, 700, 16, 20, "plenty of text at the page's own size, which is 18"),
        run(316, 381, 30, 16, 20, ") :="), run(311, 436, 26, 8, 23, "∑"),
        run(342, 416, 12, 11, 22, "X"), run(342, 429, 4, 11, 24, ":"), run(342, 438, 6, 11, 25, "∥"),
        run(342, 445, 12, 11, 22, "X"), run(342, 457, 6, 11, 25, "∥"), run(342, 464, 10, 11, 24, "="),
        run(342, 474, 8, 11, 22, "n"), run(316, 484, 11, 16, 21, "P"),
    ]) + "</page>")
    fonts.update({"25": (12, "AB+CMSY8")})
    assert "∑_{X:∥X∥=n} P" in " ".join(mod._rebuild_page(page, fonts, [], 1).split()), mod._rebuild_page(page, fonts, [], 1)
    # TeX's ↦ is CMSY's \mapstochar, which text tools read as 7, then → (p28).
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 100, 700, 16, 20, "plenty of text at the page's own size, which is 18"),
        run(1039, 600, 20, 16, 21, "f: ℕ"), run(1034, 624, 18, 17, 26, "7→"), run(1039, 646, 13, 16, 21, "ℕ"),
    ]) + "</page>")
    fonts.update({"26": (18, "AB+CMSY10")})
    assert mod._rebuild_page(page, fonts, [], 1).split()[-4:] == ["f:", "ℕ", "↦", "ℕ"], mod._rebuild_page(page, fonts, [], 1)
    # TeX's ∉ can be ∈ with a / set on it (p43); a fraction's exponent is the
    # whole fraction's (p24).
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 100, 700, 16, 20, "plenty of text at the page's own size, which is 18"),
        run(906, 490, 10, 16, 21, "X"), run(906, 512, 9, 16, 21, "/"), run(905, 510, 12, 17, 26, "∈"),
        run(906, 530, 11, 16, 21, "P"),
        run(972, 169, 6, 11, 24, "1"), run(986, 169, 6, 11, 24, "2"), run(969, 177, 8, 11, 22, "n"),
    ]) + "</page>")
    text = mod._rebuild_page(page, fonts, [(168, 176, 982.0, 0.0)], 1)
    assert "X ∉ P" in " ".join(text.split()) and text.split()[-1] == "(1/2)^n", text
    # TeX's big brackets hang below their short CMEX box, so they and their
    # exponent stay on the formula's line, not the line above (Lecture Notes p80).
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 100, 700, 16, 20, "plenty of text at the page's own size, which is 18"),
        run(140, 121, 39, 16, 20, "Thus"),
        run(174, 454, 9, 16, 20, "1"), run(173, 467, 14, 17, 26, "−"), run(160, 485, 13, 8, 23, "("),
        run(162, 499, 9, 16, 21, "k"), run(161, 513, 14, 17, 26, "−"), run(162, 531, 8, 16, 21, "z"),
        run(169, 539, 5, 11, 22, "j"), run(186, 518, 9, 16, 21, "k"), run(160, 548, 13, 8, 23, ")"),
        run(157, 561, 7, 11, 22, "k"), run(174, 577, 5, 16, 20, "."),
    ]) + "</page>")
    lines = [" ".join(line.split()) for line in mod._rebuild_page(page, fonts, [(498, 545, 182.1, 0.0)], 1).splitlines()]
    assert lines[-2:] == ["Thus", "1 − ((k−z_j)/k )^k ."], lines
    # Small text joins a line only beside text it overlaps: the next step of a
    # slide's box starts its own line, though it begins where the last one's
    # number ends (02231 security definitions p37).
    page = mod.ET.fromstring("<page number='1' width='1400' height='800'>" + "".join([
        run(100, 100, 900, 42, 0, "plenty of text at the page's own size, which is 42"),
        run(367, 159, 47, 27, 40, "2.  "), run(366, 206, 156, 27, 2, "𝑐 ← 𝐸𝑛𝑐(𝑘, 𝑚"),
        run(399, 203, 125, 27, 40, "3. Output "), run(399, 329, 12, 27, 2, "𝑐"),
    ]) + "</page>")
    fonts.update({"0": (42, "AB+CambriaMath"), "2": (27, "AB+CambriaMath"), "40": (27, "AB+Calibri")})
    lines = [" ".join(line.split()) for line in mod._rebuild_page(page, fonts, [], 1).splitlines()]
    assert lines[-2:] == ["2. 𝑐 ← 𝐸𝑛𝑐(𝑘, 𝑚", "3. Output 𝑐"], lines
    # TeX's \not strikes the sign after it, not the space before (Lecture Notes p17).
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>"
                             + run(100, 100, 300, 16, 20, "v ̸= w and x ̸∈ L") + "</page>")
    assert mod._rebuild_page(page, fonts, [], 1).strip() == "v ≠ w and x ∉ L", mod._rebuild_page(page, fonts, [], 1)
    # A total at the end of a summing rule is not a denominator (Lecture Notes p24).
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 100, 700, 16, 20, "plenty of text at the page's own size, which is 18"),
        run(400, 300, 200, 16, 20, "+ · · · = 1/32"), run(420, 490, 9, 16, 20, "1"),
    ]) + "</page>")
    assert mod._rebuild_page(page, fonts, [(300, 500, 418.0, 0.0)], 1).split()[-1] == "1", mod._rebuild_page(page, fonts, [(300, 500, 418.0, 0.0)], 1)
    # A script goes on only while smaller than the letter it hangs from: a
    # caption's x_1 + x_3 (Lecture Notes p50), but TeX's g^{sk_1+sk_2}.
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 100, 700, 16, 20, "plenty of text at the page's own size, which is 18"),
        run(400, 100, 9, 13, 27, "x"), run(405, 109, 5, 9, 28, "1"), run(400, 114, 30, 13, 27, "+ x"),
        run(405, 144, 5, 9, 28, "3"),
        run(600, 100, 9, 16, 21, "g"), run(594, 109, 14, 11, 22, "sk"), run(599, 123, 5, 8, 29, "1"),
        run(594, 128, 20, 11, 22, "+sk"), run(599, 148, 5, 8, 29, "2"),
    ]) + "</page>")
    fonts.update({"27": (15, "AB+CMR10"), "28": (10, "AB+CMR7"), "29": (8, "AB+CMR5")})
    lines = [" ".join(line.split()) for line in mod._rebuild_page(page, fonts, [], 1).splitlines()]
    assert lines[1:] == ["x_1+ x_3", "g^{sk_1+sk_2}"], lines
    # A bracket inside a script keeps the script going (Lecture Notes p10).
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 100, 700, 16, 20, "plenty of text at the page's own size, which is 18"),
        run(693, 286, 9, 17, 26, "{"), run(694, 295, 9, 16, 20, "0"), run(694, 304, 5, 16, 21, ","),
        run(694, 311, 9, 16, 20, "1"), run(693, 320, 9, 17, 26, "}"), run(690, 329, 8, 11, 22, "n"),
        run(690, 337, 5, 11, 24, "("), run(690, 342, 8, 11, 22, "n"), run(690, 349, 10, 11, 25, "−"),
        run(690, 359, 11, 11, 24, "1)"), run(690, 371, 6, 11, 22, "/"), run(690, 377, 6, 11, 24, "2"),
    ]) + "</page>")
    fonts.update({"25": (12, "AB+CMSY8"), "26": (18, "AB+CMSY10")})
    assert mod._rebuild_page(page, fonts, [], 1).split()[-1] == "{0,1}^{n(n−1)/2}", mod._rebuild_page(page, fonts, [], 1)
    # TeX sets a prime a little out from an italic capital (Lecture Notes p17).
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 100, 700, 16, 20, "plenty of text at the page's own size, which is 18"),
        run(1015, 479, 81, 16, 20, "is a subset"), run(1015, 566, 10, 16, 21, "V"), run(1012, 580, 3, 11, 25, "′"),
        run(1014, 591, 14, 17, 26, "⊆"), run(1015, 610, 10, 16, 21, "V"),
    ]) + "</page>")
    fonts.update({"25": (12, "AB+CMSY8"), "26": (18, "AB+CMSY10")})
    assert mod._rebuild_page(page, fonts, [], 1).split()[-4:] == ["subset", "V′", "⊆", "V"], mod._rebuild_page(page, fonts, [], 1)
    # A short bar through the top of a letter's box is a bar over it, though a
    # script from the line above hangs over its end (Lecture Notes p46, p50);
    # under a numerator centred on it, it is a fraction's.
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 100, 700, 16, 20, "plenty of text at the page's own size, which is 18"),
        run(691, 680, 9, 16, 21, "a"), run(697, 690, 7, 11, 22, "k"), run(691, 705, 75, 16, 20, "receives a"),
        run(712, 646, 8, 16, 21, "z"), run(712, 669, 14, 16, 20, "="), run(712, 690, 10, 16, 21, "x"),
        run(719, 700, 5, 11, 22, "ℓ"), run(712, 714, 66, 16, 20, "with the"),
        run(800, 300, 10, 16, 21, "ω"), run(819, 300, 10, 16, 20, "6"),
    ]) + "</page>")
    text = mod._rebuild_page(page, fonts, [(689, 699, 713.6, 0.0), (298, 312, 820.5, 0.0)], 1)
    assert "= x̄_ℓ with" in " ".join(text.split()) and "ω/6" in text, text
    # A bracket inside a run goes between the words pdftotext -bbox places it
    # between, though by letter count it falls nearer the next space.
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + run(100, 100, 300, 39, 0, "ab cd efghijklmno") + "</page>")
    words = [(100, 100, 300, 139, "ab"), (310, 100, 330, 139, "cd"), (340, 100, 400, 139, "efghijklmno")]
    assert mod._rebuild_page(page, fonts, [], 1, [("(", 302, 100, 308, 140)], words).strip() == "ab (cd efghijklmno"
    assert mod._rebuild_page(page, fonts, [], 1, [("(", 302, 100, 308, 140)]).strip() == "ab cd (efghijklmno"
    # A bar a tenth as thick as its text, a piece of TeX's \underbrace, is no fraction bar.
    page = mod.ET.fromstring("<page number='1' width='900' height='1200'>" + "".join([
        run(100, 100, 10, 16, 0, "a"), run(122, 100, 10, 16, 0, "b")]) + "</page>")
    fonts = {"0": (18, "AB+CMR12")}
    assert mod._rebuild_page(page, fonts, [(98, 112, 119, 0.4)], 1).strip() == "a/b"
    assert mod._rebuild_page(page, fonts, [(98, 112, 119, 2.2)], 1).split() == ["a", "b"]
    assert mod._word_boxes('<page width="960.0" height="540">\n<word xMin="1.5" yMin="2" xMax="3" yMax="4">a&amp;b</word>'
                           '</page><page width="612">') == [(960.0, [(1.5, 2.0, 3.0, 4.0, "a&b")]), (612.0, [])]


def test_rebuilt_pages_kept():
    # The rebuilt pages are kept beside the PDF with its size, and read back only
    # while the PDF is that size; get_file's text then takes them for those pages.
    with tempfile.TemporaryDirectory() as tmp:
        notes = Path(tmp) / "notes.pdf"
        notes.write_bytes(b"%PDF fake")
        side = Path(tmp) / ("notes.pdf" + mod.MATH_TEXT)
        side.write_text(json.dumps({"size": notes.stat().st_size, "pages": {"2": "x_i"}}), encoding="utf-8")
        assert mod._math_text(notes) == {"2": "x_i"}
        assert mod._numbered_pages("one\ftwo xi\f", None, mod._math_text(notes)) == ["[Page 1]\none", "[Page 2]\nx_i"]
        notes.write_bytes(b"%PDF fake, downloaded again")
        assert mod._math_text(notes) == {}
        mod._write_math_text(notes, "", set(), [], 0.0)  # no math pages: no file
        assert not side.exists() and mod._math_text(notes) == {}
    assert mod._math_pages("") == (set(), 0)


def test_drawn_brackets_and_svg_figures():
    # Word and PowerPoint draw a formula's growing brackets as filled outlines:
    # "(" and ")" in one path, closed by a lone moveto; "{", "]" and a "|". A T
    # (a logo's letter), a squat curve or a path with a part that is no bracket
    # is none, nor is an outline drawn unfilled.
    paren = "M 10 0 C 2 10 2 30 10 40 L 12 40 C 5 30 5 10 12 0 Z M 32 0 C 40 10 40 30 32 40 L 30 40 C 37 30 37 10 30 0 Z M 30 0"
    brace = ("M 10 0 C 5 0 5 2 5 8 L 5 16 L 0 20 L 5 24 L 5 32 C 5 38 5 40 10 40 L 10 38 C 7 38 7 36 7 32 "
             "L 7 24 L 2 20 L 7 16 L 7 8 C 7 4 7 2 10 2 Z")
    shapes = {paren: "()", brace: "{", "M 0 0 L 6 0 L 6 40 L 0 40 L 0 38 L 4 38 L 4 2 L 0 2 Z": "]",
              "M 0 0 L 2 0 L 2 30 L 0 30 Z": "|", "M 0 0 L 12 0 L 12 3 L 7.5 3 L 7.5 30 L 4.5 30 L 4.5 3 L 0 3 Z": "",
              "M 0 0 C 8 0 8 10 0 10 L 0 8 C 6 8 6 2 0 2 Z": "", paren + " M 50 0 L 60 0 L 60 3 Z": ""}
    for d, want in shapes.items():
        got = mod._delimiters(mod.ET.fromstring(f'<path fill="black" d="{d}"/>'))
        assert "".join(c for c, *_ in got) == want, (d, got)
    assert mod._delimiters(mod.ET.fromstring(f'<path fill="none" stroke="black" d="{brace}"/>')) == []
    assert mod._delimiters(mod.ET.fromstring(f'<path fill="black" d="{brace}"/>'))[0][1:] == (0, 0, 10, 40)

    # pdftocairo -svg, 4 pages: a rounded box and a header rule (the template)
    # and a bullet on every page; a graph's node and edges on page 2; a diagonal
    # drawn through a transform on page 3; only a table's level lines on page 4,
    # and a formula's drawn brackets, which are no figure.
    brackets = f'<path fill="rgb(0%, 0%, 0%)" transform="matrix(1, 0, 0, 1, 300, 150)" d="{paren}"/>'
    template = ('<path d="M 10 10 C 10 5 15 5 20 5 L 400 5 C 405 5 405 10 405 10 L 405 100 L 10 100 Z"/>'
                '<path d="M 0 2 L 450 2"/><path d="M 30 50 L 34 52 L 30 54 Z"/>')
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="454" height="255"><defs>
<path d="M 0 0 L 9 9 L 0 9 Z"/></defs><pageSet>
<page>{template}</page>
<page>{template}<path d="M 100 100 C 104 100 104 108 100 108 C 96 108 96 100 100 100 Z"/>
<path d="M 100 100 L 160 150 L 220 100"/></page>
<page>{template}<path transform="matrix(0.5, 0, 0, -0.5, 100, 200)" d="M 0 0 L 80 60"/></page>
<page>{template}<path d="M 50 150 L 250 150 M 50 170 L 250 170 M 150 140 L 150 180"/>{brackets}</page>
</pageSet></svg>"""
    with tempfile.TemporaryDirectory() as tmp:
        drawn = Path(tmp) / "pages.svg"
        drawn.write_text(svg, encoding="utf-8")
        assert mod._drawing_pages(drawn)[0] == {2, 3}, mod._drawing_pages(drawn)
        assert mod._drawing_pages(drawn)[2] == 454.0
        assert mod._drawing_pages(drawn)[3] == [[], [], [], [("(", 302, 150, 312, 190), (")", 330, 150, 340, 190)]]
        # A boxed answer's top and bottom, met by an upright line at both
        # ends, are no fraction bars; a bar on its own, or one upright at a
        # single end, stays a rule.
        line = '<path fill="none" stroke="black" stroke-width="0.4" d="M {} {} L {} {}"/>'
        box = "".join(line.format(*p) for p in ((400, 160, 458, 160), (400, 176, 458, 176),
                                                (400, 160, 400, 176), (458, 160, 458, 176)))
        drawn.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="595">{box}'
                         f'{line.format(100, 168, 140, 168)}{line.format(200, 168, 240, 168)}'
                         f'{line.format(200, 160, 200, 176)}</svg>', encoding="utf-8")
        assert mod._drawing_pages(drawn)[1] == [[(100, 140, 168, 0), (200, 240, 168, 0)]], mod._drawing_pages(drawn)[1]
        drawn.write_text("<svg", encoding="utf-8")
        assert mod._drawing_pages(drawn) == (set(), [], 0.0, [])
