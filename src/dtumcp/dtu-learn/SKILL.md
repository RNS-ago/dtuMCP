---
name: dtu-learn
description: How to answer questions about the user's DTU courses with the dtu-learn tools. Use it for anything about a DTU course, from its slides, notes, exercises, hand-ins, announcements and schedule to studying or exam preparation from its material.
---

# DTU courses

My course material lives in two places: DTU Learn, which needs my session and is
reached only through the `dtu-learn` tools, and public course websites, which
need no login and are fetched with your ordinary web tools. Answer from what
those return, not from memory, and never try to browse learn.inside.dtu.dk
directly — it will just show you a login page.

## Working with a course

1. **Find the course.** Call `list_courses` unless I have already given you an
   id or a course number. Never invent an id or a topic id — get them from the
   tools. Either number works as `org_unit_id`: the id in brackets (`326352`)
   or the DTU course number (`02231`). A course number I took in more than one
   term is ambiguous; the tool lists the offerings, and you should ask me which
   term I mean unless the context makes it obvious.
2. **Make sure there is a saved copy, and that it is recent.** Before anything
   else in a course, call `course_contents` on it once per conversation.
   Course material is read from that copy, on the machine running the server,
   so it is fast and works offline.
   - If the course has no saved copy, that call downloads the whole course
     first, which takes a minute or two. Go ahead without asking, but tell me
     it is happening.
   - Its first line names when the copy was saved: `(From the offline copy
     saved <date> <time>. …)`. If that is more than 7 days before today, tell
     me the date and ask whether to work from it as it is or to refresh it
     with `download_course` first, and wait for my answer. Ask once per course
     per conversation. Also refresh it when I ask you to, or when I ask about
     something newer than the copy.
   - Only `list_courses`, `calendar`, `list_assignments`, `download_course`
     and `download_term` ask DTU Learn.
3. **Search before you read.** To find where something is covered, use
   `search_courses` first, not `course_contents` and topic after topic. It
   searches the whole saved copy at once, inside files too, and gives each
   match's topic id (or `linked` path) and its page: a PDF's page, or a
   workbook's sheet.
4. **Read only what the search found.** Then `get_file` or `unit_info` for
   those topics, and `get_pdf_pages` for just the pages whose figures or
   notation matter. Do not fetch every topic by default — courses have
   hundreds, and older ones hold slides from several years. Use
   `course_contents` to see how the course is laid out, or when a search comes
   back with nothing.

## Public course websites

These need no authentication. Fetch them directly.

- `https://kurser.dtu.dk/course/<course number>` — the official course base, e.g.
  `https://kurser.dtu.dk/course/02249`. Description, learning objectives,
  schedule, exam form, credits, prerequisites. Use it for questions about what a
  course *is*, rather than what was handed out in it.
