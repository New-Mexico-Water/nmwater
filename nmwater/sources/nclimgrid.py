"""NOAA nClimGrid-Daily: 1/24 deg daily CONUS tmax/tmin/tavg/prcp, 1951-present, one NetCDF per month.

  https://www.ncei.noaa.gov/data/nclimgrid-daily/access/grids/<YYYY>/ncdd-<YYYYMM>-grd-scaled.nc  (~64 MB)
Streamed to temp, clipped to NM bbox, written to data/grids/nclimgrid/nclimgrid_<YYYYMM>.nc.
"""

from __future__ import annotations

import json
import logging
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

from ..core.grids import (
    clip_to_bbox,
    grid_done,
    grid_path,
    record_grid,
    register_grid,
    stream_download,
    write_netcdf,
)
from ..core.http import request_key
from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.nclimgrid")

BASE = "https://www.ncei.noaa.gov/data/nclimgrid-daily/access/grids"
CITATION = ("Durre, I., et al. (2022). NOAA's nClimGrid-Daily Version 1 - Daily gridded temperature and "
            "precipitation for the CONUS. NOAA NCEI. https://doi.org/10.25921/c4gt-r169")


@register
class NClimGrid(Source):
    name = "nclimgrid"
    agency = "NOAA NCEI"
    description = "nClimGrid-Daily 5 km daily tmax/tmin/tavg/prcp clipped to NM (1951-)"
    kinds = ("grid",)

    def discover(self) -> pd.DataFrame:
        w, s, e, n = self.scope.bbox_buffered
        return pd.DataFrame([{
            "native_id": "nm_bbox", "name": "nClimGrid-Daily grid clipped to NM bbox", "site_type": "grid_cell",
            "agency": "NOAA NCEI", "lat": (s + n) / 2, "lon": (w + e) / 2, "active": True,
            "raw_metadata": json.dumps({"bbox": [w, s, e, n]}),
        }])

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        start = since or date(int(self.opt("start_year", 1951)), 1, 1)
        today = date.today()
        months = []
        y, m = start.year, start.month
        while (y, m) <= (today.year, today.month):
            months.append((y, m))
            m += 1
            if m > 12:
                y, m = y + 1, 1
        if site_ids:
            months = [(int(s[:4]), int(s[4:6])) for s in site_ids]
        if limit:
            months = months[-limit:] if since else months[:limit]

        def one(ym) -> int:
            import xarray as xr

            y, m = ym
            url = f"{BASE}/{y}/ncdd-{y}{m:02d}-grd-scaled.nc"
            key = request_key("GET", url, None)
            out = grid_path(self.settings, "nclimgrid", "nclimgrid", f"{y}{m:02d}")
            # the latest 2 months are 'prelim' and get rescaled; always refresh those
            recent = (today.year - y) * 12 + (today.month - m) <= 2
            if not (refresh or recent) and grid_done(self.ledger, key) is not None and out.exists():
                summ.n_cached += 1
                return 0
            with tempfile.TemporaryDirectory(prefix="nclimgrid_") as td:
                tp = Path(td) / "m.nc"
                status, sha, nb = stream_download(self.http, self.name, url, tp)
                summ.n_requests += 1
                with xr.open_dataset(tp) as ds:
                    sub = clip_to_bbox(ds, self.scope.bbox_buffered).load()
                sub.attrs.update({"title": f"nClimGrid-Daily {y}-{m:02d} NM clip", "source": CITATION})
                write_netcdf(sub, out)
                nt = int(sub.sizes.get("time", 0))
                t0, t1 = str(sub["time"].values[0])[:10], str(sub["time"].values[-1])[:10]
            record_grid(self.ledger, self.run_id, self.name, url, None, out, sha, nb, window=(t0, t1),
                        variable="nclimgrid", key=key)
            register_grid(self.store, self.run_id, "nclimgrid", "tmax,tmin,tavg,prcp", "1/24 deg daily", t0, t1, out,
                          citation=CITATION, license="Public domain", notes="scaled product")
            return nt

        res = self.parallel(one, months, desc="months")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(months) - len(res)
        return summ
