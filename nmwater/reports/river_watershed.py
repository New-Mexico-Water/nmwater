"""Watershed conditions for each river segment: precipitation, temperature, snowpack, drought.

A segment is a HUC8 watershed, so conditions are averaged over the HUC8 polygon (WBD):
- precipitation and mean temperature: PRISM monthly 4 km grids, 1895 on;
- snow-water equivalent: SNODAS 1 km daily grids, 2003 on, sampled once a week (Mondays), Oct-Jun;
- drought: the US Drought Monitor's Drought Severity and Coverage Index (DSCI, 0 = no drought, 500 =
  all of the area in exceptional drought) for the HUC4 basin that contains the segment. The Drought
  Monitor does not publish HUC8 statistics in the archive, so the basin value is shown and labelled.

Zonal means are expensive, so they are cached in data/parquet/reference/source=river_reports/
huc8_climate.parquet (huc8, variable, date, value) and only new months or weeks are computed on later
runs. Grid cells are weighted equally (the grids are regular in latitude and longitude; over one HUC8
the area difference between cells is well under 1%).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("nmwater.reports.river_watershed")

M_TO_IN = 39.3700787
MM_TO_IN = 0.0393700787


def huc8_polygons(wbd_dir: Path, codes: list[str]):
    import geopandas as gpd

    frames = []
    for gp in sorted(wbd_dir.glob("WBD_*_HU2_GPKG.gpkg")):
        hu2 = gp.name.split("_")[1]
        want = [c for c in codes if c.startswith(hu2)]
        if not want:
            continue
        g = gpd.read_file(gp, layer="WBDHU8", columns=["huc8", "name"],
                          where="huc8 IN (" + ",".join(f"'{c}'" for c in want) + ")")
        frames.append(g)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True).to_crs(4326)


def _masks(polys, lat: np.ndarray, lon: np.ndarray) -> dict[str, np.ndarray]:
    """Boolean cell masks per HUC8 on a regular lat/lon grid (cell centres)."""
    from rasterio import features, transform

    dy, dx = float(lat[1] - lat[0]), float(lon[1] - lon[0])
    tr = transform.from_origin(float(lon[0]) - dx / 2, float(lat[0]) - dy / 2 if dy < 0 else float(lat[-1]) + dy / 2,
                               abs(dx), abs(dy))
    out = {}
    for code, geom in zip(polys["huc8"], polys.geometry):
        m = features.geometry_mask([geom], out_shape=(len(lat), len(lon)), transform=tr, invert=True, all_touched=False)
        if dy > 0:
            m = m[::-1]
        out[code] = m
    return out


def _cached(path: Path) -> pd.DataFrame:
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame(columns=["huc8", "variable", "date", "value"])


def update_climate(grids: Path, wbd_dir: Path, cache: Path, codes: list[str]) -> pd.DataFrame:
    """Add missing PRISM months and SNODAS weeks for these HUC8s to the cache; return the cache rows for them."""
    import xarray as xr

    from ..core.grids import NC_LOCK

    have = _cached(cache)
    polys = huc8_polygons(wbd_dir, codes)
    if polys is None or polys.empty:
        return have[have["huc8"].isin(codes)]
    codes = list(polys["huc8"])
    new = []

    # PRISM monthly precipitation and mean temperature
    for var, name, conv in (("ppt", "precip_in", MM_TO_IN), ("tmean", "air_temp_c", 1.0)):
        done = have[(have["variable"] == name)]
        for f in sorted((grids / "prism").glob(f"{var}_monthly_*.nc")):
            year = int(f.stem.rsplit("_", 1)[1])
            got = done[pd.to_datetime(done["date"]).dt.year == year].groupby("huc8").size()
            if all(got.get(c, 0) >= 12 for c in codes):
                continue
            with NC_LOCK, xr.open_dataset(f) as ds:
                a = ds[var].load()
            masks = _masks(polys, a["lat"].values, a["lon"].values)
            for code, m in masks.items():
                if not m.any():
                    continue
                vals = a.values[:, m]
                for t, v in zip(a["time"].values, np.nanmean(vals, axis=1)):
                    new.append((code, name, pd.Timestamp(t).date(), float(v) * conv))

    # SNODAS snow-water equivalent, Mondays October-June
    # SNODAS moved its grid slightly in October 2013, so masks are built per grid (shape and corner).
    done = set(pd.to_datetime(have.loc[have["variable"] == "swe_in", "date"]).dt.date)
    mask_cache: dict[tuple, dict] = {}
    for f in sorted((grids / "snodas").glob("snodas_*.nc")):
        d = pd.Timestamp(f.stem.split("_")[1]).date()
        if d.weekday() != 0 or d.month in (7, 8, 9) or d in done:
            continue
        with NC_LOCK, xr.open_dataset(f) as ds:
            a = ds["swe"].isel(time=0).load() if "time" in ds["swe"].dims else ds["swe"].load()
        lat, lon = a["lat"].values, a["lon"].values
        step = np.diff(lat)
        if len(lat) < 2 or step.min() < 0.9 / 120 or step.max() > 1.1 / 120:
            log.warning("snodas %s: irregular grid, skipped (see scripts/repair_snodas_grid.py)", d)
            continue
        key = (len(lat), len(lon), round(float(lat[0]), 5), round(float(lon[0]), 5))
        if key not in mask_cache:
            mask_cache[key] = _masks(polys, lat, lon)
        masks = mask_cache[key]
        for code, m in masks.items():
            if m.any():
                new.append((code, "swe_in", d, float(np.nanmean(a.values[m])) * M_TO_IN))

    if new:
        add = pd.DataFrame(new, columns=["huc8", "variable", "date", "value"])
        have = pd.concat([have, add], ignore_index=True).drop_duplicates(["huc8", "variable", "date"], keep="last")
        cache.parent.mkdir(parents=True, exist_ok=True)
        have.to_parquet(cache, index=False)
        log.info("watershed cache: %d new values", len(add))
    return have[have["huc8"].isin(codes)].reset_index(drop=True)


def drought(con, codes: list[str]) -> pd.DataFrame:
    """DSCI for the HUC4 basin of each HUC8: huc8, huc4, date, dsci."""
    huc4 = sorted({c[:4] for c in codes})
    ids = ",".join(f"'usdm:huc4:{h}'" for h in huc4)
    d = con.sql(f"""SELECT split_part(site_uid, ':', 3) AS huc4, datetime_utc::DATE AS date, value AS dsci
                    FROM observations WHERE site_uid IN ({ids}) AND variable = 'dsci'""").df()
    m = pd.DataFrame({"huc8": codes, "huc4": [c[:4] for c in codes]})
    return m.merge(d, on="huc4")


def monthly_vs_normal(clim: pd.DataFrame, variable: str, baseline: tuple[int, int] = (1991, 2020)) -> pd.DataFrame:
    """Each month's value with that calendar month's baseline median: huc8, date, value, normal, anomaly."""
    c = clim[clim["variable"] == variable].copy()
    c["date"] = pd.to_datetime(c["date"])
    c["m"] = c["date"].dt.month
    base = c[(c["date"].dt.year >= baseline[0]) & (c["date"].dt.year <= baseline[1])]
    norm = base.groupby(["huc8", "m"])["value"].median().rename("normal").reset_index()
    c = c.merge(norm, on=["huc8", "m"], how="left")
    c["anomaly"] = c["value"] - c["normal"]
    return c.drop(columns="m").sort_values(["huc8", "date"]).reset_index(drop=True)
