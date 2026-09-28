"""Automatic data checks behind the river pages."""

from datetime import date

import pandas as pd

from nmwater.reports import river_issues as ri


def _days(start, end, months=None):
    d = pd.date_range(start, end)
    return pd.Series(d[d.month.isin(months)] if months else d)


def test_month_span_wraps_the_year():
    assert ri._month_span([12, 1, 2]) == "Dec–Feb"  # noqa: RUF001
    assert ri._month_span([1, 2, 7]) == "Jan–Feb, Jul"  # noqa: RUF001


def test_seasonal_gauge_is_found_and_its_winter_is_not_a_gap():
    days = _days("2015-01-01", "2020-12-31", months=list(range(3, 12)))      # Mar–Nov only  # noqa: RUF003
    season = ri.seasonal_months(days)
    assert season == [1, 2, 12]
    assert ri.gaps(days, season) == []                                       # winters are expected
    assert ri.seasonal_months(_days("2015-01-01", "2020-12-31")) == []


def test_gaps_report_long_missing_runs():
    days = pd.concat([_days("2010-01-01", "2010-06-30"), _days("2011-01-01", "2011-12-31")])
    g = ri.gaps(days, [])
    assert len(g) == 1 and g[0][0] == pd.Timestamp("2010-07-01") and g[0][2] == 184


def test_runs_find_flatlines_and_zero_runs():
    d = pd.date_range("2020-01-01", periods=10)
    v = [1, 5, 5, 5, 0, 0, 0, 7, 7, 2]
    same = ri.runs(pd.Series(v), pd.Series(d), "same")
    zero = ri.runs(pd.Series(v), pd.Series(d), "zero")
    assert [(r[2], r[3]) for r in same] == [(3, 5.0), (2, 7.0)]
    assert [(r[0], r[2]) for r in zero] == [(pd.Timestamp("2020-01-05"), 3)]


def test_spike_needs_to_stand_out_from_neighbours_and_range():
    d = pd.date_range("2020-01-01", periods=100)
    v = pd.Series([10.0] * 100)
    v[50] = 5000.0
    assert [x[1] for x in ri.spikes(v, pd.Series(d))] == [5000.0]
    v2 = v.copy()
    v2[50] = 40.0
    assert ri.spikes(v2, pd.Series(d)) == []


def test_unit_hint_recognises_cms_and_kcfs():
    assert ri.unit_hint(35.2) and ri.unit_hint(0.0284) and ri.unit_hint(1000.0)
    assert ri.unit_hint(1.2) is None


def _river(values, sources=None, dates=None):
    dates = dates if dates is not None else pd.date_range("2020-01-01", periods=len(values))
    sources = sources or ["usgs"] * len(values)
    return pd.DataFrame({"gauge": "G", "date": dates, "source": sources, "site_uid": "u", "cfs": values})


def test_find_issues_confirms_floods_with_annual_peaks():
    v = [10.0] * 120
    v[60] = 5000.0
    ch = _river(v)
    seg = pd.DataFrame({"segment": ["S"], "week_start": [date(2020, 1, 6)], "n_gauges": [1]})
    kinds = lambda peaks: [(f.kind, f.severity) for f in ri.find_issues(ch, ch, seg, date(2020, 5, 1), {"G"}, {"G": "S"},  # noqa: E731
                                                                          ["usgs"], peaks)]
    assert ("spike", "info") in kinds({})                                        # no peak to check against
    assert not any(k == "spike" for k, _ in kinds({("G", 2020): 8000.0}))       # below the peak: a real flood
    assert ("spike", "warn") in kinds({("G", 2020): 3000.0})                    # above the peak: impossible


def test_find_issues_flags_a_biased_copy_only_as_a_warning_when_it_is_used():
    days = pd.date_range("2020-01-01", periods=60)
    usgs = _river([100.0] * 60, dates=days)
    cwms = _river([80.0] * 60, sources=["usace_cwms"] * 60, dates=days)
    seg = pd.DataFrame({"segment": ["S"], "week_start": [date(2020, 1, 6)], "n_gauges": [1]})
    daily = pd.concat([usgs, cwms])
    f = [x for x in ri.find_issues(daily, usgs, seg, date(2020, 3, 1), {"G"}, {"G": "S"}, ["usgs", "usace_cwms"])
         if x.kind == "copy_bias"]
    assert len(f) == 1 and f[0].severity == "info" and "20% lower" in f[0].text
