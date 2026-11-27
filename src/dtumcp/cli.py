"""The command line and the MCP server's entry point."""

import inspect
import json
import os
import shutil
import subprocess
import sys
from importlib.metadata import distribution
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import anyio
import httpx
from mcp.server.transport_security import TransportSecuritySettings

from .app import BASE, COOKIE_FILE, DOWNLOADS, RUN, _status_error, mcp
from .auth import login
from .courses import _copies, _copy, _stored_marks
from .files import _blocks, _saved_text
from .download import PROCESSING, _reprocess, _status, _summary
from .search import _kind_list, _search, _search_answer
from .tools import (
    calendar, course_contents, course_tab, download_course, download_term, download_video,
    get_announcement_attachment, get_file, get_pdf_pages, list_announcements, list_assignments,
    list_courses, search_courses, unit_info, video_link,
)


def reprocess(course: int | str = 0, force: bool = False) -> str:
    """Work saved courses out again with this version, without downloading them.

    Each PDF's font maps, figures and math, the search text and the index
    page are worked out again from the files already saved, as a download
    would, so an improvement to how dtumcp reads PDFs reaches courses
    downloaded before it. course is an org unit id or a course number;
    left out, every saved course. A course this version's processing
    already made is left out, unless force (-f, --force). Files new or
    changed on DTU Learn still need download_course or download_term.
    Command line only.
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
    if not force:
        # A record from before PROCESSING was kept has none: made by version 0.
        units = [ou for ou in units if (_copy(ou) or {}).get("processing", 0) < PROCESSING]
        if not units:
            again = " ".join(filter(None, [RUN, "reprocess", str(course) if course else "", "-f"]))
            return f"Already processed by this version, so nothing was reprocessed. To run the processing again anyway: {again}"
    rows = []
    for i, ou in enumerate(units, 1):
        rows.append(_reprocess(ou, f"[{i}/{len(units)}] " if len(units) > 1 else ""))
    _status(keep=True)  # a blank line between the ✓ lines and the table
    return _summary(rows)


def _powershell(command: list) -> str:
    """A command as PowerShell runs it, each part quoted."""
    return "& " + " ".join("'" + part.replace("'", "''") + "'" for part in command)


def update(branch: str = "", force_reprocess: bool = False) -> str:
    """Update dtumcp on its current branch, or on <branch>, then reprocess stale courses.

    A clone, installed with uv tool install -e or run with uv run, is
    pulled, and installed again only if its dependencies changed; a
    dtumcp installed from GitHub is installed again from there. branch
    switches to that branch first; left out, the clone stays on its
    branch and a GitHub install on what it was installed from. Then
    reprocess runs with the new version, for the courses an older
    version's processing made; force_reprocess (-fr, --force-reprocess)
    reprocesses every saved course, as reprocess --force. Command line only.
    """
    info = json.loads(distribution("dtumcp").read_text("direct_url.json") or "{}")
    url = info.get("url", "")
    tool = RUN == "dtumcp"  # uv tool install put it on PATH, rather than uv run in a clone
    if info.get("dir_info", {}).get("editable"):
        clone = Path(url2pathname(urlparse(url).path))
        git = lambda *a, **kw: subprocess.run(["git", "-C", str(clone), *a], text=True, **kw)
        if git("status", "--porcelain", "--untracked-files=no", capture_output=True).stdout.strip():
            raise RuntimeError(f"{clone} has uncommitted changes. Commit or stash them, then update again.")
        before = git("rev-parse", "HEAD", capture_output=True).stdout.strip()
        # Fetched first, so a branch only the remote has yet is found, and switched to as its own.
        if branch and (git("fetch", stdout=sys.stderr).returncode
                       or git("switch", branch, stdout=sys.stderr).returncode):
            raise RuntimeError(f"Could not switch {clone} to branch {branch}, so dtumcp is as it was.")
        if git("pull", "--ff-only", stdout=sys.stderr).returncode:
            raise RuntimeError(f"git pull failed in {clone}; nothing was installed.")
        # The pulled code runs already; installing again only brings new dependencies.
        changed = git("diff", "--quiet", before, "HEAD", "--", "pyproject.toml", "uv.lock").returncode != 0
        moved = git("rev-parse", "HEAD", capture_output=True).stdout.strip() != before
        install = ["uv", "tool", "install", "--reinstall", "-e", str(clone)] if changed and tool else None
    elif "vcs_info" in info:
        rev = branch or info["vcs_info"].get("requested_revision")
        clone, changed, moved = None, True, True  # a reinstall cannot tell a new commit from the old
        install = ["uv", "tool", "install", "--reinstall", f"git+{url}" + (f"@{rev}" if rev else "")]
    else:
        raise RuntimeError("This dtumcp was installed neither from GitHub nor from a clone, so update "
                           "cannot tell where its new version is. Install it again as the README says.")
    # Each in a process of its own, with the new code. uv run, in the clone,
    # brings its dependencies itself.
    after = [[*RUN.split(), "reprocess", *(["--force"] if force_reprocess else [])]]
    if moved and _plugin_dest().exists():
        # Only a plugin installed already, and only in the default directory.
        after.append([*RUN.split(), "install_learnharness"])
    restart = "Restart Claude, Cursor, LearnHarness and any other MCP client so they start the new version."
    if os.name == "nt" and changed:
        # Windows replaces no file a running program has open, and this process
        # has dtumcp.exe and its venv open: the install waits for it to exit.
        steps = [f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue"]
        if install:
            steps.append(f"{_powershell(install)}; if ($LASTEXITCODE -ne 0) {{ Read-Host 'The install failed. "
                         "Close Claude, Cursor and anything else running dtumcp, then run dtumcp update again. "
                         "Press Enter to close'; exit 1 }")
        steps += [*map(_powershell, after), "Read-Host 'Done. Press Enter to close'"]
        subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "; ".join(steps)],
                         cwd=clone, creationflags=subprocess.CREATE_NEW_CONSOLE)
        return f"The update goes on in a new window once this command exits. {restart}"
    try:
        if install and subprocess.run(install, stdout=sys.stderr).returncode:
            raise RuntimeError("uv tool install failed; its message is above.")
        # The new version in a process of its own: this one has the old code
        # loaded, and after an install the files it would import next are gone.
        for command in after:
            if subprocess.run(command, cwd=clone).returncode:
                raise RuntimeError(f"dtumcp is updated, but {' '.join(command)} failed; run it again.")
    except FileNotFoundError as e:  # no uv, or no dtumcp on PATH
        raise RuntimeError(f"Could not run {e.filename}: {e.strerror}")
    return f"dtumcp is up to date. {restart}" if moved else "dtumcp was up to date already; nothing changed."


PLUGINS = Path(__file__).parent / "plugins"  # the harness plugins dtumcp ships


def _plugin_dest(plugins: str = "") -> Path:
    """Where the LearnHarness plugin goes: dtu-learn/dist in the plugins
    directory, %APPDATA%'s on Windows and ~/.config's elsewhere by default."""
    default = os.path.join(os.environ.get("APPDATA", "~/.config"), "LearnHarness", "data", "plugins")
    return Path(plugins or default).expanduser() / "dtu-learn" / "dist"


def _tools_json() -> str:
    """The tools' names, descriptions and argument schemas as MCP clients see
    them, so the LearnHarness plugin offers the same tools."""
    return json.dumps([{"name": t.name, "description": t.description, "parameters": t.input_schema}
                       for t in anyio.run(mcp.list_tools)], indent=1)


def install_learnharness(plugins: str = "") -> str:
    """Install the DTU Learn plugin into LearnHarness, or update it.

    Copies the plugin dtumcp ships, with this version's tool list, into
    <plugins>/dtu-learn/dist. plugins is the plugins directory Settings ->
    Community plugins shows, if not the default; dtumcp update installs a
    plugin in the default one again. Restart LearnHarness afterwards: it
    loads plugin code only at start. Command line only.
    """
    dest = _plugin_dest(plugins)
    if dest.is_symlink():
        raise RuntimeError(f"Refusing to replace {dest}: it is a symlink.")
    # The plugin runs the dtumcp on PATH or in ~/.local/bin, as backend.cjs
    # extends PATH, so one must be there before anything is written.
    if not shutil.which("dtumcp", path=os.pathsep.join([os.environ.get("PATH", ""),
                                                          os.path.expanduser("~/.local/bin")])):
        raise RuntimeError("The plugin runs the dtumcp that uv tool install puts on PATH, and there is none. "
                           "Run uv tool install -e . in the clone first.")
    # The new dist/ is built beside the old one and swapped in by renames only
    # once it is complete, so a failed install leaves the working plugin in
    # place and an update leaves no stale files. The sibling data/ is never touched.
    new, aside = dest.with_name("dist.new"), dest.with_name("dist.old")
    shutil.rmtree(new, ignore_errors=True)
    shutil.copytree(PLUGINS / "learnharness", new, ignore=shutil.ignore_patterns("README.md"))
    (new / "tools.json").write_text(_tools_json() + "\n", encoding="utf-8")
    # Swapped by renames, each done whole or not at all, so the plugin folder is
    # never left half deleted: the old dist/ moves aside, the new one takes its
    # place, and only then is the old one deleted.
    shutil.rmtree(aside, ignore_errors=True)  # one an earlier install could not delete
    if dest.exists():
        try:
            dest.rename(aside)
        except OSError as e:  # on Windows, a file in it is open
            shutil.rmtree(new, ignore_errors=True)
            raise RuntimeError(f"Could not move the installed plugin aside ({e}), so it is left as it was. "
                               "Close LearnHarness and try again.")
    try:
        new.rename(dest)
    except OSError as e:
        if aside.exists():
            aside.rename(dest)
        shutil.rmtree(new, ignore_errors=True)
        raise RuntimeError(f"Could not put the new plugin in place ({e}), so the old one is back.")
    shutil.rmtree(aside, ignore_errors=True)
    left = f" Could not delete the old plugin at {aside}; the next install does." if aside.exists() else ""
    return (f"Installed into {dest}.{left}\nRestart LearnHarness, which loads plugin code only at start, "
            "and enable DTU Learn under Settings -> Community plugins if it is new.")


def _flag(param: str) -> str:
    """A yes/no argument's flag: force_reprocess is --force-reprocess."""
    return "--" + param.replace("_", "-")


