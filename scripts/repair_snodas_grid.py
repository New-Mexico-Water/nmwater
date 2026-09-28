"""Repair SNODAS NetCDF files written before 2026-09-28 (one-off; safe to re-run).

Those files put each SNODAS product on its own copy of the grid: the products' header corners
differ by about 1e-12 degrees, so xarray outer-joined them into a grid twice as fine in both
directions, with every product's values on alternate rows and columns and NaN between. This groups
coordinates that lie within half a cell of each other (products were offset by up to a few
thousandths of a degree, giving 2x or 3x interleaved grids), merges each group into one cell (at most
one value per cell, checked), sorts latitude ascending, and rewrites the file. Files already on a
regular 1/120-degree grid are left alone.

    uv run python scripts/repair_snodas_grid.py [--workers 6]
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


CELL = 1 / 120        # SNODAS cell size, degrees


def clusters(coord: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Group coordinates within half a cell of each other: (group index per position, group coordinate)."""
    order = np.argsort(coord, kind="stable")
    c = coord[order]
    brk = np.concatenate([[True], np.diff(c) > CELL / 2])
    gid_sorted = np.cumsum(brk) - 1
    gid = np.empty_like(gid_sorted)
    gid[order] = gid_sorted
    centre = np.array([c[gid_sorted == k].mean() for k in range(gid_sorted[-1] + 1)])
    return gid, np.round(centre, 8)


def repair(path: str) -> str:
    import xarray as xr

    from nmwater.core.grids import write_netcdf

    with xr.open_dataset(path) as ds:
        ds = ds.load()
    lat, lon = ds["lat"].values, ds["lon"].values
    regular = (np.all(np.diff(lat) > CELL * 0.9) and np.all(np.diff(lat) < CELL * 1.1)
               and np.all(np.diff(lon) > CELL * 0.9) and np.all(np.diff(lon) < CELL * 1.1))
    if regular:
        return "ok"
    gy, new_lat = clusters(lat)
    gx, new_lon = clusters(lon)
    out = {}
    for v in ds.data_vars:
        a = ds[v].values                                   # (time, lat, lon)
        acc = np.full((a.shape[0], len(new_lat), len(new_lon)), np.nan, dtype=a.dtype)
        cnt = np.zeros((a.shape[0], len(new_lat), len(new_lon)), dtype=np.int16)
        ok = ~np.isnan(a)
        ti, yi, xi = np.nonzero(ok)
        np.add.at(cnt, (ti, gy[yi], gx[xi]), 1)
        if (cnt > 1).any():
            return f"skipped: {v} has two values in one cell"
        acc[ti, gy[yi], gx[xi]] = a[ti, yi, xi]
        out[v] = (("time", "lat", "lon"), acc, ds[v].attrs)
    fixed = xr.Dataset(out, coords={"time": ds["time"].values, "lat": new_lat, "lon": new_lon}, attrs=ds.attrs)
    fixed.attrs["history"] = str(fixed.attrs.get("history", "")) + "; grid repaired (scripts/repair_snodas_grid.py)"
    write_netcdf(fixed, Path(path))
    return "repaired"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    files = sorted(str(p) for p in (ROOT / "data/grids/snodas").glob("snodas_*.nc"))
    counts: dict[str, int] = {}
    with ProcessPoolExecutor(a.workers) as ex:
        futs = {ex.submit(repair, f): f for f in files}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                r = fut.result()
            except Exception as e:           # report and carry on
                r = f"error: {e}"
                print(futs[fut], r, flush=True)
            counts[r.split(":")[0]] = counts.get(r.split(":")[0], 0) + 1
            if i % 500 == 0:
                print(i, "/", len(files), counts, flush=True)
    print("done", counts, flush=True)


if __name__ == "__main__":
    main()
