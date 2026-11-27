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


def test_usage_sections():
    # --help keeps the MCP tools apart from what only the command line has, and
    # is in colour only when asked, as at a terminal.
    plain, coloured = mod._usage(), mod._usage(colour=True)
    mcp_part, rest = plain.split("Command line only:")
    cli_part, harness_part = rest.split("For the LearnHarness plugin:")
    assert "  list_courses" in mcp_part and "  login" not in mcp_part
    assert "  login" in cli_part and "  update" in cli_part and "  video_link" not in cli_part
    assert "  video_link" in harness_part and "  search_serve" in harness_part
    assert "\033[" not in plain and "\033[1;36mupdate\033[0m" in coloured


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


def test_reprocess_flags():
    # -f and --force reach reprocess as force=True, with no question at a
    # terminal: nothing is deleted.
    script = ("import sys; from dtumcp import cli; "
              "cli.COMMANDS['reprocess'] = lambda course=0, force=False: f'{course} {force}'; "
              "sys.argv[0] = 'dtumcp'; cli.main()")
    for argv, says in [([], "0 False"), (["-f"], "0 True"), (["02231", "--force"], "02231 True")]:
        done = subprocess.run([sys.executable, "-c", script, "reprocess", *argv], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=60)
        assert done.stdout.strip() == says, (argv, done)
    assert "reprocess <course> [-f|--force]" in mod._usage()
    assert "update <branch> [-fr|--force-reprocess]" in mod._usage()
    # -fr is --force-reprocess, for update.
    script = script.replace("cli.COMMANDS['reprocess'] = lambda course=0, force=False: f'{course} {force}'",
                            "cli.COMMANDS['update'] = lambda branch='', force_reprocess=False: "
                            "f'{branch!r} {force_reprocess}'")
    for argv, says in [([], "'' False"), (["-fr"], "'' True"), (["dev", "--force-reprocess"], "'dev' True")]:
        done = subprocess.run([sys.executable, "-c", script, "update", *argv], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=60)
        assert done.stdout.strip() == says, (argv, done)


def test_reprocess_skips_processed(monkeypatch):
    # reprocess does only the courses an older processing made, unless force; a record
    # from before PROCESSING was kept counts as made by version 0.
    from pathlib import Path
    import dtumcp.cli as cli
    records = {100001: {"processing": cli.PROCESSING}, 100002: {}, 100003: {"processing": cli.PROCESSING - 1}}
    monkeypatch.setattr(cli, "_copies", lambda: [Path(f"c{ou}/_course-{ou}.json") for ou in records])
    monkeypatch.setattr(cli, "_copy", lambda ou: records[ou])
    done = []
    monkeypatch.setattr(cli, "_reprocess", lambda ou, prefix: done.append(ou))
    monkeypatch.setattr(cli, "_status", lambda **kw: None)
    monkeypatch.setattr(cli, "_summary", lambda rows: "table")
    cli.reprocess()
    assert done == [100002, 100003], done
    done.clear()
    cli.reprocess(force=True)
    assert done == [100001, 100002, 100003], done
    records[100002] = records[100003] = records[100001]
    done.clear()
    assert cli.reprocess().startswith("Already processed by this version") and not done
    assert cli.reprocess(100002).startswith("Already processed by this version") and not done
    assert cli.reprocess(100002).endswith(f"{cli.RUN} reprocess 100002 -f") and cli.reprocess().endswith("reprocess -f")


