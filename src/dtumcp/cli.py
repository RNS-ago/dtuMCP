"""The command line and the MCP server's entry point."""

import inspect
import json
import os
import sys
from pathlib import Path

import anyio
import httpx
from mcp.server.transport_security import TransportSecuritySettings

from .app import BASE, COOKIE_FILE, DOWNLOADS, RUN, _status_error, mcp
from .auth import login
from .courses import _copies, _copy, _stored_marks
from .files import _blocks, _saved_text
from .download import _reprocess, _status, _summary
from .search import _kind_list, _search, _search_answer
from .tools import (
    calendar, course_contents, course_tab, download_course, download_term, download_video,
    get_announcement_attachment, get_file, get_pdf_pages, list_announcements, list_assignments,
    list_courses, search_courses, unit_info, video_link,
)


def reprocess(course: int | str = 0) -> str:
    """Work saved courses out again with this version, without downloading them.

    Each PDF's font maps, figures and math, the search text and the index
    page are worked out again from the files already saved, as a download
    would, so an improvement to how dtumcp reads PDFs reaches courses
    downloaded before it. course is an org unit id or a course number;
    left out, every saved course. Files new or changed on DTU Learn still
    need download_course or download_term. Command line only.
    """
    saved = {}  # org unit id by course folder, every saved course once
    for record in _copies():
        saved[record.parent.name] = int(record.stem.removeprefix("_course-"))
    if not course:
        units = sorted(set(saved.values()))
    else:
        number = int(str(course).strip())
        units = [number] if number >= 100000 else [
            ou for folder, ou in saved.items() if folder.startswith(f"{number:05d} ")]
        if not units:
            raise RuntimeError(f"No saved course {course}. Download it with download_course first.")
    rows = []
    for i, ou in enumerate(units, 1):
        rows.append(_reprocess(ou, f"[{i}/{len(units)}] " if len(units) > 1 else ""))
    _status(keep=True)  # a blank line between the ✓ lines and the table
    return _summary(rows)


COMMANDS = {
    f.__name__: f
    for f in (
        login,
        list_courses,
        course_contents,
        unit_info,
        get_file,
        get_pdf_pages,
        video_link,
        download_video,
        course_tab,
        list_assignments,
        list_announcements,
        get_announcement_attachment,
        search_courses,
        calendar,
        download_course,
        download_term,
        reprocess,
    )
}


def _usage() -> str:
    """Build the help text from the same docstrings the MCP tools advertise."""
    lines = [f"Usage: {RUN} [command] [arguments]", "", "Commands:"]
    for name, fn in COMMANDS.items():
        takes = inspect.signature(fn).parameters
        params = " ".join(f"<{p}>" for p in takes if p not in ("ctx", "force"))
        lines.append(f"  {name} {params}{' [-f|--force]' if 'force' in takes else ''}".rstrip())
        lines.append(f"      {inspect.getdoc(fn).splitlines()[0]}")
    lines += [
        "  tools",
        "      Print the tools as JSON, for clients that speak no MCP (LearnHarness)",
        "  search_serve",
        "      Answer JSON search requests a line at a time, for LearnHarness",
        "  file_text <path>",
        "      Print a saved file's text, as get_file does a topic's",
        "  --json <command> [arguments]",
        "      Print a command's result as MCP content blocks, images included",
        "  <command> --args '<JSON object>'",
        "      Take the command's arguments by name, as LearnHarness passes them",
        "",
        "With no command, runs as an MCP server on stdin/stdout, which is how",
        "Claude Code, Cursor and other MCP clients start it.",
        "",
        "Environment:",
        f"  DTU_COOKIE           Cookie header to use instead of {COOKIE_FILE}",
        f"  DTU_COOKIE_FILE      Where the session is stored [{COOKIE_FILE}]",
        f"  DTU_DOWNLOADS        Where saved courses and files are kept [{DOWNLOADS}]",
        f"  DTU_BASE             Brightspace instance [{BASE}]",
        "  DTU_FIREFOX_PROFILE  Profile directory to read cookies from; skips the scan",
        "  DTU_MCP_PATH         Serve over HTTP at this path instead of stdio.",
        "                       Needs DTU_ALLOWED_HOST. Exposes your session to",
        "                       anyone who has the URL, so keep the path secret.",
        "  DTU_ALLOWED_HOST     Public hostname allowed to reach the HTTP server",
        f"  DTU_PORT             Port for the HTTP server [{os.environ.get('DTU_PORT', 8000)}]",
        "",
        "Examples:",
        f"  {RUN} login",
        f"  {RUN} list_courses",
        f"  {RUN} course_contents 326350",
        f'  {RUN} download_term "Fall 2026"',
        f'  {RUN} download_course 326350 --force',
        f'  {RUN} reprocess 41526',
    ]
    return "\n".join(lines)


