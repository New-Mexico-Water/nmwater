"""Precipitation averaged over every watershed (HUC8) in New Mexico, daily where PRISM has daily grids
and monthly before that.

    nmwater watershed-precip            # incremental; also run by `nmwater update`
    nmwater watershed-precip --rebuild  # recompute everything

Output (Parquet, then the catalog views `watershed_precip` and `watersheds`):
  data/parquet/derived/watershed_precip/year=YYYY/precip.parquet
      huc8, date, interval ('daily' | 'monthly'), precip_in, period_start_utc, period_end_utc, source
  data/parquet/derived/watersheds.parquet
      huc8, name, states, area_km2, nm_fraction, grid_fraction, has_precip, in_river_reports
  data/parquet/derived/watershed_precip_manifest.json   which grid files each year was computed from

Method
- Source: PRISM Climate Group AN81 4 km precipitation grids already in data/grids/prism (clipped to the NM
  bounding box). Daily 1981-01-01 on (ppt_daily_YYYY.nc); monthly 1895-1980 (ppt_monthly_YYYY.nc). Months
  from 1981 on are not stored: use the daily rows. Values are converted from mm to inches.
- Watersheds: every WBD HUC8 with at least MIN_NM_FRACTION of its area in New Mexico, plus the HUC8s the
  river reports use (river gauges in CO, TX and AZ).
- Zonal mean: each grid cell is weighted by the fraction of it inside the watershed (a polygon is
  rasterised at SUB x SUB sub-cells per cell), so a small watershed is not decided by which cell centres
  fall inside it. Cells without data (PRISM covers the contiguous US only: Mexico and ocean are empty) are
  left out of both the sum and the weights.
- grid_fraction: share of the watershed's area that has PRISM data in the NM-clipped grid. Below 1 the
  value is the average over the covered part only. Watersheds that extend into Colorado, Texas or Arizona
  beyond the grid edge have a grid_fraction well below 1; filter on it. A watershed with less than
  MIN_GRID_FRACTION of its area in the grid (for example the Rio Grande headwaters in Colorado) gets no
  precipitation rows at all (has_precip = false in `watersheds`): an average over a sliver of it would
  mislead. Covering them needs PRISM re-clipped to a larger area.
- Time: a PRISM day is 12:00 UTC to 12:00 UTC and carries the date it ENDS on (PRISM_datasets.pdf, Feb
  2026): the "June 5" grid is rain from about 5-6 AM Mountain on June 4 to the same time on June 5.
  period_start_utc and period_end_utc say this for every row. Monthly rows are nominal calendar months
  (00:00Z on the 1st to 00:00Z on the next 1st).
- Revisions: PRISM re-maps about the latest six months as stations report. The manifest records each
  year's grid file (size and modification time) and a year is recomputed whenever its file changes, so
  an update after `nmwater update` picks revisions up.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("nmwater.derived.watershed_precip")

MM_TO_IN = 0.0393700787
DAILY_START_YEAR = 1981            # PRISM daily grids start 1981-01-01; before that, monthly
MIN_NM_FRACTION = 0.01
SUB = 8                            # sub-cells per grid cell side when rasterising a watershed
MIN_GRID_FRACTION = 0.5            # no precipitation rows for a watershed with less than this much of it in the grid
METHOD_VERSION = 1                 # bump to force a full recompute when the method changes


def _paths(parquet_dir: Path) -> tuple[Path, Path, Path]:
    d = parquet_dir / "derived"
    return d / "watershed_precip", d / "watersheds.parquet", d / "watershed_precip_manifest.json"


# ---------------------------------------------------------------------------- watersheds
def select_watersheds(grids: Path, river_codes: list[str] | None = None):
    """GeoDataFrame of HUC8s (huc8, name, states, area_km2, nm_fraction, in_river_reports, geometry)."""
    import geopandas as gpd

    counties = gpd.read_file(grids / "tiger" / "county.gpkg").to_crs(4326)
    nm = counties[counties["STATEFP"] == "35"].union_all()
    frames = []
    for gp in sorted((grids / "wbd").glob("WBD_*_HU2_GPKG.gpkg")):
        g = gpd.read_file(gp, layer="WBDHU8", columns=["huc8", "name", "states", "areasqkm"], bbox=nm.bounds).to_crs(4326)
        frames.append(g)
    allp = pd.concat(frames, ignore_index=True)
    allp = allp[allp.intersects(nm)].copy()
    allp["nm_fraction"] = [g.intersection(nm).area / g.area for g in allp.geometry]
    river = set(river_codes or [])
    allp["in_river_reports"] = allp["huc8"].isin(river)
    keep = allp[(allp["nm_fraction"] >= MIN_NM_FRACTION) | allp["in_river_reports"]]
    extra = sorted(river - set(keep["huc8"]))
    if extra:                                    # river-report HUC8s that do not touch NM (CO headwaters, ...)
        more = []
        for gp in sorted((grids / "wbd").glob("WBD_*_HU2_GPKG.gpkg")):
            want = [c for c in extra if c.startswith(gp.name.split("_")[1])]
            if want:
                more.append(gpd.read_file(gp, layer="WBDHU8", columns=["huc8", "name", "states", "areasqkm"],
                                          where="huc8 IN (" + ",".join(f"'{c}'" for c in want) + ")").to_crs(4326))
        if more:
            m = pd.concat(more, ignore_index=True)
            m["nm_fraction"] = [g.intersection(nm).area / g.area for g in m.geometry]
            m["in_river_reports"] = True
            keep = pd.concat([keep, m], ignore_index=True)
    keep = keep.rename(columns={"areasqkm": "area_km2"}).sort_values("huc8").reset_index(drop=True)
    return keep[["huc8", "name", "states", "area_km2", "nm_fraction", "in_river_reports", "geometry"]]


def river_report_codes(db: Path) -> list[str]:
    """HUC8s that have river gauges (the segments of the river pages)."""
    if not db.exists():
        return []
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    try:
        return [c for (c,) in con.sql("SELECT DISTINCT huc8 FROM river_segments WHERE huc8 IS NOT NULL "
                                      "AND river_method = 'snap' AND site_type = 'stream'").fetchall()]
    except Exception as e:                      # catalog not built yet
        log.info("river segments unavailable (%s); using New Mexico watersheds only", e)
        return []
    finally:
        con.close()


# ---------------------------------------------------------------------------- weights
def cell_weights(polys, lat: np.ndarray, lon: np.ndarray, sub: int = SUB) -> tuple[np.ndarray, np.ndarray]:
    """(W, poly_cells): W[i, c] is the fraction of cell c (row-major over an ascending-lat grid) that lies in
    polygon i; poly_cells[i] is the polygon's area in cell areas, including the part outside the grid."""
    from rasterio import features, transform

    ny, nx = len(lat), len(lon)
    dy, dx = float(lat[1] - lat[0]), float(lon[1] - lon[0])
    if dy <= 0:
        raise ValueError("cell_weights needs ascending latitudes")
    tr = transform.from_origin(float(lon[0]) - dx / 2, float(lat[-1]) + dy / 2, dx / sub, dy / sub)
    W = np.zeros((len(polys), ny * nx))
    cells = np.zeros(len(polys))
    for i, geom in enumerate(polys.geometry):
        m = features.geometry_mask([geom], out_shape=(ny * sub, nx * sub), transform=tr, invert=True, all_touched=False)
        frac = m.reshape(ny, sub, nx, sub).mean(axis=(1, 3))[::-1]      # rows run north to south: flip to ascending lat
        W[i] = frac.ravel()
        cells[i] = geom.area / (dx * dy)
    return W, cells


