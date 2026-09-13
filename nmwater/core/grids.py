"""Helpers for gridded datasets: paths, bbox clipping, NetCDF writing, streaming downloads,
and registration of grid files in the `reference/grids` table.

Convention: data/grids/<dataset>/<variable>_<period>.nc, CF-ish NetCDF-4 with zlib compression,
lat/lon (EPSG:4326) or the native projected grid with `spatial_ref` written by rioxarray.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .config import Settings
from .http import Http, request_key
from .ledger import FetchRecord, Ledger, now_iso
from .store import Store

log = logging.getLogger("nmwater.grids")


def grid_path(settings: Settings, dataset: str, variable: str, when: Any) -> Path:
    d = settings.grids_dir / dataset
    d.mkdir(parents=True, exist_ok=True)
    tag = when if isinstance(when, str) else (when.strftime("%Y%m%d") if hasattr(when, "strftime") else str(when))
    return d / f"{variable}_{tag}.nc"


def _coord_names(ds) -> tuple[str | None, str | None]:
    lat = next((c for c in ("lat", "latitude", "y") if c in ds.coords), None)
    lon = next((c for c in ("lon", "longitude", "x") if c in ds.coords), None)
    return lat, lon


def clip_to_bbox(ds, bbox: tuple[float, float, float, float]):
    """Clip an xarray Dataset/DataArray to (west, south, east, north).

    Geographic grids are sliced on lat/lon (either ordering). Projected grids (rioxarray CRS set,
    not EPSG:4326) are clipped with rio.clip_box after transforming the bbox.
    """
    w, s, e, n = bbox
    crs = None
    try:
        crs = ds.rio.crs
    except Exception:  # noqa: BLE001
        crs = None
    lat, lon = _coord_names(ds)
    if crs is not None and not crs.is_geographic:
        import rioxarray  # noqa: F401
        from pyproj import Transformer

        tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        xs, ys = zip(*[tr.transform(x, y) for x, y in ((w, s), (e, s), (e, n), (w, n))])
        return ds.rio.clip_box(minx=min(xs), miny=min(ys), maxx=max(xs), maxy=max(ys))
    if lat is None or lon is None:
        raise ValueError("cannot find lat/lon coordinates to clip")
    lonv = ds[lon].values
    if lonv.max() > 180:  # 0..360 longitudes
        w2, e2 = w % 360, e % 360
        ds = ds.sel({lon: slice(w2, e2)}) if lonv[0] < lonv[-1] else ds.sel({lon: slice(e2, w2)})
    else:
        ds = ds.sel({lon: slice(w, e)}) if lonv[0] < lonv[-1] else ds.sel({lon: slice(e, w)})
    latv = ds[lat].values
    ds = ds.sel({lat: slice(s, n)}) if latv[0] < latv[-1] else ds.sel({lat: slice(n, s)})
    return ds


def write_netcdf(ds, path: Path, attrs: dict[str, Any] | None = None) -> Path:
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    ds = ds.copy()
    ds.attrs.update({"Conventions": "CF-1.8", "history": f"nmwater clip {now_iso()}", **(attrs or {})})
    enc = {}
    for v in ds.data_vars:
        if np.issubdtype(ds[v].dtype, np.number):
            enc[v] = {"zlib": True, "complevel": 4}
    for c in ("time",):
        if c in ds.coords and hasattr(ds[c].values, "dtype") and np.issubdtype(ds[c].values.dtype, np.datetime64):
            enc[c] = {"units": "days since 1900-01-01 00:00:00", "calendar": "standard", "dtype": "float64"}
    tmp = path.with_suffix(".tmp.nc")
    ds.to_netcdf(tmp, engine="netcdf4", encoding=enc)
    tmp.replace(path)
    return path


def register_grid(store: Store, run_id: str, dataset: str, variable: str, resolution: str,
                  time_start: str, time_end: str, path: Path, crs: str = "EPSG:4326",
                  citation: str = "", license: str = "", notes: str = "") -> None:
    row = pd.DataFrame(
        [{
            "dataset": dataset, "variable": variable, "resolution": resolution,
            "time_start": time_start, "time_end": time_end, "path": str(path),
            "bytes": path.stat().st_size if path.exists() else None, "crs": crs,
            "citation": citation, "license": license, "notes": notes, "registered_at": now_iso(),
        }]
    )
    store.append_table(row, "reference", dataset, "grids", run_id)


def grid_done(ledger: Ledger, key: str) -> Path | None:
    """Return the clipped-grid path if this request was completed before and the file exists."""
    rec = ledger.get(key)
    if rec and rec.status == "ok" and rec.raw_path and Path(rec.raw_path).exists():
        return Path(rec.raw_path)
    return None


def stream_download(http: Http, source: str, url: str, dest: Path, headers: dict[str, str] | None = None,
                    auth: Any = None, params: dict[str, Any] | None = None) -> tuple[int, str, int]:
    """Stream a (possibly large) URL to dest. Returns (http_status, sha256, bytes)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    n = 0
    client = http.client(source)
    with http.limiter(source):
        with client.stream("GET", url, headers=headers, auth=auth, params=params) as r:
            status = r.status_code
            if status >= 400:
                body = b"".join(list(r.iter_bytes()))[:300]
                raise RuntimeError(f"{source}: HTTP {status} for {url}: {body.decode('utf-8', 'replace')}")
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    h.update(chunk)
                    n += len(chunk)
    return status, h.hexdigest(), n


def record_grid(ledger: Ledger, run_id: str, source: str, url: str, params: dict[str, Any] | None,
                out_path: Path, sha256: str, nbytes: int, window: tuple[str, str] | None = None,
                variable: str | None = None, status: str = "ok", error: str | None = None,
                key: str | None = None) -> str:
    """Write a ledger row for a streamed/clipped grid download; raw_path points at the clipped file."""
    key = key or request_key("GET", url, params)
    ledger.record(FetchRecord(
        request_key=key, run_id=run_id, source=source, kind="grid", url=url, params=params,
        site_uid=None, variable=variable, window_start=window[0] if window else None,
        window_end=window[1] if window else None, status=status, http_status=200 if status == "ok" else None,
        sha256=sha256, bytes=nbytes, raw_path=str(out_path), content_type="application/x-netcdf",
        fetched_at=now_iso(), n_rows=None, error=error,
    ))
    return key


def gunzip_to(src: Path, dest: Path) -> Path:
    with gzip.open(src, "rb") as f_in, open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)
    return dest


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
