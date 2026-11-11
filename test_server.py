"""Self-checks for the pure logic in server.py. Run: python3 test_server.py"""
import ast
import configparser
import datetime as datetime_module
import io
import json
import os
from urllib.parse import quote, unquote, urlparse
import re
import subprocess
import tempfile
import time
import types
import zipfile
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path

# Load the functions under test without importing the mcp dependency.
tree = ast.parse(open("server.py").read())
wanted = {"_flatten", "_profiles", "_term", "_pdf_text", "_TextExtractor",
          "_html_text", "_urn_kind", "_ooxml_text", "_description",
          "_calendar_lines", "_local", "_safe", "_file_topics", "_index_html",
          "_d2l_file", "_linked_dest", "_relink",
          "_search_pages", "_search_entry", "_landing_html",
          "_each", "_status", "_summary", "_code",
          "_copies", "_copy", "_saved_text", "_file_text", "_decode",
          "_numbered_pages"}
constants_extra = {"OOXML_MEMBERS"}
constants = {"COLUMNS", "BASE", "QUICKLINK", "REFERENCE", "INDEX_CSS", "INDEX_JS", "LANDING_JS", "OOXML_KINDS", "BROWSERS", "SEASONS", "SEASON_NAMES", "CODE_SEASONS", "TERM_IN_NAME", "OOXML_MEMBERS", "TEXT_LIMIT"}


def needed(node):
    if getattr(node, "name", None) in wanted:
        return True
    targets = getattr(node, "targets", [])
    return any(getattr(t, "id", None) in constants for t in targets)


mod = types.ModuleType("m")
mod.__dict__.update(
    configparser=configparser,
    mimetypes=__import__("mimetypes"),
    os=os,
    re=re,
    subprocess=subprocess,
    Path=Path,
    HTMLParser=HTMLParser,
    zipfile=zipfile,
    unescape=unescape,
    escape=escape,
    json=json,
    quote=quote,
    unquote=unquote,
    urlparse=urlparse,
    datetime=datetime_module.datetime,
)
exec(
    compile(ast.Module([n for n in tree.body if needed(n)], []), "server.py", "exec"),
    mod.__dict__,
)

# --- _flatten -------------------------------------------------------------
toc = [
    {
        "ModuleId": 1,
        "Title": "Week 1",
        "Description": {"Text": "Tuesday 1/09", "Html": ""},
        "Topics": [
            {"TopicId": 11, "Title": "Slides (2026)", "TypeIdentifier": "File"},
            # Copied from last year without its file: counted, not listed.
            {"TopicId": 10, "Title": "Slides (2025)", "TypeIdentifier": "File", "IsBroken": True},
        ],
        "Modules": [
            {
                "ModuleId": 2,
                "Title": "Exercises",
                "Topics": [
                    # An internal quicklink is relative and stays hidden.
                    {
                        "TopicId": 12,
                        "Title": "Sheet",
                        "TypeIdentifier": "Link",
                        "Url": "/d2l/common/dialogs/quickLink/quickLink.d2l?ou=1",
                    },
                    # A link to the course's own site is public and worth showing.
                    {
                        "TopicId": 13,
                        "Title": "Notes",
                        "TypeIdentifier": "Link",
                        "Url": "https://courses.compute.dtu.dk/02242/",
                    },
                ],
            }
        ],
    },
    {"ModuleId": 3, "Title": "Empty"},
]
assert mod._flatten(toc) == [
    "# [1] Week 1 (info) [1 broken topic not listed]",
    "  - [11] Slides (2026) (File)",
    "  # [2] Exercises",
    "    - [12] Sheet (Link)",
    "    - [13] Notes (Link) https://courses.compute.dtu.dk/02242/",
    "# [3] Empty",
], mod._flatten(toc)
assert mod._flatten(None) == []

# A unit's own text prefers the plain rendition and falls back to the HTML one.
assert mod._description({"Description": {"Text": " Tuesday ", "Html": "<p>x</p>"}}) == "Tuesday"
assert mod._description({"Description": {"Text": "", "Html": "<p>Week <b>1</b></p>"}}) == "Week 1"
assert mod._description({}) == ""