def test_update(monkeypatch, tmp_path):
    # A clone is pulled, and installed again only when its dependencies
    # changed; one with uncommitted changes is left alone. A dtumcp from
    # GitHub is installed again from there. reprocess runs after,
    # in a process of its own.
    import types
    import pytest
    import dtumcp.cli as cli
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)  # no signing, pull.rebase or the like from the user's
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git = lambda where, *a: subprocess.run(["git", "-C", str(where), "-c", "user.name=t", "-c", "user.email=t@t",
                                            *a], check=True, capture_output=True)
    upstream, clone = tmp_path / "upstream", tmp_path / "clone"
    upstream.mkdir()
    git(upstream, "init", "-q")
    (upstream / "pyproject.toml").write_text("1")
    git(upstream, "add", ".")
    git(upstream, "commit", "-qm", "one")
    git(tmp_path, "clone", "-q", str(upstream), str(clone))

    def commit(name):
        (upstream / name).write_text(str(len(list(upstream.iterdir()))))
        git(upstream, "add", ".")
        git(upstream, "commit", "-qm", name)

    real, ran = subprocess.run, []
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **kw: real(cmd, *a, **kw) if cmd[0] == "git"
                        else ran.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    monkeypatch.setattr(cli, "RUN", "dtumcp")
    info = {"url": clone.as_uri(), "dir_info": {"editable": True}}
    monkeypatch.setattr(cli, "distribution", lambda name: types.SimpleNamespace(read_text=lambda f: json.dumps(info)))
    then = ["dtumcp", "reprocess"]
    plugin = tmp_path / "plugins" / "dtu-learn" / "dist"
    monkeypatch.setattr(cli, "_plugin_dest", lambda plugins="": plugin)

    # Nothing new: no restart to ask for.
    assert cli.update() == "dtumcp was up to date already; nothing changed." and ran == [then], ran
    ran.clear()
    commit("code.py")
    assert "Restart" in cli.update() and ran == [then], ran
    assert (clone / "code.py").exists()
    # A LearnHarness plugin installed already is installed again, but only with a new version.
    plugin.mkdir(parents=True)
    ran.clear()
    cli.update()
    assert ran == [then], ran
    commit("plugin.js")
    ran.clear()
    cli.update()
    assert ran == [then, ["dtumcp", "install_learnharness"]], ran
    plugin.rmdir()
    ran.clear()
    commit("uv.lock")
    cli.update()
    assert ran == [["uv", "tool", "install", "--reinstall", "-e", str(clone)], then], ran

    # A branch is switched to, the remote's own fetched first; -fr reprocesses every course.
    ran.clear()
    git(upstream, "switch", "-qc", "dev")
    commit("dev.py")
    git(upstream, "switch", "-q", "-")
    cli.update("dev", force_reprocess=True)
    assert ran == [["dtumcp", "reprocess", "--force"]], ran
    assert (clone / "dev.py").exists()
    with pytest.raises(RuntimeError, match="branch nope"):
        cli.update("nope")
    git(clone, "switch", "-q", "-")

    ran.clear()
    commit("more.py")
    (clone / "code.py").write_text("an edit")
    with pytest.raises(RuntimeError, match="uncommitted changes"):
        cli.update()
    assert not ran and not (clone / "more.py").exists()

    info = {"url": "https://github.com/RNS-ago/dtuMCP", "vcs_info": {"vcs": "git", "commit_id": "abc"}}
    cli.update()
    assert ran == [["uv", "tool", "install", "--reinstall", "git+https://github.com/RNS-ago/dtuMCP"], then], ran
    # A branch given replaces the one installed from; none keeps it.
    ran.clear()
    cli.update("dev")
    assert ran[0][-1] == "git+https://github.com/RNS-ago/dtuMCP@dev", ran
    ran.clear()
    info["vcs_info"]["requested_revision"] = "dev"
    cli.update()
    assert ran[0][-1] == "git+https://github.com/RNS-ago/dtuMCP@dev", ran


def test_install_learnharness(monkeypatch, tmp_path):
    # The plugin dtumcp ships and its tool list go into <plugins>/dtu-learn/dist,
    # replacing what was there whole; the plugin's data/ beside it stays.
    import shutil
    import pytest
    import dtumcp.cli as cli
    monkeypatch.setattr(shutil, "which", lambda *a, **kw: "/bin/dtumcp")
    dest = tmp_path / "dtu-learn" / "dist"
    (tmp_path / "dtu-learn" / "data").mkdir(parents=True)
    (tmp_path / "dtu-learn" / "data" / "kept").write_text("mine")
    assert str(dest) in cli.install_learnharness(str(tmp_path))
    assert {"main.js", "backend.cjs", "manifest.json", "styles.css", "assets", "tools.json"} == {
        p.name for p in dest.iterdir()}
    assert "list_courses" in {t["name"] for t in json.loads((dest / "tools.json").read_text())}
    (dest / "stale.js").write_text("old")
    cli.install_learnharness(str(tmp_path))
    assert not (dest / "stale.js").exists() and (tmp_path / "dtu-learn" / "data" / "kept").read_text() == "mine"
    assert not (tmp_path / "dtu-learn" / "dist.new").exists() and not (tmp_path / "dtu-learn" / "dist.old").exists()
    # No dtumcp for the plugin to run: nothing is written.
    monkeypatch.setattr(shutil, "which", lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match="uv tool install"):
        cli.install_learnharness(str(tmp_path / "other"))
    assert not (tmp_path / "other").exists()
    monkeypatch.setattr(shutil, "which", lambda *a, **kw: "/bin/dtumcp")
    (tmp_path / "linked" / "dtu-learn").mkdir(parents=True)
    (tmp_path / "linked" / "dtu-learn" / "dist").symlink_to(dest)
    with pytest.raises(RuntimeError, match="symlink"):
        cli.install_learnharness(str(tmp_path / "linked"))
