"""MCP skills: the io.modelcontextprotocol/skills extension (2026-07-28), which
serves dtu-learn/SKILL.md, the guide to these tools, as a skill. A client
that loads skills from servers, as Claude Code does, then picks up a changed
guide with the server, and nobody pastes it into their instructions again.

shortcut: hand-written because mcp 2.3.0 has no Skills extension; swap in
mcp.server.skills.Skills once a release ships it (it is on the SDK's main).
"""

import hashlib
from pathlib import Path

from mcp.server.extension import Extension, MethodBinding, ResourceBinding
from mcp.server.mcpserver.resources import TextResource
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, PaginatedRequestParams, RequestParams


SKILLS = "io.modelcontextprotocol/skills"
URI = "skill://dtu-learn/SKILL.md"
TEXT = Path(__file__).with_name("dtu-learn").joinpath("SKILL.md").read_text(encoding="utf-8")
TTL_MS = 3600 * 1000  # the guide changes only with the server's code


def _frontmatter(text: str) -> dict:
    """The key: value lines between the opening ---s. Enough for this file's
    plain one-line values, which is all it has; there is no YAML parser here."""
    head = text.split("---\n", 2)[1]
    return dict(line.split(": ", 1) for line in head.splitlines())


FRONTMATTER = _frontmatter(TEXT)
_data = TEXT.encode()
ENTRY = {"uri": URI, "frontmatter": FRONTMATTER,
         "resources": [{"uri": URI, "digest": f"sha256:{hashlib.sha256(_data).hexdigest()}", "size": len(_data)}]}
_CACHE = {"resultType": "complete", "ttlMs": TTL_MS, "cacheScope": "public"}


class GetSkillParams(RequestParams):
    uri: str


class Skills(Extension):
    identifier = SKILLS

    def resources(self):
        return [ResourceBinding(TextResource(
            uri=URI, name=FRONTMATTER["name"], description=FRONTMATTER["description"],
            mime_type="text/markdown", text=TEXT))]

    def methods(self):
        return [MethodBinding("skills/list", PaginatedRequestParams, self._list),
                MethodBinding("skills/get", GetSkillParams, self._get)]

    async def _list(self, ctx, params):
        return {"skills": [ENTRY], **_CACHE}

    async def _get(self, ctx, params):
        if params.uri != URI:
            raise MCPError(code=INVALID_PARAMS, message=f"Unknown skill: {params.uri}")
        return {"skill": ENTRY, **_CACHE}