def zonal_mean(a: np.ndarray, W: np.ndarray) -> np.ndarray:
    """a: (T, ny, nx) with NaN where there is no data. Returns (T, n_polygons), the weighted mean over valid cells."""
    T = a.shape[0]
    valid = np.isfinite(a).reshape(T, -1)
    x = np.where(valid, a.reshape(T, -1), 0.0)
    num = x @ W.T
    den = valid.astype(np.float64) @ W.T
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan)


def _load(path: Path, var: str = "ppt"):
    """(times, lat ascending, lon, data mm) of one PRISM file."""
    import xarray as xr

    from ..core.grids import NC_LOCK

    with NC_LOCK, xr.open_dataset(path) as ds:
        da = ds[var].sortby("lat").load()
    return pd.DatetimeIndex(da["time"].values), da["lat"].values, da["lon"].values, da.values.astype(np.float64)


# ---------------------------------------------------------------------------- rows
def _rows(codes: list[str], times: pd.DatetimeIndex, vals: np.ndarray, interval: str) -> pd.DataFrame:
    n_t, n_h = vals.shape
    d = pd.DatetimeIndex(times).normalize()
    if interval == "daily":
        end = (d + pd.Timedelta(hours=12)).tz_localize("UTC")
        start = end - pd.Timedelta(days=1)
        src = "prism_daily"
    else:
        start = d.tz_localize("UTC")
        end = (d + pd.offsets.MonthBegin(1)).tz_localize("UTC")
        src = "prism_monthly"
    out = pd.DataFrame({
        "huc8": np.tile(np.array(codes), n_t),
        "date": np.repeat(d.date, n_h),
        "interval": interval,
        "precip_in": (vals * MM_TO_IN).ravel().round(4),
        "period_start_utc": np.repeat(start.values, n_h),
        "period_end_utc": np.repeat(end.values, n_h),
        "source": src,
    })
    return out[np.isfinite(out["precip_in"])].reset_index(drop=True)


