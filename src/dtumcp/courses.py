"""Courses, terms, their contents and calendars, read from DTU Learn or a saved copy."""

import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

import httpx

from .app import DOWNLOADS, LE, LP, RUN
from .text import _html_text
from .client import _client, _get, _number, _paged


@lru_cache(maxsize=None)
def _resolve(org_unit_id: int | str) -> int:
    """Course-scoped calls need the org unit id that list_courses prints in
    brackets, but the DTU course number is the memorable one and sits right
    there in the course name, so accept either.

    ponytail: ids and course numbers are told apart by width -- DTU org unit
    ids are six digits, course numbers at most five. If that ever stops holding,
    look the value up in the enrollment list both ways instead.
    """
    org_unit_id = _number(org_unit_id)
    if org_unit_id >= 100000:
        return org_unit_id

    matches = [
        ou
        for ou in _enrolled()[0]
        if (m := re.match(r"(\d{4,5})\b", ou["Name"]))
        and int(m.group(1)) == org_unit_id
    ]
    if len(matches) == 1:
        return matches[0]["Id"]
    if matches:
        # The same course number is offered again in later terms, so there is no
        # safe default here; name the options and let the caller choose.
        listing = "\n".join(
            f"  [{ou['Id']}] {ou['Name']}"
            for ou in sorted(matches, key=_term, reverse=True)
        )
        raise RuntimeError(
            f"Course {org_unit_id:05d} has several offerings. Use one of these ids:\n{listing}"
        )
    raise RuntimeError(
        f"{org_unit_id} is not one of your course ids or course numbers. "
        "Run list_courses to see them."
    )


def _toc_index(modules) -> tuple:
    """Index a table of contents by topic id and by module id. get_file needs
    to know what kind of thing a topic is before fetching it, and the
    single-topic endpoint does not answer consistently across courses."""
    topics, units = {}, {}

    def walk(modules):
        for m in modules or []:
            units[m["ModuleId"]] = m
            for t in m.get("Topics") or []:
                topics[t["TopicId"]] = t
            walk(m.get("Modules"))

    walk(modules)
    return topics, units


def _copies() -> list[Path]:
    """The course records download_course leaves in each course folder,
    oldest first, so a course saved under two names resolves to the newer."""
    return sorted(DOWNLOADS.glob("*/_course-*.json"), key=lambda p: p.stat().st_mtime)


def _copy(ou: int) -> dict | None:
    """A course's offline copy as download_course saved it, or None: its
    table of contents, announcements and the files it saved, with root set
    to the folder holding them."""
    found = [p for p in _copies() if p.name == f"_course-{ou}.json"]
    if not found:
        return None
    return json.loads(found[-1].read_text(encoding="utf-8")) | {"root": found[-1].parent}


