"""USDA NASS Quick Stats: Census of Agriculture irrigation items and the Irrigation & Water
Management Survey for New Mexico (state and county level). Requires NASS_API_KEY.

  https://quickstats.nass.usda.gov/api/api_GET/?key=..&state_alpha=NM&source_desc=CENSUS
      &sector_desc=ECONOMICS&group_desc=IRRIGATION&year=..&format=json   (max 50,000 rows/call)
"""

from __future__ import annotations

import json
import logging
from datetime import date

import pandas as pd

from .base import FetchSummary, Source, SourceUnavailable, register

log = logging.getLogger("nmwater.nass")

BASE = "https://quickstats.nass.usda.gov/api"
QUERIES = {
    "irrigation_economics": {"source_desc": "CENSUS", "sector_desc": "ECONOMICS", "group_desc": "IRRIGATION"},
    "irrigated_land_area": {"source_desc": "CENSUS", "sector_desc": "ECONOMICS", "group_desc": "FARMS & LAND & ASSETS",
                            "commodity_desc": "AG LAND", "statisticcat_desc": "AREA", "prodn_practice_desc": "IRRIGATED"},
}


@register
class NASS(Source):
    name = "nass"
    agency = "USDA NASS"
    description = "Quick Stats irrigation (acres, water applied, source, method) for NM, census years"
    kinds = ("wateruse",)
    requires_tokens = ("NASS_API_KEY",)

    def discover(self) -> pd.DataFrame:
        return pd.DataFrame([{"native_id": "state:NM", "name": "New Mexico (NASS)", "site_type": "area",
                              "agency": "USDA NASS", "state": "NM", "active": True, "raw_metadata": "{}"}])

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        key = self.settings.tokens.get("NASS_API_KEY")
        if not key:
            raise SourceUnavailable("nass: NASS_API_KEY missing")
        # years with irrigation data: census years (every 5) and IWMS years
        years = self.get(f"{BASE}/get_param_values/", params={"key": key, "param": "year", "source_desc": "CENSUS",
                                                              "state_alpha": "NM", "group_desc": "IRRIGATION"},
                         kind="meta", refresh=refresh).read_json().get("year", [])
        summ.n_requests += 1
        if since:
            years = [y for y in years if int(y) >= since.year]
        if limit:
            years = years[-limit:]
        for qname, q in QUERIES.items():
            for y in years:
                params = {"key": key, "state_alpha": "NM", "year": str(y), "format": "json", **q}
                try:
                    art = self.get(f"{BASE}/api_GET/", params=params, kind="wateruse", variable=qname,
                                   window=(f"{y}-01-01", f"{y}-12-31"), refresh=refresh)
                except Exception as e:  # noqa: BLE001
                    summ.n_errors += 1
                    summ.notes.append(f"{qname} {y}: {str(e)[:120]}")
                    continue
                summ.n_requests += 1
                if art.from_cache:
                    summ.n_cached += 1
                data = art.read_json().get("data", [])
                if not data:
                    continue
                df = pd.DataFrame(data)
                df["query"] = qname
                self.store.write_table(df, "wateruse", self.name, f"{qname}_{y}")
                self.ledger.set_rows(art.request_key, len(df))
                summ.n_rows += len(df)
        return summ
