"""Statewide snowpack for New Mexico from NOAA NOHRSC SNODAS (1 km daily snow model): data only; nmwater/site/statewide.py publishes it.

Method
- Area: the union of the 33 TIGER counties. SNODAS cells (1/120 degree, about 0.9 km) are in the state when their centre is inside it; each cell counts with its true
  area (R^2 * dlon * (sin lat2 - sin lat1)). Cells with no data are left out of the sums and the share of the state with data is kept.
- Per day: mean snow water equivalent (SWE) over the state in inches of water, the volume of water in the snowpack in acre-feet (sum of SWE x cell area) and the share of the
  state with at least COVER_MM of SWE (snow-covered area).
- Median: for a date, the median over the baseline water years (2004-2025, the first complete SNODAS years) of the days within +-WINDOW_DAYS of the same calendar day; the
  current water year is left out. Percent of median is the day's mean SWE over that median. It is published only when the median is at least MIN_MEDIAN_IN: in summer the
  median is zero and a percent would mean nothing, so there the file says it is out of season and gives the last season's peak instead.
- SNODAS is a model that assimilates satellite, airborne and ground observations; it is an estimate, and it changed grid slightly in October 2013 (masks are built per grid).
- Cross-check: the SNOTEL stations in New Mexico (NRCS), summed against their own medians, the way NRCS reports a basin index.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("nmwater.derived.statewide_snow")

BASELINE_WY = (2004, 2025)             # water years (Oct-Sep, named for the year they end) used for the median
WINDOW_DAYS = 3
MIN_MEDIAN_IN = 0.05                   # statewide mean SWE the median must reach before a percent of median is stated
COVER_MM = 25.0                        # share of the state with at least this much SWE counts as snow-covered
M_TO_IN = 39.3700787
EARTH_KM = 6371.0088
AF_M3 = 1233.48184
MIN_VALID = 0.9                        # a day is kept only when at least this share of the state's area has data
COLS = ["date", "swe_in", "volume_af", "cover_pct", "valid_pct"]


def water_year(d: pd.Timestamp | pd.DatetimeIndex):
    d = pd.DatetimeIndex([d]) if isinstance(d, pd.Timestamp) else d
    return np.where(d.month >= 10, d.year + 1, d.year)


def cell_area_m2(lat: np.ndarray, dlat: float, dlon: float) -> np.ndarray:
    """Area in square metres of a cell centred on each latitude (a length-ny vector), dlat and dlon in degrees."""
    la = np.radians(lat)
    h = np.radians(dlat) / 2
    return (EARTH_KM * 1000) ** 2 * np.radians(abs(dlon)) * np.abs(np.sin(la + h) - np.sin(la - h))


def state_mask(poly, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Boolean (ny, nx) mask of cells whose centre is inside the polygon, in the orientation of the grid as given."""
    import geopandas as gpd

    from .river_watershed import _masks

    return _masks(gpd.GeoDataFrame({"huc8": ["NM"]}, geometry=[poly], crs=4326), lat, lon)["NM"]


def day_stats(swe_m: np.ndarray, mask: np.ndarray, area: np.ndarray) -> tuple[float, float, float, float] | None:
    """(mean SWE in, volume af, snow-covered percent, valid percent) for one day's SWE grid (metres, NaN = no data), the mask and the per-cell area (ny, nx)."""
    inside = mask
    valid = inside & np.isfinite(swe_m)
    tot = float(area[inside].sum())
    ok = float(area[valid].sum())
    if tot <= 0 or ok / tot < MIN_VALID:
        return None
    vol_m3 = float((np.where(valid, swe_m, 0.0) * area).sum())
    cover = float(area[valid & (swe_m * 1000 >= COVER_MM)].sum()) / ok
    return vol_m3 / ok * M_TO_IN, vol_m3 / AF_M3, 100 * cover, 100 * ok / tot


