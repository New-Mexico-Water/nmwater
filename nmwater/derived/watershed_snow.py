"""Snow averaged over every watershed (HUC8) and every mountain range, daily, from NOAA's SNODAS 1 km snow model.

    nmwater watershed-snow            # incremental; also run by `nmwater update`
    nmwater watershed-snow --rebuild  # recompute every day

Output (Parquet):
  data/parquet/derived/watershed_snow/year=YYYY/snow.parquet     unit, kind ('watershed' | 'range'), date, swe_in, depth_in
  data/parquet/derived/watershed_snow_units.parquet              unit, kind, name, nm_fraction, grid_fraction, cells
  data/parquet/derived/watershed_snow_manifest.json              method version and a signature of the units, so a changed unit set recomputes every day

Method
- Source: the daily SNODAS grids in data/grids/snodas (one file per day for the day before). `swe` is snow water equivalent, `snow_depth` the depth of the snow, both metres in the
  files, inches here. A file without `swe` or `snow_depth`, or on an irregular grid (see scripts/repair_snodas_grid.py), is skipped and tried again next run.
- Units: every watershed that watershed_precip lists (at least 1% in New Mexico) and every mountain range of nmwater/derived/snow_ranges.py. A range is the snowy ground of its
  mountains, so its average is high; a watershed's includes valleys and plains.
- Mean: the plain average of the 1 km cells whose centres fall in the unit (river_watershed._masks). A watershed that runs past the grid's edge (Colorado, Texas, Arizona) averages
  its covered part only; `grid_fraction` in the units file is the share covered.
- Time: a SNODAS file is the model's state for its day; the date here is that day. SNODAS moved its grid by a tenth of a cell in October 2013, which masks built per grid absorb.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("nmwater.derived.watershed_snow")

M_TO_IN = 39.3700787
METHOD_VERSION = 1


def _paths(parquet_dir: Path) -> tuple[Path, Path, Path]:
    d = parquet_dir / "derived"
    return d / "watershed_snow", d / "watershed_snow_units.parquet", d / "watershed_snow_manifest.json"


def grid_key(lat: np.ndarray, lon: np.ndarray) -> tuple:
    """Grids a tenth of a cell apart (SNODAS before and after October 2013) share masks."""
    return (len(lat), len(lon), round(float(lat.min()) * 100), round(float(lon.min()) * 100))


def regular(lat: np.ndarray) -> bool:
    step = np.abs(np.diff(lat))
    return len(lat) >= 2 and bool(step.min() >= 0.9 / 120 and step.max() <= 1.1 / 120)


def read_day(path: Path):
    """(lat, lon, swe inches, depth inches) in the file's own order, or None when the file lacks a variable or is on an irregular grid."""
    import xarray as xr

    from ..core.grids import NC_LOCK

    with NC_LOCK, xr.open_dataset(path) as ds:
        if "swe" not in ds or "snow_depth" not in ds:
            return None
        lat, lon = ds["lat"].values, ds["lon"].values
        if not regular(lat):
            return None
        out = []
        for v in ("swe", "snow_depth"):
            a = ds[v]
            out.append((a.isel(time=0) if "time" in a.dims else a).load().values.astype(float) * M_TO_IN)
    return lat, lon, out[0], out[1]


def signature(polys) -> str:
    h = hashlib.sha256()
    h.update(str(METHOD_VERSION).encode())
    for u, k, g in zip(polys["unit"], polys["kind"], polys.geometry):
        h.update(f"{u}|{k}|{g.wkb_hex if hasattr(g, 'wkb_hex') else g.wkt}".encode())
    return h.hexdigest()


