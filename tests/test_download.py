"""Tests for dtumcp.download."""
import io
import os
import sys
import types
from pathlib import Path

from _package import mod


def test_background():
    # a download outlives the client's wait
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

    # A summary nobody collected within JOB_KEEP is dropped: a call with its key
    # later is a new request and downloads again, and none says it finished.
    for key in ("course 11", "course 12"):
        gate.clear()
        assert "still running" in mod._background(key, key, f"download_course({key[-2:]})", slow, object())
        gate.set()
        mod._job["now"]["done"].wait(5)
        mod._job["now"]["ended"] -= mod.JOB_KEEP + 1
        if key == "course 11":
            got = mod._background(key, key, "download_course(11)", lambda report: "fresh", object())
            assert got == "fresh" and not mod._job, got
        else:  # as a read of another course leaves it
            mod._waiting[key] = mod._job.pop("now")
            assert mod._background("course 13", "course 13", "download_course(13)", lambda report: "13", object()) == "13"
            assert not mod._waiting


def test_each():
    # Results come back in order, and the count is reported from 0 up to the total.
    said = []
    assert mod._each(types.SimpleNamespace(map=map), lambda x: x * 2, [3, 1, 2], "files",
                     lambda *a: said.append(a)) == [6, 2, 4]
    assert said == [("files", 0, 3), ("files", 1, 3), ("files", 2, 3), ("files", 3, 3)], said
    said.clear()
    assert mod._each(types.SimpleNamespace(map=map), str, [], "search text", lambda *a: said.append(a)) == []
    assert said == [("search text", 0, 0)], said


def test_status():
    # Parts are cut to the terminal's width by their text, not their colour codes,
    # and nothing is written when stderr is not a terminal.
    class Tty(io.StringIO):
        def isatty(self):
            return True

    real_shutil, real_sys = mod.shutil, mod.sys  # put back after, for the tests below
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
    mod.shutil, mod.sys = real_shutil, real_sys


def test_summary():
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


def test_force():
    # force empties the course's folder before saving; without it, old files stay.
    mod._course_name = lambda ou: "Forced course"
    mod._get = lambda path, **kw: {"Modules": []} if path.endswith("/toc") else []
    root = mod.DOWNLOADS / "Forced course"
    root.mkdir(parents=True, exist_ok=True)
    (root / "gone.pdf").write_text("old")
    mod._download(21)
    assert (root / "gone.pdf").exists()
    mod._download(21, force=True)
    assert not (root / "gone.pdf").exists() and (root / "_course-21.json").exists()
    assert not mod._staging(21).exists()
    # A forced download that fails partway keeps the old copy and leaves nothing behind.
    (root / "kept.pdf").write_text("old")
    mod._index_html = lambda *a: 1 / 0
    try:
        mod._download(21, force=True)
    except ZeroDivisionError:
        pass
    assert (root / "kept.pdf").exists() and (root / "_course-21.json").exists()
    assert not mod._staging(21).parent.exists()
