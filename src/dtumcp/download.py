"""Downloading courses and terms, in the background, with a status line."""

import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
from datetime import datetime
from collections import Counter
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from itertools import count
from urllib.parse import quote

import anyio
from mcp.server.mcpserver import Context

from .app import DOWNLOADS, LE
from .client import _client, _get
from .courses import _copy
from .pdfmath import _page_marks
from .files import (
    REFERENCE, _course_name, _d2l_file, _dedupe_linked, _file_topics, _relink, _safe,
    _save_attachment, _save_linked, _save_topic,
)
from .index_page import _index_html, _search_entry, _search_pages, _write_landing
from .search import _index, _sync_index
from . import tasks


def _course(ou: int, ctx: Context | None = None) -> dict:
    """A course's offline copy, downloading the course first if it has none.
    Course material is read from the copy once it exists, so the first read
    of a course is slow and every later one is local. For an MCP client the
    download goes through _background, as download_course's does: it shares
    a download already running and leaves a long one in the background."""
    copy = _copy(ou)
    if copy is None:
        if ctx is None:
            _download(ou)
        else:
            # collect=False: a read has no room for other downloads' summaries,
            # so they wait in _waiting for a download tool to give them.
            note = _background(f"course {ou}", f"course {ou}", f"download_course({ou})",
                               lambda report: _summary([_download(ou, report=report)]), ctx, collect=False)
            if _copy(ou) is None:  # still running, or another download is
                raise RuntimeError(f"Course {ou} has no offline copy yet. {note}")
        copy = _copy(ou)
    return copy


# Claude Desktop gives up on a tool call after 140 s, progress or not, and a
# term's download takes minutes. So an MCP call waits this long and then
# leaves the download running; calling again picks it up.
JOB_WAIT = 110
# A finished download's summary waits this long for the call that collects
# it. Later, a call with its key is a new request (a refresh days on, after
# reads used the copy without collecting it), so it downloads again.
JOB_KEEP = 600
_job = {}  # the download running in the background, if any
_waiting = {}  # finished downloads nobody has collected yet, by key
_job_lock = threading.Lock()  # MCP runs sync tools in parallel threads
_course_locks = {}  # one per org unit id: two downloads of a course write the same files


def _start(key: str, label: str, call: str, work) -> dict:
    """Start work(report) in a thread as the background job. Called holding _job_lock."""
    job = {"key": key, "label": label, "call": call, "last": "", "start": time.monotonic(),
           "done": threading.Event()}

    def run():
        try:
            job["result"] = work(lambda text: job.__setitem__("last", text))
        except Exception as e:  # handed to the call that collects it
            job["error"] = e
        finally:
            job["ended"] = time.monotonic()
            job["done"].set()

    _job["now"] = job
    threading.Thread(target=run, daemon=True).start()
    return job


def _background(key: str, label: str, call: str, work, ctx: Context | None, collect: bool = True) -> str:
    """Run work(report) -- a download -- and return what it returns. From
    the command line it simply runs. For an MCP client it runs in a thread,
    one at a time, and the call waits up to JOB_WAIT seconds for it; if it
    is still going, the call says so and the next call with the same key
    waits on, and returns its summary once done. A finished download nobody
    collected does not block another key: it moves to _waiting, where a call
    with its key gets its summary, and a collect call puts every waiting
    summary ahead of its own. A summary left uncollected past JOB_KEEP
    seconds is dropped."""
    if ctx is None:
        return work(lambda text: None)
    with _job_lock:
        stale = lambda j: j["done"].is_set() and time.monotonic() - j["ended"] > JOB_KEEP
        if _job.get("now") and stale(_job["now"]):
            _job.pop("now")
        for old in [k for k, w in _waiting.items() if stale(w)]:
            del _waiting[old]
        job, earlier = _job.get("now"), ""
        if job and job["key"] != key and job["done"].is_set():
            _waiting[job["key"]] = _job.pop("now")
            job = None
        if not job and key in _waiting:  # finished, not yet collected: its result, once
            job = _waiting.pop(key)
        if collect:
            earlier = "".join(
                f"The earlier download of {w['label']} failed: {w['error']}\n\n" if "error" in w
                else f"The earlier download of {w['label']} finished:\n{w['result']}\n\n"
                for w in _waiting.values())
            _waiting.clear()
        if job and job["key"] != key:
            return (f"{earlier}Another download, {job['label']}, is still running. Wait for it with "
                    f"{job['call']} before starting this one.")
        if not job:
            job = _start(key, label, call, work)
    # Run as an MCP task, the call has no client deadline to beat.
    wait = JOB_WAIT if tasks.current.get() is None else float("inf")
    report, sent, until = _reporter(ctx), None, time.monotonic() + wait
    while not job["done"].wait(1) and time.monotonic() < until:
        if job["last"] != sent:
            sent = job["last"]
            report(sent)
    if not job["done"].is_set():
        return (f"{earlier}The download of {label} is still running after {int(time.monotonic() - job['start'])} s"
                f"{': ' + job['last'].strip() if job['last'].strip() else ''}. It goes on in the background. "
                f"Call {call} again for its progress, and its summary once it is done.")
    with _job_lock:
        if _job.get("now") is job:  # a parallel call may have collected it already
            _job.pop("now")
    if "error" in job:
        if earlier:
            raise RuntimeError(f"{earlier}{job['error']}") from job["error"]
        raise job["error"]
    return earlier + job["result"]


