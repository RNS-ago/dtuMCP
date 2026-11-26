"""The MCP tools, which the command line runs too."""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from mcp.server.mcpserver import Context, Image

from .app import BASE, DOWNLOADS, LE, LP, RUN, _tool
from .client import _client, _get, _number
from .courses import (
    SEASON_NAMES, _calendar_lines, _description, _enrolled, _filename, _flatten, _my_events,
    _resolve, _saved_note, _stored_marks, _term, _term_units, _toc_index, _topic_fetch, _urn_kind,
)
from .files import _file_text, _link_note, _linked_file, _safe, _saved_text
from .download import _background, _code, _course, _download, _status, _summary
from .search import _search_answer
from .video import _topic_parents, _video_link, _video_topic


@_tool
def list_courses() -> str:
    """List the DTU Learn courses you are enrolled in, grouped by term, newest first.

    When DTU Learn cannot be reached, lists the courses saved offline instead."""
    units, why = _enrolled()
    groups = {}
    for ou in units:
        groups.setdefault(_term(ou), []).append(f"[{ou['Id']}] {ou['Name']}")

    out = [f"DTU Learn could not be reached: {why}\nThese are the courses saved offline.\n"] if why else []
    for term in sorted(groups, reverse=True):
        year, season = term
        out.append(f"## {SEASON_NAMES[season]} {year}" if year else "## No term")
        out.extend(sorted(groups[term]))
        out.append("")
    return "\n".join(out).strip() or "No course enrollments found."


@_tool
def course_contents(org_unit_id: int | str, ctx: Context | None = None) -> str:
    """Show a course's content tree. Takes an org unit id or a DTU course number.

    The topic ids it prints are what get_file takes. Topics copied from an
    earlier year without their files are left out and counted on their
    unit's line. Course material is read from the offline copy
    download_course saves; a course with none is downloaded first, which
    takes a minute or two.
    """
    copy = _course(_resolve(org_unit_id), ctx)
    lines = _flatten(copy["modules"])
    return "\n".join([_saved_note(copy), "", *lines]) if lines else "Course has no content."


@_tool
def unit_info(org_unit_id: int | str, unit_id: int, ctx: Context | None = None) -> str:
    """Show a unit's own text: its schedule, instructions and notes, plus its topics.

    Unit ids are the bracketed numbers on the `#` lines of course_contents. Only
    units marked `(info)` there have any text of their own.
    """
    copy = _course(_resolve(org_unit_id), ctx)
    unit = _toc_index(copy["modules"])[1].get(unit_id)
    if unit is None:
        raise RuntimeError(
            f"No unit {unit_id} in that course. Run course_contents; unit ids are "
            "the bracketed numbers on the lines beginning with #."
        )
    out = [f"# {unit.get('Title', '?')}", ""]
    out.append(_description(unit, _link_note(copy)) or "(This unit has no text of its own.)")
    topics = _flatten([unit])[1:]  # drop the heading _flatten repeats
    if topics:
        out += ["", "Contents:", *topics]
    return "\n".join(out)


