import pytest

from _package import MODULES


@pytest.fixture(autouse=True)
def _undo_stand_ins():
    """Put back every module global a test replaced (mod.X = ...), so no test
    leans on what another left behind."""
    saved = [(m, dict(vars(m))) for m in MODULES]
    yield
    for m, before in saved:
        now = vars(m)
        for name in now.keys() - before.keys():
            del now[name]
        for name, value in before.items():
            if now.get(name) is not value:
                now[name] = value
