"""The new-snow estimate: what counts as a snowfall, unknown versus zero, windows and their coverage, and the rating against other winters."""

import numpy as np
import pandas as pd
import pytest

from nmwater.derived import snotel_snow as ss


def series(values, start="2026-01-01"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values)), dtype=float)


def test_a_depth_rise_counts_only_when_the_water_reading_rises_with_it():
    depth = series([10, 14, 14, 20, 21.5, 21.5, 24])
    swe = series([2, 2.5, 2.5, 2.5, 2.6, 2.6, 3.0])                   # water rose on days 2 (+0.5), 5 (+0.1) and 7 (+0.4); day 4's 6-inch rise has none
    new = ss.new_snow_daily(depth, swe)
    assert new.isna().iloc[0] and list(new.iloc[1:]) == [4.0, 0.0, 0.0, 1.5, 0.0, 2.5]


def test_rises_under_the_floor_over_the_cap_and_with_no_water_series_are_not_counted():
    depth = series([10, 10.9, 10.9, 45.0, 45.0, 50.0])
    swe = series([2, 2.5, 2.5, 6, 6, 7])
    assert list(ss.new_snow_daily(depth, swe).iloc[1:]) == [0.0, 0.0, 0.0, 0.0, 5.0]      # +0.9 under the floor, +34.1 over the cap
    assert (ss.new_snow_daily(depth, None).iloc[1:] == 0).all()                         # nothing confirms a rise without a water reading


def test_a_missing_day_is_unknown_not_zero():
    depth = series([10, 14, np.nan, 20, 25])
    swe = series([2, 2.5, np.nan, 3.0, 3.5])
    new = ss.new_snow_daily(depth, swe)
    assert new.isna().iloc[[0, 2, 3]].all() and new.iloc[1] == 4.0 and new.iloc[4] == 5.0     # no rise can be measured across a gap


def test_windows_sum_the_estimate_and_need_most_of_their_days():
    new = series([0.0, 2.0, 0.0, 3.0, 0.0, 0.0, 1.0, 0.0, 4.0], "2026-01-01")
    d = pd.Timestamp("2026-01-09")
    assert ss.window_total(new, d, "new24") == 4.0
    assert ss.window_total(new, d, "new7") == pytest.approx(3.0 + 1.0 + 4.0)                   # 3 Jan to 9 Jan
    gap = new.copy()
    gap.iloc[[3, 4]] = np.nan                                                                    # 2 of 7 days unknown: 71% coverage
    assert np.isnan(ss.window_total(gap, d, "new7"))
    gap2 = new.copy()
    gap2.iloc[3] = np.nan                                                                        # 1 of 7 unknown: 86%
    assert ss.window_total(gap2, d, "new7") == pytest.approx(1.0 + 4.0)


def test_the_winter_so_far_starts_on_1_october_and_a_leap_day_maps_to_the_28th():
    new = pd.Series(1.0, index=pd.date_range("2025-09-20", "2026-01-15"))
    assert ss.window_total(new, pd.Timestamp("2026-01-15"), "newseason") == (pd.Timestamp("2026-01-15") - pd.Timestamp("2025-10-01")).days + 1
    assert ss.same_day(pd.Timestamp("2024-02-29"), 2023) == pd.Timestamp("2023-02-28")
    assert ss.same_day(pd.Timestamp("2026-01-15"), 2010) == pd.Timestamp("2010-01-15") and ss.same_day(pd.Timestamp("2025-12-01"), 2010) == pd.Timestamp("2009-12-01")


def winters(per_year, start=2004, end=2026, jan=True):
    """A station whose new snow on every day of a January is per_year[w] / 31 in winter w (the water year ending in w)."""
    rows = {}
    for w in range(start, end + 1):
        idx = pd.date_range(f"{w - 1}-10-01", f"{w}-04-30")
        v = pd.Series(0.0, index=idx)
        v[f"{w}-01-01":f"{w}-01-31"] = per_year(w) / 31
        rows[w] = v
    return pd.concat(rows.values())


def test_a_total_is_rated_against_the_same_window_in_other_winters():
    st = winters(lambda w: 10.0 + (w - 2004) * 0.5)             # 10 in in 2004 up to 21 in in 2025; this winter (2026) has 40 in
    st[st.index >= "2026-01-01"] = 0.0
    st["2026-01-01":"2026-01-31"] = 40 / 31
    r = ss.rate_new_snow({"a": st}, "2026-01-31", "new7", years=(2004, 2025))
    assert r["stations"] == 1 and r["now"] == pytest.approx(7 * 40 / 31, abs=0.01)
    wide = ss.rate_new_snow({"a": st}, "2026-01-31", "newseason", years=(2004, 2025))
    assert wide["cls"] == "much above normal" and wide["pct"] > 90 and wide["usual"] == pytest.approx(15.25, abs=0.3)      # the median of 10, 10.5 ... 20.5


def test_a_station_with_too_short_a_record_gives_a_value_but_no_usual():
    st = winters(lambda w: 12.0, start=2016, end=2026)
    r = ss.rate_new_snow({"a": st}, "2026-01-31", "newseason", years=(2004, 2025))
    assert r["now"] is not None and r["usual"] is None and r["cls"] is None and r["stations"] == 1 and r["n"] == 0


def test_a_year_counts_only_when_every_station_reported_and_no_stations_gives_nothing():
    a, b = winters(lambda w: 12.0), winters(lambda w: 12.0)
    b["2010-12-01":"2010-12-31"] = np.nan                         # one station is missing a December in 2011's winter: that year drops out of the pool
    r = ss.rate_new_snow({"a": a, "b": b}, "2026-01-31", "newseason", years=(2004, 2025))
    assert r["stations"] == 2 and r["n"] == 21
    assert ss.rate_new_snow({}, "2026-01-31", "newseason")["now"] is None
