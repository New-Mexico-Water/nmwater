"""Flow compared with normal, and drying, for a river's gauges and segments.

Normal: for each gauge and week of the year, this year's weekly mean is ranked among the same week's
weekly means in a baseline period (1991-2020, the WMO standard normal, by default). Classes follow
USGS WaterWatch: below 10th percentile "much below normal", 10-24 "below", 25-75 "normal",
76-90 "above", above 90 "much above". A gauge without 20 baseline years for a week falls back to its
whole record (at least 10 years), and says so.

A segment's status is the median of its gauges' percentiles that week. It is not computed from the
segment mean, because the gauges behind a segment mean change over the decades and would make the
baseline inconsistent.

Drying: a day is dry at a gauge when its daily mean is below DRY_CFS. For each segment and year we
count days on which any of its gauges was dry, and gauge-days dry in total. Gauges that are dry on at
least NORMALLY_DRY of their days (for example just below a dam whose flow is diverted into a canal, like
the Pecos below Avalon Dam) are left out of the segment count and listed instead: a dry day there says
nothing about the river.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

BASELINE = (1991, 2020)
MIN_BASE_YEARS = 20
MIN_FALLBACK_YEARS = 10
DRY_CFS = 0.1
NORMALLY_DRY = 0.9
CLASSES = [(10, "much below normal"), (25, "below normal"), (76, "normal"), (91, "above normal"),
           (101, "much above normal")]


def classify(pct: float | None) -> str | None:
    if pct is None or (isinstance(pct, float) and np.isnan(pct)):
        return None
    for upper, label in CLASSES:
        if pct < upper:
            return label
    return CLASSES[-1][1]


def week_of_year(d: pd.Series) -> pd.Series:
    """0..51 from a week's start date (the 53rd partial week folds into the 52nd)."""
    return ((pd.to_datetime(d).dt.dayofyear - 1) // 7).clip(upper=51)


def percentile_rank(base: np.ndarray, v: float) -> float:
    """Percent of baseline values below v, counting ties as half (a mid-rank percentile)."""
    base = base[~np.isnan(base)]
    if len(base) == 0 or np.isnan(v):
        return np.nan
    return float(100.0 * ((base < v).sum() + 0.5 * (base == v).sum()) / len(base))


@dataclass
class NormalResult:
    weeks: list[date]                 # the window being rated (last 52 weeks)
    gauges: pd.DataFrame              # gauge, segment, week_start, mean_cfs, pct, cls, p10..p90, basis
    segments: pd.DataFrame            # segment, week_start, pct (median), cls, n_gauges


def flow_vs_normal(gw_all: pd.DataFrame, segment_of: dict[str, str], elig: set[str], weeks: list[date],
                   baseline: tuple[int, int] = BASELINE) -> NormalResult:
    gw = gw_all[gw_all["gauge"].isin(elig)].copy()
    gw["week_start"] = pd.to_datetime(gw["week_start"])
    gw["woy"] = week_of_year(gw["week_start"])
    gw["year"] = gw["week_start"].dt.year
    window = pd.to_datetime(pd.Series(weeks))
    cur = gw[gw["week_start"].isin(window)]
    hist = gw[gw["week_start"] < window.min()]
    rows = []
    for (gname, woy), h in hist.groupby(["gauge", "woy"]):
        base = h[(h["year"] >= baseline[0]) & (h["year"] <= baseline[1])]["mean_cfs"].to_numpy()
        basis = f"{baseline[0]}-{baseline[1]}"
        if len(base) < MIN_BASE_YEARS:
            base = h["mean_cfs"].to_numpy()
            basis = f"whole record ({h['year'].min()}-{h['year'].max()})"
            if len(base) < MIN_FALLBACK_YEARS:
                continue
        q = np.nanpercentile(base, [10, 25, 50, 75, 90])
        rows.append({"gauge": gname, "woy": woy, "basis": basis, "n_base": len(base), "_base": base,
                     "p10": q[0], "p25": q[1], "p50": q[2], "p75": q[3], "p90": q[4]})
    bands = pd.DataFrame(rows)
    if bands.empty:
        return NormalResult(weeks, pd.DataFrame(), pd.DataFrame())
    # every gauge-week in the window gets its bands, with or without a value that week
    grid = pd.MultiIndex.from_product([sorted(set(bands["gauge"])), window], names=["gauge", "week_start"]).to_frame(index=False)
    grid["woy"] = week_of_year(grid["week_start"])
    g = grid.merge(bands, on=["gauge", "woy"], how="inner").merge(
        cur[["gauge", "week_start", "mean_cfs"]], on=["gauge", "week_start"], how="left")
    g["pct"] = [percentile_rank(b, v) if not np.isnan(v) else np.nan for b, v in zip(g["_base"], g["mean_cfs"].fillna(np.nan))]
    g["cls"] = g["pct"].map(classify)
    g["segment"] = g["gauge"].map(segment_of)
    g = g.drop(columns=["_base"]).sort_values(["segment", "gauge", "week_start"]).reset_index(drop=True)
    s = g.dropna(subset=["pct"]).groupby(["segment", "week_start"])["pct"].agg(["median", "size"]).reset_index()
    s.columns = ["segment", "week_start", "pct", "n_gauges"]
    s["cls"] = s["pct"].map(classify)
    return NormalResult(weeks, g, s)


@dataclass
class DryingResult:
    by_segment: pd.DataFrame          # segment, year, any_dry_days, gauge_dry_days, days_with_data, n_gauges
    by_gauge: pd.DataFrame            # gauge, segment, year, dry_days, data_days
    as_of: date
    normally_dry: dict                # segment -> [gauges left out of the segment count]


def drying(chosen: pd.DataFrame, segment_of: dict[str, str], elig: set[str], as_of: date) -> DryingResult:
    d = chosen[chosen["gauge"].isin(elig)].copy()
    d["date"] = pd.to_datetime(d["date"])
    d["segment"] = d["gauge"].map(segment_of)
    d["year"] = d["date"].dt.year
    d["dry"] = d["cfs"] < DRY_CFS
    by_gauge = d.groupby(["gauge", "segment", "year"]).agg(dry_days=("dry", "sum"), data_days=("dry", "size")).reset_index()
    share = d.groupby("gauge")["dry"].mean()
    always = set(share[share >= NORMALLY_DRY].index)
    normally_dry: dict = {}
    for gname in sorted(always):
        normally_dry.setdefault(segment_of.get(gname), []).append(gname)
    d = d[~d["gauge"].isin(always)]
    per_day = d.groupby(["segment", "date"]).agg(any_dry=("dry", "any"), n_dry=("dry", "sum"), n=("dry", "size")).reset_index()
    per_day["year"] = per_day["date"].dt.year
    by_seg = per_day.groupby(["segment", "year"]).agg(any_dry_days=("any_dry", "sum"), gauge_dry_days=("n_dry", "sum"),
                                                      days_with_data=("n", "size"), n_gauges=("n", "max")).reset_index()
    return DryingResult(by_seg, by_gauge, as_of, normally_dry)


PERIOD_DAYS = (30, 90, 180, 365)       # flow periods beyond the week (the week keeps its weekly rating above)
MIN_PERIOD_COVERAGE = 0.75             # share of a period's days a gauge needs data for


def _window_means(vals: np.ndarray, ok: np.ndarray, ends: np.ndarray, k: int) -> np.ndarray:
    """Mean of vals over the k days ending at each index in `ends` (None where fewer than MIN_PERIOD_COVERAGE of them have data)."""
    cs = np.concatenate([[0.0], np.cumsum(np.where(ok, vals, 0.0))])
    cn = np.concatenate([[0], np.cumsum(ok.astype(int))])
    lo = ends - k + 1
    n = cn[ends + 1] - cn[np.maximum(lo, 0)]
    s = cs[ends + 1] - cs[np.maximum(lo, 0)]
    out = np.full(len(ends), np.nan)
    good = (lo >= 0) & (n >= MIN_PERIOD_COVERAGE * k)
    out[good] = s[good] / n[good]
    return out


def flow_periods(chosen: pd.DataFrame, segment_of: dict[str, str], elig: set[str], end: date, periods=PERIOD_DAYS,
                 baseline: tuple[int, int] = BASELINE) -> pd.DataFrame:
    """Mean flow over the last k days ending `end`, rated against the same calendar days in other years, per segment (the median of its gauges'
    percentiles, like the weekly rating). Baseline years are `baseline` when at least MIN_BASE_YEARS have data, else the whole record when at least
    MIN_FALLBACK_YEARS do. Columns: segment, days, pct, cls, n_gauges."""
    d = chosen[chosen["gauge"].isin(elig)].copy()
    if d.empty:
        return pd.DataFrame(columns=["segment", "days", "pct", "cls", "n_gauges"])
    d["date"] = pd.to_datetime(d["date"])
    end_ts = pd.Timestamp(end)
    rows = []
    for gname, g in d.groupby("gauge"):
        seg = segment_of.get(gname)
        s = g.groupby("date")["cfs"].mean()
        idx = pd.date_range(s.index.min(), max(s.index.max(), end_ts), freq="D")
        v = s.reindex(idx)
        ok = v.notna().to_numpy()
        vals = v.fillna(0.0).to_numpy()
        pos = {y: idx.get_indexer([pd.Timestamp(year=y, month=end_ts.month, day=min(end_ts.day, pd.Timestamp(year=y, month=end_ts.month, day=1).days_in_month))])[0]
               for y in range(idx[0].year, end_ts.year + 1)}
        for k in periods:
            cur = _window_means(vals, ok, np.array([idx.get_loc(end_ts)]), k)[0]
            if np.isnan(cur):
                continue
            years = [y for y in range(idx[0].year, end_ts.year) if pos[y] >= 0]
            ends = np.array([pos[y] for y in years])
            m = _window_means(vals, ok, ends, k)
            yrs = np.array(years)
            base = m[(yrs >= baseline[0]) & (yrs <= baseline[1]) & ~np.isnan(m)]
            if len(base) < MIN_BASE_YEARS:
                base = m[~np.isnan(m)]
                if len(base) < MIN_FALLBACK_YEARS:
                    continue
            rows.append({"gauge": gname, "segment": seg, "days": k, "pct": percentile_rank(base, cur)})
    if not rows:
        return pd.DataFrame(columns=["segment", "days", "pct", "cls", "n_gauges"])
    g = pd.DataFrame(rows).dropna(subset=["segment"])
    out = g.groupby(["segment", "days"])["pct"].agg(["median", "size"]).reset_index()
    out.columns = ["segment", "days", "pct", "n_gauges"]
    out["cls"] = out["pct"].map(classify)
    return out