@_tool
def get_file(
    org_unit_id: int | str, topic_id: int = 0, first_page: int = 1, last_page: int = 0,
    linked: str = "", first_row: int = 1, find: str = "", ctx: Context | None = None,
) -> str:
    """Fetch one content topic: a file's text, an HTML page's text, or a link's target.

    Topic ids come from course_contents. PDFs come back as extracted text with
    the saved file's path; other binaries are saved and only the path returned.
    Read from the course's offline copy, like course_contents. A PDF's text
    runs from first_page to last_page (0: its last page), cut after the page
    that reaches about 25k tokens, with a note saying where to go on from.
    An Excel workbook (.xlsx) reads the same way with its sheets as pages:
    it lists them, then gives each a row a line, cells by address with each
    formula after its result; first_row goes on within a long first sheet.
    find gives only a workbook's rows with every word of it (each at the
    start of a word, ignoring case),
    from all its sheets or first_page to last_page: the way to look up a row
    in a data sheet of thousands.
    A page's links to saved files are marked in its text, as [saved file:
    topic_id 7] or [saved file: linked="_linked/..."]. For a file the course's
    pages link to, which has no topic, pass that linked path instead of
    topic_id; search_courses gives it too.
    """
    ou = _resolve(org_unit_id)
    copy = _course(ou, ctx)
    if linked:
        path = _linked_file(copy, linked)
        return _saved_text(path, path.name, _stored_marks(path), (first_page, last_page),
                           _link_note(copy, path), first_row, find)
    topic = _toc_index(copy["modules"])[0].get(topic_id)
    if topic is None:
        raise RuntimeError(
            f"No topic {topic_id} in that course. Run course_contents to see the "
            "topic ids it does have."
        )
    title = topic.get("Title", topic_id)
    kind = topic.get("TypeIdentifier")
    url = topic.get("Url") or ""

    if kind == "Link":
        # An external link is a public page for the caller to fetch; a D2L
        # quicklink is a relative path that only means anything in a browser.
        target = url if url.startswith("http") else f"{BASE}{url}"
        return f"'{title}' is a link, not a file. It points at {target}"

    if kind == "ContentService":
        return (
            f"'{title}' is a {_urn_kind(url)} in Brightspace's media service, so there "
            f"is nothing to extract. Watch it at "
            f"{BASE}/d2l/le/lessons/{ou}/topics/{topic_id}"
        )

    saved = copy["files"].get(str(topic_id))
    if saved:
        return _saved_text(copy["root"] / saved, title, (copy.get("marks") or {}).get(str(topic_id)),
                           (first_page, last_page), _link_note(copy, copy["root"] / saved), first_row, find)
    # Not in the copy: its download failed, so try DTU Learn once more.
    r, name = _topic_fetch(ou, topic)
    return _file_text(r.content, r.headers.get("content-type", ""), name, title, pages=(first_page, last_page),
                      first_row=first_row, find=find)


PAGE_LIMIT = 10  # ponytail: ~2k tokens a page; raise it if real questions need more at once


@_tool
def get_pdf_pages(
    org_unit_id: int | str, topic_id: int = 0, first_page: int = 1, last_page: int = 0,
    linked: str = "", ctx: Context | None = None,
) -> list[str | Image]:
    """Show pages of a PDF topic as images, for what get_file's text loses.

    Figures, diagrams and mathematical notation do not survive text
    extraction. Read the text with get_file first; it numbers the pages as
    [Page n], with what the text loses on it: [Page n, has images, drawing,
    math, unreadable symbols]. A page with unreadable symbols has a font
    whose characters its text cannot name, so its formulas need the image.
    Then ask for only the pages that matter: an image costs about
    2k tokens against roughly 100 for the same page as text. last_page
    defaults to first_page; at most 10 pages at a time. For a file the
    course's pages link to, pass its linked path instead of topic_id.
    """
    last_page = last_page or first_page
    if not 1 <= first_page <= last_page or last_page - first_page >= PAGE_LIMIT:
        raise RuntimeError(
            f"Ask for pages first_page to last_page, counted from 1, at most "
            f"{PAGE_LIMIT} at a time."
        )
    ou = _resolve(org_unit_id)
    copy = _course(ou, ctx)
    if linked:
        path = _linked_file(copy, linked)
        title = path.name
    else:
        path, title = _topic_pdf(ou, copy, topic_id)
    if path.suffix.lower() != ".pdf":
        raise RuntimeError(f"'{title}' is {path.name}, not a PDF. get_file has its text.")
    return _pdf_images(path, title, first_page, last_page)


def _topic_pdf(ou: int, copy: dict, topic_id: int) -> tuple[Path, str]:
    """A File topic's saved file and title, fetched again if its download failed."""
    topic = _toc_index(copy["modules"])[0].get(topic_id)
    if topic is None:
        raise RuntimeError(
            f"No topic {topic_id} in that course. Run course_contents to see the "
            "topic ids it does have."
        )
    title = topic.get("Title", topic_id)
    if topic.get("TypeIdentifier") != "File":
        raise RuntimeError(f"'{title}' is not a file, so it has no pages. get_file says what it is.")
    saved = copy["files"].get(str(topic_id))
    if saved:
        path = copy["root"] / saved
    else:  # not in the copy: its download failed, so try DTU Learn once more
        r, name = _topic_fetch(ou, topic)
        DOWNLOADS.mkdir(parents=True, exist_ok=True)
        path = DOWNLOADS / name
        path.write_bytes(r.content)
    return path, title


