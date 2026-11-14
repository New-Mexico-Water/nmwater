"""Mountain ranges for the snow pages, drawn where snow is usual.

    uv run python -c "from nmwater.core.config import Settings; from nmwater.derived import snow_ranges as sr; sr.build(Settings.load())"

A watershed is a poor stand-in for a mountain range: it takes in valleys and plains, so its snow average is diluted and its shape is not the mountains'. This module finds where
the mountains are from the snow itself and draws each named range as the snowy ground nearest its seed (catalog/snow_ranges.yaml explains the rule and the seeds).

Method
1. Climatology: the mean SNODAS snow water equivalent (inches) over the sampled mid-winter days of each water year in `zone.years`. Only the first grid's shape is used: SNODAS moved
   its grid by about a tenth of a cell in October 2013, which does not matter at 1 km.
2. Zone: a cell belongs to a range when it is inside New Mexico, its mean is at least the range's threshold, it is within the range's radius of one of the range's seeds, and that
   range's seed is the nearest of all ranges' seeds.
3. Polygons: connected patches of a range's cells, those under `min_cells` dropped, smoothed with a small buffer in and out, simplified to about half a pixel on the state map.
The output is a GeoJSON FeatureCollection (id, name, note, cells, model_smooths; geometry as drawn, simplified for the website by the exporter) cached in data/parquet/derived/ and rebuilt when the config or the SNODAS files used change.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import numpy as np
import yaml

log = logging.getLogger("nmwater.derived.snow_ranges")

CONFIG = Path(__file__).resolve().parents[2] / "catalog" / "snow_ranges.yaml"
M_TO_IN = 39.3700787
KM_PER_DEG = 111.0
SMOOTH_CELLS = 0.6                  # the buffer in and out that joins a ragged edge, in cells
SIMPLIFY_DEG = 0.004


def load_config(path: Path = CONFIG) -> dict:
    cfg = yaml.safe_load(Path(path).read_text())
    ids = [r["id"] for r in cfg["ranges"]]
    if len(ids) != len(set(ids)):
        raise ValueError("snow_ranges.yaml: duplicate range ids")
    z = cfg["zone"]
    for r in cfg["ranges"]:
        if not r.get("seeds"):
            raise ValueError(f"snow_ranges.yaml: range {r['id']} has no seeds")
        r.setdefault("threshold_in", z["threshold_in"])
        r.setdefault("radius_km", z["radius_km"])
        r.setdefault("min_cells", z["min_cells"])
    return cfg


def _read_swe(path: Path):
    """(lat descending, lon, swe in inches) of one SNODAS file, or None when it has no SWE."""
    import xarray as xr

    from ..core.grids import NC_LOCK

    with NC_LOCK, xr.open_dataset(path) as ds:
        if "swe" not in ds:
            return None
        lat, lon = ds["lat"].values, ds["lon"].values
        a = ds["swe"]
        a = (a.isel(time=0) if "time" in a.dims else a).load().values.astype(float)
    if lat[0] < lat[-1]:
        lat, a = lat[::-1], a[::-1]
    return lat, lon, a * M_TO_IN


def climatology(snodas_dir: Path, years: tuple[int, int], dates: list[list[int]]):
    """(mean SWE in inches [ny, nx], lat descending, lon, files used) over the sampled days of the water years `years` (inclusive)."""
    acc, lat, lon, shape = [], None, None, None
    for w in range(years[0], years[1] + 1):
        for m, d in dates:
            f = snodas_dir / f"snodas_{w - 1 if m >= 10 else w}{m:02d}{d:02d}.nc"
            if not f.exists():
                continue
            got = _read_swe(f)
            if got is None or (shape is not None and got[2].shape != shape):
                continue
            if shape is None:
                lat, lon, shape = got[0], got[1], got[2].shape
            acc.append(got[2])
    if not acc:
        raise FileNotFoundError(f"no SNODAS files for the sampled dates in {snodas_dir}")
    return np.nanmean(np.stack(acc), axis=0), lat, lon, len(acc)


def label_cells(lat: np.ndarray, lon: np.ndarray, ranges: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """(index of the range whose nearest seed is nearest, that distance in km) for every cell."""
    la, lo = np.meshgrid(lat, lon, indexing="ij")
    cos = np.cos(np.radians(la))
    d = np.stack([np.min([np.hypot((la - a) * KM_PER_DEG, (lo - b) * KM_PER_DEG * cos) for a, b in r["seeds"]], axis=0) for r in ranges])
    return d.argmin(0), d.min(0)


def zone_polygons(mean: np.ndarray, lat: np.ndarray, lon: np.ndarray, in_state: np.ndarray, ranges: list[dict]) -> list[dict]:
    """One feature dict per range that has snowy ground: id, name, note, cells, model_smooths flag, shapely geometry (lon/lat)."""
    from rasterio import features, transform
    from shapely.geometry import shape
    from shapely.ops import unary_union

    dy, dx = abs(float(lat[1] - lat[0])), abs(float(lon[1] - lon[0]))
    tr = transform.from_origin(float(lon[0]) - dx / 2, float(lat[0]) + dy / 2, dx, dy)
    lab, dist = label_cells(lat, lon, ranges)
    out = []
    for i, r in enumerate(ranges):
        m = (lab == i) & in_state & np.isfinite(mean) & (mean >= r["threshold_in"]) & (dist < r["radius_km"])
        parts = [shape(g) for g, v in features.shapes(m.astype("uint8"), mask=m, transform=tr) if v == 1]
        parts = [p for p in parts if p.area / (dx * dy) >= r["min_cells"]]
        if not parts:
            continue
        geom = unary_union(parts).buffer(dx * SMOOTH_CELLS).buffer(-dx * SMOOTH_CELLS).simplify(SIMPLIFY_DEG)
        out.append({"id": r["id"], "name": r["name"], "note": r.get("note", ""), "cells": int(m.sum()), "geometry": geom})
    return out


def _signature(cfg: dict, snodas_dir: Path) -> str:
    z = cfg["zone"]
    files = [snodas_dir / f"snodas_{w - 1 if m >= 10 else w}{m:02d}{d:02d}.nc" for w in range(z["years"][0], z["years"][1] + 1) for m, d in z["dates"]]
    have = [f"{f.name}:{f.stat().st_size}" for f in files if f.exists()]
    return hashlib.sha256(json.dumps([cfg, have], sort_keys=True, default=str).encode()).hexdigest()


def build(settings, rebuild: bool = False, config: Path = CONFIG) -> dict:
    """The ranges as a GeoJSON FeatureCollection (cached; recomputed when the config or the SNODAS files used change)."""
    from shapely.geometry import mapping

    from . import river_geo as rm

    cfg = load_config(config)
    snodas = settings.grids_dir / "snodas"
    out = settings.parquet_dir / "derived" / "snow_ranges.geojson"
    sig = _signature(cfg, snodas)
    if out.exists() and not rebuild:
        cached = json.loads(out.read_text())
        if cached.get("signature") == sig:
            return cached
    from rasterio import features, transform

    mean, lat, lon, n = climatology(snodas, tuple(cfg["zone"]["years"]), cfg["zone"]["dates"])
    nm, _counties, _mains = rm.state_layers(settings.grids_dir)
    dy, dx = abs(float(lat[1] - lat[0])), abs(float(lon[1] - lon[0]))
    tr = transform.from_origin(float(lon[0]) - dx / 2, float(lat[0]) + dy / 2, dx, dy)
    in_state = features.geometry_mask([nm], out_shape=mean.shape, transform=tr, invert=True, all_touched=True)
    smooth = set(cfg["zone"].get("model_smooths", []))
    feats = [{"type": "Feature", "properties": {"id": z["id"], "name": z["name"], "note": z["note"], "cells": z["cells"], "model_smooths": z["id"] in smooth},
              "geometry": mapping(z["geometry"])} for z in zone_polygons(mean, lat, lon, in_state, cfg["ranges"])]
    fc = {"type": "FeatureCollection", "features": feats, "signature": sig, "climatology_files": n}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fc, separators=(",", ":")))
    log.info("snow ranges: %d ranges from %d climatology files", len(feats), n)
    return fc
