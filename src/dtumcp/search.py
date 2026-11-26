"""The search index over the saved courses."""

import json
import re
import sqlite3
from contextlib import closing
from functools import partial
from itertools import count
from pathlib import Path
from urllib.parse import unquote

from .app import DOWNLOADS
from .client import _number
from .courses import _copies


# Course search: one SQLite full-text (FTS5) index over every saved course,
# _search.sqlite in DOWNLOADS, with a row per page of a PDF and one per other
# entry. It is filled from the _search.js each download writes, which the
# offline pages go on searching by themselves, and brought up to date before
# every search: a course whose record changed since it was indexed is indexed
# again, and one whose folder is gone is dropped.
INDEX_VERSION = 2
INDEX_SCHEMA = (
    "DROP TABLE IF EXISTS courses",
    "DROP TABLE IF EXISTS docs",
    "DROP TABLE IF EXISTS pages",
    "CREATE TABLE courses (ou INTEGER PRIMARY KEY, name TEXT, folder TEXT, mtime REAL)",
    "CREATE TABLE docs (id INTEGER PRIMARY KEY, ou INTEGER, title TEXT, place TEXT, kind TEXT,"
    " target TEXT, broken INTEGER, pages INTEGER)",
    "CREATE INDEX docs_ou ON docs (ou)",
    # parts holds the word parts the tokenizer does not split out; see _parts.
    # Every search word is a prefix, and one of two or three letters ("co",
    # "the") would otherwise merge the entries of every word it starts, for
    # the search and again for each snippet: 1 s for "co" over 32 courses
    # becomes 0.2 s, for 40% more index.
    "CREATE VIRTUAL TABLE pages USING fts5(title, place, body, parts, doc UNINDEXED, page UNINDEXED,"
    " prefix='2 3')",
    f"PRAGMA user_version = {INDEX_VERSION}",
)
# bm25's weights for a match in the title, the units above it, the text and
# the word parts: a word in a title says more about a page than one in it.
INDEX_WEIGHTS = "8.0, 3.0, 1.0, 0.5"


def _humps(word: str) -> list[str]:
    """A word cut where a joined-up name starts a new part, lower case to
    upper ("Hamilton", "Cycle"), and between digits and letters ("3", "SAT")."""
    cuts = [i for i in range(1, len(word))
            if (word[i - 1].islower() and word[i].isupper()) or word[i - 1].isdigit() != word[i].isdigit()]
    return [word[a:b] for a, b in zip([0, *cuts], [*cuts, len(word)])]


def _parts(text: str) -> str:
    """The words in text the tokenizer would not find by themselves, so a
    search finds them: the parts of a joined-up name or of a number with
    letters ("Cycle" in "HamiltonCycle", "SAT" in "3SAT"), and a hyphenated
    word written as one ("3SAT" for "3-SAT"), which a search word with its
    hyphens dropped meets."""
    out = []
    for word in re.findall(r"[^\W_]+(?:[-‐][^\W_]+)*", text):
        pieces = re.split(r"[-‐]", word)
        if len(pieces) > 1:
            out.append("".join(pieces))
        for piece in pieces:
            if len(humps := _humps(piece)) > 1:
                out += humps
    return " ".join(out)


def _terms(query) -> list[tuple[str, str]]:
    """query's words, at most 12, each with its FTS5 term: the word's hyphens
    dropped, as _parts joins hyphenated words, what is left cut where the
    tokenizer cuts it, and matched at the start of a word, except a word that
    ends in one letter or digit, which as a prefix would match nearly every
    page ("O(n)" is the word o then the word n)."""
    terms = []
    for word in str(query).split():
        tokens = re.findall(r"[^\W_]+", re.sub(r"(?<=[^\W_])[-‐](?=[^\W_])", "", word))
        if tokens:
            terms.append((word, '"' + " ".join(tokens) + '"' + "*" * (len(tokens[-1]) > 1)))
    return terms[:12]


