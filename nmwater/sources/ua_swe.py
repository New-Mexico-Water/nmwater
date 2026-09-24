"""University of Arizona 4 km daily SWE and snow depth (NSIDC-0719), water years 1982-2023.

  https://daacdata.apps.nsidc.org/pub/DATASETS/nsidc0719_SWE_Snow_Depth_v1/4km_SWE_Depth_WY<YYYY>_v01.nc
Requires NASA Earthdata login (EARTHDATA_USERNAME / EARTHDATA_PASSWORD). Yearly CONUS files (~GBs) are
streamed with URS basic-auth redirect handling, clipped to NM, and discarded.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

from ..core.grids import NC_LOCK, clip_to_bbox, grid_done, grid_path, record_grid, register_grid, write_netcdf
from ..core.http import request_key
from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.ua_swe")

BASE = "https://daacdata.apps.nsidc.org/pub/DATASETS/nsidc0719_SWE_Snow_Depth_v1"
CITATION = ("Broxton, P., X. Zeng, and N. Dawson. 2019. Daily 4 km Gridded SWE and Snow Depth from Assimilated "
            "In-Situ and Modeled Data over the Conterminous US, Version 1. NSIDC. https://doi.org/10.5067/0GGPB220EX6A")


@register
class UASWE(Source):
    name = "ua_swe"
    agency = "University of Arizona / NSIDC"
    description = "UA 4 km daily SWE + snow depth (WY1982-2023) clipped to NM; needs Earthdata login"
    kinds = ("grid",)
    requires_tokens = ("EARTHDATA_USERNAME", "EARTHDATA_PASSWORD")

    def _auth_client(self) -> httpx.Client:
        u, p = self.settings.tokens["EARTHDATA_USERNAME"], self.settings.tokens["EARTHDATA_PASSWORD"]
        # URS flow: 401 -> redirect to urs.earthdata.nasa.gov with Basic auth -> redirect back with cookie
        return httpx.Client(auth=(u, p), follow_redirects=True, timeout=httpx.Timeout(600, connect=30),
                            headers={"User-Agent": self.http.user_agent})

    def _list(self) -> list[str]:
        with self._auth_client() as c:
            r = c.get(BASE + "/")
            r.raise_for_status()
            return sorted(set(re.findall(r'href="(4km_SWE_Depth_WY\d{4}_v01\.nc)"', r.text)))

    def discover(self) -> pd.DataFrame:
        w, s, e, n = self.scope.bbox_buffered
        return pd.DataFrame([{
            "native_id": "nm_bbox", "name": "UA SWE 4 km grid clipped to NM bbox", "site_type": "grid_cell",
            "agency": "University of Arizona", "lat": (s + n) / 2, "lon": (w + e) / 2, "active": True,
            "raw_metadata": json.dumps({"bbox": [w, s, e, n]}),
        }])

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        files = self._list()
        summ.n_requests += 1
        if since:
            wy0 = since.year + (1 if since.month >= 10 else 0)
            files = [f for f in files if int(re.search(r"WY(\d{4})", f).group(1)) >= wy0]
        if site_ids:
            files = [f for f in files if any(s in f for s in site_ids)]
        if limit:
            files = files[-limit:]

        def one(f: str) -> int:
            import xarray as xr

            wy = re.search(r"WY(\d{4})", f).group(1)
            url = f"{BASE}/{f}"
            key = request_key("GET", url, None)
            out = grid_path(self.settings, "ua_swe", "swe_depth", f"WY{wy}")
            if not refresh and grid_done(self.ledger, key) is not None and out.exists():
                summ.n_cached += 1
                return 0
            import hashlib

            with tempfile.TemporaryDirectory(prefix="uaswe_") as td:
                tp = Path(td) / f
                h = hashlib.sha256()
                nb = 0
                with self._auth_client() as c, c.stream("GET", url) as r:
                    r.raise_for_status()
                    with open(tp, "wb") as fh:
                        for chunk in r.iter_bytes(1 << 20):
                            fh.write(chunk)
                            h.update(chunk)
                            nb += len(chunk)
                summ.n_requests += 1
                with NC_LOCK, xr.open_dataset(tp) as ds:
                    sub = clip_to_bbox(ds, self.scope.bbox_buffered).load()
                sub.attrs.update({"title": f"UA SWE/depth WY{wy} NM clip", "source": CITATION})
                write_netcdf(sub, out)
                nt = int(sub.sizes.get("time", 0))
                t0, t1 = str(sub["time"].values[0])[:10], str(sub["time"].values[-1])[:10]
            record_grid(self.ledger, self.run_id, self.name, url, None, out, h.hexdigest(), nb,
                        window=(t0, t1), variable="swe_depth", key=key)
            register_grid(self.store, self.run_id, "ua_swe", "SWE,DEPTH", "4 km daily", t0, t1, out,
                          citation=CITATION, license="NSIDC / NASA open data", notes="assimilates SNOTEL+COOP with PRISM")
            return nt

        res = self.parallel(one, files, desc="water-years", workers=1)
        summ.n_rows = int(sum(res))
        summ.n_errors = len(files) - len(res)
        return summ
