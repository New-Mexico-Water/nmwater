"""NOAA US Climate Reference Network (USCRN/USRCRN) hourly02 and daily01 products.

  stations: https://www.ncei.noaa.gov/pub/data/uscrn/products/stations.tsv
  hourly:   .../products/hourly02/{year}/CRNH0203-{year}-{ST}_{Location}_{Vector}.txt   (38 cols)
  daily:    .../products/daily01/{year}/CRND0103-{year}-{ST}_{Location}_{Vector}.txt    (28 cols)
  Missing: -9999 (and -99 for soil in daily). Times are end-of-hour.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import date

import httpx
import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.uscrn")

BASE = "https://www.ncei.noaa.gov/pub/data/uscrn/products"
HOURLY_COLS = ("WBANNO UTC_DATE UTC_TIME LST_DATE LST_TIME CRX_VN LONGITUDE LATITUDE T_CALC T_HR_AVG T_MAX T_MIN "
               "P_CALC SOLARAD SOLARAD_FLAG SOLARAD_MAX SOLARAD_MAX_FLAG SOLARAD_MIN SOLARAD_MIN_FLAG SUR_TEMP_TYPE "
               "SUR_TEMP SUR_TEMP_FLAG SUR_TEMP_MAX SUR_TEMP_MAX_FLAG SUR_TEMP_MIN SUR_TEMP_MIN_FLAG RH_HR_AVG "
               "RH_HR_AVG_FLAG SOIL_MOISTURE_5 SOIL_MOISTURE_10 SOIL_MOISTURE_20 SOIL_MOISTURE_50 SOIL_MOISTURE_100 "
               "SOIL_TEMP_5 SOIL_TEMP_10 SOIL_TEMP_20 SOIL_TEMP_50 SOIL_TEMP_100").split()
DAILY_COLS = ("WBANNO LST_DATE CRX_VN LONGITUDE LATITUDE T_DAILY_MAX T_DAILY_MIN T_DAILY_MEAN T_DAILY_AVG "
              "P_DAILY_CALC SOLARAD_DAILY SUR_TEMP_DAILY_TYPE SUR_TEMP_DAILY_MAX SUR_TEMP_DAILY_MIN SUR_TEMP_DAILY_AVG "
              "RH_DAILY_MAX RH_DAILY_MIN RH_DAILY_AVG SOIL_MOISTURE_5_DAILY SOIL_MOISTURE_10_DAILY "
              "SOIL_MOISTURE_20_DAILY SOIL_MOISTURE_50_DAILY SOIL_MOISTURE_100_DAILY SOIL_TEMP_5_DAILY "
              "SOIL_TEMP_10_DAILY SOIL_TEMP_20_DAILY SOIL_TEMP_50_DAILY SOIL_TEMP_100_DAILY").split()
HOURLY_VARS = ["T_HR_AVG", "T_MAX", "T_MIN", "P_CALC", "SOLARAD", "RH_HR_AVG",
               "SOIL_MOISTURE_5", "SOIL_MOISTURE_10", "SOIL_MOISTURE_20", "SOIL_MOISTURE_50", "SOIL_MOISTURE_100",
               "SOIL_TEMP_5", "SOIL_TEMP_10", "SOIL_TEMP_20", "SOIL_TEMP_50", "SOIL_TEMP_100"]
DAILY_VARS = [c for c in DAILY_COLS if c not in ("WBANNO", "LST_DATE", "CRX_VN", "LONGITUDE", "LATITUDE",
                                                  "SUR_TEMP_DAILY_TYPE")]
FLAG_OF = {"SOLARAD": "SOLARAD_FLAG", "RH_HR_AVG": "RH_HR_AVG_FLAG"}


@register
class USCRN(Source):
    name = "uscrn"
    agency = "NOAA NCEI"
    description = "US Climate Reference Network hourly/daily precip, temp, soil moisture (2004-) NM"

    def discover(self) -> pd.DataFrame:
        art = self.get(f"{BASE}/stations.tsv", kind="meta", refresh=True)
        df = pd.read_csv(io.StringIO(art.read_text()), sep="\t", dtype=str)
        df = df[df["STATE"] == self.scope.state_abbr]
        return pd.DataFrame(
            {
                "native_id": df["WBAN"],
                "name": df["LOCATION"] + " " + df["VECTOR"] + " (" + df["NAME"] + ")",
                "lat": pd.to_numeric(df["LATITUDE"], errors="coerce"),
                "lon": pd.to_numeric(df["LONGITUDE"], errors="coerce"),
                "elevation_m": pd.to_numeric(df["ELEVATION"], errors="coerce") * 0.3048,
                "site_type": "met",
                "agency": "NOAA/" + df["NETWORK"],
                "state": df["STATE"],
                "active": df["OPERATION"] == "Operational",
                "raw_metadata": [json.dumps({k: (None if pd.isna(v) else v) for k, v in r.items()})
                                 for r in df.to_dict("records")],
            }
        )

    @staticmethod
    def _fname(meta: dict) -> str:
        return f"{meta['STATE']}_{str(meta['LOCATION']).replace(' ', '_')}_{str(meta['VECTOR']).replace(' ', '_')}"

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        sites = self.sites()
        if sites.empty:
            sites = self.discover()
            self.store.write_sites(sites, self.name)
        if site_ids:
            sites = sites[sites["native_id"].isin(site_ids)]
        if limit:
            sites = sites.head(limit)
        this_year = date.today().year
        jobs = []
        for r in sites.itertuples(index=False):
            meta = json.loads(r.raw_metadata)
            comm = meta.get("COMMISSIONING") or ""
            b = int(comm[:4]) if comm[:4].isdigit() else 2000
            closing = meta.get("CLOSING") or ""
            e = int(closing[:4]) if closing[:4].isdigit() else this_year
            if since:
                b = max(b, since.year)
            for y in range(b, e + 1):
                for prod in ("hourly02", "daily01"):
                    jobs.append((r.native_id, self._fname(meta), y, prod))

        def one(j) -> int:
            wban, fname, y, prod = j
            prefix = "CRNH0203" if prod == "hourly02" else "CRND0103"
            url = f"{BASE}/{prod}/{y}/{prefix}-{y}-{fname}.txt"
            try:
                art = self.get(url, kind=prod, site_uid=self.uid(wban), window=(f"{y}-01-01", f"{y}-12-31"),
                               refresh=refresh or y >= this_year - 1)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    return 0
                raise
            summ.n_requests += 1
            if art.from_cache:
                summ.n_cached += 1
                if self.already_written(art):
                    return 0
            df = self.normalize(art)
            if df is None or df.empty:
                return 0
            if since is not None:
                df = df[df["datetime_utc"] >= pd.Timestamp(since, tz="UTC")]
            n = self.write_obs(df, tag=f"{wban}-{y}-{prod}")
            self.ledger.set_rows(art.request_key, n)
            return n

        res = self.parallel(one, jobs, desc="station-year-products")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(jobs) - len(res)
        return summ

    def normalize(self, artifact) -> pd.DataFrame | None:
        text = artifact.read_text()
        if not text.strip():
            return None
        if artifact.kind == "hourly02":
            return self._norm(text, HOURLY_COLS, HOURLY_VARS, hourly=True)
        if artifact.kind == "daily01":
            return self._norm(text, DAILY_COLS, DAILY_VARS, hourly=False)
        return None

    def _norm(self, text: str, cols: list[str], vars_: list[str], hourly: bool) -> pd.DataFrame | None:
        df = pd.read_csv(io.StringIO(text), sep=r"\s+", header=None, names=cols, dtype=str)
        if df.empty:
            return None
        wban = df["WBANNO"].iloc[0]
        if hourly:
            ts = pd.to_datetime(df["UTC_DATE"] + df["UTC_TIME"].str.zfill(4), format="%Y%m%d%H%M", utc=True,
                                errors="coerce")
            lst = pd.to_datetime(df["LST_DATE"] + df["LST_TIME"].str.zfill(4), format="%Y%m%d%H%M", errors="coerce")
            offset = ((lst - ts.dt.tz_localize(None)).dt.total_seconds() / 60).round().astype("Int64")
            interval = "hourly"
        else:
            ts = pd.to_datetime(df["LST_DATE"], format="%Y%m%d", utc=True, errors="coerce")
            offset = pd.Series([None] * len(df), index=df.index)
            interval = "daily"
        frames = []
        for c in vars_:
            v = pd.to_numeric(df[c], errors="coerce")
            v = v.where((v > -9000) & ~((v <= -99) & c.startswith(("SOIL", "RH"))))
            flag = df[FLAG_OF[c]] if c in FLAG_OF and FLAG_OF[c] in df.columns else None
            sub = pd.DataFrame(
                {
                    "site_uid": self.uid(wban),
                    "datetime_utc": ts,
                    "value": v,
                    "qualifier": flag.where(flag != "0", None) if flag is not None else None,
                    "source_param": c,
                    "interval": interval,
                    "utc_offset_min": offset,
                }
            )
            frames.append(sub[sub["value"].notna()])
        out = pd.concat(frames, ignore_index=True)
        return self.xw.apply(out, self.name)
