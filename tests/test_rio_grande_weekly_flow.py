"""Unit tests for the weekly Rio Grande flow report's pure pieces (no catalog needed)."""

import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rio_grande_weekly_flow as rg


def test_week_starts_are_complete_monday_to_sunday_weeks():
    w = rg.week_starts(date(2026, 9, 25), 4)          # Friday: the week of Sep 21 is still open
    assert w[-1] == date(2026, 9, 14) and all(d.weekday() == 0 for d in w)
    assert len(w) == 4 and w[0] == date(2026, 8, 24)
    # data through a Sunday makes that week complete
    assert rg.week_starts(date(2026, 9, 20), 1) == [date(2026, 9, 14)]


def test_group_copies_merges_linked_sites_and_leaves_others():
    links = pd.DataFrame({"site_uid_a": ["usgs:1", "cwms:x"], "site_uid_b": ["usbr:9", "usbr:9"]})
    g = rg.group_copies(["usgs:1", "usbr:9", "cwms:x", "usgs:2"], links)
    assert g["usgs:1"] == g["usbr:9"] == g["cwms:x"] and g["usgs:2"] == "usgs:2"


def test_pick_copy_prefers_priority_and_falls_back_by_day():
    d = pd.DataFrame({
        "gauge": ["G"] * 4, "date": pd.to_datetime(["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-03"]),
        "source": ["usace_cwms", "usgs", "usace_cwms", "codwr"], "cfs": [90.0, 100.0, 80.0, 70.0],
        "site_uid": ["c", "u", "c", "d"]})
    c = rg.pick_copy(d).sort_values("date")
    assert c["source"].tolist() == ["usgs", "usace_cwms", "codwr"]      # each day uses the best copy it has
    assert c["cfs"].tolist() == [100.0, 80.0, 70.0]


def test_pick_copy_is_deterministic_when_a_source_has_two_versions():
    d = pd.DataFrame({"gauge": ["G", "G"], "date": pd.to_datetime(["2026-01-01"] * 2),
                      "source": ["usace_cwms"] * 2, "cfs": [5.0, 9.0], "site_uid": ["Caballo DS EBID", "Caballo DS"]})
    assert rg.pick_copy(d)["cfs"].tolist() == [9.0]


def test_gauge_weekly_needs_four_days_and_ignores_open_weeks():
    weeks = [date(2026, 9, 7), date(2026, 9, 14)]
    days = pd.date_range("2026-09-07", periods=3).tolist() + pd.date_range("2026-09-14", periods=5).tolist()
    d = pd.DataFrame({"gauge": "G", "date": days, "source": "usgs", "cfs": [10.0] * 3 + [20.0] * 5})
    d = pd.concat([d, pd.DataFrame({"gauge": "G", "date": [pd.Timestamp("2026-09-22")], "source": "usgs", "cfs": [999.0]})])
    w = rg.gauge_weekly(d, weeks)
    assert w["week_start"].tolist() == [date(2026, 9, 14)]               # 3 days is too few; Sep 22 is outside
    assert w["mean_cfs"].tolist() == [20.0] and w["n_days"].tolist() == [5]


def test_segment_weekly_averages_gauges_and_counts_them():
    gw = pd.DataFrame({"gauge": ["A", "B", "C"], "week_start": [date(2026, 9, 7)] * 3,
                       "mean_cfs": [100.0, 300.0, 50.0], "n_days": 7, "source_used": "usgs"})
    seg = rg.segment_weekly(gw, {"A": "Upper Rio Grande", "B": "Upper Rio Grande", "C": "Caballo"})
    up = seg[seg["segment"] == "Upper Rio Grande"].iloc[0]
    assert (up["mean_cfs"], up["n_gauges"], up["min_cfs"], up["max_cfs"]) == (200.0, 2, 100.0, 300.0)
    assert seg["segment"].tolist() == ["Upper Rio Grande", "Caballo"]     # upstream to downstream


def test_eligible_gauges_need_enough_weeks():
    gw = pd.DataFrame({"gauge": ["long"] * 30 + ["short"] * 5,
                       "week_start": [date(2026, 1, 5) + pd.Timedelta(weeks=i) for i in range(30)] + [date(2026, 1, 5)] * 5})
    assert rg.eligible_gauges(gw, 26) == {"long"}


def test_copy_agreement_ignores_near_zero_flow_in_percentages():
    daily = pd.DataFrame({"gauge": "G", "date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"] * 2),
                          "source": ["usgs"] * 3 + ["usace_cwms"] * 3,
                          "cfs": [1.0, 100.0, 200.0, 0.0, 100.0, 150.0], "site_uid": ["u"] * 3 + ["c"] * 3})
    chosen = rg.pick_copy(daily)
    a = rg.copy_agreement(daily, chosen).iloc[0]
    assert a["overlap_days"] == 3 and a["days_off"] == 1                 # only the 200 vs 150 day is off
    assert a["median_abs_pct_diff"] == 12.5                              # 0% and 25% on the two days over 10 cfs
