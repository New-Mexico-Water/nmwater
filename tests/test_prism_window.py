"""PRISM re-pulls only the last week on a routine night and the whole revision window on a revision run."""

from datetime import date, timedelta
from types import SimpleNamespace

from nmwater.sources.prism import PRISM


def _src(options=None):
    src = PRISM.__new__(PRISM)
    src.cfg = SimpleNamespace(options=options or {})
    return src


def test_routine_nights_use_the_short_window_and_everything_else_the_full_one():
    assert _src().revise_after(routine=True) == date.today() - timedelta(days=7)
    assert _src().revise_after() == date.today() - timedelta(days=190)         # a revision run, or a manual --since
    assert _src({"routine_revision_days": 3}).revise_after(routine=True) == date.today() - timedelta(days=3)
    assert _src({"revision_days": 120}).revise_after() == date.today() - timedelta(days=120)
