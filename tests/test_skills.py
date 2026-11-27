"""Tests for dtumcp.skills."""
import asyncio
import hashlib

import pytest
from mcp.shared.exceptions import MCPError

from _package import mod


def test_entry_matches_skill_file():
    # the manifest the client verifies against is the file served
    entry, data = mod.ENTRY, mod.TEXT.encode()
    assert entry["uri"].rsplit("/", 2)[1] == entry["frontmatter"]["name"] == "dtu-learn"
    assert set(entry["frontmatter"]) == {"name", "description"}
    assert entry["resources"] == [{"uri": mod.URI, "digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data)}]
    assert mod.URI in mod.INSTRUCTIONS
    assert mod.guide().startswith("# DTU courses") and mod.guide() in mod.TEXT


def test_list_and_get():
    ext = mod.Skills()
    listed = asyncio.run(ext._list(None, None))
    assert listed["resultType"] == "complete" and listed["skills"] == [mod.ENTRY] and listed["ttlMs"], listed
    assert asyncio.run(ext._get(None, mod.GetSkillParams(uri=mod.URI)))["skill"] == mod.ENTRY
    with pytest.raises(MCPError) as e:
        asyncio.run(ext._get(None, mod.GetSkillParams(uri="skill://other/SKILL.md")))
    assert e.value.error.code == -32602
