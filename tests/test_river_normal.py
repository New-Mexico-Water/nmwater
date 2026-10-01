"""Flow compared with normal, and drying."""

from datetime import date

import numpy as np
import pandas as pd

from nmwater.derived import river_normal as rn


def test_percentile_rank_counts_ties_as_half():
    base = np.array([10.0, 20.0, 30.0, 40.0])
    assert rn.percentile_rank(base, 5.0) == 0.0
    assert rn.percentile_rank(base, 25.0) == 50.0
    assert rn.percentile_rank(base, 20.0) == 37.5
    assert rn.percentile_rank(base, 50.0) == 100.0


def test_classes_follow_waterwatch():
    assert [rn.classify(p) for p in (0, 9.9, 10, 24.9, 25, 75, 76, 90, 91, 100)] == [
        "much below normal", "much below normal", "below normal", "below normal", "normal", "normal",
        "above normal", "above normal", "much above normal", "much above normal"]
    assert rn.classify(None) is None


def test_this_years_week_is_ranked_against_the_same_week_in_the_baseline():
    rows = []
    for y in range(1991, 2021):                       # 30 baseline years, flow = year - 1990 in week-of-year 8
        monday = next(d for d in pd.date_range(f"{y}-02-20", periods=14) if d.weekday() == 0 and (d.dayofyear - 1) // 7 == 8)
        rows.append({"gauge": "G", "week_start": monday.date(), "mean_cfs": float(y - 1990)})
    rows.append({"gauge": "G", "week_start": date(2026, 3, 2), "mean_cfs": 3.5})
    gw = pd.DataFrame(rows)
    res = rn.flow_vs_normal(gw, {"G": "S"}, {"G"}, [date(2026, 3, 2)])
    g = res.gauges.iloc[0]
    assert g["basis"] == "1991-2020" and round(g["pct"], 1) == 10.0 and g["cls"] == "below normal"
    assert res.segments.iloc[0]["pct"] == g["pct"]


def test_normally_dry_gauges_are_left_out_of_segment_counts():
    days = pd.date_range("2025-01-01", periods=100)
    chosen = pd.concat([
        pd.DataFrame({"gauge": "below dam", "date": days, "source": "usgs", "cfs": 0.0}),           # always dry
        pd.DataFrame({"gauge": "river", "date": days, "source": "usgs", "cfs": [0.0] * 10 + [5.0] * 90})])
    r = rn.drying(chosen, {"below dam": "S", "river": "S"}, {"below dam", "river"}, date(2025, 4, 10))
    row = r.by_segment.iloc[0]
    assert row["any_dry_days"] == 10 and r.normally_dry == {"S": ["below dam"]}
