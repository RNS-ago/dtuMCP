"""Tests for dtumcp.tasks."""
import asyncio
import types

from _package import mod


def test_slow_call_becomes_task():
    # a call that outlasts GRACE becomes a task to poll
    asyncio.run(_check_tasks())


async def _check_tasks():
    declared = types.SimpleNamespace(extensions={mod.TASKS: {}})
    ctx = types.SimpleNamespace(protocol_version="2026-07-28",
                                session=types.SimpleNamespace(client_capabilities=declared))
    call, ext, mod.GRACE = types.SimpleNamespace(name="download_course"), mod.Tasks(), 0.2

    async def fast(ctx):
        return "direct"

    async def slow(ctx):
        if task := mod.current.get():
            mod.progress(task, "files 1/2 ")
        await asyncio.sleep(0.4)
        return types.SimpleNamespace(model_dump=lambda **_: {"content": []})

    assert await ext.intercept_tool_call(call, ctx, fast) == "direct" and not mod._tasks
    got = await ext.intercept_tool_call(call, ctx, slow)
    assert got["resultType"] == "task" and got["status"] == "working" and got["statusMessage"] == "files 1/2", got
    await asyncio.sleep(0.4)
    got = await ext._get(ctx, types.SimpleNamespace(task_id=got["taskId"]))
    assert got["status"] == "completed" and got["result"] == {"content": []} and "_job" not in got, got
    # A client without the extension, or on an older protocol, waits as before.
    for other in (types.SimpleNamespace(protocol_version="2026-07-28", session=types.SimpleNamespace(client_capabilities=None)),
                  types.SimpleNamespace(protocol_version="2025-11-25", session=ctx.session)):
        assert (await ext.intercept_tool_call(call, other, slow)).model_dump() == {"content": []}
