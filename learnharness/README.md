# DTU Learn plugin for LearnHarness

Gives LearnHarness's teacher, practice and prober agents every dtuMCP tool,
named `dtu_list_courses`, `dtu_get_file` and so on, and adds a **DTU Learn**
view to the sidebar.

## Install

Install dtuMCP with `uv tool install` and run `dtumcp login` first (see the
[main README](../README.md)). Then, from the repository folder, and again after
any change to the plugin or to the tools' names, descriptions or arguments:

```bash
uv run learnharness/install.py
```

That copies the plugin and the installed `dtumcp`'s tool list into
`~/.config/LearnHarness/data/plugins/dtu-learn/dist/`
(`%APPDATA%\LearnHarness\data\plugins\dtu-learn\dist\` on Windows). If
**Settings → Community plugins** shows a different plugins directory, pass that
one as the argument. Restart LearnHarness, which loads plugin code only at
start, and enable **DTU Learn** under Settings → Community plugins.

The plugin runs the installed `dtumcp`, the same one every other client runs,
looking for it on `PATH` and in `~/.local/bin` (under `%USERPROFILE%` on
Windows), since an app started from a desktop entry or the Start menu may not
get your shell's `PATH`. With `uv tool install -e .` in a clone, a code change
reaches the plugin at its next call; a changed tool list needs `install.py`
and a restart.
It reads the same session file as every other client.

## Agents

The agents get instructions on when to use the tools, so the `dtu-learn` skill
is not needed here. Those instructions make your course the reference when you
study from it: its definitions and notation, its pages checked before teaching,
hand-ins and quizzes left for you to do under the course's own rules on
generative AI.

- The **prober** finds where the course is from its calendar and current unit.
- The **teacher** plans from the course's material and points to its exercises.
- The **practice** agent draws on exercise sheets and old exams, but never
  poses an open graded question.

`dtu_search_courses` returns each match's course, units, best PDF page (and
others as good) and the ids the other tools take, so a tutor can find where
something is covered and read just that page. After the matches with every
word it lists ones with some, each saying which words it lacks.

Each `dtu_` tool call shows as a card saying what the tutor did, such as
*Looked at pages 5–7 of "Slides from Lecture 7"*, with **Open in DTU Learn**.
A search's card lists its best matches; a PDF card shows the pages looked at.
Every card keeps the raw tool result in a fold.

When a tutor points you to material, its answer carries a card per item that
opens it over the chat at the first page. Tutors write these as a `dtu-learn`
code block, one line per item:

```
topic <org unit id> <topic id> pages <a>-<b> | <what to look at>
unit <org unit id> <unit id> | <what to look at>
```

A line naming something your saved courses lack shows as such.

## The DTU Learn view

The sidebar lists your courses by term and, for each downloaded course, its
units and topics. **Download** or **Refresh** saves or updates a course's
offline copy. **Hide broken topics** (on by default) hides topics whose file
did not come along from an earlier offering.

### Course page

Click a course's name to open it beside the tree, with tabs:

- **Announcements** (the default), from the saved copy, with attachments.
- **Assignments**: due and closing dates, group hand-ins, points,
  instructions, attached file names.
- **Calendar**: the next five months by day, deadlines marked.
- **Groups**: your group in each category, its members and their e-mail
  addresses, sign-up dates.
- **Quizzes**: open, due and close dates, attempts, time limit.
- **Coverage**: what you have studied against the course's units.

Assignments, Calendar, Groups and Quizzes are read live from DTU Learn when
first opened (courses not downloaded have them too), with **Read again**;
offline they say why they cannot load. Upcoming items come first, soonest at
the top; past ones are dimmed. **Open on DTU Learn** opens one in your browser
to hand it in or take it.

**Coverage** maps the statements LearnHarness extracts from your sessions and
files onto the course: each is found by name or alias through the course's
search (a week's slides before a whole-course book), and each top-level unit
lists a chip per statement that opens where the course covers it. It fills only
after **Extract** on LearnHarness's graph page (or **Extract files into the
graph**), and needs no connection.

### Reading material

- **Units** show their own text and contents. Unit text and announcements are
  rebuilt from a short list of plain HTML tags: links keep their address,
  every other attribute, script, style, frame and form is dropped, and a
  picture shows as its description.
- **PDFs** show their pages as they look, equations and figures included.
  Text can be selected and copied. The zoom buttons or Ctrl+scroll (touchpad
  pinch) zoom; **100%** fits the width. Ctrl+F opens a find row: Enter or the
  arrows step through matches, Shift+Enter goes back.
- **Other files** show their text. **Open file** opens a saved file in its
  usual app.
- **Videos** stream from Brightspace's media service. **Download** (its
  tooltip gives the size) saves one next to its unit's files, after which
  **Open file** plays it offline and the tree marks it `video, saved`. A course
  refresh keeps it. **Full screen** fills the window (the player's own button
  is hidden, since LearnHarness does not let a page take the screen); Escape,
  a double click or **Exit full screen** leaves.

### Links

A click previews the page over the view; closing it comes back. Ctrl+click or
a middle click opens it in your browser, and the preview has **Copy link** and
**Open in browser**. Sites that refuse framing (GitHub, for one) show as text;
others show **Show text** for their text. A PDF's links to its own pages
scroll there.

DTU Learn pages cannot be framed (no session in the frame, and DTU's sign-in
sends `X-Frame-Options: DENY`), so DTU Learn links preview from the saved copy:

- a topic or unit of a downloaded course opens as in the pane;
- a course file (`/content/...`, or a `quickLink.d2l?...&type=coursefile`)
  opens the copy `download_course` saved, with **Open file**;
- other short links (`quickLink.d2l?...&rcode=...`) are resolved once with
  your session;
- anything else (quiz, discussion, checklist, a course not downloaded) says
  why and offers your browser.

### Search

The box above the tree searches every downloaded course through the MCP's
index (see [Search](../README.md#search)); `/` focuses it from anywhere in the
view. Results replace the tree, words marked in context. A click opens one
(unless it ends a text selection); Enter opens the first. A result opens with
its words marked: a PDF at the first match from its best page, text or a unit
scrolled to the first, an announcement outlined. The tree opens down to it,
and the results stay. Escape or emptying the box goes back to the tree, where
the result sits.

The plugin keeps one `dtumcp search_serve` process, which takes about a
second to start on the first search. Files a course's pages link to are
missing from copies saved before the record listed them (`linked` in
`_course-<id>.json`); **Refresh** the course to find them.

### In a session

**DTU Learn** in a teaching or practice session's top bar opens the whole view
over the chat. Its X, Escape or the button again closes it. While something
waits for the tutor, it says how much: **DTU Learn · 2 for tutor**.

**Send to tutor** takes material into the session. Select text anywhere in the
pane and a **Send to tutor** button appears under it; a PDF's **Send page**
sends the current page as an image, as `get_pdf_pages` draws it. Queued items
wait under the tree, where each can be removed or clicked to go back to its
source. They go with your next message: quotes as text, each naming its file,
page, course and ids; pages as images. The queue empties only once the message
is accepted, and does not survive an app restart.

- In an open session, Send works with an empty box while something waits. A
  new session's first message needs text of its own.
- A model that takes no images refuses a message with a page and keeps your
  text; remove the page and send again.

The bar between tree and pane can be dragged or moved with the arrow keys when
focused; a double click resets it. The width is remembered.

## pdf.js

PDFs are drawn with [pdf.js](https://mozilla.github.io/pdf.js/) 6.4.299
(Apache-2.0, licence in `dist/assets/pdfjs/`): only its two built modules,
renamed from `.mjs` to `pdf.min.js` and `pdf.worker.min.js` because
LearnHarness does not serve `.mjs` as JavaScript. Pages are drawn as they come
near the screen and cleared when far off. Left out to keep the plugin small:
CJK character maps, the 14 standard fonts and the JPEG 2000 decoder. Such a
PDF still draws, with substitute fonts or without those images; one that
cannot be drawn shows its text.
