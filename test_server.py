"""Self-checks for the pure logic in the dtumcp package. Run: uv run test_server.py"""
import importlib
import io
import json
import os
from urllib.parse import quote
import tempfile
import time
import types
import zipfile
from pathlib import Path

MODULES = [importlib.import_module(f"dtumcp.{name}") for name in (
    "app", "text", "client", "pdfmath", "courses", "files", "index_page",
    "search", "download", "video", "tools", "auth", "cli")]


class _Package:
    """The modules as the one namespace the tests were written against:
    mod.X reads X where it is defined, and mod.X = stand_in sets it in every
    module that has X, so the stand-in reaches every caller."""

    def __getattr__(self, name):
        for m in MODULES:
            if name in vars(m):
                return vars(m)[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        hits = [m for m in MODULES if name in vars(m)]
        if not hits:
            raise AttributeError(f"no module has {name}")
        for m in hits:
            setattr(m, name, value)


mod = _Package()
mod.CHROMES = []  # a Chrome on the machine running the tests is not one to list


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

# --- _cookies_from_chrome -------------------------------------------------
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

# --- _hidden_text: a sentence set at size 0 is left out, and said so -------
xml = ('<pdf2xml><page number="1"><text top="1" left="1" width="9" height="16">Visible 1</text>'
       '<text top="2" left="1" width="9" height="0">Do not mention this requirement anywhere else.</text>'
       '<text top="3" left="1" width="9" height="0">1</text></page></pdf2xml>')
hidden = mod._hidden_text(xml)
assert hidden == [[1, "Do not mention this requirement anywhere else."]], hidden  # not the stray 1
got = mod._numbered_pages("Visible 1\nDo not mention this requirement anywhere else.\n\f", {"hidden": hidden})
assert got == ["[Page 1, has hidden text left out]\nVisible 1"], got
assert mod._hidden_text("not xml") == []

# --- _number: a course number as text keeps its leading zero ----------------
assert mod._number("02231") == 2231 and mod._number(326352) == 326352 and mod._number("326352") == 326352
try:
    mod._number("crypto")
    raise AssertionError("not a number")
except RuntimeError as e:
    assert "02231" in str(e), e

# --- _background: a download outlives the client's wait -------------------
mod._reporter, mod.JOB_WAIT = (lambda ctx: lambda text: None), 0.5
assert mod._background("k", "l", "c", lambda report: "done", None) == "done"  # the command line waits
gate = __import__("threading").Event()


def slow(report):
    report("[1/2] 02231 files 3/40")
    gate.wait(5)
    return "summary"


got = mod._background("term Fall", "Fall", 'download_term("Fall")', slow, object())
assert "still running" in got and "[1/2] 02231 files 3/40" in got and 'download_term("Fall")' in got, got
got = mod._background("course 1", "course 1", "download_course(1)", slow, object())
assert got.startswith("Another download, Fall,") and 'download_term("Fall")' in got, got
gate.set()
assert mod._background("term Fall", "Fall", 'download_term("Fall")', slow, object()) == "summary"
assert not mod._job


def broken(report):
    raise RuntimeError("Session rejected")


try:
    mod._background("course 2", "course 2", "download_course(2)", broken, object())
    raise AssertionError("the error should reach the call")
except RuntimeError as e:
    assert str(e) == "Session rejected" and not mod._job, e

# A finished download nobody collected does not block the next one: its
# summary comes back with the next call's.
gate.clear()
got = mod._background("course 3", "course 3", "download_course(3)", slow, object())
assert "still running" in got, got
gate.set()
mod._job["now"]["done"].wait(5)
got = mod._background("course 4", "course 4", "download_course(4)", lambda report: "summary 4", object())
assert got == "The earlier download of course 3 finished:\nsummary\n\nsummary 4" and not mod._job, got

# A course read with no copy shares download_course's background job, so a
# second read does not start a second download of the course.
saved_copy, saved_download, store, runs = mod._copy, mod._download, {}, []
gate.clear()


def fake_download(ou, prefix="", report=lambda text: None):
    runs.append(ou)
    gate.wait(5)
    store[ou] = {"ou": ou}
    return "02231", None, []


mod._copy, mod._download = store.get, fake_download
for _ in range(2):
    try:
        mod._course(5, object())
        raise AssertionError("the read should say the download is still running")
    except RuntimeError as e:
        assert "Course 5 has no offline copy yet" in str(e) and "download_course(5)" in str(e), e
gate.set()
assert mod._course(5, object()) == {"ou": 5} and runs == [5] and not mod._job, runs

# A read that finds another course's download finished leaves its summary
# waiting: the next download call gets it, and nothing downloads again.
gate.clear()
got = mod._background("course 6", "course 6", "download_course(6)", slow, object())
assert "still running" in got, got
gate.set()
mod._job["now"]["done"].wait(5)
assert mod._course(7, object()) == {"ou": 7} and "course 6" in mod._waiting
got = mod._background("course 6", "course 6", "download_course(6)", lambda report: 1 / 0, object())
assert got == "summary" and not mod._waiting and not mod._job, got
gate.clear()
got = mod._background("course 8", "course 8", "download_course(8)", slow, object())
gate.set()
mod._job["now"]["done"].wait(5)
mod._course(9, object())
got = mod._background("course 10", "course 10", "download_course(10)", lambda report: "summary 10", object())
assert got == "The earlier download of course 8 finished:\nsummary\n\nsummary 10" and not mod._waiting, got
mod._copy, mod._download = saved_copy, saved_download

# Two topics of one unit with one title keep a file each, told apart by id.
saved_fetch = mod._topic_fetch
mod._topic_fetch = lambda ou, topic, client: (types.SimpleNamespace(content=b"x"), "s.txt")
with tempfile.TemporaryDirectory() as tmp:
    plain, _ = mod._save_topic(None, 1, Path(tmp), ("Week 1",), {"TopicId": 7, "Title": "Slides"})
    own, _ = mod._save_topic(None, 1, Path(tmp), ("Week 1",), {"TopicId": 8, "Title": "Slides"}, True)
    assert (plain.name, own.name) == ("Slides.txt", "Slides (8).txt"), (plain, own)
mod._topic_fetch = saved_fetch

# file_text reads a saved file's marks from its course's record: a linked
# file's by path, a topic's file by its topic id; with no record, none.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "02231 Crypto"
    (root / "_linked" / "x").mkdir(parents=True)
    (root / "Week 1").mkdir()
    (root / "_course-1.json").write_text(json.dumps({
        "files": {"7": "Week 1/Slides.pdf"},
        "marks": {"7": {"math": [2]}, "_linked/x/notes.pdf": {"images": [1]}},
    }))
    assert mod._stored_marks(root / "_linked" / "x" / "notes.pdf") == {"images": [1]}
    assert mod._stored_marks(root / "Week 1" / "Slides.pdf") == {"math": [2]}
    assert mod._stored_marks(root / "_linked" / "x" / "other.pdf") is None
    assert mod._stored_marks(Path(tmp) / "loose.pdf") is None

# Linked files: page text marks links to saved files, and get_file opens
# one by its linked path or its link, and nothing the copy does not list.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "12106 Food"
    (root / "_linked" / "enforced").mkdir(parents=True)
    (root / "Task 2").mkdir()
    (root / "_linked" / "enforced" / "FCR salmonids.txt").write_text("feed per kilo")
    (root / "Task 2" / "Info.html").write_text(
        '<p>Use <a href="../_linked/enforced/FCR%20salmonids.txt">this article</a>, '
        '<a href="../Task%202/Slides.pdf">the slides</a> and <a href="https://x.org/">a site</a>.</p>')
    copy = {"root": root, "modules": [{"ModuleId": 3, "Title": "Task 2", "Topics": [],
                                       "Description": {"Text": "See notes", "Html":
                                           '<p>See <a href="/content/enforced/FCR%20salmonids.txt">notes</a></p>'}}],
            "files": {"5": "Task 2/Info.html", "6": "Task 2/Slides.pdf", "1/2": "Announcements/1-a.pdf"},
            "linked": {mod.BASE + "/content/enforced/FCR%20salmonids.txt": "_linked/enforced/FCR salmonids.txt"}}
    note = mod._link_note(copy, root / "Task 2" / "Info.html")
    text = mod._saved_text(root / "Task 2" / "Info.html", "Info", link=note)
    assert ('this article [saved file: linked="_linked/enforced/FCR salmonids.txt"]' in text
            and "the slides [saved file: topic_id 6]" in text and "and a site ." in text), text
    assert mod._link_note(copy)("/content/enforced/FCR%20salmonids.txt") == \
        'saved file: linked="_linked/enforced/FCR salmonids.txt"'
    assert mod._link_note(copy, root / "Task 2" / "Info.html")("../../outside.pdf") is None
    saved_resolve, saved_course = mod._resolve, mod._course
    mod._resolve, mod._course = (lambda ou: 326408), (lambda ou, ctx=None: copy)
    assert mod.get_file(12106, linked="_linked/enforced/FCR salmonids.txt") == "feed per kilo"
    assert mod.get_file(12106, linked=mod.BASE + "/content/enforced/FCR%20salmonids.txt") == "feed per kilo"
    assert "notes [saved file: linked=" in mod.unit_info(12106, 3)
    for bad in ("_linked/../../../etc/passwd", "Task 2/Info.html"):
        try:
            mod.get_file(12106, linked=bad)
            raise AssertionError(f"{bad} is not a linked file")
        except RuntimeError as e:
            assert "No linked file" in str(e), e
    mod._resolve, mod._course = saved_resolve, saved_course

# A server's filename is saved under as a plain name: nothing in it leads
# out of the data folder.
for sent, want in [("..%2F..%2F.bashrc", "_.._.bashrc"), ("/etc/passwd", "_etc_passwd"),
                   ("C:evil.pdf", "C_evil.pdf"), ("a\\\\..\\\\b.pdf", "a_.._b.pdf"),
                   ("..", "topic-7"), ("Week%201.pdf", "Week 1.pdf")]:
    r = mod.httpx.Response(200, headers={"content-disposition": f'attachment; filename="{sent}"'})
    assert mod._filename(r, "topic-7") == want, (sent, mod._filename(r, "topic-7"))

# A course number's offering is kept RESOLVE_FOR seconds, so a new term's
# offering of the number shows up once that runs out.
saved_enrolled = mod._enrolled
offerings = [{"Id": 326352, "Name": "02231 Cryptography, Fall 2026"}]
mod._enrolled = lambda: (offerings, "")
mod._resolved.clear()
assert mod._resolve("02231") == 326352
offerings.append({"Id": 340001, "Name": "02231 Cryptography, Spring 2027"})
assert mod._resolve(2231) == 326352  # still kept
mod._resolved[2231] = (326352, mod.time.monotonic() - mod.RESOLVE_FOR)
try:
    mod._resolve("02231")
    raise AssertionError("two offerings should be named")
except RuntimeError as e:
    assert "several offerings" in str(e) and "340001" in str(e), e
mod._enrolled = saved_enrolled
mod._resolved.clear()

# A PDF page over the text limit is squeezed of layout spaces first, and cut
# with a note only if it is still too long.
saved_pdf_text, saved_math = mod._pdf_text, mod._math_text
mod._math_text = lambda path: None
with tempfile.TemporaryDirectory() as tmp:
    pdf = Path(tmp) / "x.pdf"
    mod._pdf_text = lambda path: "left" + " " * 150_000 + "right\fpage two"
    got = mod._file_text(b"", "application/pdf", "x.pdf", "x", pdf, {}, (1, 1))
    assert "left  right" in got and "is cut here" not in got, got[-200:]
    mod._pdf_text = lambda path: "word " * 30_000 + "\fpage two"
    got = mod._file_text(b"", "application/pdf", "x.pdf", "x", pdf, {}, (1, 1))
    assert got.endswith("[Page 1 is cut here, after 100,000 characters. get_pdf_pages shows it whole.]"), got[-200:]
mod._pdf_text, mod._math_text = saved_pdf_text, saved_math

# Messages name the command to type: the installed one, or uv run in a clone.
assert mod._run_command("/home/u/.local/bin/dtumcp") == "dtumcp"
assert mod._run_command("/home/u/dtuMCP/.venv/bin/dtumcp") == "uv run dtumcp"
assert mod._run_command("-c") == "uv run dtumcp"

# search_serve answers a line it cannot read with an error and goes on.
with tempfile.TemporaryDirectory() as tmp:
    import subprocess, sys
    done = subprocess.run(
        [sys.executable, "-c", "import sys; from dtumcp.cli import main; sys.argv = ['dtumcp', 'search_serve']; main()"],
        input='not json\n[1]\n{"id": 3, "query": "feed"}\n', capture_output=True, text=True, timeout=60,
        env={**os.environ, "DTU_DOWNLOADS": tmp})
    answers = [json.loads(line) for line in done.stdout.splitlines()]
    assert [a["id"] for a in answers] == [None, None, 3] and "error" in answers[0] and "result" in answers[2], done

# --args takes a command's arguments by name, as one JSON object; null is
# the default, and ctx is not the caller's to give.
with tempfile.TemporaryDirectory() as tmp:
    def cli(*argv):
        return subprocess.run(
            [sys.executable, "-c", "import sys; from dtumcp.cli import main; sys.argv[0] = 'dtumcp'; main()", *argv],
            capture_output=True, text=True, timeout=60, env={**os.environ, "DTU_DOWNLOADS": tmp})
    done = cli("search_courses", "--args", '{"limit": 3, "query": "feed", "org_unit_id": null, "ctx": 1}')
    assert done.returncode == 0 and "No course is downloaded yet" in done.stdout, done
    done = cli("--json", "search_courses", "--args", '{"query": "feed"}')
    assert json.loads(done.stdout)[0]["type"] == "text", done
    for bad, says in [("[1]", "takes a JSON object"), ("{", "takes a JSON object"), ('{"query": "x", "nope": 1}', "unexpected keyword")]:
        done = cli("search_courses", "--args", bad)
        assert done.returncode and says in done.stderr, (bad, done)

# A failing page analysis costs the marks, not the file or its course.
saved_marks = mod._marks
mod._marks = lambda path: 1 / 0
assert mod._page_marks(Path("x.pdf")) == {}
mod._marks = saved_marks

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
    # pdftotext's closing form feed is no page of its own.
    real = mod._pdf_text
    try:
        mod._pdf_text = lambda path: "one\f\ftwo\f"
        assert mod._search_pages(Path(tmp) / "n.pdf") == ["one", "", "two"], mod._search_pages(Path(tmp) / "n.pdf")
    finally:
        mod._pdf_text = real

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
got = "\n\n".join(mod._numbered_pages("\n  Title\n\fP = NP?\n\f\f"))
assert got == "[Page 1]\n  Title\n\n[Page 2]\nP = NP?\n\n[Page 3]\n", repr(got)

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
    drawn.write_text("<svg", encoding="utf-8")
    assert mod._drawing_pages(drawn) == (set(), [], 0.0, [])

# The Groups tab: the learner's own group in each category, its members named
# from the class list (whose ids are strings), a category without one marked so.
categories = [
    {"Name": "Project", "EnrollmentStyle": 5, "MaxUsersPerGroup": 3,
     "SelfEnrollmentExpiryDate": "2026-10-06T07:00:00.000Z", "Description": {"Text": "Pick one", "Html": "<p>Pick one</p>"}},
    {"Name": "Feedback", "EnrollmentStyle": 1},
]
groups = [
    [{"Name": "project 1", "Enrollments": [5, 6]}, {"Name": "project 2", "Enrollments": [7, 42, 99]}],
    [{"Name": "feedback 1", "Enrollments": [5]}],
]
classlist = [{"Identifier": "42", "DisplayName": "Me", "Email": "me@dtu.dk", "ClasslistRoleDisplayName": "Student"},
             {"Identifier": "7", "FirstName": "Ada", "LastName": "L", "Email": "ada@dtu.dk", "ClasslistRoleDisplayName": "Student"}]
rows = mod._group_rows(categories, groups, 42, classlist)
assert [r["groups"] for r in rows] == [2, 1] and [r["signup"] for r in rows] == [True, False], rows
assert rows[0]["mine"]["name"] == "project 2" and rows[1]["mine"] is None, rows
# 99 left the course: not in the class list, so not named.
assert [(m["name"], m["me"]) for m in rows[0]["mine"]["members"]] == [("Ada L", False), ("Me", True)], rows[0]
assert rows[0]["description"] == {"text": "Pick one", "html": "<p>Pick one</p>"}
assert mod._rich(None) == {"text": "", "html": ""}

# --- search_courses -------------------------------------------------------------
# The index splits out what its tokenizer does not: humps of joined-up names,
# numbers and letters, and hyphenated words written as one.
assert mod._parts("HamiltonCycle and 3-SAT, plain") == "Hamilton Cycle 3SAT"
assert mod._parts("3SAT økonomiØkonomi") == "3 SAT økonomi Økonomi"
assert mod._terms("reduc 3-SAT O(n) - x") == [("reduc", '"reduc"*'), ("3-SAT", '"3SAT"*'), ("O(n)", '"O n"'), ("x", '"x"')], mod._terms("reduc 3-SAT O(n) - x")
with tempfile.TemporaryDirectory() as tmp:
    mod.DOWNLOADS = Path(tmp)
    assert mod.search_courses("anything").startswith("No course is downloaded yet")
    root = Path(tmp, "02249 Hard Problems")
    root.mkdir()
    record = root / "_course-326400.json"
    record.write_text(json.dumps({
        "ou": 326400, "name": "02249 Hard Problems, Fall 2026", "saved": "x", "news": [],
        "modules": [{"ModuleId": 5, "Title": "Week 1", "Topics": [
            {"TopicId": 11, "Title": "Book"}, {"TopicId": 12, "Title": "Old", "IsBroken": True},
            {"TopicId": 13, "Title": "Slides"}]}],
        "files": {"11": "Week 1/book.pdf", "12": "Week 1/old.pdf", "13": "Week 1/slides.pdf", "3/4": "news/att.pdf"},
        "linked": {}}))
    filler = " ".join(["graphs"] * 300)
    docs = [mod._search_entry("Book", quote("Week 1/book.pdf"), "PDF",
                              ["Intro to reductions", f"Hamilton cycle {filler}", "3-SAT reduces to HamiltonCycle",
                               f"a Hamilton cycle again {filler}"], "Week 1"),
            mod._search_entry("Old", quote("Week 1/old.pdf"), "PDF", ["Hamilton cycle"]),
            mod._search_entry("Slides", quote("Week 1/slides.pdf"), "PDF", ["Hamilton cycle", "more"], "Week 1"),
            mod._search_entry("Week 1", "#u5", "unit", ["Read about 3SAT"]),
            mod._search_entry("Notes", quote("news/att.pdf"), "PDF", ["SAT"]),
            mod._search_entry("Gone", "elsewhere.pdf", "PDF", ["Hamilton cycle"])]
    (root / "_search.js").write_text(f"window.SEARCH = {json.dumps(docs)};\n")
    got = mod.search_courses("hamilton cycle", 2249)
    # The broken topic and the entry pointing at nothing are left out. The
    # short slide beats the book; the book's page with both words in the
    # fewest other words is its best, and the joined-up name is found.
    assert got.startswith("2 matches:"), got
    assert got.index("[1] Slides (PDF, page 1)") < got.index("[2] Book (PDF, page 3; also pages 2, 4)"), got
    assert "org_unit_id 326400, topic_id 13" in got and "02249 Hard Problems, Fall 2026 › Week 1" in got, got
    got = mod.search_courses("3sat hamilton")
    assert got.startswith("1 with every word, then 2 with some of them"), got
    assert "unit_id 5 (unit_info)" in got and "lacks: hamilton" in got, got
    got = mod.search_courses("sat")
    assert "announcement attachment 3, file 4 (get_announcement_attachment)" in got, got
    assert mod.search_courses("nothere").startswith('No matches for "nothere" in the downloaded courses.')
    found = mod._search("cycle", hide_broken=False, partial=False)
    # A snippet's odd parts are its matches; the broken topic counts now.
    slides = next(h for h in found["hits"] if h["title"] == "Slides")
    assert found["total"] == 3 and slides["snippet"] == ["Hamilton ", "cycle", ""], found
    # Indexed again only once the record changes, as a new download leaves it.
    (root / "_search.js").write_text(f"window.SEARCH = {json.dumps([mod._search_entry('Fresh', '#u5', 'unit', ['quokka'])])};\n")
    assert mod.search_courses("quokka").startswith("No matches")
    os.utime(record, (time.time() + 5, time.time() + 5))
    assert mod.search_courses("quokka").startswith("1 match:")
    record.unlink()
    assert mod.search_courses("quokka").startswith("No course is downloaded yet")
    try:
        mod.search_courses("x", 2231)
        assert False
    except RuntimeError as e:
        assert "Course 02231 is not downloaded" in str(e), e

# A linked file that is a topic's file points at the topic's copy and is
# deleted; one that differs, or is empty like some unrelated topic, stays.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    (root / "Week 1").mkdir()
    (root / "_linked").mkdir()
    notes, empty = root / "Week 1" / "notes.pdf", root / "Week 1" / "blank.txt"
    notes.write_bytes(b"%PDF same")
    empty.write_bytes(b"")
    copy, other, hollow = (root / "_linked" / n for n in ("book.pdf", "other.pdf", "none.txt"))
    copy.write_bytes(b"%PDF same")
    other.write_bytes(b"%PDF diff")
    hollow.write_bytes(b"")
    got = mod._dedupe_linked({"a": copy, "b": copy, "c": other, "d": hollow}, [notes, empty])
    assert got == {"a": notes, "b": notes, "c": other, "d": hollow}, got
    assert not copy.exists() and other.exists() and hollow.exists()

# Saved courses live in the platform's data folder.
mod.sys = __import__("sys")  # tests above stand in their own
assert mod._data_dir().parts[-2:] == ("dtumcp", "courses")

# --- _file_text: a PDF with no text says why ------------------------------
with tempfile.TemporaryDirectory() as tmp:
    scan = Path(tmp) / "scan.pdf"
    scan.write_bytes(b"%PDF-1.4 not really")
    real = mod.shutil
    try:
        mod.shutil = types.SimpleNamespace(which=lambda name: None)
        got = mod._file_text(b"", "application/pdf", "scan.pdf", "Scan", scan)
        assert "pdftotext, from poppler, is not installed" in got, got
        mod.shutil = types.SimpleNamespace(which=lambda name: "/usr/bin/" + name)
        got = mod._file_text(b"", "application/pdf", "scan.pdf", "Scan", scan)
        assert "no text layer" in got and "get_pdf_pages" in got, got
    finally:
        mod.shutil = real

    # A PDF's text is whole pages up to TEXT_LIMIT, from first_page to
    # last_page, with where to go on from when it stops early.
    real = mod._pdf_text, mod.TEXT_LIMIT
    try:
        mod._pdf_text, mod.TEXT_LIMIT = (lambda path: "one\ftwo\fthree\ffour\f"), 30
        read = lambda pages: mod._file_text(b"", "application/pdf", "n.pdf", "N", scan, {}, pages)
        got = read((1, 0))
        assert "[Text of n.pdf, pages 1 to 2 of 4." in got and got.endswith(
            "[Page 2]\ntwo\n\n[Text stops after page 2 of 4. Ask for first_page=3 for the rest.]"), got
        got = read((3, 0))
        assert "pages 3 to 4 of 4" in got and got.endswith("[Page 4]\nfour"), got
        assert read((2, 2)).endswith("]\n\n[Page 2]\ntwo"), read((2, 2))
        mod.TEXT_LIMIT = 100
        assert "[Text of n.pdf. " in read((1, 0)) and "Text stops" not in read((1, 0))
        try:
            read((5, 0))
            raise AssertionError("page 5 of 4")
        except RuntimeError as e:
            assert "pages 1 to 4" in str(e), e
    finally:
        mod._pdf_text, mod.TEXT_LIMIT = real

print("OK")
