# dtuMCP

MCP server for DTU Learn (D2L Brightspace) course material.

DTU's Valence OAuth API needs an admin-registered application, so this server
authenticates with your own browser session cookies instead.

## Setup

```bash
uv run --script server.py login
```

That lists the Firefox profiles it finds (regular, snap and flatpak
installs) and asks which one to use. It opens DTU Learn in that
profile. Sign in, press Enter in the terminal, and the session cookies land in `~/.config/dtumcp/cookie` (mode 600). It prints your
course list to confirm.

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

Firefox only: Chromium browsers (Chrome, Brave, Edge) encrypt their cookies
with a key kept in the desktop keyring, and LibreWolf never writes session
cookies to disk. The list puts each install's `[Install...]` profile from `profiles.ini` first and marks it
`(default)` — that is the one the browser actually launches, while the
`Default=1` flag often points at a stale older profile. Set
`DTU_FIREFOX_PROFILE` to a profile directory to skip the scan.
`DTU_COOKIE` still overrides everything if you would rather paste a Cookie
header from devtools.

## Registering it with a client

Each of these starts the server over stdio, as a child process on your own
machine. Nothing listens on a port and nothing is exposed to the network.

Use the **absolute path to `uv`**. Anything launched from a desktop icon or a
`.desktop` entry does not inherit your shell's `PATH`, so a bare `uv` fails with
`spawn uv ENOENT`. `which uv` gives you the path to use.

**Claude Code** — registers for the CLI and the VS Code extension at once:

```bash
claude mcp add --scope user dtu-learn -- ~/.local/bin/uv run --script ~/repos/dtuMCP/server.py
```

**Cursor** — `~/.cursor/mcp.json` for every project, or `.cursor/mcp.json` in
one project. Then Customize → MCP and toggle `dtu-learn` on.

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

**Claude Desktop** — same `mcpServers` block, merged into the existing config
rather than replacing it, since that file also holds your preferences:

- Linux: `~/.config/Claude/claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

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

- **Claude Code** — `~/.claude/CLAUDE.md` for every session, or a project
  `CLAUDE.md`.
- **Cursor** — `~/.cursor/rules/dtu-learn.mdc`, or Settings → Rules.
- **Claude Desktop** — a Project's custom instructions.

## Tools

| Tool | What it returns |
| --- | --- |
| `list_courses` | Enrolled courses grouped by term, newest first |
| `course_contents(org_unit_id)` | Unit/topic tree, with their IDs and any external links |
| `unit_info(org_unit_id, unit_id)` | One unit's own text, plus its contents |
| `get_file(org_unit_id, topic_id)` | A topic's content, whatever kind it is (see below) |
| `list_assignments(org_unit_id)` | Assignment folders and due dates |
| `list_announcements(org_unit_id)` | Course announcements |
| `get_announcement_attachment(org_unit_id, news_id, file_id)` | A file attached to an announcement, read like `get_file` |
| `calendar(days=7, org_unit_id=0)` | What is scheduled in the next `days` days |
| `download_course(org_unit_id)` | The whole course saved for offline use, with an `index.html` to browse it |
| `download_term(term="")` | Every course of a term saved the same way; the newest term if none is given |

Anywhere an `org_unit_id` is asked for, the DTU course number works too:
`course_contents 02231` is the same as `course_contents 326352`. A course
number offered in more than one term is ambiguous, so the error lists the
offerings and their ids instead of picking one.

Binary downloads land in `~/Downloads/dtulearn` (override with `DTU_DOWNLOADS`).
A unit (D2L calls it a module) is one of the `#` groups in a course tree. Most
carry text of their own — a week's schedule, instructions for the exercises, a
welcome message — which `course_contents` marks with `(info)` and `unit_info`
returns in full. Announcements are separate and belong to the course rather than
the unit; `list_announcements` has those, with the ids of any attached files.

`download_course` makes an offline copy of a course. It walks the whole
content tree and saves every file under `~/Downloads/dtulearn/<course>/<unit>/`,
eight at a time, naming each file after its topic so a second run overwrites
instead of piling up copies. Announcement attachments go in
`<course>/Announcements/`. Beside them it writes `index.html`, one page laid
out like the course site, with two tabs. Content has a tree of the units on
the left, folded until you open a branch, and on the right every unit with its
own text and its topics, each linked to the saved file and tagged with its
type. A unit inside another one shows the units above it as links over its
heading, and a coloured bar on its left marks what it holds. Announcements has
every announcement with its attachments. The page follows the system's dark
mode.

`~/Downloads/dtulearn/index.html` is the landing page: every course downloaded
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

Links and videos hold no file, so the index links to them online. Neither do
broken topics: a course copied from an earlier offering keeps entries whose
file did not come along, which D2L flags and `course_contents` prints as
`(File, broken)`.

Unit text, announcements and HTML pages can show pictures and link to files in
the course's file area (`/content/...` on DTU Learn). Those are fetched too,
kept under `<course>/_linked/` by their DTU Learn path, and the pages are
pointed at the copies. Quicklinks from one page to another topic, quiz or tool
stay online: D2L names their target with an opaque code, so there is no local
file to point them at. Pictures and links on other websites stay online too.

`download_term` does the same for every course of a term, written like the
headings of `list_courses` (`"Fall 2026"`, `"Jan 25"`), or the newest term
when left out. A course that fails is reported and the others still download.

Both end with counts, not text — use `get_file` for the content of one topic.
One row per course, then anything that went wrong, then the landing page:

```
         files  pictures  announcements  links  videos  broken
  02249     45         0             11      9       0      75
  02231     54         1             12      0       2       0

  Open ~/Downloads/dtulearn/index.html to see every course.
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
message; the progress number only rises.

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
Extraction needs `pdftotext` from poppler-utils; without it, or for a scanned
PDF with no text layer, `get_file` just returns the path. Figures and heavy
mathematical notation do not survive extraction, so the reply names the file.
Point `DTU_BASE` at another Brightspace instance to use this elsewhere.

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

```bash
python3 test_server.py
```

## Local CLI (paste into any AI)

Run a tool directly and print the result. Nothing listens, nothing is exposed.

```bash
uv run --script server.py --help
uv run --script server.py list_courses
uv run --script server.py course_contents 02231
uv run --script server.py get_file 12345 67890
uv run --script server.py download_term "Fall 2026"
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

1. Pick a secret path and start a tunnel:

```bash
cloudflared tunnel --url http://localhost:8000
```

2. Note the `https://<random>.trycloudflare.com` hostname it prints, then start
   the server with it:

```bash
export DTU_MCP_PATH="/$(openssl rand -hex 16)/mcp"
export DTU_ALLOWED_HOST='<random>.trycloudflare.com'
uv run --script server.py
echo "https://$DTU_ALLOWED_HOST$DTU_MCP_PATH"
```

3. On claude.ai: **Customize → Connectors → + → Add custom connector**, paste
   that full URL, leave the OAuth fields empty, click **Add**.

`DTU_ALLOWED_HOST` is required in HTTP mode — it keeps DNS rebinding protection
on. A quick tunnel gets a new hostname every restart, so both the hostname and
the connector URL change each time; a named Cloudflare tunnel gives you a
stable one. Your machine has to stay online for the connector to work.