# --- _ooxml_text ----------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    docx = Path(tmp) / "a.docx"
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr(
            "word/document.xml",
            "<w:document><w:body><w:p><w:r><w:t>Cover &amp; Page</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Second line</w:t></w:r></w:p></w:body></w:document>",
        )
        z.writestr("docProps/app.xml", "<Properties><Company>ignored</Company></Properties>")
    word = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    # Paragraphs become lines, entities are decoded, other members are ignored.
    assert mod._ooxml_text(docx, word) == "Cover & Page\nSecond line", mod._ooxml_text(docx, word)
    # A content type that is not OOXML is not this function's business.
    assert mod._ooxml_text(docx, "application/pdf") is None
    # Something claiming to be OOXML but not a zip fails softly.
    plain = Path(tmp) / "b.docx"
    plain.write_bytes(b"not a zip")
    assert mod._ooxml_text(plain, word) is None

# --- _html_text -----------------------------------------------------------
# Script and style contents are dropped; block tags become line breaks.
assert mod._html_text(
    "<html><head><style>p{color:red}</style></head>"
    "<body><h2>Deadlines</h2><p>Task 1</p><p>30 September</p>"
    "<script>var x=1</script></body></html>"
) == "Deadlines\nTask 1\n30 September"
assert mod._html_text("<p>  spaced   out  </p>") == "spaced out"
assert mod._html_text("") == ""

# --- _urn_kind ------------------------------------------------------------
assert (
    mod._urn_kind(
        "d2l:brightspace:content:eu-west-1:c58c6536:video:1d2f9826/latest"
    )
    == "video"
)
assert mod._urn_kind("https://example.com/thing") == "media item"
assert mod._urn_kind("") == "media item"

# --- _pdf_text ------------------------------------------------------------
# Anything pdftotext cannot read yields None, so get_file hands over the file.
assert mod._pdf_text(Path("/nonexistent/nope.pdf")) is None
with tempfile.NamedTemporaryFile(suffix=".pdf") as empty:
    assert mod._pdf_text(Path(empty.name)) is None

# --- _term ----------------------------------------------------------------
def term(name, code=""):
    return mod._term({"Name": name, "Code": code})


# The name wins whenever it carries a term, in any of the spellings DTU uses.
assert term("02249 Computationally Hard Problems, Fall 2026") == (2026, 3)
assert term("02157 Functional Programming Fall 23") == (2023, 3)
assert term("47203 Design-Build 3: solar cooker Jan 24") == (2024, 0)
assert term("27016 Devices for measuring cell growth, January 2025") == (2025, 0)
assert term("10240 Design-Build 2: catalyst design Jun 23") == (2023, 2)
assert term("42620 Science, technology and society, Summer 2024") == (2024, 2)
assert term("02135 Introduction to Cyber Systems Spring 23") == (2023, 1)
assert term("TA Education (f2f), 31st of August - Fall 2023") == (2023, 3)

# A course spanning two terms is filed under the one it starts in.
assert term("01006 Advanced Engineering Mathematics 1 Fall22-Spring23") == (2022, 3)

# With no term in the name, fall back to the offering code.
assert term("02158 Concurrent Programming E24", "DTU_e24_02158") == (2024, 3)
assert term("02105 Algorithms 1", "DTU_f24_02105") == (2024, 1)
assert term("22400 Design-build 4", "DTU_s24_22400") == (2024, 2)

# The code's year is the academic year, whose January is the next calendar year.
assert term("27016 Devices", "DTU_v25_27016") == (2026, 0)

# Neither source has a term.
assert term("Module 0 - Your Digital Platforms at DTU", "OFFERING-132988") == (0, -1)
assert term("01006 Adv. Eng. Math. 1/GENENG 3", "OFFERING-666591") == (0, -1)

# A bare number after a season word is not a year.
assert term("Spring 5 Framework Basics") == (0, -1)