def _index() -> sqlite3.Connection:
    """The search index, made or remade when it is missing or of an older layout."""
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    # Autocommit, with transactions begun by hand: the MCP server, the
    # plugin's search process and the command line may all write it at once.
    db = sqlite3.connect(DOWNLOADS / "_search.sqlite", timeout=60, isolation_level=None)
    db.execute("PRAGMA journal_mode = WAL")  # searches read while a course is indexed
    if db.execute("PRAGMA user_version").fetchone()[0] != INDEX_VERSION:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("PRAGMA user_version").fetchone()[0] != INDEX_VERSION:
            for statement in INDEX_SCHEMA:
                db.execute(statement)
        db.execute("COMMIT")
    return db


def _drop_course(db: sqlite3.Connection, ou: int) -> None:
    db.execute("DELETE FROM pages WHERE doc IN (SELECT id FROM docs WHERE ou = ?)", (ou,))
    db.execute("DELETE FROM docs WHERE ou = ?", (ou,))
    db.execute("DELETE FROM courses WHERE ou = ?", (ou,))


def _index_course(db: sqlite3.Connection, ou: int, record: Path, mtime: float) -> None:
    """Index one saved course afresh, in one transaction, so a search sees
    the old rows or the new, never half of them."""
    copy = json.loads(record.read_text(encoding="utf-8")) | {"root": record.parent}
    entries = _search_docs(copy)
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("SELECT 1 FROM courses WHERE ou = ? AND mtime = ?", (ou, mtime)).fetchone():
            db.execute("ROLLBACK")  # another process indexed it while this one read
            return
        _drop_course(db, ou)
        db.execute("INSERT INTO courses VALUES (?, ?, ?, ?)", (ou, copy["name"], record.parent.name, mtime))
        for e in entries:
            starts = e["pages"] or [0]
            doc = db.execute(
                "INSERT INTO docs (ou, title, place, kind, target, broken, pages) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ou, e["title"], e["where"], e["kind"], json.dumps(e["target"]), e["broken"], len(starts)),
            ).lastrowid
            text = e["text"]
            for n, (a, b) in enumerate(zip(starts, [*starts[1:], len(text)]), 1):
                body = text[a:b].strip()
                db.execute("INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?)",
                           (e["title"], e["where"], body, _parts(f"{e['title']} {e['where']} {body}"), doc, n))
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def _sync_index(db: sqlite3.Connection) -> None:
    """Bring the index up to date with the saved courses: download_course
    writes a course's record last, so a record newer than its index means
    the course was downloaded again."""
    newest = {int(p.stem.removeprefix("_course-")): p for p in _copies()}  # oldest first, so the newest wins, as in _copy
    indexed = dict(db.execute("SELECT ou, mtime FROM courses"))
    for ou in indexed.keys() - newest.keys():
        db.execute("BEGIN IMMEDIATE")
        _drop_course(db, ou)
        db.execute("COMMIT")
    for ou, record in newest.items():
        if indexed.get(ou) != (mtime := record.stat().st_mtime):
            _index_course(db, ou, record, mtime)


