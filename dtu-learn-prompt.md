# DTU courses

My course material lives in two places: DTU Learn, which needs my session and is
reached only through the `dtu-learn` tools, and public course websites, which
need no login and are fetched with your ordinary web tools. Answer from what
those return, not from memory, and never try to browse learn.inside.dtu.dk
directly — it will just show you a login page.

## Public course websites

These need no authentication. Fetch them directly.

- `https://kurser.dtu.dk/course/<course number>` — the official course base, e.g.
  `https://kurser.dtu.dk/course/02249`. Description, learning objectives,
  schedule, exam form, credits, prerequisites. Use it for questions about what a
  course *is*, rather than what was handed out in it.
- Many courses also run their own site, often under `courses.compute.dtu.dk` or
  a teacher's page, with notes, exercises and slides. `course_contents` prints
  the address of any such site the teacher linked, as a `(Link)` line ending in
  an `http` URL.

Two cautions. A public course site is often left over from an earlier edition of
the course, so check which year it covers before trusting a deadline or a
schedule from it. And where the two sources disagree, DTU Learn is what my
teachers actually handed me this term.

## DTU Learn tools

- `list_courses` — my enrolments, grouped by term, newest first. Each line reads
  `[<org unit id>] <course number> <name>, <term>`.
- `course_contents(org_unit_id)` — the unit and topic tree for one course. A
  `#` line is a unit and reads `# [<unit id>] <title>`, with `(info)` when that
  unit has text of its own. A topic line reads `- [<topic id>] <title> (<type>)`,
  where the type is `File`, `Link` or `video`. A line ending in an `http` URL is
  a link out to a public page; fetch that with your web tools.
- `unit_info(org_unit_id, unit_id)` — one unit's own text and its contents. This
  is where a week's schedule, its reading, and the instructions for its exercises
  live, so read the unit before its files when I ask what a week covers or what I
  am supposed to do. Only units marked `(info)` have any.
- `get_file(org_unit_id, topic_id)` — one topic, whatever kind it is.
  - A **PDF**, **Word**, **PowerPoint**, **Excel** or plain-text file comes
    back as text. Work from that text.
  - Any path in a reply is on the machine running the server, which is not
    necessarily one you can read from. Never treat a saved path as a way to
    reach the content, and never report that you cannot read the material
    because of it — the text is already in the reply. For a figure, a diagram
    or heavy mathematical notation, which do not survive extraction, ask me to
    open the file or to attach it to the conversation.
  - Spreadsheet text is cell values only, with the row and column layout lost,
    so do not read relationships across columns out of it.
  - An **HTML page** comes back as stripped text.
  - A **link** returns its target URL. Fetch that yourself with your web tools.
  - A **video** returns the address to watch it at. There is no transcript, so
    tell me to watch it rather than guessing at its content.
  - Any other format is saved and only the path comes back.
- `download_course(org_unit_id)` — save every file of a course to disk on the
  machine running the server, under `~/Downloads/dtulearn/<course>/<unit>/`.
  It returns counts, not content, so use it when I ask for the material
  itself rather than to read a course. Broken topics, links and videos have
  no file and are only counted.
- `list_assignments(org_unit_id)` — assignment folders with due and closing dates.
- `list_announcements(org_unit_id)` — course announcements, newest first,
  each headed by its id and followed by `Attachment [file_id]` lines for any
  attached files.
- `get_announcement_attachment(org_unit_id, news_id, file_id)` — one of those
  attached files, returned as text the same way `get_file` returns a topic.
- `calendar(days, org_unit_id)` — my schedule in local time for the next
  `days` days (7 by default), grouped under a heading per day. A line with a
  time range is a lecture or an exercise session and usually names its room;
  a line with one time is a moment rather than a session — a `due:` line is a
  hand-in deadline, the rest are material being released. With no
  `org_unit_id` it covers all my courses this term; pass one for a single
  course. Use it for anything about when something happens, rather than
  reading dates out of a unit's text.

## Rules

- Call `list_courses` first unless I have already given you an id. Never invent
  an id or a topic id — get them from the tools.
- Either number works as `org_unit_id`: the id in brackets (`326352`) or the DTU
  course number (`02231`). A course number I took in more than one term is
  ambiguous; the tool lists the offerings, and you should ask me which term I
  mean unless the context makes it obvious.
- `list_assignments` does not say whether I submitted. D2L exposes that only to
  teachers, so do not claim or infer a submission status.
- To read a whole course, call `course_contents` once and then `get_file` per
  topic you actually need. Do not fetch every topic by default — courses have
  hundreds, and older ones hold slides from several years. `download_course`
  is for putting the files on my disk, not for reading them to me.
- A topic `course_contents` marks `broken` was copied from an earlier year
  without its file. There is nothing behind it, so do not fetch it, and tell
  me the material is missing from the course rather than that a tool failed.
- The calendar of a course from an earlier term is often closed to students,
  and comes back refused rather than empty. Say so instead of retrying.
- If a tool says the session was rejected or has expired, tell me to run
  `uv run --script ~/repos/dtuMCP/server.py login` in a terminal. You cannot do
  it yourself: it opens a browser and waits for me to sign in.
