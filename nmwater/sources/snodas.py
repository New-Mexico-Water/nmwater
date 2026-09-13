"""SNODAS (NOHRSC via NSIDC G02158): 1 km daily CONUS snow model, 2003-09-30 -> present.

  https://noaadata.apps.nsidc.org/NOAA/G02158/masked/<YYYY>/<MM_Mon>/SNODAS_<YYYYMMDD>.tar
Each tar holds 8 products as gzipped flat binary (.dat) + header (.txt): big-endian int16, no-data
-9999, 'Data units: <unit> / <scale>' in the header. Masked grid: 6935 x 3351 cells at 1/120 deg,
upper-left (-124.7333, 52.8750). Each day is streamed, clipped to the NM bbox, written as one
NetCDF with all products, and the CONUS tar is discarded.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import tarfile
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ..core.grids import grid_done, grid_path, record_grid, register_grid, stream_download, write_netcdf
from ..core.http import request_key
from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.snodas")

BASE = "https://noaadata.apps.nsidc.org/NOAA/G02158/masked"
MONTHS = ["01_Jan", "02_Feb", "03_Mar", "04_Apr", "05_May", "06_Jun", "07_Jul", "08_Aug", "09_Sep",
          "10_Oct", "11_Nov", "12_Dec"]
# product code (chars after 'ssmv1') -> (variable name, description)
PRODUCTS = {
    "1034": ("swe", "Snow water equivalent"),
    "1036": ("snow_depth", "Snow depth"),
    "1038": ("snowpack_temp", "Snowpack average temperature"),
    "1039": ("blowing_snow_sublimation", "Sublimation of blowing snow"),
    "1044": ("snowmelt_runoff", "Snow melt runoff at base of snowpack"),
    "1050": ("snowpack_sublimation", "Sublimation from the snowpack"),
    "1025SlL01": ("precip_liquid", "Liquid precipitation"),
    "1025SlL00": ("precip_solid", "Solid precipitation"),
}
CITATION = ("National Operational Hydrologic Remote Sensing Center. 2004. Snow Data Assimilation System (SNODAS) "
            "Data Products at NSIDC, Version 1. NSIDC. https://doi.org/10.7265/N5TB14TC")


def _parse_header(txt: str) -> dict:
    h = {}
    for line in txt.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            h[k.strip()] = v.strip()
    return h


@register
class SNODAS(Source):
    name = "snodas"
    agency = "NOAA NOHRSC / NSIDC"
    description = "SNODAS 1 km daily SWE, depth, melt, sublimation, precip clipped to NM (2003-)"
    kinds = ("grid",)

    def discover(self) -> pd.DataFrame:
        w, s, e, n = self.scope.bbox_buffered
        return pd.DataFrame([{
            "native_id": "nm_bbox", "name": "SNODAS 1 km masked grid clipped to NM bbox", "site_type": "grid_cell",
            "agency": "NOHRSC", "lat": (s + n) / 2, "lon": (w + e) / 2, "active": True,
            "raw_metadata": json.dumps({"bbox": [w, s, e, n], "products": PRODUCTS}),
        }])

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        start = since or date.fromisoformat(self.opt("start_date", "2003-09-30"))
        end = date.today() - timedelta(days=1)
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        if site_ids:  # allow --site YYYY-MM-DD for testing
            days = [date.fromisoformat(s) for s in site_ids]
        if limit:
            days = days[-limit:] if since else days[:limit]

        def one(d: date) -> int:
            return self._fetch_day(d, refresh, summ)

        res = self.parallel(one, days, desc="days")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(days) - len(res)
        return summ

    def _fetch_day(self, d: date, refresh: bool, summ: FetchSummary) -> int:
        import numpy as np
        import xarray as xr

        url = f"{BASE}/{d.year}/{MONTHS[d.month - 1]}/SNODAS_{d.strftime('%Y%m%d')}.tar"
        key = request_key("GET", url, None)
        out = grid_path(self.settings, "snodas", "snodas", d.strftime("%Y%m%d"))
        if not refresh and grid_done(self.ledger, key) is not None and out.exists():
            summ.n_cached += 1
            return 0
        w, s, e, n = self.scope.bbox_buffered
        with tempfile.TemporaryDirectory(prefix="snodas_") as td:
            tp = Path(td) / "day.tar"
            try:
                status, sha, nb = stream_download(self.http, self.name, url, tp)
            except RuntimeError as ex:
                if "404" in str(ex):
                    record_grid(self.ledger, self.run_id, self.name, url, None, out, "", 0, status="empty",
                                error="404 no file for this day", key=key, window=(d.isoformat(), d.isoformat()))
                    return 0
                raise
            summ.n_requests += 1
            data_vars = {}
            with tarfile.open(tp) as tar:
                members = {m.name: m for m in tar.getmembers()}
                for name, m in members.items():
                    if not name.endswith(".txt.gz"):
                        continue
                    code = re.search(r"ssmv[01](\d{4}(?:SlL0[01])?)", name)
                    if not code or code.group(1) not in PRODUCTS:
                        continue
                    var, desc = PRODUCTS[code.group(1)]
                    hdr = _parse_header(gzip.decompress(tar.extractfile(m).read()).decode("utf-8", "replace"))
                    dat_name = name.replace(".txt.gz", ".dat.gz")
                    if dat_name not in members:
                        continue
                    raw = gzip.decompress(tar.extractfile(members[dat_name]).read())
                    nrow, ncol = int(hdr["Number of rows"]), int(hdr["Number of columns"])
                    arr = np.frombuffer(raw, dtype=">i2").reshape(nrow, ncol).astype("float32")
                    nodata = float(hdr.get("No data value", "-9999"))
                    arr[arr == nodata] = np.nan
                    units = hdr.get("Data units", "")
                    scale = 1.0
                    um = re.match(r"(.+?)\s*/\s*([0-9.]+)", units)
                    if um:
                        units, scale = um.group(1).strip(), float(um.group(2))
                    arr = arr / scale
                    xmin, xmax = float(hdr["Minimum x-axis coordinate"]), float(hdr["Maximum x-axis coordinate"])
                    ymin, ymax = float(hdr["Minimum y-axis coordinate"]), float(hdr["Maximum y-axis coordinate"])
                    dx, dy = float(hdr["X-axis resolution"]), float(hdr["Y-axis resolution"])
                    lons = xmin + dx / 2 + dx * np.arange(ncol)
                    lats = ymax - dy / 2 - dy * np.arange(nrow)
                    ci = np.where((lons >= w) & (lons <= e))[0]
                    ri = np.where((lats >= s) & (lats <= n))[0]
                    sub = arr[ri[0]:ri[-1] + 1, ci[0]:ci[-1] + 1]
                    da = xr.DataArray(sub[np.newaxis, :, :], dims=("time", "lat", "lon"),
                                      coords={"time": [pd.Timestamp(d)], "lat": lats[ri], "lon": lons[ci]},
                                      attrs={"units": units, "long_name": desc, "snodas_product": code.group(1)})
                    data_vars[var] = da
            if not data_vars:
                raise RuntimeError(f"snodas: no products decoded for {d}")
            ds = xr.Dataset(data_vars)
            ds.attrs.update({"title": f"SNODAS masked daily {d} NM clip", "source": CITATION})
            write_netcdf(ds, out)
        record_grid(self.ledger, self.run_id, self.name, url, None, out, sha, nb,
                    window=(d.isoformat(), d.isoformat()), variable="snodas", key=key)
        register_grid(self.store, self.run_id, "snodas", "all", "1km (30 arc-sec) daily", d.isoformat(), d.isoformat(),
                      out, citation=CITATION, license="Public domain", notes=",".join(data_vars))
        return len(data_vars)
