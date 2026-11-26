"""MCP server for DTU Learn (D2L Brightspace) course material.

Auth: run `dtumcp login` (`uv run dtumcp login` in a clone). That lists the
Firefox profiles it finds, Chrome and your default browser, opens DTU Learn in
the one you pick, waits for you to sign in, lifts the session cookies out of
that profile's cookie database (asks Chrome for them, or has you paste them,
for the default browser) and saves them to ~/.config/dtumcp/cookie.

DTU_COOKIE still overrides the saved file if you would rather paste a Cookie
header from devtools yourself.

The modules, each importing only those above it:
  app         settings, the MCP server, the tool decorator
  text        plain text out of HTML, Word, PowerPoint and PDF
  client      the HTTP client for DTU Learn
  pdfmath     PDF fonts and formulas, written out
  courses     courses, terms, contents, calendars
  files       saved files and the files a course links to
  index_page  the index.html over the saved courses
  search      the search index
  download    downloading courses and terms
  video       finding and fetching videos
  tools       the MCP tools
  auth        login
  cli         the command line and main()
"""
