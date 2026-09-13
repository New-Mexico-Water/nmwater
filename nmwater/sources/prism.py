"""PRISM (Oregon State) 4 km time series: monthly 1895-now (ppt, tmin, tmax, tmean, tdmean, vpdmin,
vpdmax) and daily 1981-now (ppt, tmin, tmax, tmean). Files are CONUS COGs in zips:

  https://data.prism.oregonstate.edu/time_series/us/an/4km/<var>/monthly/<YYYY>/prism_<var>_us_25m_<YYYYMM>.zip
  https://data.prism.oregonstate.edu/time_series/us/an/4km/<var>/daily/<YYYY>/prism_<var>_us_25m_<YYYYMMDD>.zip

Each zip is streamed to a temp file, the COG is clipped to the NM bbox, and the CONUS zip is discarded.
Clipped rasters are stacked into one NetCDF per variable-year (monthly: 12 steps; daily: ~365).
PRISM revises grids for ~6 months after release; `--since` re-pulls anything inside that window.
Rate rule: the same file may be downloaded at most twice per 24 h; the ledger prevents repeats.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from ..core.grids import clip_to_bbox, grid_done, grid_path, record_grid, register_grid, stream_download, write_netcdf
from ..core.http import request_key
from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.prism")

BASE = "https://data.prism.oregonstate.edu/time_series/us/an/4km"
MONTHLY_VARS = ["ppt", "tmin", "tmax", "tmean", "tdmean", "vpdmin", "vpdmax"]
DAILY_VARS = ["ppt", "tmin", "tmax", "tmean"]
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) nmwater/0.1"}
CITATION = "PRISM Climate Group, Oregon State University, https://prism.oregonstate.edu, accessed 2026."
LICENSE = "Free for reproduction and redistribution with attribution (OSU copyright)."
UNITS = {"ppt": "mm", "tmin": "degC", "tmax": "degC", "tmean": "degC", "tdmean": "degC", "vpdmin": "hPa", "vpdmax": "hPa"}


@register
class PRISM(Source):
    name = "prism"
    agency = "PRISM Climate Group (OSU)"
    description = "PRISM 4 km monthly (1895-) and daily (1981-) precip/temp/VPD clipped to NM"
    kinds = ("grid",)

    def _list(self, var: str, freq: str, year: int) -> list[str]:
        url = f"{BASE}/{var}/{freq}/{year}/"
        art = self.get(url, kind="listing", headers=UA, refresh=True)
        return sorted(set(re.findall(r'href="(prism_[a-z]+_us_25m_\d{6,8}\.zip)"', art.read_text())))

    def discover(self) -> pd.DataFrame:
        w, s, e, n = self.scope.bbox_buffered
        return pd.DataFrame([{
            "native_id": "nm_bbox", "name": "PRISM 4 km grid clipped to NM bbox", "site_type": "grid_cell",
            "agency": "PRISM", "lat": (s + n) / 2, "lon": (w + e) / 2, "active": True,
            "raw_metadata": json.dumps({"bbox": [w, s, e, n], "monthly": MONTHLY_VARS, "daily": DAILY_VARS}),
        }])

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        kinds = opts.get("kinds") or ["monthly", "daily"]
        this_year = date.today().year
        revise_after = date.today() - timedelta(days=int(self.opt("revision_days", 190)))
        jobs: list[tuple[str, str, int]] = []
        if "monthly" in kinds:
            y0 = since.year if since else int(self.opt("monthly_start", 1895))
            for v in self.opt("monthly_vars", MONTHLY_VARS):
                jobs += [(v, "monthly", y) for y in range(y0, this_year + 1)]
        if "daily" in kinds:
            y0 = since.year if since else int(self.opt("daily_start", 1981))
            for v in self.opt("daily_vars", DAILY_VARS):
                jobs += [(v, "daily", y) for y in range(y0, this_year + 1)]
        if site_ids:
            jobs = [j for j in jobs if j[0] in site_ids]
        if limit:
            jobs = jobs[:limit]

        def one(j) -> int:
            var, freq, year = j
            return self._fetch_var_year(var, freq, year, since, refresh, revise_after, summ, limit)

        res = self.parallel(one, jobs, desc="var-years", workers=1)
        summ.n_rows = int(sum(res))
        summ.n_errors = len(jobs) - len(res)
        return summ

    def _fetch_var_year(self, var, freq, year, since, refresh, revise_after, summ, limit) -> int:
        import rioxarray  # noqa: F401
        import xarray as xr

        files = self._list(var, freq, year)
        # monthly dir also contains the annual file prism_<var>_us_25m_YYYY.zip; keep YYYYMM (6) / YYYYMMDD (8)
        want = 6 if freq == "monthly" else 8
        files = [f for f in files if len(re.search(r"_(\d+)\.zip$", f).group(1)) == want]
        if limit and limit <= 2:
            files = files[:1]
        out = grid_path(self.settings, "prism", f"{var}_{freq}", str(year))
        arrays = []
        times = []
        n_new = 0
        with tempfile.TemporaryDirectory(prefix="prism_") as td:
            for f in files:
                stamp = re.search(r"_(\d+)\.zip$", f).group(1)
                d = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]) if len(stamp) == 8 else 1)
                url = f"{BASE}/{var}/{freq}/{year}/{f}"
                key = request_key("GET", url, None)
                clip_nc = self.settings.grids_dir / "prism" / "_parts" / f"{var}_{freq}_{stamp}.nc"
                needs = refresh or grid_done(self.ledger, key) is None or (since is not None and d >= revise_after)
                if needs or not clip_nc.exists():
                    zp = Path(td) / f
                    status, sha, nb = stream_download(self.http, self.name, url, zp, headers=UA)
                    summ.n_requests += 1
                    with zipfile.ZipFile(zp) as z:
                        tifs = [m for m in z.namelist() if m.lower().endswith(".tif")]
                        if not tifs:
                            raise RuntimeError(f"no tif in {f}: {z.namelist()[:5]}")
                        z.extract(tifs[0], td)
                        tif = Path(td) / tifs[0]
                    da = rioxarray.open_rasterio(tif, masked=True).squeeze("band", drop=True)
                    da = clip_to_bbox(da, self.scope.bbox_buffered).load()
                    da = da.rename({"x": "lon", "y": "lat"}) if "x" in da.dims else da
                    da.name = var
                    da.attrs.update({"units": UNITS.get(var, ""), "source_file": f})
                    ds = da.to_dataset()
                    write_netcdf(ds, clip_nc)
                    zp.unlink(missing_ok=True)
                    tif.unlink(missing_ok=True)
                    record_grid(self.ledger, self.run_id, self.name, url, None, clip_nc, sha, nb,
                                window=(d.isoformat(), d.isoformat()), variable=f"{var}_{freq}", key=key)
                    n_new += 1
                else:
                    summ.n_cached += 1
                ds = xr.open_dataset(clip_nc)
                arrays.append(ds[var].load())
                times.append(pd.Timestamp(d))
                ds.close()
        if not arrays:
            return 0
        stacked = xr.concat(arrays, dim=pd.Index(times, name="time"))
        stacked.name = var
        write_netcdf(stacked.to_dataset(), out, attrs={"title": f"PRISM {var} {freq} {year} NM clip", "source": CITATION})
        register_grid(self.store, self.run_id, "prism", f"{var}_{freq}", "4km (25 arc-sec)",
                      str(times[0].date()), str(times[-1].date()), out, citation=CITATION, license=LICENSE,
                      notes=f"{len(times)} steps; AN dataset; COG clipped")
        return n_new