def unit_polygons(settings):
    """GeoDataFrame (EPSG:4326) of the units: columns unit, kind, name, nm_fraction, geometry."""
    import geopandas as gpd
    from shapely.geometry import shape

    from . import river_watershed as rw
    from . import snow_ranges as sr
    from . import watershed_precip as wp

    _p, ws_pq, _m = wp._paths(settings.parquet_dir)
    ws = pd.read_parquet(ws_pq)
    ws = ws[ws["nm_fraction"] >= wp.MIN_NM_FRACTION].sort_values("huc8")
    polys = rw.huc8_polygons(settings.grids_dir / "wbd", list(ws["huc8"]))
    names = dict(zip(ws["huc8"], ws["name"]))
    frac = dict(zip(ws["huc8"], ws["nm_fraction"]))
    rows = [{"unit": r.huc8, "kind": "watershed", "name": names.get(r.huc8) or r.name, "nm_fraction": float(frac[r.huc8]), "geometry": r.geometry} for r in polys.itertuples()]
    for f in sr.build(settings)["features"]:
        rows.append({"unit": f["properties"]["id"], "kind": "range", "name": f["properties"]["name"], "nm_fraction": 1.0, "geometry": shape(f["geometry"])})
    return gpd.GeoDataFrame(rows, crs=4326)


def compute(snodas_dir: Path, polys, out_dir: Path, units_file: Path, manifest_file: Path, rebuild: bool = False) -> dict:
    """Add every SNODAS day not yet in `out_dir` for the units in `polys` (all days when the units or the method changed)."""
    from .river_watershed import _masks

    units = list(polys["unit"])
    kinds = dict(zip(polys["unit"], polys["kind"]))
    sig = signature(polys)
    old = json.loads(manifest_file.read_text()) if manifest_file.exists() else {}
    if rebuild or old.get("signature") != sig:
        if out_dir.exists():
            for p in out_dir.rglob("*.parquet"):
                p.unlink()
        have: set = set()
    else:
        have = set(pd.to_datetime(pd.read_parquet(out_dir)["date"]).dt.date) if out_dir.exists() and any(out_dir.rglob("*.parquet")) else set()
    mask_cache: dict = {}
    rows, cover = [], {}
    masked = polys.rename(columns={"unit": "huc8"})
    for f in sorted(snodas_dir.glob("snodas_*.nc")):
        day = pd.Timestamp(f.stem.split("_")[1]).date()
        if day in have:
            continue
        got = read_day(f)
        if got is None:
            continue
        lat, lon, swe, dep = got
        key = grid_key(lat, lon)
        if key not in mask_cache:
            mask_cache[key] = _masks(masked, lat, lon)
            dx, dy = abs(float(lon[1] - lon[0])), abs(float(lat[1] - lat[0]))
            for u, g in zip(polys["unit"], polys.geometry):
                cover[u] = (int(mask_cache[key][u].sum()), min(1.0, float(mask_cache[key][u].sum()) * dx * dy / g.area) if g.area > 0 else 0.0)
        masks = mask_cache[key]
        for u in units:
            m = masks[u]
            if m.any():
                rows.append((u, kinds[u], day, float(np.nanmean(swe[m])), float(np.nanmean(dep[m]))))
    if rows:
        add = pd.DataFrame(rows, columns=["unit", "kind", "date", "swe_in", "depth_in"])
        add["date"] = pd.to_datetime(add["date"])
        for y, g in add.groupby(add["date"].dt.year):
            p = out_dir / f"year={y}" / "snow.parquet"
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists():
                g = pd.concat([pd.read_parquet(p), g], ignore_index=True).drop_duplicates(["unit", "date"], keep="last")
            g.sort_values(["unit", "date"]).to_parquet(p, index=False)
    info = pd.DataFrame({"unit": polys["unit"], "kind": polys["kind"], "name": polys["name"], "nm_fraction": polys["nm_fraction"],
                         "cells": [cover.get(u, (None, None))[0] for u in polys["unit"]], "grid_fraction": [cover.get(u, (None, None))[1] for u in polys["unit"]]})
    if cover or not units_file.exists():
        units_file.parent.mkdir(parents=True, exist_ok=True)
        if not cover and units_file.exists():
            info = pd.read_parquet(units_file)
        info.to_parquet(units_file, index=False)
    manifest_file.write_text(json.dumps({"method_version": METHOD_VERSION, "signature": sig}))
    return {"units": len(units), "days_added": len({r[2] for r in rows}), "rows": len(rows)}


def update(settings, rebuild: bool = False) -> dict:
    out_dir, units_file, manifest_file = _paths(settings.parquet_dir)
    r = compute(settings.grids_dir / "snodas", unit_polygons(settings), out_dir, units_file, manifest_file, rebuild)
    log.info("watershed snow: %d units, %d days added", r["units"], r["days_added"])
    return r