def _search_docs(copy: dict) -> list[dict]:
    """A saved course's search entries as download_course wrote them, each with
    what it points at, as {"topic": id}, {"unit": id}, {"news": id},
    {"attachment": "news id/file id"} or {"linked": path}, and whether its
    topic is broken. Entries pointing at nothing the copy knows are left out."""
    try:
        script = (copy["root"] / "_search.js").read_text(encoding="utf-8")
        docs = json.loads(script[script.index("=") + 1:].strip().removesuffix(";"))
    except (OSError, ValueError):
        return []  # a course saved before the search existed: downloading it again adds one
    by_path = {saved: {"attachment": key} if "/" in key else {"topic": int(key)}
               for key, saved in (copy.get("files") or {}).items()}
    for saved in (copy.get("linked") or {}).values():
        by_path.setdefault(saved, {"linked": saved})
    by_url, broken = {}, set()

    def walk(modules):
        for m in modules or []:
            for t in m.get("Topics") or []:
                if t.get("Url"):
                    by_url[t["Url"]] = t["TopicId"]
                if t.get("IsBroken"):
                    broken.add(t["TopicId"])
            walk(m.get("Modules"))

    walk(copy.get("modules"))

    def target(href: str):
        if anchor := re.fullmatch(r"#([un])(\d+)", href):
            return {"unit" if anchor[1] == "u" else "news": int(anchor[2])}
        if lesson := re.search(r"/d2l/le/lessons/\d+/topics/(\d+)", href):
            return {"topic": int(lesson[1])}
        if href in by_url:
            return {"topic": by_url[href]}
        return by_path.get(unquote(href))

    entries = []
    for doc in docs:
        if not (to := target(doc.get("h", ""))):
            continue
        entries.append({"title": doc.get("t", ""), "where": doc.get("u", ""), "kind": doc.get("k", ""),
                        "text": doc.get("x", ""), "pages": doc.get("p"), "target": to,
                        "broken": to.get("topic") in broken})
    return entries


def _search(query, only: int = 0, limit: int = 15, partial: bool = True, hide_broken: bool = True) -> dict:
    """The saved courses searched for query's words, ranked. A course
    material item (a file, a unit's text, an announcement) ranks by how many
    of the words it has, then by its best page: how many of the words that
    one page holds, then its BM25 score, which weighs a word by how rare it
    is across the courses and a page by how short it is, so a short slide
    on the subject beats a book that mentions it often. Without partial,
    only items with every word. Returns total and complete (how many matched,
    and with every word) and the first limit hits, as the plugin's view
    shows them: the item, its course, what it points at, its best PDF page
    and others as good, the words it lacks, and a snippet split so that its
    odd parts are matches."""
    terms = _terms(query)
    if not terms:
        return {"total": 0, "complete": 0, "hits": []}
    anything = " OR ".join(term for _, term in terms)
    with closing(_index()) as db:
        _sync_index(db)
        courses = {ou: (name, mtime) for ou, name, mtime in db.execute("SELECT ou, name, mtime FROM courses")}
        docs = {row[0]: row for row in db.execute(
            "SELECT id, ou, title, place, kind, target, broken, pages FROM docs"
            + " WHERE ou = ?" * bool(only), (only,) if only else ())}
        rows = {rowid: [doc, page, score, set()] for rowid, doc, page, score in db.execute(
            f"SELECT rowid, doc, page, bm25(pages, {INDEX_WEIGHTS}) FROM pages WHERE pages MATCH ?", (anything,))
            if doc in docs and not (hide_broken and docs[doc][6])}
        for i, (_, term) in enumerate(terms):
            for (rowid,) in db.execute("SELECT rowid FROM pages WHERE pages MATCH ?", (term,)):
                if rowid in rows:
                    rows[rowid][3].add(i)
        by_doc = {}
        for rowid, (doc, page, score, words) in rows.items():
            by_doc.setdefault(doc, []).append((len(words), score, page, rowid, words))
        found = []
        for doc, pages in by_doc.items():
            words = set().union(*(p[4] for p in pages))
            if partial or len(words) == len(terms):
                pages.sort(key=lambda p: (-p[0], p[1]))  # most of the words, then the best score
                found.append((len(words), pages[0][0], pages[0][1], doc, pages, words))
        found.sort(key=lambda f: (-f[0], -f[1], f[2]))
        hits = []
        for _, on_page, _, doc, pages, words in found[:limit]:
            _, ou, title, place, kind, target, _, count = docs[doc]
            name, mtime = courses[ou]
            hit = {"ou": ou, "course": name, "saved": mtime * 1000, "title": title, "where": place,
                   "kind": kind, "target": json.loads(target), "pageCount": count}
            if missing := [word for i, (word, _) in enumerate(terms) if i not in words]:
                hit["missing"] = missing
            if count > 1:
                hit["page"] = pages[0][2]
                if also := sorted([p[2] for p in pages[1:] if p[0] == on_page][:5]):
                    hit["also"] = also
            hit["row"] = pages[0][3]
            hits.append(hit)
        # The hits' snippets in one query rather than one each.
        rows = [hit.pop("row") for hit in hits]
        snippets = dict(db.execute(
            "SELECT rowid, snippet(pages, 2, char(2), char(3), '…', 32) FROM pages "
            f"WHERE pages MATCH ? AND rowid IN ({', '.join('?' * len(rows))})", (anything, *rows)))
        for hit, row in zip(hits, rows):
            if snippet := snippets.get(row):
                hit["snippet"] = re.split("[\x02\x03]", snippet)
    return {"total": len(found), "complete": sum(1 for f in found if f[0] == len(terms)), "hits": hits}


