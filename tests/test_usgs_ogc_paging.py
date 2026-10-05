"""Issue #50: a full page that cannot be split further by time must be paged on, not returned as if it were complete."""

from datetime import date
from types import SimpleNamespace

import pandas as pd

from nmwater.sources.usgs import USGS


class _Usgs(USGS):
    ogc = "https://example.test"                                      # the real one is a property that reads the configuration


def _src(total_rows: int, page: int = 4):
    """A USGS source whose API holds `total_rows` rows, served `page` at a time by limit/offset."""
    s = _Usgs.__new__(_Usgs)
    s.OGC_PAGE = page
    calls = []

    def get(url, params=None, **kw):
        calls.append(dict(params))
        off = int(params.get("offset", 0))
        rows = list(range(off, min(off + page, total_rows)))
        text = "id,v\n" + "\n".join(f"{i},{i * 2}" for i in rows) if rows else ""
        return SimpleNamespace(from_cache=False, read_text=lambda: text)
    s.get = get
    return s, calls


def _fetch(s, collection="field-measurements"):
    summ = SimpleNamespace(n_requests=0, n_cached=0, notes=[])
    frames = s._ogc_fetch_window(collection, {"f": "csv", "limit": s.OGC_PAGE}, date(2020, 6, 1), date(2020, 6, 1), False, summ, depth=3)
    return pd.concat(frames, ignore_index=True), summ


def test_a_one_day_window_with_more_rows_than_a_page_returns_every_row():
    s, calls = _src(11)                                              # pages of 4: 4 + 4 + 3
    df, summ = _fetch(s)
    assert len(df) == 11 and df["id"].astype(int).tolist() == list(range(11))
    assert [c.get("offset") for c in calls] == [None, 4, 8] and summ.n_requests == 3


def test_exactly_a_full_last_page_is_followed_by_one_empty_request_and_nothing_is_lost():
    s, calls = _src(8)
    df, _ = _fetch(s)
    assert len(df) == 8


def test_collections_that_reject_a_datetime_filter_are_paged_too():
    s, calls = _src(9)
    df, _ = _fetch(s, "peaks")
    assert len(df) == 9 and all("datetime" not in c for c in calls)


def test_a_result_that_still_does_not_fit_after_the_page_limit_says_so():
    s, _ = _src(10_000)
    s.OGC_MAX_PAGES = 3
    df, summ = _fetch(s)
    assert len(df) == 12 and any("truncated" in n for n in summ.notes)
