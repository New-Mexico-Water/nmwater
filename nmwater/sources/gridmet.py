"""gridMET (Climatology Lab, U. Idaho): 4 km daily CONUS met + reference ET + drought indices, 1979-now.

THREDDS NetCDF Subset Service on the aggregated per-variable datasets; server-side bbox clip:
  https://thredds.northwestknowledge.net/thredds/ncss/agg_met_<var>_1979_CurrentYear_CONUS.nc
      ?var=<ncvar>&north=&south=&east=&west=&time_start=&time_end=&accept=netcdf
(accept=netcdf4 returns 500 'HDF error'; netcdf3 works). One request per variable-year (~20 MB).
Grid files land in data/grids/gridmet/<var>_<year>.nc; registered in reference/grids.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

import pandas as pd

from ..core.grids import grid_path, register_grid
from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.gridmet")

BASE = "https://thredds.northwestknowledge.net/thredds"
DEFAULT_VARS = ["pr", "tmmn", "tmmx", "rmin", "rmax", "sph", "srad", "vs", "vpd", "etr", "pet",
                "pdsi", "spi30d", "spi90d", "spi180d", "spi1y", "spei30d", "spei1y", "eddi30d", "eddi1y"]
DROUGHT_PREFIXES = ("pdsi", "spi", "spei", "eddi")
CITATION = ("Abatzoglou, J.T. (2013), Development of gridded surface meteorological data for ecological "
            "applications and modelling. Int. J. Climatol. 33:121-131. gridMET via Climatology Lab.")


@register
class GridMET(Source):
    name = "gridmet"
    agency = "Climatology Lab / U. Idaho"
    description = "gridMET 4 km daily met, ETr/ETo, and drought indices, NM bbox subsets (1979-)"
    kinds = ("grid",)

    def _dataset(self, var: str) -> str:
        return f"agg_met_{var}_1979_CurrentYear_CONUS.nc"

    def _ncvar(self, var: str) -> str:
        """Discover the NetCDF variable name inside the aggregated dataset from its NCSS form page."""
        art = self.get(f"{BASE}/ncss/grid/{self._dataset(var)}/dataset.html", kind="meta")
        html = art.read_text()
        m = [v for v in re.findall(r'name="var" value="([^"]+)"', html) if v not in ("category", "crs")]
        if not m:
            raise RuntimeError(f"gridmet: cannot find variable name for {var}")
        return m[0]

    def discover(self) -> pd.DataFrame:
        w, s, e, n = self.scope.bbox_buffered
        return pd.DataFrame([{
            "native_id": "nm_bbox", "name": "gridMET 4 km grid clipped to NM bbox", "site_type": "grid_cell",
            "agency": "Climatology Lab", "lat": (s + n) / 2, "lon": (w + e) / 2, "active": True,
            "raw_metadata": json.dumps({"bbox": [w, s, e, n], "variables": self.opt("variables", DEFAULT_VARS)}),
        }])

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        variables = list(self.opt("variables", DEFAULT_VARS))
        if site_ids:  # allow --site to select variables for testing
            variables = [v for v in variables if v in site_ids] or [s for s in site_ids]
        y0 = int(self.opt("start_year", 1979))
        this_year = date.today().year
        years = list(range(since.year if since else y0, this_year + 1))
        if limit:
            years = years[-limit:]
        w, s, e, n = self.scope.bbox_buffered
        # the drought indices (pentad series) begin 1980-01-05; a 1979 request is a 400
        jobs = [(v, y) for v in variables for y in years if not (y < 1980 and v.startswith(DROUGHT_PREFIXES))]

        def one(j) -> int:
            var, year = j
            ncvar = self._ncvar(var)
            params = {"var": ncvar, "north": f"{n:.4f}", "south": f"{s:.4f}", "east": f"{e:.4f}", "west": f"{w:.4f}",
                      "time_start": f"{year}-01-01T00:00:00Z", "time_end": f"{year}-12-31T23:59:59Z",
                      "accept": "netcdf"}
            # The current year keeps growing: always refresh it; older years are final.
            do_refresh = refresh or year == this_year or (since is not None and year >= since.year)
            art = self.get(f"{BASE}/ncss/{self._dataset(var)}", params=params, kind="grid", variable=var,
                           window=(f"{year}-01-01", f"{year}-12-31"), refresh=do_refresh,
                           key_extra=f"day={date.today().isoformat()}" if do_refresh and year == this_year else None)
            summ.n_requests += 1
            if art.from_cache:
                summ.n_cached += 1
                if self.already_written(art):
                    return 0
            out = grid_path(self.settings, "gridmet", var, str(year))
            import xarray as xr

            from ..core.grids import NC_LOCK, write_netcdf

            tmp = out.with_suffix(".raw.nc")
            tmp.write_bytes(art.read_bytes())
            with NC_LOCK, xr.open_dataset(tmp, engine="netcdf4") as ds0:
                ds = ds0.load()
            tmp.unlink(missing_ok=True)
            if "day" in ds.dims:
                ds = ds.rename({"day": "time"})
            for v in ds.data_vars:
                if ds[v].dtype == "float64":
                    ds[v] = ds[v].astype("float32")
            nt = int(ds.sizes.get("time", 0))
            t0 = str(ds["time"].values[0])[:10] if "time" in ds else f"{year}-01-01"
            t1 = str(ds["time"].values[-1])[:10] if "time" in ds else f"{year}-12-31"
            write_netcdf(ds, out, attrs={"title": f"gridMET {var} {year} NM clip", "source": CITATION})
            register_grid(self.store, self.run_id, "gridmet", var, "4km (1/24 deg) daily", t0, t1, out,
                          citation=CITATION, license="Public domain / free use with attribution",
                          notes=f"ncvar={ncvar}; NCSS bbox subset; nc3")
            self.ledger.set_rows(art.request_key, nt)
            return nt

        res = self.parallel(one, jobs, desc="var-years")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(jobs) - len(res)
        return summ
