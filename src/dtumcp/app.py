"""Settings, the MCP server and the tool decorator every module registers with."""

import logging
import os
import sys
from functools import wraps
from pathlib import Path

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .skills import URI, Skills
from .tasks import Tasks


BASE = os.environ.get("DTU_BASE", "https://learn.inside.dtu.dk")
LP, LE = "1.63", "1.97"


def _data_dir() -> Path:
    """Where the saved courses live by default: the platform's place for an
    app's own data, which, unlike Downloads, nobody clears out by hand."""
    if sys.platform == "darwin":
        base = "~/Library/Application Support"
    else:
        # Windows too, not AppData: Microsoft Store apps (Python, Claude) and
        # what they start write AppData to a private folder only they can see.
        base = os.environ.get("XDG_DATA_HOME") or "~/.local/share"
    return Path(base, "dtumcp", "courses").expanduser()


DOWNLOADS = Path(os.environ.get("DTU_DOWNLOADS") or _data_dir()).expanduser()
COOKIE_FILE = Path(os.environ.get("DTU_COOKIE_FILE", "~/.config/dtumcp/cookie")).expanduser()
def _run_command(argv0: str) -> str:
    """How to run this again, for messages: dtumcp where uv tool install put
    it on PATH, or uv run dtumcp in a clone, whose .venv is on PATH only
    under uv run."""
    path = Path(argv0)
    return "dtumcp" if path.stem == "dtumcp" and ".venv" not in path.parts else "uv run dtumcp"


RUN = _run_command(sys.argv[0])

INSTRUCTIONS = f"""\
These tools reach the user's DTU Learn courses. Before working with a DTU
course, load the dtu-learn skill ({URI}). It says how to find a course, keep
its saved copy current, search before reading, and read PDFs, workbooks and
figures. If your client cannot load skills from a server, call the guide
tool instead, which returns the same text."""

mcp = MCPServer("dtu-learn", instructions=INSTRUCTIONS, extensions=[Tasks(), Skills()])


def _status_error(e: httpx.HTTPStatusError) -> str:
    """What an error answer from DTU Learn means, for the model or the terminal.
    The URL goes without its query, which for a video can be a signed link."""
    r = e.response
    said = f"DTU Learn answered {r.status_code} {r.reason_phrase} for {e.request.url.copy_with(query=None)}"
    if r.status_code >= 500:
        return f"{said}: DTU Learn is down or overloaded, not this server. Try again in a few minutes."
    return f"{said}."


def _tool(fn):
    """Register fn as an MCP tool and return it as it is, for the command line.
    MCP hands the model a ToolError's message but only "Error executing tool"
    for any other exception, and this server's RuntimeErrors (session expired,
    no pdftoppm, unknown course) are what the model must read to act."""
    @wraps(fn)
    def run(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except RuntimeError as e:
            raise ToolError(str(e)) from e
        except httpx.TransportError as e:
            raise ToolError(f"Could not reach DTU Learn or Brightspace: {e}") from e
        except httpx.HTTPStatusError as e:
            raise ToolError(_status_error(e)) from e
    mcp.tool()(run)
    return fn
logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request