def daily(grids: Path, poly, cache: Path, rebuild: bool = False) -> pd.DataFrame:
    """Statewide daily SNODAS series (date, swe_in, volume_af, cover_pct, valid_pct), read incrementally from data/grids/snodas and kept in `cache`."""
    import xarray as xr

    from ..core.grids import NC_LOCK

    have = pd.DataFrame(columns=COLS) if rebuild or not cache.exists() else pd.read_parquet(cache)
    done = set(pd.to_datetime(have["date"]).dt.date)
    rows, masks = [], {}
    for f in sorted((grids / "snodas").glob("snodas_*.nc")):
        d = pd.Timestamp(f.stem.split("_")[1]).date()
        if d in done:
            continue
        with NC_LOCK, xr.open_dataset(f) as ds:
            if "swe" not in ds:
                log.warning("snodas %s: no swe product, skipped", d)
                continue
            lat, lon = ds["lat"].values, ds["lon"].values
            step = np.diff(lat)
            if len(lat) < 2 or step.min() < 0.9 / 120 or step.max() > 1.1 / 120:
                log.warning("snodas %s: irregular grid, skipped", d)
                continue
            a = ds["swe"].isel(time=0).load() if "time" in ds["swe"].dims else ds["swe"].load()
        key = (len(lat), len(lon), round(float(lat[0]), 5), round(float(lon[0]), 5))
        if key not in masks:
            area = cell_area_m2(lat, abs(float(lat[1] - lat[0])), abs(float(lon[1] - lon[0])))[:, None] * np.ones((1, len(lon)))
            masks[key] = (state_mask(poly, lat, lon), area)
        m, area = masks[key]
        s = day_stats(a.values.astype(np.float64), m, area)
        if s:
            rows.append((pd.Timestamp(d), *s))
    if rows:
        have = pd.concat([have, pd.DataFrame(rows, columns=COLS)], ignore_index=True).drop_duplicates("date", keep="last")
        have["date"] = pd.to_datetime(have["date"])
        have = have.sort_values("date").reset_index(drop=True)
        cache.parent.mkdir(parents=True, exist_ok=True)
        have.to_parquet(cache, index=False)
        log.info("snow: %d new days (%d total)", len(rows), len(have))
    have["date"] = pd.to_datetime(have["date"])
    return have


# ---------------------------------------------------------------------------- comparison with other years
def _key(d: pd.DatetimeIndex) -> np.ndarray:
    return d.month * 100 + d.day


def _wy_order() -> list[int]:
    """Month-day keys (366 of them, Feb 29 included) in water-year order, October first."""
    days = pd.date_range("2003-10-01", "2004-09-30")
    return [int(k) for k in _key(days)]


def band(df: pd.DataFrame, value: str = "swe_in", exclude_wy: int | None = None, wy_range: tuple[int, int] = BASELINE_WY, half: int = WINDOW_DAYS) -> pd.DataFrame:
    """Per calendar day (water-year order, index = month*100+day): p10, p25, p50, p75, p90 and n (years) of `value` over the baseline water years, from the days within
    +-`half` of that calendar day, leaving out `exclude_wy`."""
    d = df.dropna(subset=[value]).copy()
    d["wy"] = water_year(pd.DatetimeIndex(d["date"]))
    d = d[(d["wy"] >= wy_range[0]) & (d["wy"] <= wy_range[1]) & (d["wy"] != exclude_wy)]
    d["key"] = _key(pd.DatetimeIndex(d["date"]))
    wide = d.pivot_table(index="key", columns="wy", values=value, aggfunc="last").reindex(_wy_order())
    A = wide.to_numpy(dtype=float)
    out = []
    for i in range(len(A)):
        s = A[max(0, i - half): i + half + 1].ravel()
        s = s[np.isfinite(s)]
        out.append([*(np.percentile(s, [10, 25, 50, 75, 90]) if len(s) else [np.nan] * 5), int(np.isfinite(A[i]).sum())])
    return pd.DataFrame(out, columns=["p10", "p25", "p50", "p75", "p90", "years"], index=pd.Index(_wy_order(), name="key"))


def percent_of_median(swe_in: float, median_in: float | None) -> float | None:
    """Percent of the median, or None when the median is below MIN_MEDIAN_IN (no snow season, so a percent says nothing)."""
    if median_in is None or not np.isfinite(median_in) or median_in < MIN_MEDIAN_IN:
        return None
    return 100.0 * swe_in / median_in


def peaks(df: pd.DataFrame) -> pd.DataFrame:
    """Per water year: the day of the highest mean SWE, that SWE, the volume, and the share of the state covered that day. Only years with at least 330 days of data."""
    d = df.copy()
    d["wy"] = water_year(pd.DatetimeIndex(d["date"]))
    out = []
    for wy, g in d.groupby("wy"):
        if len(g) < 330:
            continue
        r = g.loc[g["swe_in"].idxmax()]
        out.append((int(wy), r["date"], float(r["swe_in"]), float(r["volume_af"]), float(r["cover_pct"])))
    return pd.DataFrame(out, columns=["wy", "date", "swe_in", "volume_af", "cover_pct"])


