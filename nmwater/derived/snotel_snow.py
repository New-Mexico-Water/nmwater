"""New snow at SNOTEL stations, estimated from the depth and water-in-snow readings (SNOTEL does not report snowfall).

A day has new snow when the snow depth rises by FLOOR_IN to CAP_IN inches since the day before and the snow-water reading rises by at least SWE_STEP_IN the same day. Depth sensors
jitter and real snow adds water, so the water reading is what separates a snowfall from sensor noise: counting depth rises alone added 220 to 440 inches a winter at some stations.
The rise is the day's new snow in inches of snow. Snow settles and melts, so a total undercounts what a person measuring on a board would have recorded, and the pages say so.
A day with no reading, or no reading the day before, is unknown, never zero.

Windows: the last 24 hours (the day itself), the last 7 days and the winter so far (since 1 October). A total needs MIN_COVERAGE of its days.
Rating: the mean total over a place's stations against the same stations' totals for the same window in each baseline year; a station counts only with MIN_WINTERS winters of
record, and a year only when every counted station reported (snow_normal.MIN_POOL_NEW of 22 years are needed to state a usual).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import snow_normal as sn

FLOOR_IN = 1.0
CAP_IN = 30.0
SWE_STEP_IN = 0.1
MIN_COVERAGE = 0.85
MIN_WINTERS = 17
KINDS = ("new24", "new7", "newseason")


def new_snow_daily(depth: pd.Series, swe: pd.Series | None) -> pd.Series:
    """Daily estimated new snow in inches, indexed by day: 0 where nothing counted, NaN where a reading is missing."""
    d = depth.asfreq("D")
    rise = d.diff()
    water = (swe.asfreq("D").diff() if swe is not None and len(swe) else pd.Series(np.nan, index=d.index)).reindex(d.index)
    counted = (rise >= FLOOR_IN) & (rise <= CAP_IN) & (water >= SWE_STEP_IN)
    return rise.where(counted, 0.0).where(rise.notna())


def window_total(new: pd.Series, day, kind: str) -> float:
    """The estimate over the window ending `day`, or NaN when under MIN_COVERAGE of its days have data."""
    day = pd.Timestamp(day)
    if kind == "new24":
        v = new.get(day, np.nan)
        return float(v) if pd.notna(v) else np.nan
    start = day - pd.Timedelta(days=6) if kind == "new7" else pd.Timestamp(year=day.year - (0 if day.month >= 10 else 1), month=10, day=1)
    w = new[start:day]
    need = (day - start).days + 1
    return np.nan if len(w.dropna()) < MIN_COVERAGE * need else float(w.sum())


def same_day(day, water_year: int) -> pd.Timestamp:
    """`day`'s month and day in the calendar year of `water_year` that holds it (a Feb 29 becomes Feb 28 in other years)."""
    day = pd.Timestamp(day)
    y = water_year - 1 if day.month >= 10 else water_year
    return pd.Timestamp(year=y, month=day.month, day=min(day.day, pd.Timestamp(year=y, month=day.month, day=1).days_in_month))


def rate_new_snow(new_by_station: dict[str, pd.Series], day, kind: str, years: tuple[int, int] = sn.BASELINE) -> dict:
    """{now, usual, pct, cls, little, n, stations} for the mean of the stations' totals. `stations` counts those with enough winters; none -> now None."""
    empty = {"now": None, "usual": None, "pct": None, "cls": None, "little": False, "n": 0, "stations": 0}
    day = pd.Timestamp(day)
    now = {s: window_total(n, day, kind) for s, n in new_by_station.items()}
    ok = [s for s, v in now.items() if not np.isnan(v)]
    if not ok:
        return empty
    wys = list(range(years[0], years[1] + 1))
    hist = {s: {w: window_total(new_by_station[s], same_day(day, w), kind) for w in wys} for s in ok}
    elig = [s for s in ok if sum(1 for v in hist[s].values() if not np.isnan(v)) >= MIN_WINTERS]
    if not elig:
        return {**empty, "now": round(float(np.mean([now[s] for s in ok])), 2), "stations": len(ok)}
    mean_now = float(np.mean([now[s] for s in elig]))
    pool = np.array([float(np.mean([hist[s][w] for s in elig])) if all(not np.isnan(hist[s][w]) for s in elig) else np.nan for w in wys])
    r = sn.rate(mean_now, pool[~np.isnan(pool)], kind, min_pool=sn.MIN_POOL_NEW)
    return {**r, "stations": len(elig)}
