"""The command line and the MCP server's entry point."""

import inspect
import json
import os
import sys
from pathlib import Path

import anyio
import httpx
from mcp.server.transport_security import TransportSecuritySettings

from .app import BASE, COOKIE_FILE, DOWNLOADS, RUN, mcp
from .auth import login
from .files import _blocks, _saved_text
from .download import _status
from .search import _search, _search_answer
from .tools import (
    calendar, course_contents, course_tab, download_course, download_term, download_video,
    get_announcement_attachment, get_file, get_pdf_pages, list_announcements, list_assignments,
    list_courses, search_courses, unit_info, video_link,
)


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
    )
}


def _usage() -> str:
    """Build the help text from the same docstrings the MCP tools advertise."""
    lines = [f"Usage: {RUN} [command] [arguments]", "", "Commands:"]
    for name, fn in COMMANDS.items():
        params = " ".join(f"<{p}>" for p in inspect.signature(fn).parameters if p != "ctx")
        lines.append(f"  {name} {params}".rstrip())
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
    ]
    return "\n".join(lines)


def main() -> None:
    # On Windows a pipe gets the locale's code page and \r\n line ends, which
    # the LearnHarness plugin, reading UTF-8 lines, would garble or fail to match.
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", newline="\n")
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
            print(_saved_text(path, path.name))
            raise SystemExit(0)
        if name == "search_serve":
            # The LearnHarness plugin's searches: a JSON request a line on
            # stdin, its answer a line on stdout, from one process that stays,
            # rather than a process per keystroke in the view's search box.
            # {"id", "tool": true, "query", "org_unit_id", "limit"} answers as
            # search_courses does, with the search for the tool card; any
            # other {"id", "query", "only", "limit", "partial", "hideBroken"}
            # answers with the search alone.
            for line in sys.stdin:
                ask = json.loads(line)
                try:
                    if ask.get("tool"):
                        text, found = _search_answer(ask.get("query", ""), int(ask.get("org_unit_id") or 0),
                                                     int(ask.get("limit") or 15))
                        answer = {"text": text, "found": found}
                    else:
                        answer = _search(ask.get("query", ""), int(ask.get("only") or 0), int(ask.get("limit") or 100),
                                         bool(ask.get("partial")), bool(ask.get("hideBroken")))
                    print(json.dumps({"id": ask.get("id"), "result": answer}, ensure_ascii=False), flush=True)
                except Exception as e:  # one bad search must not end the process
                    print(json.dumps({"id": ask.get("id"), "error": str(e) or type(e).__name__}), flush=True)
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
        args = [int(a) if a.lstrip("-").isdigit() else a for a in sys.argv[2:]]
        try:
            # Let inspect report a wrong argument count instead of a traceback.
            inspect.signature(COMMANDS[name]).bind(*args)
        except TypeError as e:
            raise SystemExit(f"{name}: {e}\n\n{_usage()}")
        try:
            result = COMMANDS[name](*args)
        except RuntimeError as e:
            # Expired sessions, unknown course ids and the like are ordinary
            # outcomes here; a traceback only buries the message.
            _status()  # or the message lands on the end of a download's status line
            raise SystemExit(str(e))
        except httpx.TransportError as e:
            # No network, or DTU Learn down: also an ordinary outcome.
            _status()
            raise SystemExit(f"Could not reach DTU Learn or Brightspace: {e}")
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