def rated_series(df: pd.DataFrame, bnd: pd.DataFrame) -> pd.Series:
    """Percent of median for each day of df against the per-calendar-day median in `bnd` (NaN where it is out of season)."""
    med = bnd["p50"].reindex(_key(pd.DatetimeIndex(df["date"]))).to_numpy()
    sw = df["swe_in"].to_numpy(dtype=float)
    pct = np.where(np.isfinite(med) & (med >= MIN_MEDIAN_IN), 100 * sw / np.where(med > 0, med, np.nan), np.nan)
    return pd.Series(pct, index=df["date"])


def snotel_percent_of_median(db_con, df_dates: pd.DatetimeIndex) -> pd.Series:
    """NRCS-style index for the SNOTEL stations in New Mexico: the sum of the stations' SWE over the sum of their medians for the same calendar day (1991-2020 is not
    long enough for all stations to have a median, so the SNODAS baseline years are used for both). Daily mean of the hourly readings, stations with at least 10 years."""
    q = db_con.sql("""SELECT o.site_uid, (o.datetime_utc AT TIME ZONE 'UTC')::DATE AS d, avg(o.value) AS swe
                      FROM observations o JOIN sites s USING (site_uid)
                      WHERE o.variable = 'swe' AND o.source = 'nrcs' AND s.state = 'NM' AND o.site_uid LIKE '%:SNTL' AND o.value IS NOT NULL AND o.value >= 0
                      GROUP BY 1, 2""").df()
    if q.empty:
        return pd.Series(dtype=float)
    q["d"] = pd.to_datetime(q["d"])
    q["wy"] = water_year(pd.DatetimeIndex(q["d"]))
    q["key"] = _key(pd.DatetimeIndex(q["d"]))
    base = q[(q["wy"] >= BASELINE_WY[0]) & (q["wy"] <= BASELINE_WY[1])]
    wide = base.pivot_table(index=["site_uid", "key"], columns="wy", values="swe", aggfunc="last")
    med = wide.median(axis=1, skipna=True)
    n = wide.notna().sum(axis=1)
    med = med[n >= 10]
    q = q.join(med.rename("med"), on=["site_uid", "key"]).dropna(subset=["med"])
    g = q.groupby("d").agg(swe=("swe", "sum"), med=("med", "sum"), sites=("site_uid", "nunique"))
    g = g[(g["sites"] >= 8) & (g["med"] > 0)]
    return (100 * g["swe"] / g["med"]).reindex(df_dates)


# ---------------------------------------------------------------------------- the published summary
COMPARE_DAYS = (7, 30, 90, 180, 365)


def _row(df: pd.DataFrame, bnd: pd.DataFrame, when: pd.Timestamp) -> dict | None:
    r = df[df["date"] == when]
    if r.empty:
        return None
    r = r.iloc[0]
    med = float(bnd["p50"].get(int(_key(pd.DatetimeIndex([when]))[0]), np.nan))
    pct = percent_of_median(float(r["swe_in"]), med)
    return {"date": when.date().isoformat(), "swe_in": float(r["swe_in"]), "volume_af": float(r["volume_af"]), "cover_pct": float(r["cover_pct"]),
            "median_in": None if not np.isfinite(med) else med, "percent_of_median": pct, "in_season": pct is not None}


def season_wy(df: pd.DataFrame, bnd: pd.DataFrame) -> int:
    """The water year of the most recent day in snow season (median at least MIN_MEDIAN_IN): the season to show now, even in summer or just after October 1."""
    d = df[df["date"].map(lambda t: float(bnd["p50"].get(int(t.month * 100 + t.day), 0.0)) >= MIN_MEDIAN_IN)]
    return int(water_year(d["date"].iloc[-1])[0]) if len(d) else int(water_year(df["date"].iloc[-1])[0])