COLUMNS = ("files", "pictures", "announcements", "links", "videos", "broken")


def _code(name: str) -> str:
    """The course number a course name starts with, to label its summary row."""
    found = re.match(r"\d{5}\b", name)
    return found.group() if found else name[:20]


def _summary(rows: list) -> str:
    """What the download tools end with: a row of counts per course, then
    what went wrong, then where to browse. rows are _download's results;
    a course that failed outright has None for counts."""
    width = max(len(code) for code, _, _ in rows)
    table = [" " * width + "".join(f"  {c}" for c in COLUMNS)]
    table += [
        code.ljust(width) + "".join(f"  {counts[c]:>{len(c)}}" for c in COLUMNS)
        for code, counts, _ in rows if counts
    ]
    out = [f"  {line}" for line in table] if len(table) > 1 else []
    problems = [
        line
        for code, _, found in rows for head, details in found
        for line in [f"  ✗ {code}: {head}", *(f"      {d}" for d in details)]
    ]
    if problems:
        out += ["", *problems]
    out += ["", f"  Open {DOWNLOADS / 'index.html'} to see every course."]
    return "\n".join(out).lstrip("\n")


def _status(*parts: tuple[str, str], keep: bool = False) -> None:
    """Rewrite the live status line on stderr, or leave it behind with keep.
    parts are (SGR colour code, text) pairs, cut to the terminal's width.
    Only on a terminal: under an MCP client stderr goes to a log, where a line
    per file would only fill it."""
    if not sys.stderr.isatty():
        return
    room, line = shutil.get_terminal_size().columns - 1, ""
    for colour, text in parts:
        text = text[:room]
        room -= len(text)
        line += f"\033[{colour}m{text}\033[0m" if colour and not os.environ.get("NO_COLOR") else text
    print("\r\033[K" + line, end="\n" if keep else "", file=sys.stderr, flush=True)


def _reporter(ctx: Context | None):
    """Send each status line to an MCP client as a progress notification too,
    or as an MCP task's statusMessage. A no-op from the CLI, and in the SDK
    when the client asked for none."""
    if ctx is None:
        return lambda text: None
    if task := tasks.current.get():
        return partial(tasks.progress, task)
    # The spec wants progress to rise with every notification. The counts
    # restart each step, so they ride in the message and this only ticks.
    ticks = count(1)
    # Tools run in an anyio worker thread; the notification goes out on the
    # event loop.
    return lambda text: anyio.from_thread.run(partial(ctx.report_progress, next(ticks), None, text))


def _each(pool, fn, items, phase: str, say) -> list:
    """pool.map that reports "phase 12/245" through say as results come in."""
    items, out = list(items), []
    say(phase, 0, len(items))
    for result in pool.map(fn, items):
        out.append(result)
        say(phase, len(out), len(items))
    return out


def _download(ou: int, prefix: str = "", report=lambda text: None, force: bool = False) -> tuple:
    """download_course's work, for one org unit id. prefix leads the status
    line, so a term download can say which course of how many it is on.
    report gets the same line as plain text, for _reporter. Returns a row
    for _summary: the course number, its counts and its problems. Two
    downloads of one course in this process take turns. force builds the
    copy afresh, so files the course no longer has go too."""
    with _course_locks.setdefault(ou, threading.Lock()):
        try:
            return _fetch_course(ou, prefix, report, force)
        finally:  # a forced download that failed leaves its half-built copy here
            shutil.rmtree(_staging(ou), ignore_errors=True)
            try:
                _staging(ou).parent.rmdir()
            except OSError:
                pass  # absent, or another course's forced download is using it


