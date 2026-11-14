"""What is usual for a date, and how a value rates against it, for the snow pages (SNODAS series per place, SNOTEL new-snow totals).

A series is a daily value per water year (1 October to 30 September, Feb 29 dropped so every year has 365 days). The usual for a day is the values on the same day and the
WINDOW_DAYS either side in each baseline year; the rating is the mid-rank percentile of today's value in that pool, in the site's five classes (river_normal.classify).
No usual is stated from fewer than MIN_POOL values. `little` marks a value and a usual that are both under the measure's LITTLE_IN: a rating of a few hundredths of an inch
against another few hundredths says nothing. What a page does with `little` (and with MIN_USUAL_IN and PERCENT_FROM_IN, which travel in the bundle) is the page's business.
"""

from __future__ import annotations

import calendar

import numpy as np
import pandas as pd

from . import river_normal as rn

BASELINE = (2004, 2025)                 # water years: the first complete SNODAS winter to the last complete one
WINDOW_DAYS = 3
MIN_POOL = 20                           # values in the pool before a usual is stated
MIN_POOL_NEW = 15                       # new-snow totals pool a place's stations, and a year counts only when every one of them reported
DAYS = 365
BAND_KEYS = ("p10", "p25", "p50", "p75", "p90")
BAND_Q = (10, 25, 50, 75, 90)
MEASURES = ("swe", "depth", "new24", "new7", "newseason")
LITTLE_IN = {"swe": 0.05, "depth": 0.5, "new24": 1.0, "new7": 1.0, "newseason": 1.0}
MIN_USUAL_IN = {"swe": 0.05, "depth": 0.5, "new24": 0.5, "new7": 1.0, "newseason": 2.0}     # a watershed is rated only where this much snow is usual
PERCENT_FROM_IN = {"swe": 1.0, "depth": 5.0, "new24": 2.0, "new7": 4.0, "newseason": 10.0}   # a percent of usual is stated only above this usual amount


def water_year(d: pd.Timestamp) -> int:
    return d.year + (1 if d.month >= 10 else 0)


def day_index(d: pd.Timestamp) -> tuple[int, int] | None:
    """(water year, day of water year 0..364), or None for Feb 29."""
    if d.month == 2 and d.day == 29:
        return None
    wy = water_year(d)
    i = (d - pd.Timestamp(year=wy - 1, month=10, day=1)).days
    if calendar.isleap(wy) and 3 <= d.month <= 9:
        i -= 1
    return wy, i


def season_matrix(dates: pd.DatetimeIndex, values: np.ndarray, years: range) -> dict[int, np.ndarray]:
    """{water year: [n_series, 365]} (NaN where a day is missing). `values` is [n_days, n_series] or [n_days]."""
    v = np.asarray(values, float)
    if v.ndim == 1:
        v = v[:, None]
    out = {w: np.full((v.shape[1], DAYS), np.nan) for w in years}
    for k, d in enumerate(pd.DatetimeIndex(dates)):
        di = day_index(d)
        if di is None or di[0] not in out or not 0 <= di[1] < DAYS:
            continue
        out[di[0]][:, di[1]] = v[k]
    return out


def pool(mats: dict[int, np.ndarray], series: int, i: int, years: tuple[int, int] = BASELINE, half: int = WINDOW_DAYS) -> np.ndarray:
    """The baseline years' values on day i and `half` days either side (no wrap past the ends of the water year), NaN removed."""
    lo, hi = max(0, i - half), min(DAYS, i + half + 1)
    a = np.concatenate([mats[w][series, lo:hi] for w in range(years[0], years[1] + 1) if w in mats]) if mats else np.array([])
    return a[~np.isnan(a)]


def band(mats: dict[int, np.ndarray], series: int, years: tuple[int, int] = BASELINE) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {k: [] for k in BAND_KEYS}
    for i in range(DAYS):
        p = pool(mats, series, i, years)
        for k, q in zip(BAND_KEYS, BAND_Q):
            out[k].append(round(float(np.percentile(p, q)), 3) if len(p) >= MIN_POOL else None)
    return out


def rate(now: float | None, p: np.ndarray, measure: str, min_pool: int = MIN_POOL) -> dict:
    """{now, usual, pct, cls, little, n} for a value against a pool of past values."""
    n = len(p)
    if now is None or not np.isfinite(now):
        return {"now": None, "usual": None, "pct": None, "cls": None, "little": False, "n": n}
    now = float(now)
    if n < min_pool:
        return {"now": round(now, 3), "usual": None, "pct": None, "cls": None, "little": False, "n": n}
    med = float(np.median(p))
    pct = rn.percentile_rank(p, now)
    little = now < LITTLE_IN[measure] and med < LITTLE_IN[measure]
    return {"now": round(now, 3), "usual": round(med, 3), "pct": round(pct, 1), "cls": None if little else rn.classify(pct), "little": bool(little), "n": n}