SHORT = {"force": "-f", "force_reprocess": "-fr"}  # the yes/no arguments with a short flag too

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
        update,
        install_learnharness,
    )
}


LEARNHARNESS = {"video_link", "course_tab"}  # commands only the LearnHarness plugin calls


def _usage(colour: bool = False) -> str:
    """Build the help text from the same docstrings the MCP tools advertise,
    in colour for --help at a terminal."""
    paint = lambda code, text: f"\033[{code}m{text}\033[0m" if colour and text else text
    heading = lambda text: ["", paint("1", text)]

    def entry(name: str, args: str, about: str) -> list:
        return [" ".join(filter(None, [f"  {paint('1;36', name)}", paint("33", args)])), f"      {about}"]

    def command(name: str) -> list:
        fn = COMMANDS[name]
        takes = inspect.signature(fn).parameters
        flags = [p for p, v in takes.items() if v.default is False]
        params = " ".join(f"<{p}>" for p in takes if p != "ctx" and p not in flags)
        options = " ".join(f"[{SHORT[p]}|{_flag(p)}]" if p in SHORT else f"[{_flag(p)}]" for p in flags)
        return entry(name, " ".join(filter(None, [params, options])), inspect.getdoc(fn).splitlines()[0])

    tools = {t.name for t in anyio.run(mcp.list_tools)}
    variable = lambda name, about: f"  {paint('36', f'{name:<20}')} {about}"
    lines = [f"{paint('1', 'Usage:')} {RUN} [command] [arguments]"]
    lines += heading("MCP tools, which an agent calls too:")
    for name in COMMANDS:
        if name in tools:
            lines += command(name)
    lines += heading("Command line only:")
    for name in COMMANDS:
        if name not in tools and name not in LEARNHARNESS:
            lines += command(name)
    lines += entry("file_text", "<path>", "Print a saved file's text, as get_file does a topic's")
    lines += entry("--json", "<command> [arguments]", "Print a command's result as MCP content blocks, images included")
    lines += heading("For the LearnHarness plugin:")
    for name in COMMANDS:
        if name in LEARNHARNESS:
            lines += command(name)
    lines += entry("tools", "", "Print the tools as JSON, for clients that speak no MCP (LearnHarness)")
    lines += entry("search_serve", "", "Answer JSON search requests a line at a time, for LearnHarness")
    lines += entry("<command> --args", "'<JSON object>'", "Take the command's arguments by name, as LearnHarness passes them")
    lines += [
        "",
        "With no command, runs as an MCP server on stdin/stdout, which is how",
        "Claude Code, Cursor and other MCP clients start it.",
    ]
    lines += heading("Environment:")
    lines += [
        variable("DTU_COOKIE", f"Cookie header to use instead of {COOKIE_FILE}"),
        variable("DTU_COOKIE_FILE", f"Where the session is stored [{COOKIE_FILE}]"),
        variable("DTU_DOWNLOADS", f"Where saved courses and files are kept [{DOWNLOADS}]"),
        variable("DTU_BASE", f"Brightspace instance [{BASE}]"),
        variable("DTU_FIREFOX_PROFILE", "Profile directory to read cookies from; skips the scan"),
        variable("DTU_MCP_PATH", "Serve over HTTP at this path instead of stdio."),
        "                       Needs DTU_ALLOWED_HOST. " + paint("31", "Exposes your session to"),
        "                       " + paint("31", "anyone who has the URL, so keep the path secret."),
        variable("DTU_ALLOWED_HOST", "Public hostname allowed to reach the HTTP server"),
        variable("DTU_PORT", f"Port for the HTTP server [{os.environ.get('DTU_PORT', 8000)}]"),
    ]
    lines += heading("Examples:")
    lines += [f"  {paint('2', RUN)} {example}" for example in (
        "login",
        "list_courses",
        "course_contents 326350",
        'download_term "Fall 2026"',
        "download_course 326350 --force",
        "reprocess 41526",
        "update",
    )]
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
            print(_usage(sys.stdout.isatty() and not os.environ.get("NO_COLOR")))
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
            print(_tools_json())
            raise SystemExit(0)
        if name not in COMMANDS:
            raise SystemExit(f"Unknown command {name!r}.\n\n{_usage()}")
        # -f or --force, anywhere after the command, for the downloads: replace
        # each course's folder with a fresh copy. Any other yes/no argument
        # is --<name> too, as update's --force-reprocess, and some have a short one (SHORT).
        flags = {_flag(p): p for p, v in inspect.signature(COMMANDS[name]).parameters.items()
                 if v.default is False}
        flags |= {SHORT[p]: p for p in list(flags.values()) if p in SHORT}
        on = {flags[a] for a in sys.argv[2:] if a in flags}
        sys.argv[2:] = [a for a in sys.argv[2:] if a not in flags]
        forced = "force" in on
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
        named.update(dict.fromkeys(on, True))
        try:
            # Let inspect report a wrong argument count instead of a traceback.
            inspect.signature(COMMANDS[name]).bind(*args, **named)
        except TypeError as e:
            raise SystemExit(f"{name}: {e}\n\n{_usage()}")
        if forced and name.startswith("download_") and sys.stdin.isatty():
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