# Newest term sorts first, and within a year the latest season leads.
assert sorted([(2025, 1), (2026, 3), (0, -1), (2026, 0)], reverse=True) == [
    (2026, 3),
    (2026, 0),
    (2025, 1),
    (0, -1),
]

# --- _profiles ------------------------------------------------------------
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

# --- _calendar_lines ------------------------------------------------------
os.environ["TZ"] = "Europe/Copenhagen"
time.tzset()  # the tool prints local time, so pin the zone the events were booked in

events = [
    # Out of order on purpose: the day headings depend on sorting them first.
    {"StartDateTime": "2026-09-21T19:00:00.000Z", "EndDateTime": "2026-09-21T19:00:00.000Z",
     "IsAllDayEvent": False, "Title": "Optional Hand-in Exercise 3", "LocationName": "",
     "OrgUnitName": "02249 Computationally Hard Problems, Fall 2026",
     "AssociatedEntity": {"AssociatedEntityType": "D2L.LE.Dropbox.Dropbox"}},
    {"StartDateTime": "2026-09-21T06:00:00.000Z", "EndDateTime": "2026-09-21T08:15:00.000Z",
     "IsAllDayEvent": False, "Title": "Lecture", "LocationName": "LYN.306.AUD35.R:135",
     "OrgUnitName": "02231 Cryptography fundamentals, Fall 2026", "AssociatedEntity": None},
    {"StartDateTime": "2026-09-22T06:00:00.000Z", "EndDateTime": "2026-09-22T06:00:00.000Z",
     "IsAllDayEvent": True, "Title": "Project week", "LocationName": " ".join("room" for _ in range(20)),
     "OrgUnitName": "Study line", "AssociatedEntity": None},
]
assert mod._calendar_lines(events) == [
    "\n## Mon 21 Sep 2026",
    "08:00-10:15  02231  Lecture — LYN.306.AUD35.R:135",
    "21:00  02249  due: Optional Hand-in Exercise 3",
    "\n## Tue 22 Sep 2026",
    "all day  Study line  Project week — " + "room " * 11 + "ro...",
], mod._calendar_lines(events)

# --- _file_topics ---------------------------------------------------------
tree = [
    {
        "Title": "Week 1",
        "Topics": [
            {"TopicId": 1, "Title": "Slides", "TypeIdentifier": "File"},
            # Copied from last year without its file: there is nothing to fetch.
            {"TopicId": 2, "Title": "Old slides", "TypeIdentifier": "File", "IsBroken": True},
            {"TopicId": 3, "Title": "Notes", "TypeIdentifier": "Link"},
        ],
        "Modules": [
            {"Title": "Exercises: sheet 1/2", "Topics": [
                {"TopicId": 4, "Title": "Sheet", "TypeIdentifier": "File"}]},
        ],
    },
    {"Title": "Empty"},
]
assert [(p, t["TopicId"]) for p, t in mod._file_topics(tree)] == [
    (("Week 1",), 1),
    (("Week 1", "Exercises_ sheet 1_2"), 4),
], list(mod._file_topics(tree))

# --- _safe ----------------------------------------------------------------
assert mod._safe("Lecture 1/2: a*b?") == "Lecture 1_2_ a_b_"
assert mod._safe("...") == "untitled"
assert len(mod._safe("x" * 200)) == 80
assert mod._safe("a" * 78 + ", Fall") == "a" * 78