def _staging(ou: int):
    """Where a forced download builds its copy, a level deeper than _copies
    looks, so the old copy is the one read until the new one is whole."""
    return DOWNLOADS / "_partial" / str(ou)


def _without_hidden(texts: list, hidden: list) -> None:
    """Take text set at size 0 (_hidden_text, as [page, text] pairs) out of
    a file's search text, a string per page, in place."""
    for n, text in hidden:
        if 0 < n <= len(texts or ()):
            texts[n - 1] = texts[n - 1].replace(" ".join(text.split()), "").strip()


def _fetch_course(ou: int, prefix: str, report, force: bool = False) -> tuple:
    name = _course_name(ou)

    def say(what, done=0, total=0):
        bar, tally = [], f" {done}/{total}" if total else ""
        if total:
            filled = 12 * done // total
            bar = [("36", "█" * filled), ("2", "░" * (12 - filled) + " ")]
        # Counts first: a long course name is what gets cut.
        _status(("2", "    " + prefix), *bar, ("1", what + tally), ("2", " · "), ("", name))
        report(f"{prefix}{what}{tally} · {name}")

    say("reading the course")
    modules = _get(f"/d2l/api/le/{LE}/{ou}/content/toc").get("Modules")
    news = _get(f"/d2l/api/le/{LE}/{ou}/news/")
    jobs = list(_file_topics(modules))
    atts = [(n["Id"], a) for n in news for a in n.get("Attachments") or []]

    final = DOWNLOADS / _safe(name)
    root = _staging(ou) if force else final
    shutil.rmtree(_staging(ou), ignore_errors=True)  # left by a process that died mid-download
    root.mkdir(parents=True, exist_ok=True)
    # ponytail: eight at a time; a course is a few hundred files at most, so
    # nothing here needs a rate limiter or a resumable queue.
    with _client() as c, ThreadPoolExecutor(max_workers=8) as pool:
        # Saved by title, so titles one unit shares (also once cut to length,
        # or differing only in case) get their topic ids too.
        place = [tuple(p.lower() for p in parents) + (_safe(t.get("Title", "")).lower(),)
                 for parents, t in jobs]
        shared = Counter(place)
        tasks = [partial(_save_topic, c, ou, root, *job, shared[at] > 1) for job, at in zip(jobs, place)]
        tasks += [partial(_save_attachment, c, ou, root, *job) for job in atts]
        results = _each(pool, lambda task: task(), tasks, "files", say)

        keys = [t["TopicId"] for _, t in jobs] + [(nid, a["FileId"]) for nid, a in atts]
        index = {
            key: quote(p.relative_to(root).as_posix())
            for key, (p, _) in zip(keys, results) if p
        }
        found = _each(pool, lambda r: _search_pages(r[0]) if r[0] else [], results, "search text", say)
        texts = dict(zip(keys, found))
        pdfs = [(key, p) for key, (p, _) in zip(keys, results) if p and p.suffix.lower() == ".pdf"]
        marks = dict(zip((key for key, _ in pdfs),
                         _each(pool, lambda job: _page_marks(job[1]), pdfs, "figures and math", say)))
        for key, found in marks.items():  # hidden text is left out of search too (_hidden_text)
            _without_hidden(texts.get(key), found.get("hidden", ()))
        html, docs = _index_html(name, ou, modules, news, index, texts)
        # The index and every saved HTML page can show pictures and link to
        # files in D2L's file area. Fetch each of those once, then point the
        # pages at the copies.
        pages = {root / "index.html": html}
        for p, _ in results:
            if p and p.suffix.lower() in (".html", ".htm"):
                try:
                    pages[p] = p.read_bytes().decode("utf-8")
                except UnicodeDecodeError:
                    pass  # not ours to mangle; it keeps its online links
        refs = {
            url for html in pages.values() for m in REFERENCE.finditer(html)
            if (url := _d2l_file(m.group(2)))
        }
        linked = dict(_each(pool, lambda url: _save_linked(c, root, url), refs,
                            "pictures and linked files", say))
        saved_files = [p for p, _ in results if p]
        linked = _dedupe_linked({url: dest for url, dest in linked.items() if dest}, saved_files)
        # Linked files have no topic, so they are searchable by their file name;
        # one that is a topic's file is found as that topic already. Two links
        # may reach one file, which is searched once.
        topic_files = set(saved_files)
        own = list(dict.fromkeys(d for d in linked.values() if d not in topic_files))
        # Linked PDFs get their marks now too, as topic PDFs do, so file_text
        # does not work them out again on every open (_stored_marks).
        own_pdfs = [d for d in own if d.suffix.lower() == ".pdf"]
        linked_marks = dict(zip(own_pdfs, _each(pool, _page_marks, own_pdfs,
                                                "figures and math of linked files", say)))

        def linked_text(dest):  # with its hidden text left out, as a topic's is
            found = _search_pages(dest)
            _without_hidden(found, (linked_marks.get(dest) or {}).get("hidden", ()))
            return found

        docs += [
            _search_entry(dest.name, quote(dest.relative_to(root).as_posix()),
                          dest.suffix[1:].upper() or "file", found)
            for dest, found in zip(own, _each(
                pool, linked_text, own, "search text of linked files", say))
            if found
        ]
    say("writing the pages")
    for page, html in pages.items():
        page.write_text(_relink(html, page, linked), encoding="utf-8")
    # A script file rather than JSON: a page opened from disk may load a
    # <script src> but not fetch() a file beside it.
    (root / "_search.js").write_text(
        f"window.SEARCH = {json.dumps(docs, ensure_ascii=False)};\n", encoding="utf-8"
    )
    # What the reading tools answer from once the course is saved: the tree
    # and announcements as D2L sent them, and where each saved file went,
    # keyed by topic id or by "news id/file id". Written last and swapped in
    # whole, so a copy exists only once everything above has been saved.
    record = {
        "ou": ou, "name": name, "saved": f"{datetime.now():%Y-%m-%d %H:%M}",
        "modules": modules, "news": news,
        "files": {
            "/".join(map(str, key)) if isinstance(key, tuple) else str(key):
                p.relative_to(root).as_posix()
            for key, (p, _) in zip(keys, results) if p
        },
        # What each saved PDF's pages hold that their text loses, for
        # get_file's page headings: {"images": [3], "drawing": [5], "math": [...]}.
        # A linked PDF's are kept by its path, as "_linked/...": it has no topic.
        "marks": {"/".join(map(str, key)) if isinstance(key, tuple) else str(key): found
                  for key, found in marks.items()}
                 | {dest.relative_to(root).as_posix(): found for dest, found in linked_marks.items()},
        # Files the course's pages link to, by the link as the page has it:
        # a /content/ address, or a short link to a course file.
        "linked": {url: dest.relative_to(root).as_posix() for url, dest in linked.items()},
        # Videos are downloaded only on demand, by download_video; a refresh
        # keeps the ones still on disk.
        "videos": {
            topic: path for topic, path in ((_copy(ou) or {}).get("videos") or {}).items()
            if (root / path).exists()
        },
    }
    part = root / f"_course-{ou}.json.part"
    part.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    part.replace(root / f"_course-{ou}.json")
    if root != final:
        # The whole new copy takes the old one's place; the old one goes only then.
        old = root.with_name(f"{ou}.old")
        shutil.rmtree(old, ignore_errors=True)
        if final.exists():
            final.rename(old)
        try:
            root.rename(final)
        except OSError:
            if old.exists():
                old.rename(final)  # the old copy back, or the course would have none
            raise
        shutil.rmtree(old, ignore_errors=True)
    _write_landing()
    # Indexed now rather than on the first search, which would wait for it.
    # A failure here costs nothing: the next search indexes the course.
    try:
        say("indexing for search")
        with closing(_index()) as db:
            _sync_index(db)
    except sqlite3.Error:
        pass
    # The course's bar collapses into one line that stays.
    _status(("32", "  ✓ "), ("2", prefix), ("", name), keep=True)

    def topics(modules):
        for m in modules or []:
            yield from m.get("Topics") or []
            yield from topics(m.get("Modules"))

    kinds = Counter("broken" if t.get("IsBroken") else t.get("TypeIdentifier") for t in topics(modules))
    counts = {
        "files": sum(1 for p, _ in results if p),
        "pictures": len(linked),
        "announcements": len(news),
        # Videos are ContentService topics; any other kind is a link of some sort.
        "videos": kinds["ContentService"],
        "broken": kinds["broken"],
        "links": sum(n for kind, n in kinds.items() if kind not in ("File", "ContentService", "broken")),
    }
    problems = []
    failed = [err for p, err in results if err]
    if failed:
        problems.append((f"{len(failed)} files failed", failed))
    if len(refs) > len(linked):
        problems.append((f"{len(refs) - len(linked)} pictures or linked files could not be "
                         "fetched; the pages link to them online", []))
    return _code(name), counts, problems
