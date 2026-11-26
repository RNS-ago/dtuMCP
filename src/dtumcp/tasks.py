"""MCP tasks: the io.modelcontextprotocol/tasks extension (2026-07-28), for
the tools that can outlast a client's wait for a call.

A client that declares the extension on a call to one of them, as Claude
Code does, gets its answer at once if it comes within GRACE seconds, and
otherwise a task to poll with tasks/get, which runs the call to its end with
no deadline. Every other client gets the call as before: download's
_background stops waiting after JOB_WAIT and the model calls again.
"""

import asyncio
import contextvars
import time
import uuid
from datetime import datetime, timezone

from mcp.server.extension import Extension, MethodBinding
from mcp.server.mcpserver.server import require_client_extension
from mcp.shared.exceptions import MCPError
from mcp_types import INTERNAL_ERROR, INVALID_PARAMS, RequestParams
from mcp_types.version import MODERN_PROTOCOL_VERSIONS


TASKS = "io.modelcontextprotocol/tasks"
# The tools that can download a course, and so take minutes.
SLOW = {
    "download_course", "download_term", "course_contents", "unit_info", "get_file",
    "get_pdf_pages", "list_announcements", "get_announcement_attachment",
}
GRACE = 5  # seconds a call may take and still answer directly, not as a task
TTL_MS = 24 * 3600 * 1000  # a task is forgotten a day after it started; no download takes that
POLL_MS = 3000

# The task a tool call runs as, for download to read in the tool's thread:
# there is no deadline to stop at, and progress goes in its statusMessage,
# since the spec has no progress notifications for tasks.
current = contextvars.ContextVar("current_task", default=None)
_tasks = {}  # by taskId; keys starting with _ stay off the wire


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _declared(ctx) -> bool:
    """Whether this request's client declared the tasks extension, on a
    2026-07-28 connection: a task cannot be sent over an older one."""
    capabilities = ctx.session.client_capabilities
    return (ctx.protocol_version in MODERN_PROTOCOL_VERSIONS
            and bool(capabilities and capabilities.extensions and TASKS in capabilities.extensions))


def _wire(task: dict) -> dict:
    return {k: v for k, v in task.items() if not k.startswith("_")}


def _finish(task: dict, job: asyncio.Task) -> None:
    """Record how a task's call ended, unless it was cancelled first."""
    if task["status"] != "working":
        return
    if job.cancelled():
        task["status"] = "cancelled"
    elif isinstance(job.exception(), MCPError):
        task.update(status="failed", error=job.exception().error.model_dump(by_alias=True, exclude_none=True))
    elif job.exception():
        task.update(status="failed", error={"code": INTERNAL_ERROR, "message": str(job.exception())})
    else:
        # A tool's own error is an isError result, and so a completed task.
        task.update(status="completed", result=job.result().model_dump(by_alias=True, mode="json", exclude_none=True))
    task.pop("statusMessage", None)
    task["lastUpdatedAt"] = _now()


def progress(task: dict, text: str) -> None:
    """Show a status line as the task's statusMessage."""
    task.update(statusMessage=text.strip(), lastUpdatedAt=_now())


class TaskParams(RequestParams):
    task_id: str


class Tasks(Extension):
    identifier = TASKS

    def methods(self):
        return [MethodBinding(m, TaskParams, h) for m, h in (
            ("tasks/get", self._get), ("tasks/update", self._update), ("tasks/cancel", self._cancel))]

    async def intercept_tool_call(self, params, ctx, call_next):
        if params.name not in SLOW or not _declared(ctx):
            return await call_next(ctx)
        now = time.monotonic()
        for key in [k for k, t in _tasks.items() if now - t["_born"] > TTL_MS / 1000]:
            del _tasks[key]
        stamp = _now()
        task = {"taskId": str(uuid.uuid4()), "status": "working", "createdAt": stamp, "lastUpdatedAt": stamp,
                "ttlMs": TTL_MS, "pollIntervalMs": POLL_MS, "_born": now}

        async def run():
            current.set(task)  # this asyncio task's own context, which the tool's thread inherits
            return await call_next(ctx)

        job = task["_job"] = asyncio.create_task(run())
        _tasks[task["taskId"]] = task
        await asyncio.wait([job], timeout=GRACE)
        if job.done():
            del _tasks[task["taskId"]]
            return job.result()
        job.add_done_callback(lambda job: _finish(task, job))
        return {"resultType": "task", **_wire(task)}

    def _task(self, ctx, params) -> dict:
        require_client_extension(ctx, TASKS)
        task = _tasks.get(params.task_id)
        if task is None:
            raise MCPError(code=INVALID_PARAMS, message="Failed to retrieve task: Task not found")
        return task

    async def _get(self, ctx, params):
        return _wire(self._task(ctx, params))

    async def _update(self, ctx, params):
        self._task(ctx, params)  # no tool here asks for input, so there is nothing to answer
        return {}

    async def _cancel(self, ctx, params):
        task = self._task(ctx, params)
        if task["status"] == "working":
            # The tool's thread runs on, and a download it waits for finishes
            # for the next call to collect, as when a client gives up on a call.
            task.update(status="cancelled", lastUpdatedAt=_now())
            task.pop("statusMessage", None)
            task["_job"].cancel()
        return {}
