# dtuMCP

MCP server for DTU Learn (D2L Brightspace): course trees, files as text, PDF
pages as images, announcements, assignments, the calendar, and full-text
search over offline copies of your courses.

DTU's Valence OAuth API needs an admin-registered application, so this server
signs in with your own browser session cookies instead.

## Install

Run these in a terminal, not from inside a client: the Microsoft Store build of
Claude cannot set up Python for the programs it starts.

### 1. uv

[uv](https://docs.astral.sh/uv/getting-started/installation/) fetches Python
and the server's dependencies.

```bash
# Linux and macOS
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows (PowerShell), or: winget install --id=astral-sh.uv -e
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. poppler

`pdftotext` and `pdftoppm` read PDF text and draw PDF pages. Without them,
`get_file` returns only a PDF's path, `get_pdf_pages` fails, and search finds
PDFs by title only.

| OS | Command |
| --- | --- |
| Debian, Ubuntu | `sudo apt install poppler-utils` |
| macOS | `brew install poppler` |
| Windows | `winget install --id oschwartz10612.Poppler -e` (or `scoop install poppler`) |

On Windows you can also unpack a
[poppler-windows](https://github.com/oschwartz10612/poppler-windows) release
and put its `bin` folder on `PATH`. Check with `pdftotext -v` in a new
terminal.

### 3. dtumcp

Open a new terminal so it finds `uv`, then:

```bash
uv tool install git+https://github.com/RNS-ago/dtuMCP
```

This puts a `dtumcp` command in `~/.local/bin` (`%USERPROFILE%\.local\bin` on
Windows). Add `--reinstall` to update.

To work on the code, run `uv tool install -e .` in a clone instead: `dtumcp`
then runs the clone's `server.py`, so a `git pull` or an edit takes effect at
the next start. `uv run --script server.py` also works in place of `dtumcp`.

### 4. Sign in

```bash
dtumcp login
```

Pick a browser from the list: your Firefox profiles, Chrome, or your default
browser. DTU Learn opens; sign in, press Enter in the terminal, and it prints
your course list. The cookies are saved to `~/.config/dtumcp/cookie` (mode
600; `%USERPROFILE%\.config\dtumcp\cookie` on Windows), which every client
reads, so one login covers them all.

> **That file is your DTU session.** Anyone who reads it is logged in as you.
> Keep it off shared machines and out of git.

Sessions expire after a few hours idle or at logout. When a tool says the
session was rejected, run `dtumcp login` again.

<details>
<summary>How each browser is read</summary>

- **Firefox** (regular, snap and flatpak on Linux; regular on Windows). The
  profile marked `(default)` is the one Firefox launches; `profiles.ini`'s
  `Default=1` often points at a stale one. Session cookies live only in
  memory and the session-restore file, which Firefox rewrites every ~15
  seconds: if `login` finds no cookie right after you sign in, wait and run it
  again. `DTU_FIREFOX_PROFILE` names a profile directory and skips the scan.
- **Chrome** opens in a window of its own, on a profile kept in
  `~/.config/dtumcp/chrome`. Chrome encrypts its cookie database, so `login`
  asks the running browser over its DevTools protocol, then closes the window.
  The profile keeps your sign-in, so keep it as private as the cookie file.
  While the window is open, other programs on this computer can reach its
  DevTools port.
- **Default browser** (Edge, Brave, LibreWolf, ...): `login` asks you to paste
  the cookies. Open the developer tools (F12), then Storage (Firefox) or
  Application (Chrome, Edge) → Cookies → `https://learn.inside.dtu.dk`, and
  copy `d2lSessionVal` and `d2lSecureSessionVal`.
- `DTU_COOKIE` set to a Cookie header overrides all of the above.

</details>

## Connect a client

Clients start the server over stdio, as a child process. Nothing listens on a
port.

Use the **absolute path** to `dtumcp`: an app launched from a desktop icon or
the Start menu may not get your shell's `PATH`, and a bare `dtumcp` then fails
with `spawn dtumcp ENOENT`. Find it with `command -v dtumcp`, or in PowerShell
`(Get-Command dtumcp).Source` (usually `C:\Users\you\.local\bin\dtumcp.exe`).

**Claude Code** (CLI and VS Code extension):

```bash
claude mcp add --scope user dtu-learn -- "$(command -v dtumcp)"
```

```powershell
claude mcp add --scope user dtu-learn -- (Get-Command dtumcp).Source
```

**Cursor** and **Claude Desktop** take this JSON, with the path written out in
full (their variable support is unreliable on Windows). On Windows use
`"C:\\Users\\you\\.local\\bin\\dtumcp.exe"`: double every backslash, or use
forward slashes.

```json
{
  "mcpServers": {
    "dtu-learn": {
      "command": "/home/you/.local/bin/dtumcp"
    }
  }
}
```

- **Cursor**: `~/.cursor/mcp.json` (`%USERPROFILE%\.cursor\mcp.json`), or
  `.cursor/mcp.json` in one project. Then Customize → MCP and toggle
  `dtu-learn` on.
- **Claude Desktop**: Settings → Developer → Edit Config. Merge the block into
  the existing file, which also holds your preferences, and edit it with the
  app closed, since the app rewrites it. The file is at
  - Linux: `~/.config/Claude/claude_desktop_config.json`
  - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
  - Windows: `%APPDATA%\Claude\claude_desktop_config.json`, or for the
    Microsoft Store install
    `%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`

**Quit the client fully and reopen it after any change** to the config, to
`server.py`, or by `uv tool install --reinstall`. A client starts the server
once and keeps running the code it loaded.

### Tell your agent to use it

Having the tools is not the same as an agent reaching for them. Without
instructions it answers course questions from memory, invents org unit ids, or
calls material unreachable because a reply mentioned a file path. Paste
[`dtu-learn-prompt.md`](dtu-learn-prompt.md) into:

- **Claude Code**: `~/.claude/CLAUDE.md` (`%USERPROFILE%\.claude\CLAUDE.md`),
  or a project's `CLAUDE.md`.
- **Cursor**: `~/.cursor/rules/dtu-learn.mdc`
  (`%USERPROFILE%\.cursor\rules\dtu-learn.mdc`), or Settings → Rules.
- **Claude Desktop**: a Project's custom instructions.

It also covers the public course websites, which need no login and are fetched
with ordinary web tools.

### LearnHarness

[`learnharness/`](learnharness/README.md) is a LearnHarness plugin: it gives
the tutor agents these tools with their own instructions, and adds a DTU Learn
sidebar with a PDF viewer, course pages, search and **Send to tutor**.

### claude.ai in the browser

claude.ai only reaches remote MCP servers over public HTTPS, so this needs the
server in HTTP mode behind a tunnel.

> **The tunnel URL is a live door into your DTU account.** The cookies stay on
> your machine, but anyone with the URL can call every tool. The secret path is
> the only gate: make it long and random, never paste the URL anywhere, and stop
> the tunnel when you are done.

1. Install [`cloudflared`](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
   (Windows: `winget install --id Cloudflare.cloudflared`) and start a tunnel:

   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```

2. Note the `https://<random>.trycloudflare.com` hostname it prints. In a
   second terminal, pick a secret path, print the connector URL and start the
   server:

   ```bash
   # Linux and macOS
   export DTU_MCP_PATH="/$(openssl rand -hex 16)/mcp"
   export DTU_ALLOWED_HOST='<random>.trycloudflare.com'
   echo "https://$DTU_ALLOWED_HOST$DTU_MCP_PATH"
   dtumcp
   ```

   ```powershell
   # Windows (PowerShell)
   $env:DTU_MCP_PATH = "/$([guid]::NewGuid().ToString('N'))/mcp"
   $env:DTU_ALLOWED_HOST = '<random>.trycloudflare.com'
   "https://$env:DTU_ALLOWED_HOST$env:DTU_MCP_PATH"
   dtumcp
   ```

3. On claude.ai: **Customize → Connectors → + → Add custom connector**, paste
   the full URL, leave the OAuth fields empty, click **Add**.

`DTU_ALLOWED_HOST` is required in HTTP mode: it keeps DNS rebinding protection
on. A quick tunnel gets a new hostname on every restart, so the connector URL
changes too; a named Cloudflare tunnel keeps one. Your machine has to stay
online.

## Tools

| Tool | What it returns |
| --- | --- |
| `list_courses` | Enrolled courses grouped by term, newest first |
| `course_contents(org_unit_id)` | Unit/topic tree, with their IDs and any external links |
| `unit_info(org_unit_id, unit_id)` | One unit's own text, plus its contents |
| `get_file(org_unit_id, topic_id)` | A topic's content, whatever kind it is |
| `get_pdf_pages(org_unit_id, topic_id, first_page, last_page=0)` | Pages of a PDF topic as images, for figures and notation |
| `list_assignments(org_unit_id)` | Assignment folders and due dates |
| `list_announcements(org_unit_id)` | Course announcements |
| `get_announcement_attachment(org_unit_id, news_id, file_id)` | A file attached to an announcement, read like `get_file` |
| `search_courses(query, org_unit_id=0, limit=15)` | Where words occur in the downloaded courses, with the ids and PDF page to read next |
| `calendar(days=7, org_unit_id=0)` | What is scheduled in the next `days` days |
| `download_course(org_unit_id)` | The whole course saved for offline use, with an `index.html` to browse it |
| `download_term(term="")` | Every course of a term saved the same way; the newest term if none is given |

- **Course numbers work as ids**: `course_contents 02231` is the same as
  `course_contents 326352`. A number offered in more than one term is
  ambiguous, so the error lists the offerings instead of picking one.
- **Material is read from an offline copy.** The first call about a course
  downloads it (a minute or two); later calls are local and keep working with
  an expired session or no network, when `list_courses` lists the saved
  courses. Run `download_course` again to update a copy. `calendar` and
  `list_assignments` always ask DTU Learn.
- **`get_file` answers by kind.** A file comes back as text where possible:
  PDF via `pdftotext` with each page headed `[Page n]`, `.docx`/`.pptx`/`.xlsx`
  unzipped (spreadsheets as cell values), and anything else that decodes as
  UTF-8. An HTML page comes back as stripped text, a link as its URL, a video
  as the address to watch it at. Text goes in the reply because some clients
  sandbox their file tools away from where the server saves files.
- **PDF page headings say what the text lost**, as `[Page n, has images,
  drawing, math]`, so an agent knows which pages to look at:
  - *images*: a picture `pdfimages` lists, drawn at least half a square inch
    and on no more than half the pages (logos and slide backgrounds are left
    out).
  - *drawing*: a vector figure in `pdftocairo`'s SVG, found by its curves and
    slanted lines, less shapes on most pages (the slide template). A figure of
    level and upright lines alone, such as a grid or a number line, is missed.
  - *math*: 20 or more characters set in math fonts (TeX's, or any font named
    for math), or a displayed formula: a line with math that starts well right
    of the margin, as a centred formula does. Read with `pdftohtml`.

  These are worked out once per PDF while a course downloads (a few seconds
  for a long deck) and kept in its `_course-<id>.json`; a copy saved before
  works them out on each read until it is downloaded again.
- **`get_pdf_pages`** draws pages with `pdftoppm` at 1568 pixels on the long
  side, the most a model takes in before scaling down. A page costs about 2k
  tokens against roughly 100 as text, so it takes at most 10 per call: read the
  text first, then ask for the pages that matter.
- **`calendar`** covers every course of the newest term unless given one, in
  local time. D2L returns at most 100 events, so a long window is reported as
  capped.
- **Broken topics**, entries copied from an earlier offering without their
  file, are left out and counted on their unit's line:
  `# [1105457] Week 1 [2 broken topics not listed]`.

### Offline copies

`download_course` saves every file under `<course>/<unit>/` in the data folder,
named after its topic so a rerun overwrites. Announcement attachments go in
`<course>/Announcements/`, and pictures and course files that pages link to go
in `<course>/_linked/`, with the pages pointed at the copies. Videos are left
out (`dtumcp download_video <course> <topic>` saves one), as are links and
broken topics. `download_term` does every course of a term, written like
`list_courses`'s headings (`"Fall 2026"`, `"Jan 25"`); one failing course does
not stop the rest.

Each course gets an `index.html` laid out like the course site, with Content
and Announcements tabs, and the data folder gets a landing page linking every
saved course. Its search box (`/` focuses it) looks through titles, unit text,
announcements and the text of PDFs, slides, Word documents and HTML pages;
every word must start a word in the result, so `rsa` finds RSA but not
adversary. Add a word from a course's name, such as `crypto rsa`, to search
one course.

The data folder is `~/.local/share/dtumcp/courses` on Linux (or under
`$XDG_DATA_HOME`), `~/Library/Application Support/dtumcp/courses` on macOS, and
`%USERPROFILE%\.local\share\dtumcp\courses` on Windows. Not `AppData`, because
Microsoft Store apps such as Store Python and Claude, and the programs they
start, write there to a private folder nothing else sees. Windows also limits
paths to 260 characters unless `LongPathsEnabled` is on, which a deeply nested
course can pass.

Both tools end with counts:

```
         files  pictures  announcements  links  videos  broken
  02249     45         0             11      9       0      75
  02231     54         1             12      0       2       0

  Open ~/.local/share/dtumcp/courses/index.html to see every course.
```

PDFs set with TeX are fixed as they are saved. Their fonts draw the right
symbols but tell text tools the wrong characters: pdfTeX's map from font codes
to Unicode leaves out the big operators, the calligraphic and blackboard
capitals and the `\big` brackets, so a sum reads as `X` or `P`, 𝒩𝒫 as `N P`,
ℕ as `N`, and brackets as invisible control characters. The download completes
those maps (for TeX's `CMEX`, `CMSY` and `MSBM` fonts) in an update added at
the end of the file, which leaves every original byte and every drawn page as
it was and is kept only when poppler reads the result with as many pages. So
`get_file`, search, a PDF viewer's copy and paste and the LearnHarness viewer
all get ∑, 𝒩𝒫, ℕ and the brackets. Encrypted and signed PDFs are left alone,
and a course saved before this is fixed when it is downloaded again.

In a terminal they show a live status line per course; set `NO_COLOR` for
plain text. MCP clients that ask for progress get the same line as progress
notifications.

### Search

`search_courses` uses one SQLite full-text index over every downloaded course,
`_search.sqlite` in the data folder, with a row per PDF page. It updates
itself before each search, and deleting it is safe: it rebuilds in about 0.8 s
a course.

Each word matches the start of a word, so `reduc` finds "reductions", and each
part of a joined name, so `cycle` finds "HamiltonCycle" and `sat` finds
"3SAT"; `3SAT` and `3-SAT` find each other. Items with every word rank first,
then those with some, each saying which words it lacks. Ranking is BM25 by
best page, weighting titles 8× and unit names 3×, and favouring short pages, so
a slide on the subject beats a book that mentions it. Spreadsheets are found by
title only. A search takes 5–50 ms over 32 courses.

### Terms

`list_courses` groups courses under headings like `## Fall 2026`, read from the
course name in any of DTU's spellings (`Fall 23`, `Jan 24`, `January 2025`,
`Fall22-Spring23` under the term it starts). Otherwise it uses the offering
code, `DTU_e24_02158`, where `e`/`f`/`s`/`v` are efterår, forår, sommer and
vinter. The code holds the *academic* year, so `DTU_v25` is Winter 2026.
Courses with neither go under `## No term`.

## Command line

Run any tool directly and paste the output into any chat. This is also the
quickest check that your cookies still work.

```bash
dtumcp --help
dtumcp list_courses
dtumcp course_contents 02231
dtumcp get_file 12345 67890
dtumcp download_term "Fall 2026"
dtumcp download_video 02231 1243820
```

`get_pdf_pages` names each image instead of printing it; `--json` prints MCP
content blocks with images in base64. `dtumcp --help` lists the environment
variables, including `DTU_DOWNLOADS` for another data folder and `DTU_BASE`
for another Brightspace instance.

## Test

```bash
uv run --no-project test_server.py
```
