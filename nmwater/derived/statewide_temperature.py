"""Statewide air temperature for New Mexico from PRISM mean-temperature grids (4 km, AN81): data only; nmwater/site/statewide.py publishes it.

Method
- Area: the 33 counties' union (TIGER) is the state polygon. Each 4 km cell is weighted by the fraction of it inside the polygon (as watershed_precip does) times
  cos(latitude), the area of a lat/lon cell. Cells without data are left out of both the sum and the weights; the share of the state the grid covers is published.
- Daily series 1981-: PRISM days (12:00 UTC to 12:00 UTC, dated by the day they end). Monthly series 1895-: used for the long history. From 1981 the monthly
  figures are the PRISM monthly grids, not averages of the daily ones, so the two can differ slightly.
- Windows (7, 30, 90, 180 and 365 days ending on the latest day): mean temperature of the window against the same calendar days in each year of 1991-2020; the
  anomaly is the difference from the mean of those years, the percentile ranks this window among them (the same classes as flow and rain; the website says
  colder or warmer). At least MIN_YEARS years are needed.
- Monthly anomaly: each month against the mean of the same calendar month in 1991-2020. Annual: the mean of the 12 monthly anomalies, complete years only.
- It is a model estimate (PRISM interpolates station data), not a thermometer reading, and the newest days are provisional (PRISM revises about six months).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import river_normal as rn
from .watershed_precip import DAILY_START_YEAR, SUB, cell_weights

log = logging.getLogger("nmwater.derived.statewide_temperature")

BASELINE = (1991, 2020)
MIN_YEARS = 20
WINDOWS = (7, 30, 90, 180, 365)


def nm_polygon(grids: Path):
    import geopandas as gpd

    counties = gpd.read_file(grids / "tiger" / "county.gpkg").to_crs(4326)
    return counties[counties["STATEFP"] == "35"].union_all()


def _load(path: Path):
    import xarray as xr

    from ..core.grids import NC_LOCK

    with NC_LOCK, xr.open_dataset(path) as ds:
        da = ds["tmean"].sortby("lat").load()
    return pd.DatetimeIndex(da["time"].values), da["lat"].values, da["lon"].values, da.values.astype(np.float64)


def state_weights(poly, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Weight of each cell (row-major, ascending latitude): share of the cell inside New Mexico times cos(latitude)."""
    import geopandas as gpd

    W, _ = cell_weights(gpd.GeoSeries([poly], crs=4326).to_frame("geometry"), lat, lon, SUB)
    return W[0] * np.repeat(np.cos(np.radians(lat)), len(lon))