# --- _index_html ----------------------------------------------------------
page = mod._index_html(
    "Course <1>", 7,
    [{"ModuleId": 1, "Title": "Week 1",
      "Description": {"Text": "", "Html": '<p>Room <b>101</b></p><img src="/content/x.png"><a href="//cdn.x/y">'},
      "Topics": [
          {"TopicId": 11, "Title": "Slides", "TypeIdentifier": "File"},
          {"TopicId": 12, "Title": "Site", "TypeIdentifier": "Link", "Url": "https://x.dk"},
          {"TopicId": 13, "Title": "Old", "TypeIdentifier": "File", "IsBroken": True},
      ],
      "Modules": [{"ModuleId": 2, "Title": "Sub", "Topics": []}]}],
    [{"Id": 5, "Title": "Hi", "StartDate": "2026-09-01T10:00",
      "Body": {"Text": "a < b", "Html": ""},
      "Attachments": [{"FileId": 9, "FileName": "a.pdf"}, {"FileId": 8, "FileName": "b.pdf"}]}],
    {11: "Week%201/Slides.pdf", (5, 9): "Announcements/5-a.pdf"},
    {11: ["Intro to RSA", "Proof of RSA"], (5, 9): ["Homework one"]},
)
page, docs = page
for want in [
    "<title>Course &lt;1&gt;</title>",
    "<p>Room <b>101</b></p>",                       # unit HTML kept as is
    '<img src="https://learn.inside.dtu.dk/content/x.png">',  # D2L path made absolute
    '<a href="//cdn.x/y">',                         # protocol-relative left alone
    '<a href="Week%201/Slides.pdf">Slides</a><span class="tag">PDF</span></li>',  # saved file, local link
    '<a href="https://x.dk">Site</a><span class="tag online">Link · online</span>',
    '/d2l/le/lessons/7/topics/13">Old</a><span class="tag online">File, broken · online</span>',
    # nested unit one level down, under links to the units above it
    '<section id="u2">\n<p class="crumbs"><a href="#u1">Week 1</a></p>\n<h3>Sub</h3>',
    # sidebar tree: folded, linking to the sections
    '<li><details><summary><a href="#u1">Week 1</a></summary><ul><li><a href="#u2">Sub</a></li></ul></details></li>',
    '<a class="back" href="../index.html">',
    "Announcements (1)</label>",
    "<p>a &lt; b</p>",                              # plain text escaped
    '<a href="Announcements/5-a.pdf">a.pdf</a>',
    'b.pdf<span class="tag online">download failed</span>',
]:
    assert want in page, (want, page)
assert "No announcements." in mod._index_html("C", 7, [], [], {}, {})[0]
assert '<script src="_search.js"></script>' in page

# Search entries, in course order: units, their topics, then announcements.
# A PDF's pages are joined with the offset each starts at.
assert docs == [
    {"t": "Week 1", "h": "#u1", "k": "unit", "x": "Room 101"},
    {"t": "Slides", "h": "Week%201/Slides.pdf", "k": "PDF",
     "x": "Intro to RSA Proof of RSA", "p": [0, 13], "u": "Week 1"},
    {"t": "Site", "h": "https://x.dk", "k": "Link · online", "x": "", "u": "Week 1"},
    {"t": "Old", "h": mod.BASE + "/d2l/le/lessons/7/topics/13", "k": "File, broken · online",
     "x": "", "u": "Week 1"},
    {"t": "Sub", "h": "#u2", "k": "unit", "x": "", "u": "Week 1"},
    {"t": "Hi", "h": "#n5", "k": "announcement", "x": "a < b"},
    {"t": "a.pdf", "h": "Announcements/5-a.pdf", "k": "attachment", "x": "Homework one"},
], docs

# --- _search_pages ----------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    pptx = Path(tmp) / "s.pptx"
    with zipfile.ZipFile(pptx, "w") as z:
        z.writestr("ppt/slides/slide1.xml", "<p:sld><a:p><a:t>Block   ciphers</a:t></a:p><a:p><a:t>AES</a:t></a:p></p:sld>")
    assert mod._search_pages(pptx) == ["Block ciphers AES"], mod._search_pages(pptx)
    html = Path(tmp) / "p.html"
    html.write_text("<h1>Deadlines</h1><p>Task 1:\n 3 Oct</p>")
    assert mod._search_pages(html) == ["Deadlines Task 1: 3 Oct"], mod._search_pages(html)
    empty = Path(tmp) / "e.html"
    empty.write_text("<p> </p>")
    assert mod._search_pages(empty) == []
    assert mod._search_pages(Path(tmp) / "movie.mp4") == []

