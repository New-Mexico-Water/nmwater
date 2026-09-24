"""USGS water use: (a) legacy county-level compilations on ScienceBase (2010, 2015; 2000/2005 when
found) and (b) the National Water Availability Assessment Data Companion (NWDC) modeled monthly
2000-2020 water use (irrigation, public supply, thermoelectric) by HUC12 within each NM county.

  ScienceBase: https://www.sciencebase.gov/catalog/item/<id>?format=json&fields=files
  NWDC API:    https://api.water.usgs.gov/nwaa-data/data?model=<id>&variable=<v>&timeRes=monthly
                   &startDate=YYYY-MM&endDate=YYYY-MM&location=countyCd:35001&intersection=overlap
                   &limit=600&skip=N&format=json
Tables are written under parquet/wateruse/source=usgs_wateruse/ with raw column names preserved.
"""

from __future__ import annotations

import io
import logging
from datetime import date

import pandas as pd

from ..core.constants import NM_COUNTY_FIPS
from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.usgs_wateruse")

SB_ITEMS = {
    "2015": ("5af3311be4b0da30c1b245d8", "usco2015v2.0.csv"),
    "2010": ("599c5b09e4b0b589267ed66e", "county_data.csv"),
}
NWDC = "https://api.water.usgs.gov/nwaa-data"
NWDC_MODELS = ["wu-irrigation-wd", "wu-irrigation-cu", "wu-public-supply-wd", "wu-public-supply-cu", "wu-thermoelectric"]
NM_COUNTIES = list(NM_COUNTY_FIPS)


@register
class USGSWaterUse(Source):
    name = "usgs_wateruse"
    agency = "USGS"
    description = "USGS county water-use compilations (2010, 2015) + NWDC modeled monthly water use 2000-2020"
    kinds = ("wateruse",)

    def discover(self) -> pd.DataFrame:
        rows = [{"native_id": f"county:{c}", "name": f"NM county {c}", "site_type": "area", "agency": "USGS",
                 "state": "NM", "county_fips": c, "active": True, "raw_metadata": "{}"} for c in NM_COUNTIES]
        return pd.DataFrame(rows)

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        kinds = opts.get("kinds") or ["sciencebase", "nwdc"]
        if "sciencebase" in kinds:
            summ.add(self._fetch_sciencebase(refresh))
        if "nwdc" in kinds:
            summ.add(self._fetch_nwdc(refresh, limit, site_ids))
        return summ

    def _fetch_sciencebase(self, refresh: bool) -> FetchSummary:
        summ = FetchSummary(self.name)
        for year, (item, fname) in SB_ITEMS.items():
            meta = self.get(f"https://www.sciencebase.gov/catalog/item/{item}", params={"format": "json", "fields": "files"},
                            kind="meta", refresh=refresh)
            summ.n_requests += 1
            files = {f["name"]: f["url"] for f in meta.read_json().get("files", [])}
            if fname not in files:
                summ.notes.append(f"sciencebase {year}: {fname} not found in item {item}")
                continue
            art = self.get(files[fname], kind="wateruse", variable=f"county_{year}", refresh=refresh)
            summ.n_requests += 1
            if art.from_cache:
                summ.n_cached += 1
            df = pd.read_csv(io.BytesIO(art.read_bytes()), dtype=str, low_memory=False, encoding_errors="replace")
            cols = {c.upper(): c for c in df.columns}
            st = cols.get("STATE") or cols.get("STATEFIPS") or cols.get("STATE_FIPS")
            if st is not None:
                nm = df[df[st].astype(str).str.strip().isin(["NM", "35"])]
                if nm.empty and "FIPS" in cols:
                    nm = df[df[cols["FIPS"]].astype(str).str.zfill(5).str.startswith("35")]
            elif "FIPS" in cols:
                nm = df[df[cols["FIPS"]].astype(str).str.zfill(5).str.startswith("35")]
            else:
                nm = df
            nm = nm.copy()
            nm["compilation_year"] = year
            self.store.write_table(nm, "wateruse", self.name, f"usgs_county_{year}")
            self.ledger.set_rows(art.request_key, len(nm))
            summ.n_rows += len(nm)
        return summ

    def _model_vars(self, model: str) -> list[str]:
        art = self.get(f"https://water.usgs.gov/nwaa-data/config/api/model/{model}.json", kind="meta")
        return [v["displayId"] for v in art.read_json().get("variables", [])]

    def _fetch_nwdc(self, refresh: bool, limit, site_ids) -> FetchSummary:
        summ = FetchSummary(self.name)
        counties = [c for c in NM_COUNTIES if not site_ids or c in site_ids or f"county:{c}" in site_ids]
        if limit:
            counties = counties[:limit]
        jobs = [(m, v, c) for m in NWDC_MODELS for v in self._model_vars(m) for c in counties]
        models_meta = {}
        for m in NWDC_MODELS:
            info = self.get(f"{NWDC}/models", kind="meta").read_json()
            models_meta = {x["model_id"]: x for x in info}
            break

        def one(j) -> int:
            model, var, county = j
            mm = models_meta.get(model, {})
            params = {"model": model, "variable": var, "timeRes": "monthly", "startDate": mm.get("start_date", "2000-01"),
                      "endDate": mm.get("end_date", "2020-12"), "location": f"countyCd:{county}",
                      "intersection": "overlap", "limit": 600, "skip": 0, "format": "json"}  # API max 600
            rows = []
            skip = 0
            for _ in range(200):
                params["skip"] = skip
                art = self.get(f"{NWDC}/data", params=dict(params), kind="wateruse", site_uid=self.uid(f"county:{county}"),
                               variable=f"{model}:{var}", refresh=refresh)
                summ.n_requests += 1
                if art.from_cache:
                    summ.n_cached += 1
                j2 = art.read_json()
                meta = j2.get("metadata", {})
                data = j2.get("data", {})
                for key_name, series in data.items():  # e.g. huc12_id -> {huc12: [...]}
                    for area, recs in series.items():
                        for r in recs:
                            rows.append({"model": model, "variable": var, "county_fips": county, "area_type": key_name,
                                         "area_id": area, "year_month": r.get("year_month") or r.get("date"),
                                         "value_mgd": r.get(var)})
                got = int(meta.get("recordsReturned", 0) or 0)
                total = int(meta.get("totalRecords", 0) or 0)
                skip += got
                if got == 0 or skip >= total:
                    break
            if not rows:
                return 0
            df = pd.DataFrame(rows)
            self.store.append_table(df, "wateruse", self.name, f"nwdc_{model}", self.run_id)
            return len(df)

        res = self.parallel(one, jobs, desc="nwdc county-model-var")
        summ.n_rows += int(sum(res))
        summ.n_errors += len(jobs) - len(res)
        return summ
