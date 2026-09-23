"""Unit tests for the reservoir report engine's pure pieces (no catalog needed)."""

import sys
from itertools import pairwise
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import reservoir_fill as rf


def test_pava_is_non_increasing_and_pins_heavy_weights():
    v = np.array([100.0, 90.0, 95.0, 80.0, 85.0])
    w = np.array([1.0, 1.0, 1.0, 1e6, 1.0])
    fit = rf.pava(v, w)
    assert all(a >= b - 1e-9 for a, b in pairwise(fit))
    assert abs(fit[3] - 80.0) < 1e-3          # pinned value survives
    assert fit[0] == 100.0


def _synthetic(steps: dict[int, float], elev_by_year: dict[int, tuple[float, float]]) -> pd.DataFrame:
    rows = []
    for y, (lo, hi) in elev_by_year.items():
        dates = pd.date_range(f"{y}-01-01", f"{y}-12-31", freq="D")
        elev = np.linspace(lo, hi, len(dates))
        rows.append(pd.DataFrame({"date": dates, "elev": elev, "y": y, "off": steps[y] + 0.0 * elev}))
    return pd.concat(rows, ignore_index=True)


def test_segment_eras_finds_a_step_with_overlapping_years():
    years = range(2000, 2008)
    steps = {y: (1000.0 if y < 2004 else 0.0) for y in years}
    pr = _synthetic(steps, dict.fromkeys(years, (100.0, 120.0)))
    eras = rf.segment_eras(pr, tol=50.0)
    assert eras[2003] != eras[2004]
    assert eras[2000] == eras[2003] and eras[2004] == eras[2007]


def test_segment_eras_finds_a_step_when_years_do_not_overlap():
    # the lake drops below everything it touched the year before (Navajo 2021 -> 2022)
    years = range(2016, 2024)
    steps = {y: (55000.0 if y < 2022 else 0.0) for y in years}
    elev = {y: ((200.0, 220.0) if y < 2022 else (150.0, 170.0)) for y in years}
    eras = rf.segment_eras(_synthetic(steps, elev), tol=500.0)
    assert eras[2021] != eras[2022]


def test_era_tolerance_scales_with_noise_and_pool():
    pr = _synthetic({2000: 0.0, 2001: 0.0}, {2000: (0, 10), 2001: (0, 10)})
    assert rf.era_tolerance(pr, 400000.0) == 400.0     # 0.1% of the pool when noise is zero
    assert rf.era_tolerance(pr, 1000.0) == 5.0          # never below 5 af


def test_daily_despikes_isolated_subdaily_readings():
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    ts = pd.date_range("2025-09-07 12:00", periods=12, freq="h", tz="UTC")
    vals = [0.0] * 12
    vals[6] = 72874.0                                    # Galisteo's glitch
    con.register("_o", pd.DataFrame({"site_uid": "s:1", "variable": "reservoir_storage",
                                     "interval": "hourly", "datetime_utc": ts, "value": vals}))
    con.execute("create table observations as select * from _o")
    d = rf.daily(con, ["s:1"], ["reservoir_storage"])
    assert d.value.max() == 0.0


def test_daily_keeps_a_genuine_rising_limb():
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    ts = pd.date_range("2025-09-07 12:00", periods=6, freq="h", tz="UTC")
    vals = [0.0, 100.0, 800.0, 1500.0, 1600.0, 1550.0]
    con.register("_o", pd.DataFrame({"site_uid": "s:1", "variable": "reservoir_storage",
                                     "interval": "hourly", "datetime_utc": ts, "value": vals}))
    con.execute("create table observations as select * from _o")
    d = rf.daily(con, ["s:1"], ["reservoir_storage"])
    assert abs(d.value.iloc[0] - np.mean(vals)) < 1e-6


def test_dry_threshold():
    assert rf.dry_threshold(0.0) == 5.0
    assert abs(rf.dry_threshold(89468.0) - 44.734) < 1e-3
