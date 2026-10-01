"""Recent-rain numbers for the Watershed tab, checked on a synthetic daily series with known answers."""

import duckdb
import pandas as pd
import pytest

from nmwater.derived import river_watershed as rw
from nmwater.reports.river_page import coverage_note


def catalog(daily: pd.Series, huc8="13020101"):
    con = duckdb.connect()
    df = pd.DataFrame({"huc8": huc8, "date": daily.index.date, "interval": "daily", "precip_in": daily.values})
    con.register("p", df)
    con.execute("CREATE TABLE watershed_precip AS SELECT * FROM p")
    con.execute("CREATE TABLE watersheds AS SELECT '13020101' AS huc8, 0.98 AS grid_fraction, true AS has_precip, 0.9 AS nm_fraction, 'CO,NM' AS states")
    return con


def series(end="2026-09-26", per_day=0.1, wet_days: dict | None = None):
    idx = pd.date_range("1990-01-01", end)
    s = pd.Series(per_day, index=idx)
    for d, v in (wet_days or {}).items():
        s[pd.Timestamp(d)] = v
    return s


def test_last_30_days_against_the_same_dates_in_1991_2020():
    s = series(wet_days={"2026-09-20": 2.0, "2026-09-10": 0.5})
    out, cov = rw.precip_summary(catalog(s), ["13020101"])
    r = out["13020101"]["recent"]
    assert r["total"] == pytest.approx(30 * 0.1 + 1.9 + 0.4)            # two wet days on top of 0.1 a day
    assert r["normal"] == pytest.approx(3.0)                            # every baseline year is a flat 0.1 a day
    assert r["pct_normal"] == pytest.approx(r["total"] / 3.0 * 100)
    assert r["percentile"] == pytest.approx(100.0)                      # wetter than all 30 baseline years
    assert r["wettest_in"] == pytest.approx(2.0) and r["wettest_date"] == pd.Timestamp("2026-09-20")
    assert r["window_start"] == pd.Timestamp("2026-08-28") and r["last"] == pd.Timestamp("2026-09-26")


def test_a_month_in_progress_is_compared_with_the_same_days_of_other_years():
    s = series(wet_days={"2026-09-05": 1.0})
    x = rw.precip_summary(catalog(s), ["13020101"])[0]["13020101"]
    assert x["month_complete"][-1] is False and x["month_days"][-1] == 26
    assert x["month_total"][-1] == pytest.approx(26 * 0.1 + 0.9)
    assert x["month_normal"][-1] == pytest.approx(2.6)                  # 26 days at 0.1, not a full September (3.0)
    assert x["month_complete"][-2] is True and x["month_normal"][-2] == pytest.approx(3.1)   # August has 31 days
    assert len(x["months"]) == rw.MONTHS_SHOWN


def test_recent_days_and_the_year_for_the_csv():
    x = rw.precip_summary(catalog(series()), ["13020101"])[0]["13020101"]
    assert len(x["daily"]) == rw.RECENT_DAYS and x["daily_dates"][-1] == pd.Timestamp("2026-09-26")
    assert len(x["year_daily"]) == 365


def test_dry_window_has_no_percent_of_normal():
    out = rw.precip_summary(catalog(series(per_day=0.0)), ["13020101"])[0]["13020101"]["recent"]
    assert out["total"] == 0 and out["pct_normal"] is None


def test_none_without_the_view():
    assert rw.precip_summary(duckdb.connect(), ["13020101"]) is None


def test_coverage_notes():
    assert coverage_note(None) == "" and coverage_note({"grid_fraction": 0.99, "has_precip": True}) == ""
    assert "69%" in coverage_note({"grid_fraction": 0.69, "has_precip": True, "states": "CO,NM"})
    assert "no data in Mexico" not in coverage_note({"grid_fraction": 0.69, "has_precip": True, "states": "CO,NM"})
    assert "no data in Mexico" in coverage_note({"grid_fraction": 0.28, "has_precip": True, "states": "MX,NM"})
    assert "not shown" in coverage_note({"grid_fraction": 0.003, "has_precip": False, "states": "CO"})
