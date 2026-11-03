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


# ---- #51: a file fetched in the last 24 hours is not downloaded again by a revision re-pull
from datetime import datetime, timezone

from nmwater.sources.prism import fetched_within


class _Ledger:
    def __init__(self, rec):
        self.rec = rec

    def get(self, key):
        return self.rec


def test_a_file_fetched_a_few_hours_ago_is_skipped_but_one_from_yesterday_is_not():
    now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    rec = lambda t, status="ok": SimpleNamespace(status=status, fetched_at=t)    # noqa: E731
    assert fetched_within(_Ledger(rec("2026-11-04T06:00:00Z")), "k", now=now)
    assert not fetched_within(_Ledger(rec("2026-11-03T11:00:00Z")), "k", now=now)
    assert not fetched_within(_Ledger(rec("2026-11-04T06:00:00Z", "error")), "k", now=now)
    assert not fetched_within(_Ledger(None), "k", now=now)