def _stored_marks(path: Path) -> dict | None:
    """What _page_marks found in a saved file when its course downloaded, from
    the record in the course's folder: by path for a file a page links to, by
    topic or attachment key for one the course lists. None when no record has
    it, as for a course saved before linked files were looked at."""
    path = path.resolve()
    for root in path.parents:
        records = sorted(root.glob("_course-*.json"))
        if not records:
            continue
        try:
            record = json.loads(records[-1].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        marks, saved = record.get("marks") or {}, path.relative_to(root).as_posix()
        if saved in marks:
            return marks[saved]
        # A linked file that is a copy of a topic's is that topic's file (_dedupe_linked).
        key = next((k for k, p in (record.get("files") or {}).items() if p == saved), None)
        return marks.get(key)
    return None


def _saved_note(copy: dict) -> str:
    return (f"(From the offline copy saved {copy['saved']}. download_course "
            "brings it up to date.)")


def _enrolled() -> tuple[list, str]:
    """The org units of my courses, and if DTU Learn could not be asked, why.
    With the session expired or no network, the courses saved offline stand
    in, so what was downloaded can still be found and read."""
    try:
        items = _paged(f"/d2l/api/lp/{LP}/enrollments/myenrollments/", orgUnitTypeId=3)
        return [it["OrgUnit"] for it in items], ""
    except (RuntimeError, httpx.HTTPError) as e:
        saved = {}
        for p in _copies():
            copy = json.loads(p.read_text(encoding="utf-8"))
            saved[copy["ou"]] = {"Id": copy["ou"], "Name": copy["name"]}
        if not saved:
            raise
        return list(saved.values()), str(e)


def _description(node, link=None) -> str:
    """Modules and topics carry a Description in both a plain and an HTML
    rendition. Text is already clean; fall back to stripping the HTML for the
    ones where D2L only filled that in. With link (_link_note) the HTML is
    read, as the plain text has no links to mark."""
    desc = node.get("Description") or {}
    if link and (desc.get("Html") or "").strip():
        return _html_text(desc["Html"], link)
    return (desc.get("Text") or "").strip() or _html_text(desc.get("Html") or "")


def _urn_kind(url: str) -> str:
    """A ContentService topic points at Brightspace's media service with a URN,
    d2l:brightspace:content:<region>:<tenant>:<kind>:<id>/<revision>, whose
    <kind> says what the item actually is."""
    parts = url.split(":")
    if url.startswith("d2l:brightspace:content:") and len(parts) > 5:
        return parts[5]
    return "media item"


def _flatten(modules, depth=0):
    """TOC modules nest arbitrarily; flatten to one line per module and topic.

    A course copied from last year keeps topics whose file did not come with
    it. D2L flags them and answers 404 for their content, and they sit beside
    this year's topic of nearly the same title, so they are left out and only
    counted on their unit's line, where nobody can pick one by mistake.
    """
    out = []
    for m in modules or []:
        topics = m.get("Topics") or []
        info = " (info)" if _description(m) else ""
        broken = sum(1 for t in topics if t.get("IsBroken"))
        left_out = f" [{broken} broken topic{'s' * (broken > 1)} not listed]" if broken else ""
        out.append("  " * depth + f"# [{m['ModuleId']}] {m.get('Title', '?')}{info}{left_out}")
        for t in topics:
            if t.get("IsBroken"):
                continue
            kind = t.get("TypeIdentifier", "?")
            # Link topics point either inside D2L (a relative quicklink, no use
            # to anyone outside it) or out to the course's own website, which is
            # public. Only the latter is worth printing.
            url = t.get("Url") or ""
            external = f" {url}" if url.startswith("http") else ""
            if kind == "ContentService":
                kind = _urn_kind(url)  # "video" reads better than "ContentService"
            out.append(
                "  " * (depth + 1)
                + f"- [{t['TopicId']}] {t.get('Title', '?')} ({kind}){external}"
            )
        out.extend(_flatten(m.get("Modules"), depth + 1))
    return out


SEASONS = {  # ordered by where the term falls in the calendar year
    "winter": 0, "january": 0, "jan": 0,
    "spring": 1,
    "summer": 2, "june": 2, "jun": 2,
    "fall": 3, "autumn": 3,
}
SEASON_NAMES = {0: "Winter", 1: "Spring", 2: "Summer", 3: "Fall"}
CODE_SEASONS = {"v": 0, "f": 1, "s": 2, "e": 3}  # vinter, forår, sommer, efterår
TERM_IN_NAME = re.compile(
    r"\b(winter|january|jan|spring|summer|june|jun|fall|autumn)\s*'?(\d{4}|\d{2})\b",
    re.I,
)


def _term(org_unit) -> tuple[int, int]:
    """Sortable (year, season) for a course. DTU writes the term into the course
    name -- "Fall 2026", "Jan 24", "Fall22-Spring23" -- which is authoritative;
    the Code is only a fallback for names that carry no term at all."""
    match = TERM_IN_NAME.search(org_unit["Name"])
    if match:
        year = int(match.group(2))
        return (year + 2000 if year < 100 else year), SEASONS[match.group(1).lower()]

    match = re.match(r"DTU_([efsv])(\d\d)_", org_unit.get("Code") or "")
    if match:
        season = CODE_SEASONS[match.group(1)]
        year = 2000 + int(match.group(2))
        # The code carries the academic year, and that year's January falls in
        # the next calendar one: DTU_v25_27016 is named "Winter 2026".
        return (year + 1 if season == 0 else year), season

    return 0, -1  # sorts last, under "No term"


def _topic_fetch(ou: int, topic, client=None):
    """One topic's file response and the name D2L gives it. get_file wants the
    text out of it and download_course wants the bytes on disk, but the fetch,
    its errors and the filename are the same for both."""
    topic_id = topic["TopicId"]
    c = client or _client()
    try:
        r = c.get(f"/d2l/api/le/{LE}/{ou}/content/topics/{topic_id}/file")
    finally:
        if client is None:
            c.close()
    if r.status_code in (401, 403):
        raise RuntimeError(
            f"Session rejected (HTTP %d). Run '{RUN} login' again."
            % r.status_code
        )
    if r.status_code == 404:
        why = (
            "is marked broken in the course: it was copied from an earlier "
            "offering without its file, and nobody but the teacher can fix that"
            if topic.get("IsBroken")
            else f"is a {topic.get('TypeIdentifier')} topic with no file behind it"
        )
        raise RuntimeError(f"'{topic.get('Title', topic_id)}' {why}.")
    r.raise_for_status()

    return r, _filename(r, f"topic-{topic_id}")


def _filename(r: httpx.Response, fallback: str) -> str:
    disp = r.headers.get("content-disposition", "")
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disp)
    # D2L percent-encodes the filename, so a space arrives as %20.
    return unquote(match.group(1)) if match else fallback