def _confirm_force(name: str, args: list) -> None:
    """Say what --force deletes and stop unless the user says yes."""
    # The id comes as text: download_course takes int | str, which main leaves alone.
    copy = _copy(int(args[0])) if name == "download_course" and args and str(args[0]).isdigit() else None
    what = (f"the saved copy in\n  {copy['root']}\n" if copy
            else "the saved copy of every course in that term\n" if name == "download_term"
            else "the course's saved copy ")
    print(f"--force replaces {what}with a fresh download. Files the course no longer has,\n"
          "and videos saved with download_video, are deleted.", file=sys.stderr)
    print("Continue? [y/N] ", end="", file=sys.stderr, flush=True)
    try:
        answer = input()
    except EOFError:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        raise SystemExit("Nothing was deleted.")


def main() -> None:
    # On Windows a pipe gets the locale's code page and \r\n line ends, which
    # the LearnHarness plugin, reading UTF-8 lines, would garble or fail to match.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", newline="\n")
    sys.stdin.reconfigure(encoding="utf-8")  # search_serve's requests come in as UTF-8 lines too
    # A command name as the first argument runs that one thing and prints the
    # result, for pasting into claude.ai or any other chat. No server, no
    # network exposure.
    # --json prints the result as MCP content blocks, images included, for
    # the LearnHarness plugin. Plain output names an image instead.
    as_json = sys.argv[1:2] == ["--json"]
    if as_json:
        del sys.argv[1]
    if len(sys.argv) > 1:
        name = sys.argv[1]
        if name in ("-h", "--help", "help"):
            print(_usage())
            raise SystemExit(0)
        if name == "file_text" and len(sys.argv) == 3:
            # A saved file's text, as get_file gives a topic's: for files a
            # course links to, which have no topic. Command line only; the
            # LearnHarness plugin checks the path is one a course saved.
            path = Path(sys.argv[2])
            print(_saved_text(path, path.name, _stored_marks(path)))
            raise SystemExit(0)
        if name == "search_serve":
            # The LearnHarness plugin's searches: a JSON request a line on
            # stdin, its answer a line on stdout, from one process that stays,
            # rather than a process per keystroke in the view's search box.
            # {"id", "tool": true, "query", "org_unit_id", "limit", "kind"}
            # answers as search_courses does, with the search for the tool
            # card; any other {"id", "query", "only", "limit", "partial",
            # "hideBroken", "kind"} answers with the search alone.
            for line in sys.stdin:
                ask = {}
                try:
                    ask = json.loads(line)  # in the try: one bad line must not end the process either
                    if ask.get("tool"):
                        text, found = _search_answer(ask.get("query", ""), int(ask.get("org_unit_id") or 0),
                                                     int(ask.get("limit") or 15), ask.get("kind") or "")
                        answer = {"text": text, "found": found}
                    else:
                        answer = _search(ask.get("query", ""), int(ask.get("only") or 0), int(ask.get("limit") or 100),
                                         bool(ask.get("partial")), bool(ask.get("hideBroken")),
                                         _kind_list(ask.get("kind") or ""))
                    print(json.dumps({"id": ask.get("id"), "result": answer}, ensure_ascii=False), flush=True)
                except Exception as e:  # one bad search must not end the process
                    asked = ask.get("id") if isinstance(ask, dict) else None
                    print(json.dumps({"id": asked, "error": str(e) or type(e).__name__}), flush=True)
            raise SystemExit(0)
        if name == "tools":
            # Names, descriptions and argument schemas as MCP clients see
            # them, so the LearnHarness plugin offers the same tools.
            tools = anyio.run(mcp.list_tools)
            print(json.dumps([
                {"name": t.name, "description": t.description, "parameters": t.input_schema}
                for t in tools
            ], indent=1))
            raise SystemExit(0)
        if name not in COMMANDS:
            raise SystemExit(f"Unknown command {name!r}.\n\n{_usage()}")
        # -f or --force, anywhere after the command, for the downloads: replace
        # each course's folder with a fresh copy.
        forced = "force" in inspect.signature(COMMANDS[name]).parameters and bool(
            {"-f", "--force"} & set(sys.argv[2:]))
        if forced:
            sys.argv[2:] = [a for a in sys.argv[2:] if a not in ("-f", "--force")]
        # Numbers only where the command takes an int: download_term "2026"
        # stays text, and a course number keeps its leading 0.
        params = list(inspect.signature(COMMANDS[name]).parameters.values())
        args = [int(a) if a.lstrip("-").isdigit() and i < len(params) and params[i].annotation in (int, "int")
                else a for i, a in enumerate(sys.argv[2:])]
        named = {}
        if sys.argv[2:3] == ["--args"] and len(sys.argv) == 4:
            # The arguments by name, as one JSON object: how the LearnHarness
            # plugin calls, so a tools.json older than this dtumcp can lack an
            # argument but never put a value in another's place. null is left
            # to the default, and ctx is the MCP server's alone.
            try:
                named = json.loads(sys.argv[3])
            except ValueError as e:
                raise SystemExit(f"{name}: --args takes a JSON object: {e}")
            if not isinstance(named, dict):
                raise SystemExit(f"{name}: --args takes a JSON object, not {sys.argv[3]}")
            # As MCP takes them: a name the command lacks is dropped (a tools.json
            # newer than this dtumcp), and a number sent as text is a number
            # where the command takes an int.
            takes = inspect.signature(COMMANDS[name]).parameters
            args, named = [], {
                k: int(v) if isinstance(v, str) and v.lstrip("-").isdigit() and takes[k].annotation in (int, "int") else v
                for k, v in named.items() if v is not None and k != "ctx" and k in takes}
        if forced:
            named["force"] = True
        try:
            # Let inspect report a wrong argument count instead of a traceback.
            inspect.signature(COMMANDS[name]).bind(*args, **named)
        except TypeError as e:
            raise SystemExit(f"{name}: {e}\n\n{_usage()}")
        if forced and sys.stdin.isatty():
            # Asked only at a terminal: an MCP client asks before running a tool,
            # and LearnHarness and scripts have no one to answer.
            _confirm_force(name, args)
        try:
            result = COMMANDS[name](*args, **named)
        except RuntimeError as e:
            # Expired sessions, unknown course ids and the like are ordinary
            # outcomes here; a traceback only buries the message.
            _status()  # or the message lands on the end of a download's status line
            raise SystemExit(str(e))
        except httpx.TransportError as e:
            # No network, or DTU Learn down: also an ordinary outcome.
            _status()
            raise SystemExit(f"Could not reach DTU Learn or Brightspace: {e}")
        except httpx.HTTPStatusError as e:
            _status()
            raise SystemExit(_status_error(e))
        if result is not None:
            blocks = _blocks(result)
            print(json.dumps(blocks) if as_json else "\n".join(
                b.get("text") or f"[{b['mimeType']} image, {len(b['data']) * 3 // 4096} KB]"
                for b in blocks
            ))
        raise SystemExit(0)

    # Setting DTU_MCP_PATH switches on the remote transport that claude.ai
    # connectors require. That path is the only thing standing between the
    # public internet and your DTU session, so make it a long random string
    # and treat the resulting URL as a password.
    path = os.environ.get("DTU_MCP_PATH")
    if not path:
        mcp.run()
    elif not os.environ.get("DTU_ALLOWED_HOST"):
        raise SystemExit(
            "DTU_MCP_PATH is set but DTU_ALLOWED_HOST is not. Set it to the public "
            "hostname of your tunnel (for example abc-def.trycloudflare.com) so DNS "
            "rebinding protection stays on."
        )
    else:
        mcp.run(
            transport="streamable-http",
            host="127.0.0.1",
            port=int(os.environ.get("DTU_PORT", 8000)),
            streamable_http_path=path,
            transport_security=TransportSecuritySettings(
                allowed_hosts=[os.environ["DTU_ALLOWED_HOST"]],
                allowed_origins=["https://claude.ai"],
            ),
        )