def _search_answer(query, org_unit_id: int | str = 0, limit: int = 15) -> tuple[str, dict]:
    """search_courses' text, and the search it shows, for the plugin's tool card."""
    with closing(_index()) as db:
        _sync_index(db)
        names = dict(db.execute("SELECT ou, name FROM courses ORDER BY folder"))
    only = org_unit_id = _number(org_unit_id or 0)
    if 0 < org_unit_id <= 99999:
        # A course number, as _resolve tells them apart, matched to the saved courses.
        code = f"{org_unit_id:05d}"
        matches = [ou for ou, name in names.items() if name.startswith(code + " ")]
        if len(matches) != 1:
            raise RuntimeError(
                f"Course {code} is downloaded for several terms: {', '.join(map(str, matches))}. "
                "Pass the org unit id." if matches else
                f"Course {code} is not downloaded. Run list_courses and download_course.")
        only = matches[0]
    found = _search(query, only, min(50, max(1, limit)))
    total, hits, complete = found["total"], found["hits"], found["complete"]
    if not total:
        if not names:
            return "No course is downloaded yet, so there is nothing to search. download_course saves one.", found
        saved = "; ".join(f"[{ou}] {name}" for ou, name in names.items())
        return (f'No matches for "{query}" in {f"course {only}" if only else "the downloaded courses"}. '
                f"Downloaded: {saved}."), found
    shown = f"; the best {len(hits)} shown" if total > len(hits) else ""
    if complete == total:
        lines = [f"{total} match{'' if total == 1 else 'es'}{shown}:"]
    elif complete:
        lines = [f"{complete} with every word, then {total - complete} with some of them, "
                 f"each saying which it lacks{shown}:"]
    else:
        lines = [f'Nothing has every word of "{query}". These have the most of them, each saying '
                 f"which it lacks; try the course's own words for those{shown}:"]
    for i, hit in enumerate(hits, 1):
        (kind, key), = hit["target"].items()
        ou = hit["ou"]
        ids = {
            "topic": f"org_unit_id {ou}, topic_id {key}",
            "unit": f"org_unit_id {ou}, unit_id {key} (unit_info)",
            "attachment": f"org_unit_id {ou}, announcement attachment "
                          f"{str(key).replace('/', ', file ')} (get_announcement_attachment)",
            "news": f"org_unit_id {ou}, announcement {key} (list_announcements)",
        }.get(kind, f"org_unit_id {ou}; a file the course's pages link to, not readable with the tools")
        pages = ""
        if hit.get("page"):
            pages = f", page {hit['page']}" + (f"; also pages {', '.join(map(str, hit['also']))}" if "also" in hit else "")
        lacks = f" — lacks: {', '.join(hit['missing'])}" if "missing" in hit else ""
        lines += ["", f"[{i}] {hit['title']} ({hit['kind']}{pages}){lacks}",
                  f"    {' › '.join(filter(None, (hit['course'], hit['where'])))}",
                  f"    {ids}"]
        if "snippet" in hit:
            lines.append(f"    {''.join(hit['snippet'])}")
    return "\n".join(lines), found