def _term_units(want: tuple | None = None) -> list:
    """The org units of the courses I am enrolled in for one (year, season)
    term, by default the newest. A calendar of every course I ever took is not
    what "my calendar" means, nor a download of them what "this term" does."""
    units = [
        it["OrgUnit"]
        for it in _paged(f"/d2l/api/lp/{LP}/enrollments/myenrollments/", orgUnitTypeId=3)
    ]
    if want is None:
        want = max((_term(ou) for ou in units), default=(0, -1))
    return [ou for ou in units if _term(ou) == want]


def _my_events(ids: list[int], start: datetime, end: datetime) -> list:
    path = f"/d2l/api/le/{LE}/calendar/events/myEvents/"
    window = {
        d: t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        for d, t in (("startDateTime", start), ("endDateTime", end))
    }
    csv = ",".join(str(i) for i in ids)
    try:
        return _get(path, **window, orgUnitIdsCSV=csv)["Objects"]
    except RuntimeError:
        # One course whose calendar is closed to students answers 403 for the
        # whole batch, so ask course by course and leave that one out. If every
        # course refuses it is the session that is gone, so let the error stand.
        events, refused = [], 0
        for ou in ids:
            try:
                events += _get(path, **window, orgUnitIdsCSV=str(ou))["Objects"]
            except RuntimeError:
                refused += 1
        if refused == len(ids):
            raise
        return events


def _local(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone()


def _calendar_lines(events: list) -> list[str]:
    """Group events by local day. A lecture spans a time range; a deadline or a
    release date is a single instant, so it prints one time instead of two."""
    lines, day = [], None
    for e in sorted(events, key=lambda e: e["StartDateTime"]):
        start, end = _local(e["StartDateTime"]), _local(e["EndDateTime"])
        if start.date() != day:
            day = start.date()
            lines.append(f"\n## {start:%a %d %b %Y}")

        when = "all day" if e["IsAllDayEvent"] else f"{start:%H:%M}"
        if not e["IsAllDayEvent"] and end != start:
            when += f"-{end:%H:%M}"
        entity = (e.get("AssociatedEntity") or {}).get("AssociatedEntityType") or ""
        due = "due: " if "Dropbox" in entity or "Quiz" in entity else ""
        number = re.match(r"\d{4,5}\b", e["OrgUnitName"] or "")
        course = number.group() if number else (e["OrgUnitName"] or "")
        where = (e.get("LocationName") or "").strip()
        if len(where) > 60:  # a session booked into six rooms lists them all
            where = where[:57] + "..."
        lines.append(f"{when}  {course}  {due}{e['Title']}" + (f" — {where}" if where else ""))
    return lines