def _stamp(f: Path) -> dict:
    s = f.stat()
    return {"file": f.name, "size": s.st_size, "mtime_ns": s.st_mtime_ns}


def update(settings, db: Path | None = None, rebuild: bool = False) -> dict:
    """Compute what is missing or whose grid file changed. Returns {"years": n, "rows": n, "watersheds": n}."""
    out_dir, ws_path, man_path = _paths(settings.parquet_dir)
    grids = settings.grids_dir
    polys = select_watersheds(grids, river_report_codes(db or settings.duckdb_path))
    codes = list(polys["huc8"])
    man = json.loads(man_path.read_text()) if man_path.exists() and not rebuild else {}
    if man.get("method") != METHOD_VERSION or man.get("codes") != codes:
        man, rebuild = {}, True                                   # the set of watersheds or the method changed
    done = man.get("years", {})

    files = {}
    for f in sorted((grids / "prism").glob("ppt_daily_*.nc")):
        y = int(f.stem.rsplit("_", 1)[1])
        if y >= DAILY_START_YEAR:
            files[y] = f
    for f in sorted((grids / "prism").glob("ppt_monthly_*.nc")):
        y = int(f.stem.rsplit("_", 1)[1])
        if y < DAILY_START_YEAR:
            files[y] = f
    todo = [y for y, f in files.items() if rebuild or done.get(str(y)) != _stamp(f)]
    log.info("watershed precipitation: %d watersheds, %d of %d years to compute", len(codes), len(todo), len(files))

    W = cells = emit = None
    grid_key = None
    n_rows = 0
    for y in todo:
        f = files[y]
        times, lat, lon, a = _load(f)
        key = (len(lat), len(lon), round(float(lat[0]), 4), round(float(lon[0]), 4))
        if key != grid_key:                                       # all PRISM clips share one grid; rebuilt if that changes
            W, cells = cell_weights(polys, lat, lon)
            grid_key = key
            valid0 = np.isfinite(a[0]).ravel().astype(float)
            polys["grid_fraction"] = np.round((W @ valid0) / cells, 4)
            polys["has_precip"] = polys["grid_fraction"] >= MIN_GRID_FRACTION
            emit = polys["has_precip"].to_numpy()
        interval = "daily" if y >= DAILY_START_YEAR else "monthly"
        z = zonal_mean(a, W)[:, emit]
        df = _rows([c for c, e in zip(codes, emit) if e], times, z, interval)
        yd = out_dir / f"year={y}"
        yd.mkdir(parents=True, exist_ok=True)
        tmp = yd / "precip.parquet.tmp"
        df.to_parquet(tmp, index=False, compression="zstd")
        tmp.replace(yd / "precip.parquet")
        n_rows += len(df)
        done[str(y)] = _stamp(f)
        man_path.parent.mkdir(parents=True, exist_ok=True)       # record progress as we go: an interrupted run resumes
        man_path.write_text(json.dumps({"method": METHOD_VERSION, "codes": codes, "years": done}, indent=1))
    if rebuild or todo or not ws_path.exists():
        if "grid_fraction" not in polys.columns:
            _, lat, lon, first = _load(next(iter(files.values())))
            W, cells = cell_weights(polys, lat, lon)
            polys["grid_fraction"] = np.round((W @ np.isfinite(first[0]).ravel().astype(float)) / cells, 4)
            polys["has_precip"] = polys["grid_fraction"] >= MIN_GRID_FRACTION
        keep = polys.drop(columns="geometry").copy()
        keep["nm_fraction"] = keep["nm_fraction"].round(4)
        keep[["huc8", "name", "states", "area_km2", "nm_fraction", "grid_fraction", "has_precip", "in_river_reports"]
             ].to_parquet(ws_path, index=False)
    with_precip = int(polys["has_precip"].sum()) if "has_precip" in polys else int(pd.read_parquet(ws_path)["has_precip"].sum())
    return {"years": len(todo), "rows": n_rows, "watersheds": len(codes), "with_precip": with_precip}
