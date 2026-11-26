"""Tests for dtumcp.files."""
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

from _package import mod


def test_file_topics():
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


def test_safe():
    assert mod._safe("Lecture 1/2: a*b?") == "Lecture 1_2_ a_b_"
    assert mod._safe("...") == "untitled"
    assert len(mod._safe("x" * 200)) == 80
    assert mod._safe("a" * 78 + ", Fall") == "a" * 78
    # Names Windows keeps for devices get a mark, with an extension too.
    assert [mod._safe(n) for n in ("Aux", "con.txt", "LPT1", "Auxiliary", "com.pdf")] == [
        "Aux_", "con_.txt", "LPT1_", "Auxiliary", "com.pdf"]


def test_office_types():
    # Saved Office files are read as Office files without the system's type table
    # (Windows without Office, a slim container): a fresh process hides it.
    got = subprocess.run([sys.executable, "-c", "import mimetypes; mimetypes.knownfiles[:] = []; import dtumcp.files; "
                          "print(*(mimetypes.guess_type('a' + s)[0] for s in ('.xlsx', '.docx', '.pptx')))"],
                         capture_output=True, text=True, check=True).stdout.split()
    assert [t.rsplit(".", 2)[-2] for t in got] == ["spreadsheetml", "wordprocessingml", "presentationml"], got


def test_save_topic():
    # Two topics of one unit with one title keep a file each, told apart by id.
    saved_fetch = mod._topic_fetch
    mod._topic_fetch = lambda ou, topic, client: (types.SimpleNamespace(content=b"x"), "s.txt")
    with tempfile.TemporaryDirectory() as tmp:
        plain, _ = mod._save_topic(None, 1, Path(tmp), ("Week 1",), {"TopicId": 7, "Title": "Slides"})
        own, _ = mod._save_topic(None, 1, Path(tmp), ("Week 1",), {"TopicId": 8, "Title": "Slides"}, True)
        assert (plain.name, own.name) == ("Slides.txt", "Slides (8).txt"), (plain, own)
        # A file the disk refuses fails alone, with what went wrong, and a page
        # keeps its online link to a linked file it refuses.
        saved_save = mod._save

        def refused(dest, content):
            raise PermissionError(13, "Permission denied")

        mod._save = refused
        got = mod._save_topic(None, 1, Path(tmp), ("Week 1",), {"TopicId": 9, "Title": "Open"})
        assert got[0] is None and got[1].startswith("Open: could not be saved at ") and got[1].endswith(
            "Open.txt: Permission denied"), got
        fake = types.SimpleNamespace(get=lambda url: types.SimpleNamespace(
            is_success=True, url=__import__("httpx").URL(mod.BASE + "/content/a/b.pdf"), content=b"x"))
        assert mod._save_linked(fake, Path(tmp), "u") == ("u", None)
        mod._save = saved_save
    mod._topic_fetch = saved_fetch


def test_saved_marks():
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


def test_linked_files():
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


def test_server_filename():
    # A server's filename is saved under as a plain name: nothing in it leads
    # out of the data folder.
    for sent, want in [("..%2F..%2F.bashrc", "_.._.bashrc"), ("/etc/passwd", "_etc_passwd"),
                       ("C:evil.pdf", "C_evil.pdf"), ("a\\\\..\\\\b.pdf", "a_.._b.pdf"),
                       ("..", "topic-7"), ("Week%201.pdf", "Week 1.pdf")]:
        r = mod.httpx.Response(200, headers={"content-disposition": f'attachment; filename="{sent}"'})
        assert mod._filename(r, "topic-7") == want, (sent, mod._filename(r, "topic-7"))


def test_long_pdf_page():
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


def test_d2l_file():
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


def test_offline_copies():
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


def test_file_text():
    # a PDF with no text says why
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
