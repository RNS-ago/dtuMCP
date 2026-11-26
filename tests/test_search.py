"""Tests for dtumcp.search."""
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

from _package import mod


def test_kinds():
    # search_courses' kind: an item's shown kind, an attachment's extension too,
    # linked for a file the pages link to; asked for by words or a comma list.
    assert mod._kinds("PDF", "Slides", '{"topic": 7}') == {"pdf"}
    assert mod._kinds("File, broken · online", "Exam", '{"topic": 8}') == {"file"}
    assert mod._kinds("Link · online", "Site", '{"topic": 9}') == {"link"}
    assert mod._kinds("attachment", "homework1.PDF", '{"attachment": "1/2"}') == {"attachment", "pdf"}
    assert mod._kinds("XLSX", "data.xlsx", '{"linked": "_linked/x/data.xlsx"}') == {"xlsx", "linked"}
    assert mod._kind_list(" .PDF, xlsx  unit,") == {"pdf", "xlsx", "unit"} and mod._kind_list("") == set()
    assert mod._kind_list(["pdf", ".PPTX"]) == {"pdf", "pptx"} and mod._kind_list([]) == set()


def test_search_courses():
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
            "files": {"11": "Week 1/book.pdf", "12": "Week 1/old.pdf", "13": "Week 1/slides.pdf", "14": "Week 1/zebra.pdf", "3/4": "news/att.pdf"},
            "linked": {}}))
        filler = " ".join(["graphs"] * 300)
        docs = [mod._search_entry("Book", quote("Week 1/book.pdf"), "PDF",
                                  ["Intro to reductions", f"Hamilton cycle {filler}", "3-SAT reduces to HamiltonCycle",
                                   f"a Hamilton cycle again {filler}"], "Week 1"),
                mod._search_entry("Old", quote("Week 1/old.pdf"), "PDF", ["Hamilton cycle"]),
                mod._search_entry("Slides", quote("Week 1/slides.pdf"), "PDF", ["Hamilton cycle", "more"], "Week 1"),
                mod._search_entry("Week 1", "#u5", "unit", ["Read about 3SAT"]),
                mod._search_entry("Notes", quote("news/att.pdf"), "PDF", ["SAT"]),
                mod._search_entry("Gone", "elsewhere.pdf", "PDF", ["Hamilton cycle"]),
                mod._search_entry("ZebraCrossing notes", quote("Week 1/zebra.pdf"), "PDF",
                                  ["stripes", "more stripes", "a zebra again"], "Week 1")]
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
        # A word only in the title, or in a part of it, names no page; one in a
        # page's text names that page.
        assert "[1] ZebraCrossing notes (PDF) — found in its title only, not its text\n" in (got := mod.search_courses("crossing")), got
        assert "found in its title and unit path only" in (got := mod.search_courses("zebracrossing week")), got
        assert "snippet" not in mod._search("crossing")["hits"][0]
        assert "[1] ZebraCrossing notes (PDF, page 3)\n" in (got := mod.search_courses("zebra")), got
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
        assert mod.search_courses("quokka", kind="unit").startswith("1 match:")
        assert mod._search("quokka")["kinds"] == {"unit": 1}  # for the view's filter, counted before kinds keeps to any
        assert mod._search("quokka", kinds={"pdf"})["kinds"] == {"unit": 1} and not mod._search("quokka", kinds={"pdf"})["hits"]
        assert mod.search_courses("wombat", kind="unit").startswith('No matches for "wombat" among items of kind unit')
        try:
            mod.search_courses("quokka", kind="pdf")
            raise AssertionError("an unknown kind is named")
        except RuntimeError as e:
            assert "of kind pdf. The kinds there are: unit." in str(e), e
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
    assert mod._data_dir().parts[-2:] == ("dtumcp", "courses")


def test_index_records():
    # A _search.js that cannot be read now is read again on the next search,
    # not taken for a course with nothing in it; and a copied record
    # ("_course-1 - Copy.json") is no course's and breaks no search.
    with tempfile.TemporaryDirectory() as tmp:
        mod.DOWNLOADS = Path(tmp)
        root = Path(tmp, "02249 Hard Problems")
        root.mkdir()
        record = root / "_course-326400.json"
        record.write_text(json.dumps({"ou": 326400, "name": "02249 Hard Problems", "saved": "x", "news": [],
                                      "modules": [], "files": {}, "linked": {}}))
        (root / "_course-326400 - Copy.json").write_text(record.read_text())
        (root / "_search.js").write_text("window.SEARCH = [{")  # half written
        assert mod.search_courses("quokka").startswith("No matches")
        entry = mod._search_entry("Week 1", "#u5", "unit", ["quokka"])
        (root / "_search.js").write_text(f"window.SEARCH = {json.dumps([entry])};\n")
        assert mod.search_courses("quokka").startswith("1 match:")  # the record unchanged
