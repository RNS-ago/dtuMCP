#!/bin/sh
# Install the DTU Learn plugin into LearnHarness, or update it: puts the
# plugin, server.py and the tool list in <plugins directory>/dtu-learn/dist.
# Pass the plugins directory shown in Settings -> Community plugins if it is
# not the default. Restart LearnHarness afterwards to load the new code.
#
# The new dist/ is built beside the old one and swapped in only once it is
# complete, so a failed install leaves the working plugin in place and an
# update leaves no stale files. The sibling data/ is never touched.
set -e
here=$(cd "$(dirname "$0")" && pwd)
dest="${1:-$HOME/.config/LearnHarness/data/plugins}/dtu-learn/dist"
if [ -L "$dest" ]; then
	echo "Refusing to replace $dest: it is a symlink." >&2
	exit 1
fi
rm -rf "$dest.new"
mkdir -p "$dest.new"
cp -R "$here"/dist/. "$dest.new/"
cp "$here/../server.py" "$dest.new/"
uv run --quiet --script "$dest.new/server.py" tools > "$dest.new/tools.json"
rm -rf "$dest"
mv "$dest.new" "$dest"
echo "Installed into $dest"