def _pdf_images(path: Path, title, first_page: int, last_page: int) -> list[str | Image]:
    """Pages first_page to last_page of a PDF as images, each after its label."""
    with tempfile.TemporaryDirectory() as tmp:
        # 1568 pixels on the long side is as much as a model takes in before
        # scaling an image down, so anything sharper is paid for and lost.
        try:
            subprocess.run(
                ["pdftoppm", "-png", "-scale-to", "1568", "-f", str(first_page),
                 "-l", str(last_page), str(path), f"{tmp}/page"],
                check=True, capture_output=True, timeout=120,
            )
        except FileNotFoundError:
            raise RuntimeError("Showing PDF pages needs pdftoppm, from poppler-utils.")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"pdftoppm could not read {path.name}: {e.stderr.decode().strip()}")
        # page-07.png: zero-padded to the width of the page count, so they sort.
        images = sorted(Path(tmp).glob("page-*.png"))
        if not images:
            raise RuntimeError(f"'{title}' has fewer than {first_page} pages.")
        out = []
        for image in images:
            out += [f"Page {int(image.stem.split('-')[-1])} of '{title}':",
                    Image(data=image.read_bytes(), format="png")]
    return out


@_tool
def download_course(org_unit_id: int | str, ctx: Context | None = None) -> str:
    """Save a course for offline use: its files, unit text and announcements.

    Files land in <course>/<unit>/ in this server's data folder
    (~/.local/share/dtumcp/courses on Linux), on the machine running it, with an index.html beside them that lays the course out like
    its site. Pictures and files the pages link to are fetched as well. Links
    and videos are not files; the index points at them online.
    course_contents, unit_info, get_file and the announcement tools read
    this copy, so run it again to bring them up to date.
    Nothing comes back as text -- for that, ask for the one topic with get_file.
    One that takes over about two minutes goes on in the background: call
    download_course again with the same course for its progress and summary.
    """
    ou = _resolve(org_unit_id)

    def work(report):
        row = _download(ou, report=report)
        _status(keep=True)  # a blank line between the ✓ line and the table
        return _summary([row])

    return _background(f"course {ou}", f"course {org_unit_id}", f"download_course({org_unit_id})", work, ctx)


@_tool
def download_term(term: str = "", ctx: Context | None = None) -> str:
    """Download every course of one term for offline use, as download_course does.

    term is written like the headings of list_courses: "Fall 2026", "Jan 25".
    Left out, it is the newest term you are enrolled in. A course that fails
    is reported and the rest still download. A term takes minutes, so after
    about two minutes the download goes on in the background: call
    download_term again with the same term for its progress and summary.
    """
    want = None
    if term:
        want = _term({"Name": term})
        if want == (0, -1):
            raise RuntimeError(
                f"No term in {term!r}. Write it like the headings of list_courses, "
                "for example 'Fall 2026', 'Spring 2025' or 'Jan 25'."
            )
    units = _term_units(want)
    if not units:
        raise RuntimeError(f"You have no courses in {term or 'any term'}. Run list_courses to see your terms.")

    def work(report):
        rows = []
        for i, ou in enumerate(units, 1):
            prefix = f"[{i}/{len(units)}] "
            try:
                rows.append(_download(ou["Id"], prefix, report))
            except (RuntimeError, httpx.HTTPError) as e:
                rows.append((_code(ou["Name"]), None, [("could not download", [str(e)])]))
                _status(("31", "  ✗ "), ("2", prefix), ("", ou["Name"]), keep=True)
        _status(keep=True)  # a blank line between the ✓ lines and the table
        return _summary(rows)

    label = term or "the newest term"
    return _background(f"term {label}", label, f'download_term("{term}")' if term else "download_term()",
                       work, ctx)


@_tool
def search_courses(query: str, org_unit_id: int | str = 0, limit: int = 15, kind: str | list[str] = "") -> str:
    """Search the downloaded courses for words: titles, unit text, announcements
    and the text inside files, a PDF's page by page. Use it to find where
    something is covered before reading files one by one.
    Each word matches the start of a word or of a part of a joined-up name, so
    "reduc" finds "reductions", "cycle" finds "HamiltonCycle", and "3SAT" and
    "3-SAT" find each other. Matches with every word come first, best first:
    words on one page, in a title, and rare in the courses count for more.
    Then come those with some of the words, each saying which it lacks.
    Course material uses its own terms and abbreviations: when a search comes
    back thin, try those, or fewer words.
    A workbook's sheets are searched only through their first 100k
    characters, a few hundred rows: a row further down a long data sheet is
    not found here, so look it up with get_file's find on that workbook.
    Each match comes with its course, the units above it, its kind, its best
    PDF page and others as good, the ids that get_file, unit_info,
    get_pdf_pages or get_announcement_attachment take, and the text around
    the match.
    org_unit_id limits it to one course (an org unit id or a DTU course
    number); left out, every downloaded course. limit is at most 50.
    kind limits it to kinds of item, one or several ("pdf", "xlsx pptx" or
    ["pdf", "pptx"]): a
    file's extension as its kind shows it (pdf, xlsx, pptx, docx, html,
    txt, ...), or unit, announcement, attachment (also found by its
    extension), linked (a file the pages link to), link or video. Left out,
    every kind.
    Only downloaded courses are searched; download_course adds one.
    """
    return _search_answer(query, org_unit_id, limit, kind)[0]


