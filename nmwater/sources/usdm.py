"""US Drought Monitor statistics API (weekly, 2000-01-04 -> present).

    https://usdmdataservices.unl.edu/api/{State,County,ClimateDivision,HUC}Statistics/
        GetDroughtSeverityStatisticsByAreaPercent?aoi=&startdate=M/D/YYYY&enddate=&statisticsType=1
        GetDSCI?aoi=&startdate=&enddate=&statisticsType=1
Areas: state 35, NM counties, NM climate divisions 2901-2908, HUC4 and HUC8 in scope.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import date

import pandas as pd

from ..core.constants import NM_COUNTY_FIPS
from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.usdm")

BASE = "https://usdmdataservices.unl.edu/api"
NM_COUNTIES = list(NM_COUNTY_FIPS)
NM_CLIMDIVS = [f"29{d:02d}" for d in range(1, 9)]


@register
class USDM(Source):
    name = "usdm"
    agency = "NDMC/USDA/NOAA"
    description = "US Drought Monitor weekly % area by category and DSCI for NM state/counties/climdivs/HUCs (2000-)"

    def _areas(self) -> list[tuple[str, str, str]]:
        """(area_type_path, aoi, native_id)"""
        areas = [("StateStatistics", "35", "state:35")]
        areas += [("CountyStatistics", c, f"county:{c}") for c in NM_COUNTIES]
        areas += [("ClimateDivisionStatistics", d, f"climdiv:{d}") for d in NM_CLIMDIVS]
        areas += [("HUCStatistics", h, f"huc4:{h}") for h in self.scope.huc4]
        huc8 = self._huc8_in_scope()
        areas += [("HUCStatistics", h, f"huc8:{h}") for h in huc8]
        return areas

    def _huc8_in_scope(self) -> list[str]:
        p = self.store.root / "reference" / "source=wbd" / "huc8_in_scope.parquet"
        if p.exists():
            try:
                return sorted(pd.read_parquet(p)["huc8"].astype(str).unique())
            except Exception as e:
                log.warning("could not read huc8_in_scope: %s", e)
        return [h for h in self.scope.huc8]

    def discover(self) -> pd.DataFrame:
        rows = []
        for path, aoi, nid in self._areas():
            kind = nid.split(":")[0]
            rows.append({"native_id": nid, "name": f"USDM {kind} {aoi}", "site_type": "area", "agency": "NDMC",
                         "state": "NM" if kind in ("state", "county", "climdiv") else None,
                         "county_fips": aoi if kind == "county" else None,
                         "huc8": aoi if kind == "huc8" else None, "active": True,
                         "raw_metadata": json.dumps({"area_type": path, "aoi": aoi})})
        return pd.DataFrame(rows)

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        areas = self._areas()
        if site_ids:
            areas = [a for a in areas if a[2] in site_ids or a[1] in site_ids]
        if limit:
            areas = areas[:limit]
        start = since or date(2000, 1, 4)
        end = date.today()
        sd, ed = f"{start.month}/{start.day}/{start.year}", f"{end.month}/{end.day}/{end.year}"
        do_refresh = refresh or since is not None

        def one(a) -> int:
            path, aoi, nid = a
            n = 0
            for stat in ("GetDroughtSeverityStatisticsByAreaPercent", "GetDSCI"):
                url = f"{BASE}/{path}/{stat}"
                params = {"aoi": aoi, "startdate": sd, "enddate": ed, "statisticsType": "1"}
                art = self.get(url, params=params, kind="data", site_uid=self.uid(nid), variable=stat,
                               window=(start.isoformat(), end.isoformat()), refresh=do_refresh,
                               key_extra=f"since={start.isoformat()}" if since else None)
                summ.n_requests += 1
                if art.from_cache:
                    summ.n_cached += 1
                    if self.already_written(art):
                        continue
                df = self.normalize(art)
                k = self.write_obs(df, tag=nid.replace(":", "-")) if df is not None else 0
                self.ledger.set_rows(art.request_key, k)
                n += k
            return n

        res = self.parallel(one, areas, desc="areas")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(areas) - len(res)
        return summ

    def normalize(self, artifact) -> pd.DataFrame | None:
        text = artifact.read_text().strip()
        if not text or text.startswith("[]") or text.startswith("<"):
            return None
        df = pd.read_csv(io.StringIO(text), dtype=str)
        if df.empty or "MapDate" not in df.columns:
            return None
        aoi = (artifact.params or {}).get("aoi", "")
        path = artifact.url.split("/api/")[1].split("/")[0]
        kind = {"StateStatistics": "state", "CountyStatistics": "county", "ClimateDivisionStatistics": "climdiv",
                "HUCStatistics": "huc"}[path]
        if kind == "huc":
            kind = "huc4" if len(aoi) == 4 else "huc8"
        nid = f"{kind}:{aoi}"
        dt = pd.to_datetime(df["MapDate"], format="%Y%m%d", errors="coerce", utc=True)
        frames = []
        cols = [c for c in ("D0", "D1", "D2", "D3", "D4", "DSCI") if c in df.columns]
        for c in cols:
            frames.append(pd.DataFrame({
                "site_uid": self.uid(nid), "datetime_utc": dt, "value": pd.to_numeric(df[c], errors="coerce"),
                "source_param": c, "interval": "weekly", "statistic": "observed", "qualifier": None,
                "utc_offset_min": None,
            }))
        out = pd.concat(frames, ignore_index=True)
        out = out[out["value"].notna() & out["datetime_utc"].notna()]
        return self.xw.apply(out, self.name)
