"""Tests for dtumcp.cli."""
import json
import os
import subprocess
import sys
import tempfile

from _package import mod


def test_command_names():
    # Messages name the command to type: the installed one, or uv run in a clone.
    assert mod._run_command("/home/u/.local/bin/dtumcp") == "dtumcp"
    assert mod._run_command("/home/u/dtuMCP/.venv/bin/dtumcp") == "uv run dtumcp"
    assert mod._run_command("-c") == "uv run dtumcp"


def test_search_serve():
    # search_serve answers a line it cannot read with an error and goes on.
    with tempfile.TemporaryDirectory() as tmp:
        done = subprocess.run(
            [sys.executable, "-c", "import sys; from dtumcp.cli import main; sys.argv = ['dtumcp', 'search_serve']; main()"],
            input='not json\n[1]\n{"id": 3, "query": "feed"}\n', capture_output=True, text=True, timeout=60,
            env={**os.environ, "DTU_DOWNLOADS": tmp})
        answers = [json.loads(line) for line in done.stdout.splitlines()]
        assert [a["id"] for a in answers] == [None, None, 3] and "error" in answers[0] and "result" in answers[2], done


def test_args():
    # --args takes a command's arguments by name, as one JSON object; null is
    # the default, and ctx is not the caller's to give.
    with tempfile.TemporaryDirectory() as tmp:
        def cli(*argv):
            return subprocess.run(
                [sys.executable, "-c", "import sys; from dtumcp.cli import main; sys.argv[0] = 'dtumcp'; main()", *argv],
                capture_output=True, text=True, timeout=60, env={**os.environ, "DTU_DOWNLOADS": tmp})
        done = cli("search_courses", "--args", '{"limit": 3, "query": "feed", "org_unit_id": null, "ctx": 1}')
        assert done.returncode == 0 and "No course is downloaded yet" in done.stdout, done
        # As MCP takes them: a name the command lacks is dropped, and a number
        # sent as text is a number where the command takes an int.
        done = cli("search_courses", "--args", '{"query": "feed", "limit": "3", "nope": 1}')
        assert done.returncode == 0 and "No course is downloaded yet" in done.stdout, done
        done = cli("--json", "search_courses", "--args", '{"query": "feed"}')
        assert json.loads(done.stdout)[0]["type"] == "text", done
        for bad, says in [("[1]", "takes a JSON object"), ("{", "takes a JSON object"), ('{"nope": 1}', "missing a required argument: 'query'")]:
            done = cli("search_courses", "--args", bad)
            assert done.returncode and says in done.stderr, (bad, done)