@_tool
def list_assignments(org_unit_id: int | str) -> str:
    """List assignment folders in a course with their due and closing dates."""
    lines = []
    for f in _get(f"/d2l/api/le/{LE}/{_resolve(org_unit_id)}/dropbox/folders/"):
        due = (f.get("DueDate") or "no due date")[:16]
        closes = ((f.get("Availability") or {}).get("EndDate") or "")[:16]
        # The TotalFiles / TotalUsersWithSubmissions counters on this endpoint are
        # instructor-only; D2L returns -1 to students, so submission status is not
        # reported here rather than guessed from them.
        lines.append(
            f"[{f['Id']}] {f['Name']} — due {due}"
            + (f" — closes {closes}" if closes and closes[:10] != due[:10] else "")
        )
    return "\n".join(lines) or "No assignments."


@_tool
def list_announcements(org_unit_id: int | str, ctx: Context | None = None) -> str:
    """List course announcements (news items), newest first.

    Each one's heading carries its id, and any attached files are listed under
    it with their file ids; get_announcement_attachment takes both. Read from
    the course's offline copy, like course_contents.
    """
    copy = _course(_resolve(org_unit_id), ctx)
    lines = [_saved_note(copy)]
    for n in copy["news"]:
        lines.append(
            f"## [{n['Id']}] {n['Title']} ({(n.get('StartDate') or '')[:10]})\n"
            f"{n['Body']['Text']}"
        )
        lines[-1] += "".join(
            f"\nAttachment [{a['FileId']}] {a['FileName']} ({a.get('Size', '?')} bytes)"
            for a in n.get("Attachments") or []
        )
    return "\n\n".join(lines) if len(lines) > 1 else "No announcements."


@_tool
def get_announcement_attachment(
    org_unit_id: int | str, news_id: int, file_id: int, first_page: int = 1, last_page: int = 0,
    first_row: int = 1, find: str = "", ctx: Context | None = None,
) -> str:
    """Fetch a file attached to an announcement: its text, or where it was saved.

    The ids come from list_announcements: news_id from the heading, file_id
    from the Attachment line. Handled like get_file handles a content file,
    first_page, last_page, first_row and find included.
    """
    ou = _resolve(org_unit_id)
    copy = _course(ou, ctx)
    saved = copy["files"].get(f"{news_id}/{file_id}")
    if saved:
        marks = (copy.get("marks") or {}).get(f"{news_id}/{file_id}")
        return _saved_text(copy["root"] / saved, Path(saved).name, marks, (first_page, last_page),
                           first_row=first_row, find=find)
    with _client() as c:
        r = c.get(f"/d2l/api/le/{LE}/{ou}/news/{news_id}/attachments/{file_id}")
    if r.status_code in (401, 403):
        raise RuntimeError(
            f"Session rejected (HTTP %d). Run '{RUN} login' again."
            % r.status_code
        )
    if r.status_code == 404:
        raise RuntimeError(
            f"No attachment {file_id} on announcement {news_id}. Run "
            "list_announcements for the ids it does have."
        )
    r.raise_for_status()
    name = _filename(r, f"attachment-{file_id}")
    return _file_text(r.content, r.headers.get("content-type", ""), name, name, pages=(first_page, last_page),
                      first_row=first_row, find=find)


@_tool
def calendar(days: int = 7, org_unit_id: int | str = 0) -> str:
    """Show what is scheduled from today to `days` ahead, in local time: lectures,
    exercise sessions, hand-in deadlines and material release dates.

    Covers every course of the current term, or one course when org_unit_id is
    given (an org unit id or a DTU course number)."""
    ids = [_resolve(org_unit_id)] if _number(org_unit_id or 0) else [ou["Id"] for ou in _term_units()]
    if not ids:
        return "No course enrollments found."
    start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    events = _my_events(ids, start, start + timedelta(days=days))
    if not events:
        return f"Nothing scheduled in the next {days} days."
    lines = _calendar_lines(events)
    if len(events) >= 100:
        # D2L stops at 100 events and reports no continuation to page through.
        lines.append("\n(100 events — the API caps it there. Ask for fewer days.)")
    return "\n".join(lines).strip()