def weighted_mean(a: np.ndarray, w: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(mean over valid cells, share of the state's weight that had data) per time step, for a: (T, ny, nx)."""
    T = a.shape[0]
    valid = np.isfinite(a).reshape(T, -1)
    x = np.where(valid, a.reshape(T, -1), 0.0)
    num, den = x @ w, valid.astype(np.float64) @ w
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan), den / w.sum()


def series(grids: Path, poly=None) -> tuple[pd.Series, pd.Series, float]:
    """(daily mean temperature degC from 1981, monthly mean degC from 1895, share of the state with data) over New Mexico."""
    poly = poly if poly is not None else nm_polygon(grids)
    pr = grids / "prism"
    w, cover = None, 0.0
    out = {}
    for kind in ("daily", "monthly"):
        parts = []
        for f in sorted(pr.glob(f"tmean_{kind}_*.nc")):
            t, lat, lon, a = _load(f)
            if w is None:
                w = state_weights(poly, lat, lon)
            m, c = weighted_mean(a, w)
            cover = float(np.nanmax([cover, c.max()]))
            parts.append(pd.Series(m, index=t))
        s = pd.concat(parts).sort_index()
        out[kind] = s[~s.index.duplicated(keep="last")].dropna()
    monthly = out["monthly"]
    return out["daily"], monthly, cover


def window_stats(daily: pd.Series, window: int, baseline: tuple[int, int] = BASELINE) -> dict | None:
    """The last `window` days against the same calendar days in each baseline year: mean, normal, anomaly, percentile, class."""
    if len(daily) < window:
        return None
    last = daily.index.max()
    now = daily.iloc[-window:]
    mean = float(now.mean())
    ref = []
    for y in range(baseline[0], baseline[1] + 1):
        day = min(last.day, pd.Timestamp(year=y, month=last.month, day=1).days_in_month)
        end = pd.Timestamp(year=y, month=last.month, day=day)
        w = daily[end - pd.Timedelta(days=window - 1):end]
        if len(w) == window:
            ref.append(float(w.mean()))
    out = {"days": window, "window_start": (last - pd.Timedelta(days=window - 1)).date().isoformat(), "window_end": last.date().isoformat(), "mean_c": mean,
           "normal_c": None, "anomaly_c": None, "percentile": None, "class": None, "years": len(ref)}
    if len(ref) < MIN_YEARS:
        return out
    r = np.array(ref)
    pct = rn.percentile_rank(r, mean)
    out.update(normal_c=float(r.mean()), anomaly_c=float(mean - r.mean()), percentile=float(pct), **{"class": rn.classify(pct)})
    return out


def monthly_anomaly(monthly: pd.Series, baseline: tuple[int, int] = BASELINE) -> pd.DataFrame:
    """Per month: mean temperature, the 1991-2020 mean for that calendar month, and the anomaly."""
    base = monthly[(monthly.index.year >= baseline[0]) & (monthly.index.year <= baseline[1])]
    clim = base.groupby(base.index.month).mean()
    out = pd.DataFrame({"tmean_c": monthly})
    out["normal_c"] = clim.reindex(monthly.index.month).to_numpy()
    out["anomaly_c"] = out["tmean_c"] - out["normal_c"]
    return out


def annual_anomaly(m: pd.DataFrame) -> pd.DataFrame:
    """Calendar-year means for complete years: mean of the twelve monthly means and of the monthly anomalies."""
    g = m.groupby(m.index.year)
    n = g["tmean_c"].count()
    y = pd.DataFrame({"tmean_c": g["tmean_c"].mean(), "anomaly_c": g["anomaly_c"].mean()})
    return y[n == 12]


def recent_daily(daily: pd.Series, days: int = 365, baseline: tuple[int, int] = BASELINE, half: int = 7) -> pd.DataFrame:
    """The last `days` daily means with the baseline mean for the same calendar day (mean over the baseline years of the days within +-`half` of it)."""
    base = daily[(daily.index.year >= baseline[0]) & (daily.index.year <= baseline[1])]
    key = base.index.month * 100 + base.index.day
    clim = base.groupby(key).mean()                                      # 366 calendar days
    keys = np.array(clim.index)
    order = np.argsort([pd.Timestamp(2000, k // 100, k % 100).dayofyear for k in keys])
    v = clim.to_numpy()[order]
    pad = np.concatenate([v[-half:], v, v[:half]])
    sm = np.convolve(pad, np.ones(2 * half + 1) / (2 * half + 1), mode="valid")
    smooth = pd.Series(sm, index=keys[order])
    tail = daily.iloc[-days:]
    return pd.DataFrame({"tmean_c": tail, "normal_c": smooth.reindex(tail.index.month * 100 + tail.index.day).to_numpy()})


def crosscheck(grids: Path, poly, monthly: pd.Series, since: int = 1991) -> dict | None:
    """PRISM against NOAA nClimGrid (an independent gridded product from different interpolation) for the monthly statewide mean, same weights."""
    import xarray as xr

    from ..core.grids import NC_LOCK

    files = sorted((grids / "nclimgrid").glob("nclimgrid_*.nc"))
    w, shape, rows = None, None, {}
    for f in files:
        ym = f.stem.split("_")[1]
        if int(ym[:4]) < since:
            continue
        with NC_LOCK, xr.open_dataset(f) as ds:
            da = ds["tavg"].sortby("lat").load()
        if w is None:
            w, shape = state_weights(poly, da["lat"].values, da["lon"].values), (len(da["lat"]), len(da["lon"]))
        if (len(da["lat"]), len(da["lon"])) != shape:
            continue
        m, _ = weighted_mean(da.values.astype(np.float64), w)
        rows[pd.Timestamp(f"{ym}01")] = float(np.nanmean(m))
    if not rows:
        return None
    j = pd.DataFrame({"prism": monthly, "other": pd.Series(rows)}).dropna()
    if len(j) < 120:
        return None
    d = j["prism"] - j["other"]
    return {"source": "NOAA nClimGrid (tavg)", "months": int(len(j)), "first": j.index[0].date().isoformat(), "last": j.index[-1].date().isoformat(),
            "bias_c": float(d.mean()), "mean_abs_diff_c": float(d.abs().mean()), "max_abs_diff_c": float(d.abs().max()), "correlation": float(j["prism"].corr(j["other"]))}
