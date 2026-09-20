# dtuMCP

MCP server for DTU Learn (D2L Brightspace) course material.

DTU's Valence OAuth API needs an admin-registered application, so this server
authenticates with your own browser session cookies instead.

## Setup

```bash
uv run --script server.py login
```

That opens DTU Learn in Firefox. Sign in, press Enter in the terminal, and the
session cookies land in `~/.config/dtumcp/cookie` (mode 600). It prints your
course list to confirm.

`d2lSessionVal` is a session cookie, and Firefox never writes those to
`cookies.sqlite` — they live in memory, mirrored into
`sessionstore-backups/recovery.jsonlz4` for session restore. So `login` reads
both files. Firefox rewrites the session-restore file every ~15 seconds; if
`login` reports no cookie right after you signed in, wait a moment and run it
again.

That file is your DTU session: anyone who reads it is logged in as you. Keep it
off shared machines and out of git.

Then register the server once:

```bash
claude mcp add --scope user dtu-learn -- uv run --script /home/ago/repos/dtuMCP/server.py
```

Sessions expire after a few hours of inactivity or at logout. When a tool says
the session was rejected, run `login` again.

Firefox only. It reads the profile named by the `[Install...]` section of
`profiles.ini`, not the `Default=1` flag — those can point at different
profiles, and the `[Install...]` one is what Firefox actually launches. Set
`DTU_FIREFOX_PROFILE` if yours lives elsewhere (snap and flatpak Firefox do). `DTU_COOKIE` still overrides everything if you would
rather paste a Cookie header from devtools.

## Tools

| Tool | What it returns |
| --- | --- |
| `list_courses` | Enrolled courses grouped by term, newest first |
| `course_contents(org_unit_id)` | Module/topic tree, with topic IDs and any external links |
| `get_file(org_unit_id, topic_id)` | A topic's content, whatever kind it is (see below) |
| `list_assignments(org_unit_id)` | Assignment folders and due dates |
| `list_announcements(org_unit_id)` | Course announcements |

Anywhere an `org_unit_id` is asked for, the DTU course number works too:
`course_contents 02231` is the same as `course_contents 326352`. A course
number offered in more than one term is ambiguous, so the error lists the
offerings and their ids instead of picking one.

Binary downloads land in `~/Downloads/dtulearn` (override with `DTU_DOWNLOADS`).
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