def video_link(org_unit_id: int | str, topic_id: int) -> str:
    """Print a short-lived link to play a video topic, for the LearnHarness view.

    Not an MCP tool: the link is a credential for as long as it lasts.
    """
    ou, _, topic = _video_topic(org_unit_id, topic_id)
    return _video_link(ou, topic, "inline")


def download_video(org_unit_id: int | str, topic_id: int) -> str:
    """Download a video topic into its course's offline copy, next to its unit's files.

    Videos are left out of download_course, being large; this fetches one on
    demand. The course's record notes where it went, and a refresh keeps it.
    """
    ou, copy, topic = _video_topic(org_unit_id, topic_id)
    root = copy["root"]
    dest = root.joinpath(*(_topic_parents(copy["modules"], topic_id) or ()), _safe(topic.get("Title", "video")) + ".mp4")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    # DTU_PROGRESS asks for a line per percent on stderr, "progress <bytes>
    # <total>", for the LearnHarness plugin to show; a terminal gets the
    # status line instead.
    report, shown = os.environ.get("DTU_PROGRESS"), -1
    with httpx.stream("GET", _video_link(ou, topic, "attachment"), timeout=60, follow_redirects=True) as r:
        r.raise_for_status()
        total, done = int(r.headers.get("content-length") or 0), 0
        with part.open("wb") as out:
            for chunk in r.iter_bytes(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if total:
                    _status(("1", f"  {topic.get('Title')} "), ("2", f"{done / 1e6:.0f}/{total / 1e6:.0f} MB"))
                    if report and done * 100 // total != shown:
                        shown = done * 100 // total
                        print(f"progress {done} {total}", file=sys.stderr, flush=True)
    part.replace(dest)
    _status(("32", "  ✓ "), ("", topic.get("Title", "")), keep=True)
    # Note it in the course's record, swapped in whole like download_course's.
    record_path = root / f"_course-{ou}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record.setdefault("videos", {})[str(topic_id)] = dest.relative_to(root).as_posix()
    tmp = record_path.with_name(record_path.name + ".part")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    tmp.replace(record_path)
    return f"Saved {dest} ({dest.stat().st_size / 1e6:.0f} MB)."


def _rich(field) -> dict:
    """D2L rich text, a plain and an HTML rendition, as the view takes it."""
    field = field or {}
    return {"text": field.get("Text") or "", "html": field.get("Html") or ""}


# Group categories a student signs up to themselves, by D2L's EnrollmentStyle:
# people per group, number of groups, or both, with self-enrolment.
SELF_ENROLMENT = {3, 4, 5}


def _group_rows(categories: list, groups: list, me: int, classlist: list) -> list:
    """A course's group categories as its Groups tab shows them, each with
    the learner's own group and its members, if they are in one. groups holds
    each category's groups, in the same order; their Enrollments are user
    ids, which the class list names."""
    people = {int(u["Identifier"]): u for u in classlist}
    rows = []
    for category, members_of in zip(categories, groups):
        mine = next((g for g in members_of if me in (g.get("Enrollments") or [])), None)
        rows.append({
            "name": category["Name"],
            "description": _rich(category.get("Description")),
            "groups": len(members_of),
            "size": category.get("MaxUsersPerGroup"),
            "signup": category.get("EnrollmentStyle") in SELF_ENROLMENT,
            "opens": category.get("SelfEnrollmentStartDate"),
            "closes": category.get("SelfEnrollmentExpiryDate"),
            "mine": mine and {
                "name": mine["Name"],
                "description": _rich(mine.get("Description")),
                "members": [
                    {"name": people[i].get("DisplayName") or f"{people[i].get('FirstName', '')} {people[i].get('LastName', '')}".strip(),
                     "email": people[i].get("Email") or "", "role": people[i].get("ClasslistRoleDisplayName") or "",
                     "me": i == me}
                    for i in mine["Enrollments"] if i in people
                ],
            },
        })
    return rows


def course_tab(org_unit_id: int | str, tab: str) -> str:
    """Print one tab of a course's page in the LearnHarness view as JSON.

    tab is assignments, quizzes, groups or calendar, read live from DTU Learn.
    Not an MCP tool: the agents have list_assignments and calendar, and this
    carries what a page shows rather than what an answer needs.
    """
    ou = _resolve(org_unit_id)
    if tab == "assignments":
        items = [
            {
                "id": f["Id"], "name": f["Name"], "due": f.get("DueDate"),
                "start": (f.get("Availability") or {}).get("StartDate"),
                "end": (f.get("Availability") or {}).get("EndDate"),
                "instructions": _rich(f.get("CustomInstructions")),
                "files": [a.get("FileName") for a in f.get("Attachments") or []],
                "links": [{"name": link.get("LinkName") or link.get("Href"), "url": link.get("Href")}
                          for link in f.get("LinkAttachments") or []],
                # A group assignment names the group category it is handed in by.
                "group": bool(f.get("GroupTypeId")),
                "points": (f.get("Assessment") or {}).get("ScoreDenominator"),
                "url": f"{BASE}/d2l/lms/dropbox/user/folder_submit_files.d2l?db={f['Id']}&ou={ou}",
            }
            for f in _get(f"/d2l/api/le/{LE}/{ou}/dropbox/folders/") if not f.get("IsHidden")
        ]
        return json.dumps({"items": items}, ensure_ascii=False)
    if tab == "quizzes":
        page = _get(f"/d2l/api/le/{LE}/{ou}/quizzes/")
        quizzes = page.get("Objects") or []
        while page.get("Next"):
            page = _get(page["Next"])
            quizzes += page.get("Objects") or []
        items = []
        for q in quizzes:
            if not q.get("IsActive"):
                continue  # hidden from students on DTU Learn too
            attempts = q.get("AttemptsAllowed") or {}
            limit = q.get("SubmissionTimeLimit") or {}
            description = q.get("Description") or {}
            items.append({
                "id": q["QuizId"], "name": q["Name"],
                "start": q.get("StartDate"), "due": q.get("DueDate"), "end": q.get("EndDate"),
                "attempts": None if attempts.get("IsUnlimited") else attempts.get("NumberOfAttemptsAllowed"),
                "minutes": limit.get("TimeLimitValue") if limit.get("IsEnforced") else None,
                "description": _rich(description.get("Text") if description.get("IsDisplayed", True) else None),
                "url": f"{BASE}/d2l/lms/quizzing/user/quiz_summary.d2l?qi={q['QuizId']}&ou={ou}",
            })
        return json.dumps({"items": items}, ensure_ascii=False)
    if tab == "groups":
        me = int(_get(f"/d2l/api/lp/{LP}/users/whoami")["Identifier"])
        categories = _get(f"/d2l/api/lp/{LP}/{ou}/groupcategories/")
        with ThreadPoolExecutor(max_workers=8) as pool:
            groups = list(pool.map(
                lambda c: _get(f"/d2l/api/lp/{LP}/{ou}/groupcategories/{c['GroupCategoryId']}/groups/"),
                categories,
            ))
        # Names only for the learner's own groups, so the class list only then.
        mine = any(me in (g.get("Enrollments") or []) for gs in groups for g in gs)
        classlist = _get(f"/d2l/api/le/{LE}/{ou}/classlist/") if mine else []
        return json.dumps({"items": _group_rows(categories, groups, me, classlist)}, ensure_ascii=False)
    if tab == "calendar":
        # From today, as far ahead as a term runs.
        start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        events = _my_events([ou], start, start + timedelta(days=150))
        items = []
        for e in sorted(events, key=lambda e: e["StartDateTime"]):
            entity = (e.get("AssociatedEntity") or {}).get("AssociatedEntityType") or ""
            # Event descriptions are HTML. A booked lecture's only says that
            # TimeEdit made it, which is no news on every line of the week.
            html = (e.get("Description") or "").strip()
            if "room bookings in TimeEdit" in html:
                html = ""
            items.append({
                "title": e["Title"], "start": e["StartDateTime"], "end": e["EndDateTime"],
                "allDay": e["IsAllDayEvent"], "where": (e.get("LocationName") or "").strip(),
                "due": "Dropbox" in entity or "Quiz" in entity,
                "description": {"text": "", "html": html},
            })
        # D2L stops at 100 events and reports no continuation to page through.
        return json.dumps({"items": items, "capped": len(events) >= 100}, ensure_ascii=False)
    raise RuntimeError(f"No tab {tab!r}: assignments, quizzes, groups or calendar.")