# --- _d2l_file, _linked_dest, _relink ---------------------------------------
B = mod.BASE
assert mod._d2l_file("/content/enforced/1-X/a.png") == B + "/content/enforced/1-X/a.png"
assert mod._d2l_file(B + "/content/a.pdf?ou=1&amp;x=2") == B + "/content/a.pdf?ou=1&x=2"
assert mod._d2l_file(B + "/d2l/common/dialogs/quickLink/quickLink.d2l?ou=1") is None
# A short link to a course file is one: DTU Learn redirects it to the file.
ql = "/d2l/common/dialogs/quickLink/quickLink.d2l?ou=1&amp;type=coursefile&amp;fileId=Notes.pdf"
assert mod._d2l_file(ql) == B + ql.replace("&amp;", "&"), mod._d2l_file(ql)
assert mod._d2l_file(B + "/d2l/common/dialogs/quickLink/quickLink.d2l?ou=1&type=content&rcode=x") is None
assert mod._d2l_file("content/a.pdf") is None      # relative: already local in the index
assert mod._d2l_file("https://elsewhere.dk/content/a.png") is None

root = Path("/r")
assert mod._linked_dest(root, B + "/content/enforced/1-X/W%201/a%20b.pdf") == \
    root / "_linked/enforced/1-X/W 1/a b.pdf"
assert mod._linked_dest(root, B + "/content/") == root / "_linked/untitled"

page = '<img src="https://learn.inside.dtu.dk/content/p.png"> <a href="/content/f.pdf">f</a> <a href="https://x.dk">x</a>'
got = mod._relink(page, root / "Week 1/page.html", {
    B + "/content/p.png": root / "_linked/p.png",
    B + "/content/f.pdf": root / "_linked/my f.pdf",
})
assert got == '<img src="../_linked/p.png"> <a href="../_linked/my%20f.pdf">f</a> <a href="https://x.dk">x</a>', got

# --- _landing_html ---------------------------------------------------------
land = mod._landing_html([
    ("10060 Physics, Spring 2025", "10060", "2025-06-01"),
    ("Odd <course> </script>", "Odd", "2026-01-01"),
    ("02231 Crypto, Fall 2026", "02231%20Crypto", "2026-11-11"),
])
# Newest term first, a course with no term last, names escaped.
assert land.index("<h2>Fall 2026</h2>") < land.index("<h2>Spring 2025</h2>") < land.index("<h2>No term</h2>"), land
assert '<a href="02231%20Crypto/index.html">02231 Crypto, Fall 2026</a><span class="tag">saved 2026-11-11</span>' in land
assert "Odd &lt;course&gt;" in land
# Each course's search data is loaded and taken in turn, then searched as one.
assert '<script src="02231%20Crypto/_search.js"></script><script>take("02231%20Crypto", "02231 Crypto, Fall 2026")</script>' in land
assert 'take("Odd", "Odd <course> <\\/script>")' in land   # a name cannot close the script
assert land.index("function take") < land.index("_search.js") < land.index("window.SEARCH = all;")
assert "No courses downloaded yet." in mod._landing_html([])

# --- _each ------------------------------------------------------------------
# Results come back in order, and the count is reported from 0 up to the total.
said = []
assert mod._each(types.SimpleNamespace(map=map), lambda x: x * 2, [3, 1, 2], "files",
                 lambda *a: said.append(a)) == [6, 2, 4]
assert said == [("files", 0, 3), ("files", 1, 3), ("files", 2, 3), ("files", 3, 3)], said
said.clear()
assert mod._each(types.SimpleNamespace(map=map), str, [], "search text", lambda *a: said.append(a)) == []
assert said == [("search text", 0, 0)], said

# --- _status ----------------------------------------------------------------
# Parts are cut to the terminal's width by their text, not their colour codes,
# and nothing is written when stderr is not a terminal.
class Tty(io.StringIO):
    def isatty(self):
        return True

