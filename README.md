# dtuMCP

MCP server for DTU Learn (D2L Brightspace) course material.

DTU's Valence OAuth API needs an admin-registered application, so this server
authenticates with your own browser session cookies instead.

## Setup

The server needs [uv](https://docs.astral.sh/uv/getting-started/installation/),
which fetches Python and the server's dependencies itself. Install it, then
clone this repository.

Linux and macOS:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows, in PowerShell (or `winget install --id=astral-sh.uv -e`):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Open a new terminal afterwards so it finds `uv`. Every `uv run` command in this
README works the same in PowerShell, cmd or a Linux shell, run from the
repository folder. For PDF text and page images, also install poppler (see
[Tools](#tools)).

Then sign in:

```bash
uv run --script server.py login
```

That lists the Firefox profiles it finds (regular, snap and flatpak installs
on Linux, the regular install on Windows), then Chrome if it is installed,
then your default browser, and asks which one to use. It opens DTU Learn in that profile. Sign in, press Enter in
the terminal, and the session cookies land in `~/.config/dtumcp/cookie` (mode
600; `%USERPROFILE%\.config\dtumcp\cookie` on Windows). It prints your
course list to confirm.

Chrome opens in a window of its own, on a profile kept in
`~/.config/dtumcp/chrome`. Chrome encrypts its cookie database (on Windows with
a key only Chrome itself may use) and keeps session cookies in memory, so
`login` asks the running browser for them over its DevTools protocol, which
Chrome serves only for a profile other than your usual one. Sign in there,
press Enter, and `login` closes the window once it has the cookies. The profile
stays, so DTU's sign-in may remember you next time; it holds your sign-in, so
keep it as private as the cookie file. While that window is open, other
programs on this computer can reach its DevTools port too.

With the default browser, the cookies cannot be read off the disk, so `login`
asks you to paste them: open the developer tools (F12), then Storage (Firefox)
or Application (Chrome, Edge) → Cookies → `https://learn.inside.dtu.dk`, and
copy the values of `d2lSessionVal` and `d2lSecureSessionVal`.

`d2lSessionVal` is a session cookie, and Firefox never writes those to
`cookies.sqlite` — they live in memory, mirrored into
`sessionstore-backups/recovery.jsonlz4` for session restore. So `login` reads
both files. Firefox rewrites the session-restore file every ~15 seconds; if
`login` reports no cookie right after you signed in, wait a moment and run it
again.

That file is your DTU session: anyone who reads it is logged in as you. Keep it
off shared machines and out of git.

One `login` covers every client: they all read the same file.

Sessions expire after a few hours of inactivity or at logout. When a tool says
the session was rejected, run `login` again.

Other browsers (Edge, Brave, LibreWolf, which never writes session cookies to
disk) go through the default browser entry. The list puts each install's `[Install...]` profile from `profiles.ini` first and marks it
`(default)` — that is the one the browser actually launches, while the
`Default=1` flag often points at a stale older profile. Set
`DTU_FIREFOX_PROFILE` to a profile directory to skip the scan.
`DTU_COOKIE` still overrides everything if you would rather paste a Cookie
header from devtools.

## Registering it with a client

Each of these starts the server over stdio, as a child process on your own
machine. Nothing listens on a port and nothing is exposed to the network.

Use the **absolute path to `uv`** and to `server.py`. Anything launched from a
desktop icon, the Start menu or a `.desktop` entry may not inherit your shell's
`PATH`, so a bare `uv` fails with `spawn uv ENOENT`. `which uv` gives you the
path to use; on Windows, `where.exe uv` (usually
`C:\Users\you\.local\bin\uv.exe`). Replace `you` and `repos\dtuMCP` below with
your user name and where you cloned the repository.

**Claude Code** — registers for the CLI and the VS Code extension at once.

Linux and macOS:

```bash
claude mcp add --scope user dtu-learn -- ~/.local/bin/uv run --script ~/repos/dtuMCP/server.py
```

Windows (write the paths out in full: PowerShell and cmd do not expand `~` for
other programs):

```powershell
claude mcp add --scope user dtu-learn -- C:\Users\you\.local\bin\uv.exe run --script C:\Users\you\repos\dtuMCP\server.py
```

**Cursor** — `~/.cursor/mcp.json` (`%USERPROFILE%\.cursor\mcp.json` on
Windows) for every project, or `.cursor/mcp.json` in one project. Then
Customize → MCP and toggle `dtu-learn` on.

```json
{
  "mcpServers": {
    "dtu-learn": {
      "command": "/home/you/.local/bin/uv",
      "args": ["run", "--script", "/home/you/repos/dtuMCP/server.py"]
    }
  }
}
```

On Windows, double every backslash in JSON (or use forward slashes):

```json
{
  "mcpServers": {
    "dtu-learn": {
      "command": "C:\\Users\\you\\.local\\bin\\uv.exe",
      "args": ["run", "--script", "C:\\Users\\you\\repos\\dtuMCP\\server.py"]
    }
  }
}
```

**Claude Desktop** — same `mcpServers` block (the Windows one on Windows),
merged into the existing config rather than replacing it, since that file also
holds your preferences. Settings → Developer → Edit Config opens it:

- Linux: `~/.config/Claude/claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`, or for the Microsoft
  Store install
  `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`

Both paths must exist on your machine: a Linux path left in on Windows fails
with `spawn /home/you/.local/bin/uv ENOENT`, or, in `args`, with `can't open
file 'C:\\home\\you\\repos\\dtuMCP\\server.py'`.

**Quit the client fully and reopen it after any change** — to the config or to
`server.py`. An MCP server is spawned once at startup and held for the life of
the client, so a running one keeps executing the copy of `server.py` it loaded
when it started. Editing the file changes nothing until the client restarts.

Claude Desktop also rewrites its config file when preferences change, so edit it
while the app is closed or your `mcpServers` block can be overwritten.

## Telling your agent to use it

`dtu-learn-prompt.md` is a set of instructions to give whatever agent you point
at your coursework. Having the tools available is not the same as an agent
reaching for them: without this it will answer course questions from memory,
invent org unit ids, or report material as unreachable because a reply mentioned
a file path. The file also covers the public course websites, which need no
login and are fetched with ordinary web tools rather than these.

Paste its contents into:

- **Claude Code** — `~/.claude/CLAUDE.md` (`%USERPROFILE%\.claude\CLAUDE.md`
  on Windows) for every session, or a project `CLAUDE.md`.
- **Cursor** — `~/.cursor/rules/dtu-learn.mdc`
  (`%USERPROFILE%\.cursor\rules\dtu-learn.mdc` on Windows), or Settings → Rules.
- **Claude Desktop** — a Project's custom instructions.

## LearnHarness

`learnharness/` is a LearnHarness plugin. It gives the teacher, practice and
prober agents every tool below, named `dtu_list_courses`, `dtu_get_file` and
so on, with instructions on when to use them, so `dtu-learn-prompt.md` is not
needed there. Those instructions also make your course the reference when
you study from it: its definitions and notation, its pages checked before
teaching, hand-ins and quizzes left for you to do under the course's own rules
on generative AI. The prober finds where the course is from its calendar and
current unit, the teacher plans from the course's material and points to its
exercises, and the practice agent draws on its exercise sheets and old exams
but never poses an open graded question. They also get `dtu_search_courses`, the MCP's `search_courses`
(see [Search](#search)), returning each match's
course, units, its best PDF page (and others as good) and the ids the other tools take, so a tutor can find where something is
covered and then read just that page. Course material has its own words for
things, so after the matches with every word it lists ones with some of them,
each saying which words it lacks. It also adds a **DTU Learn** view to the sidebar: your courses by
term and, for each downloaded course, its units and topics. Click a course's
name to open it beside the tree on its page, which has tabs:

Each `dtu_` tool call in a session shows as a card saying what the tutor
did, such as *Looked at pages 5–7 of "Slides from Lecture 7"*, with **Open in
DTU Learn** for a file, its pages, a unit or a course. A search's card lists
its best matches, each opening where it is; a card for PDF pages shows the
pages the tutor looked at, each opening at itself. Every card keeps what the
tool returned, as the tutor got it, in a fold.

When a tutor points you to course material, its answer carries a card per
item: the file's or unit's title, its course and the pages, which opens it
over the chat, at the first of those pages, in the same browser as the
session's **DTU Learn** button. The tutors write them as a `dtu-learn` code block, a line per
item, `topic <org unit id> <topic id> pages <a>-<b> | <what to look at>` or
`unit <org unit id> <unit id> | ...`, which the plugin draws; a line naming
something your saved courses lack shows as such.

- **Announcements**, the one it opens on, from the saved copy, with their
  attachments.
- **Assignments**: due and closing dates and how far off they are, group
  hand-ins, points, instructions, and the names of attached files.
- **Calendar**: the next five months by day, deadlines marked.
- **Groups**: each group category with your group in it and its members, with
  their e-mail addresses, and when sign-up opens or closes.
- **Quizzes**: when each opens, is due and closes, attempts and time limit.
- **Coverage**: what you have studied against the course's units, below.

Upcoming assignments and quizzes come first, soonest at the top, and past ones
last, dimmed. **Open on DTU Learn** opens one in your browser, to hand it in or
take it. **Coverage** holds LearnHarness's map of what you have studied, the
statements it extracts from your sessions and files, against the saved course:
each statement is found in the course by its name or an alias through the
course's search (a week's slides before a book that spans the whole course),
and each top-level unit lists what you have studied there, a chip per
statement that opens where the course covers it, or says nothing has been
studied from it yet. The map fills only when you run **Extract** on
LearnHarness's graph page (or **Extract files into the graph**); until then the
tab says so. It needs no connection. These four tabs are read live from DTU Learn when first opened (a
course not downloaded has them too), with when they were read and **Read
again**; offline they say why they cannot be. A unit's name opens it and shows
its own text and what it holds.
Unit text and announcements are shown from their HTML, so their links work,
rebuilt from a short list of plain tags with every attribute but a link's
address left out; scripts, styles, frames and forms are dropped, and a
picture shows as its description. Click a topic to
read it there too: a PDF shows its pages as they look, equations
and figures included, and anything else its text. A PDF's text can be selected
and copied, to quote it to your agent, and the buttons above it or Ctrl+scroll
(a touchpad pinch) zoom it; **100%** fits it to the width again.

Links work in texts, announcements and PDFs. A click previews the page inside
LearnHarness, over the view, and closing the preview comes back to where you
were. Ctrl+click or a middle click opens it in your browser instead, and the
preview has **Copy link** and **Open in browser**. The preview shows the page
itself where the site lets other apps frame it, with **Show text** for its
text, and only its text where the site does not (GitHub, for one). A PDF's
links to its own pages scroll there.

A DTU Learn page cannot be framed: the frame has no DTU Learn session, and
DTU's sign-in page refuses to be framed itself (`X-Frame-Options: DENY`). So a
DTU Learn link to a topic or a unit of a downloaded course previews that topic
or unit from the saved copy, a PDF drawn as in the pane. Short links
(`quickLink.d2l?...&rcode=...`) name their target by a code, so the plugin asks
DTU Learn once, with your session, where one leads. A link to a file in the
course's file area, `/content/...` or a short link to a course file
(`type=coursefile`), previews the copy `download_course` saved, a PDF in the
viewer and anything else as text, with **Open file**. Anything else, such as a
quiz, a discussion, a checklist or a course you have not downloaded, says why
and offers to open it in your browser.

A video plays in the pane, streamed from Brightspace's media service, where
DTU Learn keeps videos apart from the course's files. Videos are large, so
`download_course` leaves them out: **Download**, whose tooltip gives the
video's size, shows its progress as it saves one into the course
folder, next to its unit's files, and from then on **Open file** plays it in
your video player, offline too, and the tree marks it `video, saved`. A course
refresh keeps it. The media service does not take the DTU Learn session; the
plugin does what the player does, getting a short-lived token from DTU Learn
with your session and, with it, a signed link to the video, which names the
topic it belongs to. If LearnHarness will not play the stream, the pane says
so; Download and Open file still work. LearnHarness does not let a page
element take the screen, so the player's own full-screen button is hidden;
**Full screen** has the video fill the window instead, until Escape, a double
click or **Exit full screen**.

The search box above the tree searches every downloaded course through the
MCP's index (see [Search](#search)): titles,
the units above them, unit text, announcements and the text inside files, a
PDF's by page. The plugin keeps one `server.py search_serve` process for its
searches, which takes about a second to start on the first. `/` goes to it from anywhere in the view. Its results take the
tree's place, with the words marked in the text around them. A click
anywhere on one opens it, unless it ends a drag that selects text to copy;
Enter in the box opens the first. A result opens in the pane with the words marked in it: a PDF at
the first match from the page the result named, a text or a unit scrolled to
its first, an announcement scrolled to and outlined. The tree opens down to
it, its row marked, and the results stay while you open them. Escape, from
anywhere in the view, or emptying the box leaves the search and shows the
tree where the result sits.

A PDF has a find row under its title (Ctrl+F goes to it): it marks every
match on the pages, counts them, and Enter or the arrows go to the next one,
Shift+Enter to the one before. A PDF opened from a search result arrives with
the search's words in it, so its matches can be stepped through from there.
A PDF result opens at its best page. Files a course's pages link to are left out of a
copy saved before the record listed them (`linked` in `_course-<id>.json`);
**Refresh** the course to find them.

In a teaching or practice session, **DTU Learn** in the top bar opens the
whole view over the chat: the tree, search, PDFs, course pages and Send to
tutor, without leaving the session. Its X, Escape or the button again closes
it, and the chat is as you left it. While something waits for the tutor, the
button says how much, **DTU Learn · 2 for tutor**.

**Send to tutor** takes course material into a teaching or practice session.
Select text anywhere in the pane, in a PDF, a file's text, a unit or a course
page, and a **Send to tutor** button appears under it; a PDF's **Send page**
sends the page you are looking at, drawn as an image the way
`get_pdf_pages` draws pages, so equations and figures arrive as they look.
What you send waits under the tree, where each item can be removed, or
clicked to go back to where it is from, at its page; it goes
with your next message in any teaching or practice session: quotes as text
under what you wrote, each saying where it is from (file, page, course and
the ids the `dtu_` tools take), pages as images. The list empties only once
the message has been accepted. It holds nothing across a restart of the app.
In an open session, Send works with an empty box while something waits; a new
session's first message needs text of its own, as LearnHarness asks of it,
and carries the material all the same.
A model that takes no images refuses a message with a page, and keeps what you
wrote; remove the page and send again.

The bar between the tree and the pane can be dragged to share the width
differently, or focused and moved with the arrow keys; a double click puts it
back. The width is remembered, and a PDF is redrawn sharp at its new size. **Open file** opens a saved
file in its usual app. **Download** or **Refresh** on a course saves or updates its offline
copy.

Install it, and again after any change to `server.py`:

```bash
uv run learnharness/install.py
```

That copies the plugin, `server.py` and its tool list into
`~/.config/LearnHarness/data/plugins/dtu-learn/dist/`
(`%APPDATA%\LearnHarness\data\plugins\dtu-learn\dist\` on Windows). If **Settings →
Community plugins** shows a different plugins directory, pass that one as the
argument. Restart LearnHarness, which loads plugin code only at start, and
enable **DTU Learn** under Settings → Community plugins.

The plugin runs its copy of `server.py` through `uv`. It looks for `uv` on
`PATH`, in `~/.local/bin` and in `~/.cargo/bin` (under `%USERPROFILE%` on
Windows), since an app started from a desktop entry or the Start menu may not
get your shell's `PATH`. It reads the same session file
as every other client, so one `login` covers it too.

PDFs are drawn with [pdf.js](https://mozilla.github.io/pdf.js/) 6.4.299
(Apache-2.0, licence in `learnharness/dist/assets/pdfjs/`), only its two
built modules, renamed from `.mjs` to `pdf.min.js` and `pdf.worker.min.js`
because LearnHarness does not serve `.mjs` as JavaScript. A page is drawn when it
comes near the screen and cleared when it is far off, so a long deck holds only
a few pages' pixels. Left out to keep the plugin small: the character maps for
CJK fonts, the 14 standard fonts for PDFs that do not embed theirs, and the
JPEG 2000 decoder. Such a PDF still draws, with substitute fonts or without
those images; if one cannot be drawn at all, its text is shown instead.

## Tools

| Tool | What it returns |
| --- | --- |
| `list_courses` | Enrolled courses grouped by term, newest first |
| `course_contents(org_unit_id)` | Unit/topic tree, with their IDs and any external links |
| `unit_info(org_unit_id, unit_id)` | One unit's own text, plus its contents |
| `get_file(org_unit_id, topic_id)` | A topic's content, whatever kind it is (see below) |
| `get_pdf_pages(org_unit_id, topic_id, first_page, last_page=0)` | Pages of a PDF topic as images, for figures and notation |
| `list_assignments(org_unit_id)` | Assignment folders and due dates |
| `list_announcements(org_unit_id)` | Course announcements |
| `get_announcement_attachment(org_unit_id, news_id, file_id)` | A file attached to an announcement, read like `get_file` |
| `search_courses(query, org_unit_id=0, limit=15)` | Where words occur in the downloaded courses, with the ids and PDF page to read next |
| `calendar(days=7, org_unit_id=0)` | What is scheduled in the next `days` days |
| `download_course(org_unit_id)` | The whole course saved for offline use, with an `index.html` to browse it |
| `download_term(term="")` | Every course of a term saved the same way; the newest term if none is given |

Course material is fetched once and then read from disk. `course_contents`,
`unit_info`, `get_file`, `list_announcements` and
`get_announcement_attachment` answer from the course's offline copy (see
`download_course` below). The first of them to ask about a course with no
copy downloads it, which takes a minute or two; every later call is local.
`course_contents` and `list_announcements` start with the date the copy was
saved. Run `download_course` again to bring a copy up to date. `calendar` and
`list_assignments` always ask DTU Learn.

Copies keep working when the session has expired or there is no network.
`list_courses` then lists the courses saved offline, and says why DTU Learn
could not be reached. A copy made before this existed has no `_course-<id>.json`,
so its course is downloaded again on first use.

Anywhere an `org_unit_id` is asked for, the DTU course number works too:
`course_contents 02231` is the same as `course_contents 326352`. A course
number offered in more than one term is ambiguous, so the error lists the
offerings and their ids instead of picking one.

Saved courses and files are kept in the data folder: `~/.local/share/dtumcp/courses`
on Linux (or under `$XDG_DATA_HOME`), `%LOCALAPPDATA%\dtumcp\courses` on Windows
and `~/Library/Application Support/dtumcp/courses` on macOS. `DTU_DOWNLOADS`
names another.
A unit (D2L calls it a module) is one of the `#` groups in a course tree. Most
carry text of their own — a week's schedule, instructions for the exercises, a
welcome message — which `course_contents` marks with `(info)` and `unit_info`
returns in full. Announcements are separate and belong to the course rather than
the unit; `list_announcements` has those, with the ids of any attached files.

`download_course` makes an offline copy of a course. It walks the whole
content tree and saves every file under `<course>/<unit>/` in the data folder,
eight at a time, naming each file after its topic so a second run overwrites
instead of piling up copies. Windows limits a path to 260 characters unless
long paths are switched on (`LongPathsEnabled` in the registry), and a deeply
nested course can pass that. Announcement attachments go in
`<course>/Announcements/`. `_course-<id>.json` keeps the content tree, the
announcements, where each file went, and which saved file each of the pages'
links to the course's file area stands for; the reading tools and the
LearnHarness view answer from it.
Beside them it writes `index.html`, one page laid
out like the course site, with two tabs. Content has a tree of the units on
the left, folded until you open a branch, and on the right every unit with its
own text and its topics, each linked to the saved file and tagged with its
type. A unit inside another one shows the units above it as links over its
heading, and a coloured bar on its left marks what it holds. Announcements has
every announcement with its attachments. The page follows the system's dark
mode.

`index.html` in the data folder is the landing page: every course downloaded
so far, grouped by term with the newest first, each with the date it was
saved. Every course page links back to it from "← All courses". It is rebuilt
from the course folders on each `download_course`, so start there to read
your courses without a connection. Its search box searches every course at
once; each result names its course, so adding a word from the course name,
such as `crypto rsa`, keeps the search to that course.

On either page, `/` jumps to the search box. It replaces the browser's own
use of the key (Quick Find in Firefox), except while you are typing in a text
field.

The search box above the tabs looks through titles, unit text, announcements
and the text inside the saved PDFs, slides, Word documents and HTML pages. Every
word you type has to start a word in the result, its text or the units above
it, so `rsa` finds RSA but not adversary, and `milk description` finds the
Description topic inside the Milk case study. Results show the units they sit
in and the match in context, and a PDF result opens on the page it was found
on. The text is extracted while the course downloads and kept in
`_search.js` beside `index.html`; that is about 8 MB for a course with a hundred
PDFs. Spreadsheets are found by title only, because their cells are mostly
numbers and in one course came to 235 MB. Reading PDFs needs `pdftotext`, as
for `get_file`; without it PDFs are found by title only. Tabs and the tree work
without JavaScript; the search box needs it.

Links hold no file, and videos, being large, are left out; `download_video`
saves one on demand. The index links to both online. Neither do
broken topics: a course copied from an earlier offering keeps entries whose
file did not come along, which D2L flags. They often sit beside this year's
topic of nearly the same title, such as `Slides from Lecture 1 (2025-09-02)`
next to `(2026-09-01)`, so `course_contents` leaves them out and only counts
them on their unit's line: `# [1105457] Week 1 [2 broken topics not listed]`.
The LearnHarness view hides them too; untick **Hide broken topics** to list
them, marked broken.

Unit text, announcements and HTML pages can show pictures and link to files in
the course's file area (`/content/...` on DTU Learn). Those are fetched too,
kept under `<course>/_linked/` by their DTU Learn path, and the pages are
pointed at the copies. So are short links to a course file
(`quickLink.d2l?...&type=coursefile`), which DTU Learn redirects to the file.
Other short links, to another topic, a quiz or a tool, stay online: D2L names
their target with an opaque code, so there is no local file to point them at.
Pictures and links on other websites stay online too.

`download_term` does the same for every course of a term, written like the
headings of `list_courses` (`"Fall 2026"`, `"Jan 25"`), or the newest term
when left out. A course that fails is reported and the others still download.

Both end with counts, not text — use `get_file` for the content of one topic.
One row per course, then anything that went wrong, then the landing page:

```
         files  pictures  announcements  links  videos  broken
  02249     45         0             11      9       0      75
  02231     54         1             12      0       2       0

  Open ~/.local/share/dtumcp/courses/index.html to see every course.
```

`pictures` counts pictures and files the pages link to. `links`, `videos` and
`broken` topics have nothing to download; the index points at the first two
online.

Run from a terminal, both show a live status line in colour: which course of
how many, a small bar for the step it is on (files, search text, pictures and
linked files, writing the pages) and how far along, for example
`[2/4] ███████░░░░░ files 34/54 · 02231 Cryptography fundamentals`. When a
course finishes, its bar collapses into one line that stays: a green `✓` or,
if it failed, a red `✗`. Set `NO_COLOR` for plain text. Nothing is printed
when the output is not a terminal.

MCP clients get the same line as progress notifications, one per update,
if they ask for progress when they call the tool. The counts are in the
message; the progress number only rises. The reading tools report the same
way while they download a course on first use.

`calendar` reads the Brightspace calendar in local time: lectures and
exercise sessions with their rooms, hand-in deadlines, and the dates material
is released. With no `org_unit_id` it covers every course of the newest term
you are enrolled in; give one to ask about a single course. D2L answers with
at most 100 events and offers no way to page past them, so a long window is
reported as capped rather than silently cut.

`get_file` looks the topic up in the table of contents first and answers by
kind: a **file** is downloaded, an **HTML page** comes back as stripped text, a
**link** returns its target URL for you to fetch yourself, and a **video** in
Brightspace's media service returns the address to watch it at, since there is
nothing to extract.

Files are returned as text wherever that is possible: PDF via `pdftotext`, and
`.docx`, `.pptx` and `.xlsx` by unzipping the OOXML with stdlib `zipfile`.
Spreadsheets give cell values without their layout. Anything D2L serves as
`d2l/unknowntype` that decodes as UTF-8 — source code, PRISM models, LaTeX — is
returned as text too. This matters because some MCP clients sandbox their file
tools away from where this server writes: a saved path can be unreachable to the
assistant, so the content has to come back in the reply itself.

PDFs are saved there too, but `get_file` returns their extracted text: a 34-page
lecture deck is about 4k tokens as text against roughly 50k as page images.
Extraction needs `pdftotext` from poppler-utils (`sudo apt install
poppler-utils` on Debian and Ubuntu, `brew install poppler` on macOS; on
Windows, `scoop install poppler`, or a [poppler-windows](https://github.com/oschwartz10612/poppler-windows)
release with its `bin` folder on `PATH`); without it, or for a scanned
PDF with no text layer, `get_file` just returns the path. The text heads each
page `[Page n]`.

Figures and heavy mathematical notation do not survive extraction. For those,
`get_pdf_pages` returns chosen pages as PNG images, drawn by `pdftoppm` from
the same poppler-utils at 1568 pixels on the long side, the most a model takes
in before scaling an image down. A page costs about 2k tokens against roughly
100 as text, so it takes at most 10 pages a call: read the text first, then ask
for the pages that matter. From the command line it names each image instead
of printing it; `--json` prints MCP content blocks with the images in base64.
Point `DTU_BASE` at another Brightspace instance to use this elsewhere.

## Search

`search_courses`, the plugin's search box and its Coverage tab search one
SQLite full-text index over every downloaded course, `_search.sqlite` in the
data folder, with a row per PDF page and one per other item. It is filled
from the `_search.js` each download writes, at the end of the download, and
checked before each search: a course downloaded again since is indexed again,
one whose folder is gone is dropped. Deleting the file is safe; the next search
rebuilds it, about 0.8 s a course. It comes to about two and a half times the
course text: 25 MB for four courses, 198 MB for 32, of which 55 MB are the
lookups that keep two- and three-letter words fast.

Each word matches the start of a word, so `reduc` finds "reductions", and also
each part of a joined-up name or of a number with letters, so `cycle` finds
"HamiltonCycle" and `sat` finds "3SAT"; a hyphenated word is also found
written as one, so `3SAT` and `3-SAT` find each other. A one-letter word
matches only itself. Items with every word come first, then those with some,
each saying which words it lacks. Among them an item ranks by its best page:
how many of the words that page holds, then its BM25 score, which counts a
word for more the rarer it is across the courses, and for more in a title (8×)
or the units above it (3×) than in the text, and favours a short page, so a
slide on the subject beats a book that mentions it in passing. A search takes
5–50 ms with 32 courses, 0.2 s for a word as common as `the`.

The offline pages' search box (above) does not use the index: a page opened
from disk cannot read SQLite, so it scans `_search.js` itself, matching word
starts only.

## How terms are worked out

Courses are grouped under headings like `## Fall 2026` and `## Winter 2025`,
newest first. The term comes from the course name, which DTU spells several
ways (`Fall 2026`, `Fall 23`, `Jan 24`, `January 2025`, `Fall22-Spring23` —
filed under the term it starts in). Names with no term fall back to the
offering code, `DTU_e24_02158`, where `e`/`f`/`s`/`v` are efterår, forår,
sommer and vinter. That code holds the *academic* year, whose January lands in
the next calendar year, so `DTU_v25` is Winter 2026. Anything with neither ends
up under `## No term`.

## Test

From the repository folder:

```bash
uv run test_server.py
```

## Local CLI (paste into any AI)

Run a tool directly and print the result. Nothing listens, nothing is exposed.

```bash
uv run --script server.py --help
uv run --script server.py list_courses
uv run --script server.py course_contents 02231
uv run --script server.py get_file 12345 67890
uv run --script server.py download_term "Fall 2026"
uv run --script server.py download_video 02231 1243820
```

Paste the output into claude.ai or any other chat. This is also the quickest way
to check that your cookies still work.

## Using it from claude.ai in the browser

claude.ai only talks to remote MCP servers over public HTTPS, so the stdio mode
above does not apply. Run the server in HTTP mode and expose it through a
tunnel.

**Read this first.** The tunnel URL is a live door into your DTU account: the
session cookies stay on your machine, but anyone who learns the URL can call
every tool through it. The secret path is the only gate. Use a long random
path, never paste the URL anywhere, and stop the tunnel when you are done.

1. Install [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
   (on Windows, `winget install --id Cloudflare.cloudflared`) and start a
   tunnel:

```bash
cloudflared tunnel --url http://localhost:8000
```

2. Note the `https://<random>.trycloudflare.com` hostname it prints. In a
   second terminal, pick a secret path, print the connector URL and start the
   server with them.

Linux and macOS:

```bash
export DTU_MCP_PATH="/$(openssl rand -hex 16)/mcp"
export DTU_ALLOWED_HOST='<random>.trycloudflare.com'
echo "https://$DTU_ALLOWED_HOST$DTU_MCP_PATH"
uv run --script server.py
```

Windows (PowerShell):

```powershell
$env:DTU_MCP_PATH = "/$([guid]::NewGuid().ToString('N'))/mcp"
$env:DTU_ALLOWED_HOST = '<random>.trycloudflare.com'
"https://$env:DTU_ALLOWED_HOST$env:DTU_MCP_PATH"
uv run --script server.py
```

3. On claude.ai: **Customize → Connectors → + → Add custom connector**, paste
   that full URL, leave the OAuth fields empty, click **Add**.

`DTU_ALLOWED_HOST` is required in HTTP mode — it keeps DNS rebinding protection
on. A quick tunnel gets a new hostname every restart, so both the hostname and
the connector URL change each time; a named Cloudflare tunnel gives you a
stable one. Your machine has to stay online for the connector to work.
