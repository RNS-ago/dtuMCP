"""The index.html and landing pages over the saved courses."""

import json
import re
from datetime import datetime
from html import escape, unescape
from pathlib import Path
from urllib.parse import quote, unquote

from .app import BASE, DOWNLOADS
from .text import _html_text, _ooxml_text, _pdf_text, _sheet_pages
from .courses import SEASON_NAMES, _description, _term, _urn_kind


# Spreadsheets are left out: their cells are mostly numbers from data tables,
# which in one course came to 235 MB of search text against 8 MB for
# everything else. They are still found by title.
OOXML_KINDS = {".docx": "wordprocessingml", ".pptx": "presentationml"}


def _search_pages(path: Path) -> list[str]:
    """A saved file's text for the search box: one string per page of a PDF
    or sheet of a workbook, one for anything else. Empty when no text can be got out of it."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        # pdftotext ends each page with \f; split on all, the last makes a page past the end.
        pages = (_pdf_text(path) or "").removesuffix("\f").split("\f")
    elif suffix == ".xlsx":
        pages = _sheet_pages(path)
    elif suffix in OOXML_KINDS:
        pages = [_ooxml_text(path, OOXML_KINDS[suffix]) or ""]
    elif suffix in (".html", ".htm"):
        pages = [_html_text(path.read_bytes().decode("utf-8", "replace"))]
    else:
        return []
    pages = [" ".join(p.split()) for p in pages]
    return pages if any(pages) else []


def _search_entry(title: str, href: str, tag: str, pages=(), where: str = "") -> dict:
    """One search result: its text joined into one string, and for a PDF the
    offset each page starts at, so a match can link to its page. where names
    the units above it, since many courses repeat titles like "Description"."""
    starts, text = [], ""
    for page in pages:
        starts.append(len(text))
        text += page + " "
    entry = {"t": title, "h": href, "k": tag, "x": text.rstrip()}
    if len(starts) > 1:
        entry["p"] = starts
    if where:
        entry["u"] = where
    return entry


def _index_html(name: str, ou: int, modules, news, saved: dict, texts: dict) -> tuple:
    """The page that stands in for the course site offline, and the entries its
    search box looks through. saved maps a topic id, or a (news id, file id)
    pair for an attachment, to its path relative to the page; texts maps the
    same keys to the file's pages of text. Anything not saved links back to
    DTU Learn, which needs a connection."""
    docs = []
    # Teacher HTML points at /content/... on D2L, which would mean
    # file:///content/... here. Make it absolute; _relink then swaps in
    # the local copies of whatever it could fetch.
    def rich(field):  # D2L stores rich text in a plain and an HTML rendition
        field = field or {}
        html = field.get("Html") or f"<p>{escape(field.get('Text') or '')}</p>"
        return re.sub(r'\b(src|href)="/(?!/)', rf'\1="{BASE}/', html)

    def item(href, label, tag, online=False, key=None, where=""):
        docs.append(_search_entry(label, href, tag, texts.get(key, ()), where))
        cls = ' class="tag online"' if online else ' class="tag"'
        return (f'<li><a href="{escape(href)}">{escape(label)}</a>'
                f"<span{cls}>{escape(tag)}</span></li>")

    def topic(t, where):
        tid, url, kind = t["TopicId"], t.get("Url") or "", t.get("TypeIdentifier", "?")
        title = t.get("Title", "?")
        if tid in saved:
            tag = Path(unquote(saved[tid])).suffix[1:].upper() or "file"
            return item(saved[tid], title, tag, key=tid, where=where)
        if kind == "ContentService":
            kind = _urn_kind(url)
        if t.get("IsBroken"):
            kind += ", broken"
        href = url if url.startswith("http") else f"{BASE}/d2l/le/lessons/{ou}/topics/{tid}"
        return item(href, title, f"{kind} · online", online=True, where=where)

    def tree(modules):
        """The sidebar: every unit, nested and folded, linking to its section."""
        for m in modules or []:
            a = f'<a href="#u{m["ModuleId"]}">{escape(m.get("Title", "?"))}</a>'
            if m.get("Modules"):
                yield f"<li><details><summary>{a}</summary><ul>"
                yield from tree(m["Modules"])
                yield "</ul></details></li>"
            else:
                yield f"<li>{a}</li>"

    def units(modules, depth=2, trail=()):
        """Each unit's section. trail holds the (id, title) of the units above
        it, shown as links over the heading so a deep unit says where it sits."""
        for m in modules or []:
            h, title = min(depth, 6), m.get("Title", "?")
            parents = " › ".join(t for _, t in trail)
            docs.append(_search_entry(title, f'#u{m["ModuleId"]}', "unit",
                                      [" ".join(_description(m).split())], parents))
            yield f'<section id="u{m["ModuleId"]}">'
            if trail:
                yield '<p class="crumbs">' + " › ".join(
                    f'<a href="#u{i}">{escape(t)}</a>' for i, t in trail) + "</p>"
            yield f"<h{h}>{escape(title)}</h{h}>"
            if _description(m):
                yield f'<div class="text">{rich(m.get("Description"))}</div>'
            here = " › ".join([*(t for _, t in trail), title])
            if m.get("Topics"):
                yield '<ul class="topics">' + "".join(topic(t, here) for t in m["Topics"]) + "</ul>"
            yield from units(m.get("Modules"), depth + 1, (*trail, (m["ModuleId"], title)))
            yield "</section>"

    def announcements():
        for n in news:
            body = n.get("Body") or {}
            text = (body.get("Text") or "").strip() or _html_text(body.get("Html") or "")
            docs.append(_search_entry(n.get("Title", "?"), f'#n{n["Id"]}', "announcement", [" ".join(text.split())]))
            yield (f'<article id="n{n["Id"]}"><h2>{escape(n.get("Title", "?"))}</h2>'
                   f'<time>{(n.get("StartDate") or "")[:10]}</time>'
                   f'<div class="text">{rich(n.get("Body"))}</div>')
            files = n.get("Attachments") or []
            if files:
                yield '<ul class="topics">' + "".join(
                    item(saved[(n["Id"], a["FileId"])], a["FileName"], "attachment", key=(n["Id"], a["FileId"]))
                    if (n["Id"], a["FileId"]) in saved
                    else f'<li>{escape(a["FileName"])}<span class="tag online">download failed</span></li>'
                    for a in files
                ) + "</ul>"
            yield "</article>"

    html = "\n".join([
        '<!doctype html><html lang="en"><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(name)}</title><style>{INDEX_CSS}</style>",
        f'<header><a class="back" href="../index.html">← All courses</a><h1>{escape(name)}</h1>',
        f'<p>Saved from DTU Learn on {datetime.now():%Y-%m-%d}</p>',
        '<div class="tabs" role="tablist">',
        '<input type="radio" name="tab" id="t-content" checked><label for="t-content">Content</label>',
        f'<input type="radio" name="tab" id="t-news"><label for="t-news">Announcements ({len(news)})</label>',
        '<input type="search" id="q" placeholder="Search this course, inside files too (press /)" aria-label="Search this course">',
        "</div></header>",
        f'<div id="p-content" class="panel"><nav aria-label="Units"><ul>{"".join(tree(modules))}</ul></nav>',
        '<main>', *units(modules), "</main></div>",
        '<div id="p-news" class="panel"><main>',
        *(announcements() if news else ["<p>No announcements.</p>"]),
        "</main></div>",
        '<main id="results" aria-live="polite"></main>',
        f'<script src="_search.js"></script><script>{INDEX_JS}</script>',
    ])
    return html, docs


def _landing_html(courses) -> str:
    """The page above the course folders: every downloaded course, grouped by
    term with the newest first, and a search box over all of them. courses
    holds (name, folder as a URL path, saved date)."""
    groups = {}
    for name, folder, saved in courses:
        groups.setdefault(_term({"Name": name}), []).append(
            (name, f'<li><a href="{escape(folder)}/index.html">{escape(name)}</a>'
                   f'<span class="tag">saved {escape(saved)}</span></li>')
        )
    body = []
    for year, season in sorted(groups, reverse=True):
        body.append(f"<h2>{SEASON_NAMES[season]} {year}</h2>" if year else "<h2>No term</h2>")
        body.append('<ul class="topics">' + "".join(li for _, li in sorted(groups[year, season])) + "</ul>")

    def js(value):  # a string literal that cannot close the <script> it sits in
        return json.dumps(value).replace("</", "<\\/")

    # Every course's _search.js sets window.SEARCH. Loaded one after another,
    # each is taken in turn, its links made relative to this page and its
    # results labelled with the course, before the next one replaces it.
    # ponytail: all courses load up front, ~10 MB each for the big ones; load
    # on first keystroke if the landing page ever opens slowly.
    loads = [
        f'<script src="{escape(folder)}/_search.js"></script><script>take({js(folder)}, {js(name)})</script>'
        for name, folder, _ in courses
    ]
    return "\n".join([
        '<!doctype html><html lang="en"><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>DTU Learn courses</title><style>{INDEX_CSS}</style>",
        "<header><h1>DTU Learn courses</h1><p>Saved for offline reading. "
        "Run download_course again to add a course or bring one up to date.</p>",
        '<div class="tabs"><input type="search" id="q" placeholder="Search every course (press /)" '
        'aria-label="Search every course"></div></header>',
        '<main id="courses">', *(body or ["<p>No courses downloaded yet.</p>"]), "</main>",
        '<main id="results" aria-live="polite"></main>',
        f"<script>{LANDING_JS}</script>", *loads,
        f"<script>window.SEARCH = all;{INDEX_JS}</script>",
    ])


def _write_landing() -> None:
    """Rebuild the landing page from the course folders on disk, so it lists
    every course downloaded so far, not only the one just fetched."""
    courses = []
    for page in DOWNLOADS.glob("*/index.html"):
        found = re.search(r"<title>(.*?)</title>", page.read_text(encoding="utf-8", errors="replace"))
        saved = datetime.fromtimestamp(page.stat().st_mtime)
        courses.append((
            unescape(found.group(1)) if found else page.parent.name,
            quote(page.parent.name),
            f"{saved:%Y-%m-%d}",
        ))
    (DOWNLOADS / "index.html").write_text(_landing_html(courses), encoding="utf-8")


# The tabs are radio buttons, so they work with no script and from the keyboard.
# System colours (Canvas, CanvasText, LinkText) follow the reader's dark mode.
INDEX_CSS = """
:root{color-scheme:light dark;--line:color-mix(in srgb,CanvasText 14%,transparent);
--muted:color-mix(in srgb,CanvasText 62%,transparent)}
*{box-sizing:border-box}
body{margin:0;background:Canvas;color:CanvasText;font:16px/1.5 system-ui,sans-serif}
header{padding:1rem 1rem 0;border-bottom:1px solid var(--line)}
h1{margin:0;font-size:1.5rem}
header p{margin:.2rem 0 .6rem;color:var(--muted);font-size:.9rem}
.tabs{display:flex;flex-wrap:wrap;align-items:center;gap:.25rem}
#q{flex:0 1 22rem;margin:0 0 .4rem auto;padding:.4rem .6rem;font:inherit;color:inherit;
background:Canvas;border:1px solid var(--line);border-radius:.4rem}
#results{display:none}
body.searching #results{display:block}
body.searching .panel,body.searching #courses{display:none!important}
.hits li{flex-wrap:wrap}
.hits p{flex-basis:100%;margin:.15rem 0 0;color:var(--muted);font-size:.9rem}
.hits p.where{margin:0;font-size:.8rem}
.tabs input[type=radio]{position:absolute;opacity:0}
.tabs label{padding:.5rem .9rem;cursor:pointer;color:var(--muted);border-bottom:3px solid transparent}
.tabs input:checked+label{color:CanvasText;font-weight:600;border-color:LinkText}
.tabs input:focus-visible+label{outline:2px solid LinkText;outline-offset:-2px}
.panel{display:none}
body:has(#t-content:checked) #p-content{display:grid;grid-template-columns:18rem minmax(0,1fr)}
body:has(#t-news:checked) #p-news{display:block}
nav{position:sticky;top:0;align-self:start;max-height:100vh;overflow:auto;
padding:1rem;border-right:1px solid var(--line);font-size:.92rem}
nav ul{list-style:none;margin:0 0 0 .3rem;padding-left:.8rem;border-left:1px solid var(--line)}
nav>ul{margin:0;padding-left:0;border:0}
nav li{margin:.15rem 0}
nav a{color:inherit;text-decoration:none}
nav ul:has(details)>li>a{display:block;padding-left:1.1em}
nav a:hover{color:LinkText;text-decoration:underline}
summary{cursor:pointer}
main{padding:1rem;max-width:60rem;min-width:0}
section{scroll-margin-top:1rem;margin-bottom:1.5rem}
main>section+section{border-top:1px solid var(--line);padding-top:1.5rem}
section section{margin:1.25rem 0 0;padding-left:1rem;
border-left:3px solid color-mix(in srgb,LinkText 35%,transparent)}
.crumbs{margin:0;font-size:.85rem;color:var(--muted)}
.crumbs a{color:inherit}
.back{font-size:.9rem;color:var(--muted);text-decoration:none}
.back:hover{color:LinkText}
h2,h3,h4,h5,h6{margin:0 0 .5rem}
.text{overflow-x:auto}
.text img{max-width:100%;height:auto}
.topics{list-style:none;margin:.5rem 0;padding:0}
.topics li{display:flex;gap:.5rem;align-items:baseline;padding:.35rem 0;border-bottom:1px solid var(--line)}
.tag{margin-left:auto;flex:none;font-size:.75rem;padding:0 .45rem;border:1px solid var(--line);
border-radius:.3rem;color:var(--muted)}
.tag.online{border-style:dashed}
article{padding:1rem 0;border-bottom:1px solid var(--line)}
article h2{font-size:1.15rem;margin:0}
time{color:var(--muted);font-size:.9rem}
@media (max-width:45rem){
body:has(#t-content:checked) #p-content{grid-template-columns:1fr}
nav{position:static;max-height:none;border-right:0;border-bottom:1px solid var(--line)}
#q{flex-basis:100%;margin-left:0}}
"""

# Every search word has to start a word in a result's title, the units above
# it, or its text, so "rsa" finds "RSA" but not "adversary". Results whose
# title and units hold every word come first, the rest stay in course order.
# A PDF match links to its page.
# ponytail: a linear regex scan, a few ms over a course's ~10 MB of text.
# An inverted index only if a course ever makes typing lag.
INDEX_JS = r"""
// lt is the title and the units above it, so "milk description" finds the
// Description topic inside the Milk case study.
// p, a PDF's page starts, counts characters, as Python does; JS strings count
// UTF-16 units, two for a character like 𝑥, so they are converted here.
function unitStarts(text, starts) {
  let units = 0, chars = 0;
  return starts.map(start => {
    for (; chars < start && units < text.length; chars++) units += text.codePointAt(units) > 0xffff ? 2 : 1;
    return units;
  });
}
const docs = (window.SEARCH || []).map(d => ({...d, p: d.p && unitStarts(d.x, d.p), lt: (d.t + " " + (d.u || "")).toLowerCase(), lx: d.x.toLowerCase()}));
const q = document.getElementById("q"), out = document.getElementById("results");
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};
function run() {
  const words = q.value.toLowerCase().split(/\s+/).filter(Boolean);
  document.body.classList.toggle("searching", words.length > 0);
  out.replaceChildren();
  if (!words.length) return;
  // \p{L}\p{N} rather than \b, which only knows ASCII and would miss "økonomi".
  const start = "(?<![\\p{L}\\p{N}])";
  const esc = w => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const res = words.map(w => new RegExp(start + esc(w), "u"));
  const inTitle = d => res.every(r => r.test(d.lt));
  const hits = docs.filter(d => res.every(r => r.test(d.lt) || r.test(d.lx)))
    .sort((a, b) => inTitle(b) - inTitle(a));
  out.append(el("p", "", hits.length ? `${hits.length} results` : "No results."));
  const mark = new RegExp("(" + start + "(?:" + words.map(esc).join("|") + "))", "giu");
  const list = el("ul", "topics hits");
  for (const d of hits.slice(0, 200)) {
    const at = Math.min(...res.map(r => d.lx.search(r)).filter(i => i >= 0));
    let href = d.h, tag = d.k;
    if (d.p && isFinite(at)) {
      const page = d.p.filter(start => start <= at).length;
      href += "#page=" + page;
      tag += " · p. " + page;
    }
    const li = el("li"), a = el("a", "", d.t);
    a.href = href;
    li.append(a, el("span", "tag", tag));
    if (d.u) li.append(el("p", "where", d.u));
    if (isFinite(at)) {
      const from = Math.max(0, at - 70);
      const snippet = el("p");
      d.x.slice(from, at + 150).split(mark).forEach((part, i) =>
        snippet.append(i % 2 ? el("mark", "", part) : part));
      if (from) snippet.prepend("…");
      li.append(snippet);
    }
    list.append(li);
  }
  out.append(list);
}
q.addEventListener("input", run);
// A unit or an announcement lives in a tab: open that tab before jumping to it.
out.addEventListener("click", e => {
  const href = e.target.closest("a")?.getAttribute("href") || "";
  if (!href.startsWith("#")) return;
  document.getElementById(href[1] === "n" ? "t-news" : "t-content").checked = true;
  q.value = "";
  run();
});
// The landing page's results link to course/index.html#n123. An announcement
// sits in a hidden tab, where the browser cannot scroll to it: open its tab.
function openTarget() {
  const id = location.hash.slice(1), target = document.getElementById(id);
  const tab = document.getElementById(/^n\d+$/.test(id) ? "t-news" : /^u\d+$/.test(id) ? "t-content" : "");
  if (target && tab) {
    tab.checked = true;
    target.scrollIntoView();
  }
}
addEventListener("hashchange", openTarget);
openTarget();
// "/" jumps to the search box from anywhere, in place of the browser's own
// use of it (Quick Find in Firefox), unless you are typing in a text field.
addEventListener("keydown", e => {
  if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.target.matches?.("input:not([type=radio]), textarea, select, [contenteditable]")) return;
  e.preventDefault();
  q.focus();
  q.select();
}, true);
run();  // the browser may restore the box's text on reload
"""

# Collects each course's search entries on the landing page; see _landing_html.
# Links become relative to the landing page, and the course name leads the
# trail, so "crypto rsa" keeps to the cryptography course.
LANDING_JS = r"""
const all = [];
function take(folder, name) {
  for (const d of window.SEARCH || []) {
    const href = /^[a-z]+:/.test(d.h) ? d.h : folder + "/" + (d.h.startsWith("#") ? "index.html" : "") + d.h;
    all.push({...d, h: href, u: d.u ? name + " › " + d.u : name});
  }
  window.SEARCH = undefined;  // a course without _search.js must not repeat the last one
}
"""
