"""Settings, the MCP server and the tool decorator every module registers with."""

import logging
import os
import sys
from functools import wraps
from pathlib import Path

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError


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
# How to run this again, for messages: the installed command, or the package
# from a clone (python -m dtumcp).
RUN = "dtumcp" if Path(sys.argv[0]).stem == "dtumcp" else "uv run dtumcp"

mcp = MCPServer("dtu-learn")


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
    mcp.tool()(run)
    return fn
logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request