mod.shutil = types.SimpleNamespace(get_terminal_size=lambda: os.terminal_size((11, 24)))
os.environ.pop("NO_COLOR", None)
mod.sys = types.SimpleNamespace(stderr=Tty())
mod._status(("32", "✓ "), ("", "02231 Cryptography"), ("2", " · more"), keep=True)
assert mod.sys.stderr.getvalue() == "\r\033[K\033[32m✓ \033[0m02231 Cr\033[2m\033[0m\n", repr(mod.sys.stderr.getvalue())
os.environ["NO_COLOR"] = "1"
mod.sys = types.SimpleNamespace(stderr=Tty())
mod._status(("36", "██"), ("1", "files 3/9"))
assert mod.sys.stderr.getvalue() == "\r\033[K██files 3/", repr(mod.sys.stderr.getvalue())
del os.environ["NO_COLOR"]
mod.sys = types.SimpleNamespace(stderr=io.StringIO())
mod._status(("1", "files 3/9"))
assert mod.sys.stderr.getvalue() == ""

# --- _summary ----------------------------------------------------------------
# One aligned row per course, problems under the table, one pointer to the
# landing page. A course that failed outright gets no row.
mod.DOWNLOADS = Path("/dl")
assert mod._code("02249 Computationally Hard Problems, Fall 2026") == "02249"
assert mod._code("Sustainability lab") == "Sustainability lab"
row = dict(files=245, pictures=31, announcements=22, links=101, videos=0, broken=24)
got = mod._summary([
    ("12106", row, [("2 files failed", ["Week 3: 404", "Exam: 404"])]),
    ("02242", None, [("could not download", ["timed out"])]),
])
assert got == """\
         files  pictures  announcements  links  videos  broken
  12106    245        31             22    101       0      24

  ✗ 12106: 2 files failed
      Week 3: 404
      Exam: 404
  ✗ 02242: could not download
      timed out

  Open /dl/index.html to see every course.""", got
assert mod._summary([("02242", None, [("could not download", ["timed out"])])]).startswith("  ✗ 02242")

# --- offline copies ------------------------------------------------------------
# The reading tools find a course's newest copy by its id, and read saved files
# by extension: a MATLAB .m file is text, though guess_type calls it Mathematica.
with tempfile.TemporaryDirectory() as tmp:
    mod.DOWNLOADS = Path(tmp)
    assert mod._copy(326352) is None
    for stamp, folder in enumerate(("Old name", "02231 Cryptography"), 1):
        root = Path(tmp, folder)
        root.mkdir()
        record = root / "_course-326352.json"
        record.write_text(json.dumps({"ou": 326352, "name": folder, "saved": folder,
                                      "modules": [], "news": [], "files": {"7": "Week 1/rsa.m"}}))
        os.utime(record, (stamp, stamp))
    copy = mod._copy(326352)
    assert copy["name"] == "02231 Cryptography" and copy["root"] == root, copy
    assert mod._copy(326350) is None
    (root / "Week 1").mkdir()
    (root / "Week 1" / "rsa.m").write_text("n = p * q; % modulus\n")
    assert mod._saved_text(root / copy["files"]["7"], "rsa") == "n = p * q; % modulus\n"
    (root / "page.html").write_text("<p>Øvelse</p><p>two</p>", encoding="utf-8")
    got = mod._saved_text(root / "page.html", "Page")
    assert got == "[Page]\n\nØvelse\ntwo", got
    (root / "blob.bin").write_bytes(b"\x89PNG\x00\xff")
    got = mod._saved_text(root / "blob.bin", "blob")
    assert got.startswith("No text could be extracted from blob.bin"), got
assert mod._decode("æ".encode("cp1252"), "text/plain; charset=windows-1252") == "æ"
assert mod._decode(b"x", "text/plain; charset=nonsense") == "x"

# pdftotext ends every page with a form feed; get_file numbers them instead,
# so a reader can ask get_pdf_pages for the page with the figure.
got = mod._numbered_pages("\n  Title\n\fP = NP?\n\f\f")
assert got == "[Page 1]\n  Title\n\n[Page 2]\nP = NP?\n\n[Page 3]\n", repr(got)

print("OK")
