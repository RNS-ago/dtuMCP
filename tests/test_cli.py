"""Tests for dtumcp.cli."""
import io
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


def test_force_flag():
    # -f and --force reach the download tools as force=True, anywhere after the command.
    script = ("import sys; from dtumcp import cli; "
              "cli.COMMANDS['download_course'] = lambda org_unit_id, force=False, ctx=None: f'{org_unit_id} {force}'; "
              "sys.argv[0] = 'dtumcp'; cli.main()")
    for argv, says in [(["1"], "1 False"), (["-f", "1"], "1 True"), (["1", "--force"], "1 True")]:
        done = subprocess.run([sys.executable, "-c", script, "download_course", *argv], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=60)
        assert done.stdout.strip() == says, (argv, done)
    assert "download_course <org_unit_id> [-f|--force]" in mod._usage()


def test_confirm_force(monkeypatch):
    # At a terminal, --force goes on only on a yes; no answer is a no.
    for answer, goes_on in [("y\n", True), ("YES\n", True), ("n\n", False), ("\n", False), ("", False)]:
        monkeypatch.setattr(sys, "stdin", io.StringIO(answer))
        try:
            mod._confirm_force("download_term", ["Fall 2026"])
            went = True
        except SystemExit as e:
            went = False
            assert str(e) == "Nothing was deleted.", e
        assert went == goes_on, answer

    # A course id names the folder about to be replaced, given as the command line gives it: text.
    mod._copy = lambda ou: {"root": f"/courses/{ou}"} if ou == 326352 else None
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    mod._confirm_force("download_course", ["326352"])
    assert "/courses/326352" in sys.stderr.getvalue(), sys.stderr.getvalue()
