"""Tests for dtumcp.index_page."""
import tempfile
import zipfile
from pathlib import Path

from _package import mod


def test_index_html():
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


def test_search_pages():
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


def test_landing_html():
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
