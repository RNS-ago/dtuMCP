"""The dtumcp modules as the one namespace the tests are written against."""
import importlib
import os
import tempfile

# A data folder of the tests' own, so nothing a test saves lands in the real one.
os.environ["DTU_DOWNLOADS"] = tempfile.mkdtemp(prefix="dtumcp-tests-")

MODULES = [importlib.import_module(f"dtumcp.{name}") for name in (
    "app", "text", "client", "pdfmath", "courses", "files", "index_page",
    "search", "download", "video", "tools", "auth", "cli")]


class _Package:
    """mod.X reads X where it is defined, and mod.X = stand_in sets it in every
    module that has X, so the stand-in reaches every caller."""

    def __getattr__(self, name):
        for m in MODULES:
            if name in vars(m):
                return vars(m)[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        hits = [m for m in MODULES if name in vars(m)]
        if not hits:
            raise AttributeError(f"no module has {name}")
        for m in hits:
            setattr(m, name, value)


mod = _Package()
mod.CHROMES = []  # a Chrome on the machine running the tests is not one to list
