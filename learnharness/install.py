#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Install the DTU Learn plugin into LearnHarness, or update it: puts the
plugin and the installed dtumcp's tool list in <plugins directory>/dtu-learn/dist.
The plugin runs the dtumcp that uv tool install put on PATH.
Pass the plugins directory shown in Settings -> Community plugins if it is
not the default. Restart LearnHarness afterwards to load the new code.

    uv run learnharness/install.py [plugins directory]

The new dist/ is built beside the old one and swapped in by renames only
once it is complete, so a failed install leaves the working plugin in place
and an update leaves no stale files. The sibling data/ is never touched.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
# Where the app keeps its data: %APPDATA% on Windows, ~/.config on Linux.
plugins = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("APPDATA", "~/.config"), "LearnHarness", "data", "plugins")
dest = Path(plugins).expanduser() / "dtu-learn" / "dist"
if dest.is_symlink():
    raise SystemExit(f"Refusing to replace {dest}: it is a symlink.")
# The tool list first, so a missing or failing dtumcp stops the install
# before anything is written. Looked for where the plugin looks, as
# backend.cjs extends PATH.
dtumcp = shutil.which("dtumcp", path=os.pathsep.join([os.environ.get("PATH", ""), os.path.expanduser("~/.local/bin")]))
if not dtumcp:
    raise SystemExit("dtumcp is not installed. Run `uv tool install git+https://github.com/RNS-ago/dtuMCP` "
                     "(or `uv tool install -e .` in a clone) first.")
tools = subprocess.run([dtumcp, "tools"], stdout=subprocess.PIPE)
if tools.returncode:
    raise SystemExit("dtumcp could not list its tools, so the installed plugin is left as it was.")
new = dest.with_name("dist.new")
shutil.rmtree(new, ignore_errors=True)
shutil.copytree(here / "dist", new)
(new / "tools.json").write_bytes(tools.stdout)
# Swapped by renames, each done whole or not at all, so the plugin folder is
# never left half deleted: the old dist/ moves aside, the new one takes its
# place, and only then is the old one deleted.
aside = dest.with_name("dist.old")
shutil.rmtree(aside, ignore_errors=True)  # one an earlier install could not delete
if dest.exists():
    try:
        dest.rename(aside)
    except OSError as e:  # on Windows, a file in it is open
        shutil.rmtree(new, ignore_errors=True)
        raise SystemExit(f"Could not move the installed plugin aside ({e}), so it is left as it was. "
                         "Close LearnHarness and try again.")
try:
    new.rename(dest)
except OSError as e:
    if aside.exists():
        aside.rename(dest)
    shutil.rmtree(new, ignore_errors=True)
    raise SystemExit(f"Could not put the new plugin in place ({e}), so the old one is back.")
shutil.rmtree(aside, ignore_errors=True)
if aside.exists():
    print(f"Could not delete the old plugin at {aside}; the next install does.")
print(f"Installed into {dest}")