def summarise(df: pd.DataFrame) -> dict | None:
    """Everything the bundle says about snowpack, from the daily series."""
    if len(df) < 365:
        return None
    last = df["date"].iloc[-1]
    cur = int(water_year(last)[0])
    bnd = band(df, exclude_wy=cur)
    now = _row(df, bnd, last)
    compare = []
    for k in COMPARE_DAYS:
        r = _row(df, bnd, last - pd.Timedelta(days=k))
        if r:
            compare.append({"days": k, **r})
    swy = season_wy(df, bnd)
    bnd_s = band(df, exclude_wy=swy)
    pk = peaks(df)
    this = pk[pk["wy"] == swy]
    if len(this) == 0:                          # the season in progress has fewer than 330 days: take its peak so far
        g = df[water_year(pd.DatetimeIndex(df["date"])) == swy]
        r = g.loc[g["swe_in"].idxmax()]
        this = pd.DataFrame([(swy, r["date"], float(r["swe_in"]), float(r["volume_af"]), float(r["cover_pct"]))], columns=pk.columns)
    complete = swy < cur or not (now and now["in_season"])      # the season is over once the date is out of season
    base_pk = pk[(pk["wy"] >= BASELINE_WY[0]) & (pk["wy"] <= BASELINE_WY[1]) & (pk["wy"] != swy)]
    med_pk = float(base_pk["swe_in"].median()) if len(base_pk) else None
    t = this.iloc[0]
    allpk = pd.concat([base_pk, this]).drop_duplicates("wy")
    rank = int((allpk["swe_in"] > t["swe_in"]).sum()) + 1
    last_season = {"water_year": swy, "complete": bool(complete), "peak_date": t["date"].date().isoformat(), "peak_swe_in": float(t["swe_in"]), "peak_volume_af": float(t["volume_af"]),
                   "peak_cover_pct": float(t["cover_pct"]), "median_peak_in": med_pk, "percent_of_median_peak": None if not med_pk else float(100 * t["swe_in"] / med_pk),
                   "rank": rank, "of": int(len(allpk)), "years": f"{BASELINE_WY[0]}-{BASELINE_WY[1]}"}
    first, end = pd.Timestamp(swy - 1, 10, 1), pd.Timestamp(swy, 9, 30)
    days = pd.date_range(first, end)
    got = df.set_index("date")["swe_in"].reindex(days)
    k = _key(days)
    iso = lambda ix: [d.date().isoformat() for d in ix]
    num = lambda v: [None if not np.isfinite(x) else round(float(x), 4) for x in v]
    season = {"water_year": swy, "dates": iso(days), "swe_in": num(got.where(days <= last).to_numpy()),
              **{q: num(bnd_s[q].reindex(k).to_numpy()) for q in ("p10", "p25", "p50", "p75", "p90")}}
    history = {"season": season, "peaks": {"water_years": [int(x) for x in pk["wy"]], "dates": iso(pk["date"]), "swe_in": num(pk["swe_in"]), "volume_af": num(pk["volume_af"]),
                                           "cover_pct": num(pk["cover_pct"])}}
    return {"as_of": last.date().isoformat(), "now": now, "compare": compare, "last_season": last_season, "history": history, "baseline": f"{BASELINE_WY[0]}-{BASELINE_WY[1]}",
            "valid_pct": float(df["valid_pct"].iloc[-1]), "min_median_in": MIN_MEDIAN_IN}


def crosscheck(df: pd.DataFrame, snotel: pd.Series) -> dict | None:
    """SNODAS statewide percent of median against the SNOTEL index, on Dec-Apr days, daily and as season (Dec-Apr) means."""
    bnd = band(df)
    s = rated_series(df, bnd)
    j = pd.DataFrame({"snodas": s.to_numpy(), "snotel": snotel.reindex(pd.DatetimeIndex(df["date"])).to_numpy()}, index=pd.DatetimeIndex(df["date"])).dropna()
    j = j[j.index.month.isin([12, 1, 2, 3, 4])]
    if len(j) < 365:
        return None
    d = j["snodas"] - j["snotel"]
    sea = j.groupby(water_year(j.index)).mean()
    sea = sea[j.groupby(water_year(j.index)).size() >= 100]
    ds = sea["snodas"] - sea["snotel"]
    return {"source": "NRCS SNOTEL stations in New Mexico (sum of SWE over sum of medians)", "months": "December to April", "days": int(len(j)), "correlation": float(j["snodas"].corr(j["snotel"])),
            "median_abs_diff_points": float(d.abs().median()), "bias_points": float(d.mean()),
            "seasons": int(len(sea)), "season_correlation": float(sea["snodas"].corr(sea["snotel"])) if len(sea) > 2 else None, "season_mean_abs_diff_points": float(ds.abs().mean()) if len(sea) else None}