- Many courses also run their own site, with notes, exercises and slides.
  There is no one place for them: each is set up by the course's teachers,
  often on the subdomain of the department that teaches it (DTU Compute's
  `courses.compute.dtu.dk/02110`, `www2.compute.dtu.dk/courses/02450` or
  `02402.compute.dtu.dk`, DTU Health Tech's `teaching.healthtech.dtu.dk/22100`),
  and otherwise on GitHub (a `github.io` page or a repository) or a teacher's
  own page. Many courses have none. `course_contents` prints the address of
  any such site the teacher linked, as a `(Link)` line ending in an `http`
  URL; start from that rather than guessing an address.

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
    back as text. Work from that text. A PDF's pages are headed `[Page n]`.
  - A file a course's pages link to has no topic id. Pass its path instead,
    as `get_file(org_unit_id, linked="_linked/...")`; `search_courses` gives
    it, and the text of a page or unit marks each link to a saved file as
    `[saved file: linked="_linked/..."]` or `[saved file: topic_id 7]`, after
    the link's words. Follow those when a page points you at a reading.
  - Any path in a reply is on the machine running the server, which is not
    necessarily one you can read from. Never treat a saved path as a way to
    reach the content, and never report that you cannot read the material
    because of it — the text is already in the reply. Only if you can run
    code on that machine yourself, as in Claude Code, may you open a saved
    file there, for instance a long workbook with pandas.
  - If a PDF comes back as "No text could be extracted": when the reason is
    that `pdftotext` is not installed, tell me to install poppler (see the
    dtuMCP README); when it has no text layer, as a scan has none, read its
    pages with `get_pdf_pages`.
  - A long PDF's text stops after about 25k tokens, at the end of a page,
    with a note naming the page to go on from: call `get_file` again with
    that `first_page`. When you need only part of a long PDF, find its pages
    with `search_courses` and ask for just those, with `first_page` and
    `last_page`. A Word or PowerPoint file is cut, ending `[Text truncated
    here.]`.
  - Figures, diagrams and mathematical notation do not survive extraction.
    When they matter, look at just those pages with
    `get_pdf_pages(org_unit_id, topic_id, first_page, last_page)`, which
    returns them as images, at most 10 pages per call (for a linked PDF,
    `linked="_linked/..."` in place of `topic_id`). A page costs about 2k
    tokens against roughly 100 as text, so never ask for a whole document.
  - On pages with math, formulas are written out from their layout: `x_{i}`
    is a subscript, `x^{2}` a superscript, `(a)/(b)` a fraction and
    `∑_{i=1}^{n}` a sum with its limits. Matrices, cases and aligned
    equations come out only roughly, so look at those pages when their layout
    matters.
  - To tell which pages to look at: a page's heading says what its text
    lost, as `[Page n, has images, drawing, math]`. In a mathematical text
    most pages have math, so look at those only when the exact notation
    matters. A figure made only of level and upright lines (a grid, a table,
    a number line) is not marked; for those, look for what the text keeps: a
    caption or label (`Figure 3.2`, `Theorem 4`) and the lines that refer to
    it, or a page with only a title and a few words. `search_courses` gives
    the page of a caption or term too.
  - A page headed `has unreadable symbols` uses a font whose characters the
    text cannot name, so its symbols come out as stray letters, digits or
    punctuation (`L^8_{6!7$0}` for `L^Σ_{CPA−0}`). Look at that page before
    quoting or explaining any formula on it.
  - A page headed `hidden text left out` had sentences set at size 0, which
    no reader sees; they are not part of the page. Tell me the page had
    hidden text when you use it.
  - Sums, products, calligraphic and blackboard letters (`∑`, `∏`, `𝒩𝒫`,
    `ℕ`) come through in PDFs set with TeX, except encrypted or signed ones
    and copies saved before the fix, where a sum still reads as a capital `X`
    or `P` and `𝒩𝒫` as `N P`. Where a formula needs a sum, check the page
    before reading such a capital as a variable.
  - An **Excel workbook** (.xlsx) reads with its sheets as pages. The reply
    lists the sheets with their size (`5. "Ecoinvent data" A1:X24023`), then
    gives a row a line: its number, then each cell with anything in it by
    column letter, as `12: A "Diesel" · C 0.5 · D 0.52 = B12*C12`. Text is in
    quotes, dates are `YYYY-MM-DD`, and a formula's cell gives its result,
    then `=` and the formula; `= as in D5` means the formula of D5, copied
    with its references moved along. Use the formulas to explain how a
    number is worked out.
  - Course workbooks often hold a data sheet of thousands of rows beside a
    few working sheets. A first call shows the sheets that fit and names the
    ones too long; a long sheet asked for goes on with `first_row`, as its
    note says. To look something up in such a sheet, do not page through
    it: pass `find` with the words to look for, as `get_file(...,
    first_page=5, last_page=5, find="adipic acid")`, which returns only the
    rows with every word, after the sheet's first rows for its column
    labels. A search hit in a workbook names its cells (`A196`), so you can
    also ask for that row with `first_row`. But `search_courses` reads only
    the first few hundred rows of each sheet (100k characters), so a row
    further down a data sheet is never among its hits, and it may show
    rows near the top that merely share your words instead. When what you
    look for belongs in a data sheet, use `find` on that workbook rather
    than trusting the search's silence.
  - An **HTML page** comes back as stripped text, a **link** as its target URL
    to fetch with your web tools, and a **video** as the address to watch it
    at. A video has no transcript, so tell me to watch it rather than guessing
    at its content. Any other format is saved and only the path comes back.
- `download_course(org_unit_id)` — refresh a course's saved copy from DTU
  Learn. It returns counts, not content, so never use it to read a course to
  me. Broken topics, links and videos have no file and are only counted.
- `download_term(term)` — the same for every course of a term, written like
  the headings of `list_courses` (`"Fall 2026"`); the newest term if left out.
  A download that runs past about two minutes goes on in the background, and
  the call says so: call the same tool again with the same course or term
  for its progress and, once done, its summary. Start no other download
  until it is done.
- `search_courses(query, org_unit_id, limit, kind)` — searches the saved courses
  for words: titles, unit text, announcements and the text inside files, a
  PDF's page by page. Each word matches the start of a word, so `reduc` finds
  "reductions". Matches with every word come first, best first, then those
  with some of them, each saying which words it lacks. Each match gives its
  course, the units above it, its page (a PDF's page, a workbook's sheet),
  and the ids `get_file`, `unit_info`, `get_pdf_pages` or
  `get_announcement_attachment` take; a file the pages link to gives the
  `linked` path that `get_file` and `get_pdf_pages` take instead. Pass
  `kind` to keep to kinds of item, the kind each match shows in brackets:
  one or several, as `"pdf pptx"` or `["pdf", "pptx"]`: a file's extension, or `unit`, `announcement`,
  `attachment`, `linked`, `link` or `video` — for instance `kind="xlsx"`
  when you are after a calculation, or `kind="pdf pptx"` for slides and
  notes. Left out, every kind is searched. Pass an
  `org_unit_id` to keep to one course. Courses use their own terms and
  abbreviations: when a search comes back thin, try those, or fewer words.
  Only saved courses are searched.
- `list_assignments(org_unit_id)` — assignment folders with due and closing dates.
- `list_announcements(org_unit_id)` — course announcements, newest first,
  each headed by its id and followed by `Attachment [file_id]` lines for any
  attached files.
- `get_announcement_attachment(org_unit_id, news_id, file_id)` — one of those
  attached files, returned as text the same way `get_file` returns a topic,
  with `first_page`, `last_page`, `first_row` and `find` too.
- `calendar(days, org_unit_id)` — my schedule in local time for the next
  `days` days (7 by default), grouped under a heading per day. A line with a
  time range is a lecture or an exercise session and usually names its room;
  a line with one time is a moment rather than a session — a `due:` line is a
  hand-in deadline, the rest are material being released. With no
  `org_unit_id` it covers all my courses this term; pass one for a single
  course. Use it for anything about when something happens, rather than
  reading dates out of a unit's text.

## Rules

- `list_assignments` does not say whether I submitted. D2L exposes that only to
  teachers, so do not claim or infer a submission status.
- `course_contents` leaves out topics copied from an earlier year without
  their files, and only counts them on their unit's line as
  `[N broken topics not listed]`. If what I ask for is not among the listed
  topics and its unit has broken ones, tell me the material is missing from
  the course rather than that a tool failed.
- The calendar of a course from an earlier term is often closed to students,
  and comes back refused rather than empty. Say so instead of retrying.
- If a tool says the session was rejected or has expired, tell me to run
  `dtumcp login` in a terminal. You cannot do
  it yourself: it opens a browser and waits for me to sign in. Courses already
  downloaded can still be read meanwhile.
