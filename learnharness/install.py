#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Install the DTU Learn plugin into LearnHarness, or update it: puts the
plugin, server.py and the tool list in <plugins directory>/dtu-learn/dist.
Pass the plugins directory shown in Settings -> Community plugins if it is
not the default. Restart LearnHarness afterwards to load the new code.

    uv run learnharness/install.py [plugins directory]

The new dist/ is built beside the old one and swapped in only once it is
complete, so a failed install leaves the working plugin in place and an
update leaves no stale files. The sibling data/ is never touched.
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
new = dest.with_name("dist.new")
shutil.rmtree(new, ignore_errors=True)
shutil.copytree(here / "dist", new)
shutil.copy(here.parent / "server.py", new)
tools = subprocess.run(["uv", "run", "--quiet", "--script", str(new / "server.py"), "tools"], stdout=subprocess.PIPE)
if tools.returncode:
    raise SystemExit("server.py could not list its tools, so the installed plugin is left as it was.")
(new / "tools.json").write_bytes(tools.stdout)
if dest.exists():
    shutil.rmtree(dest)
new.rename(dest)
print(f"Installed into {dest}")
