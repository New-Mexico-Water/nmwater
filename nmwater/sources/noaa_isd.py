"""NOAA NCEI Integrated Surface Database, ISD-Lite hourly files (1941-) for NM + border stations.

  history:  https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv
  data:     https://www.ncei.noaa.gov/pub/data/noaa/isd-lite/{year}/{USAF}-{WBAN}-{year}.gz
            fixed width: year month day hour  temp(0.1C) dewpt(0.1C) slp(0.1hPa) wdir wspd(0.1m/s)
                         sky precip1h(0.1mm) precip6h(0.1mm); -9999 missing
  GHCNh station list -> reference table only.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
from datetime import date

import httpx
import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.noaa_isd")

HISTORY_URL = "https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv"
LITE_URL = "https://www.ncei.noaa.gov/pub/data/noaa/isd-lite/{year}/{usaf}-{wban}-{year}.gz"
GHCNH_URL = "https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/doc/ghcnh-station-list.csv"
COLS = ["year", "month", "day", "hour", "temp", "dewpt", "slp", "wdir", "wspd", "sky", "precip1h", "precip6h"]


@register
class NOAAISD(Source):
    name = "noaa_isd"
    agency = "NOAA NCEI"
    description = "ISD-Lite hourly airport/military weather (1941-) for NM + border"

    def _history(self, refresh: bool = True) -> pd.DataFrame:
        art = self.get(HISTORY_URL, kind="meta", refresh=refresh)
        df = pd.read_csv(io.StringIO(art.read_text()), dtype=str)
        df.columns = [c.strip().upper() for c in df.columns]
        df["LAT"] = pd.to_numeric(df["LAT"], errors="coerce")
        df["LON"] = pd.to_numeric(df["LON"], errors="coerce")
        df["ELEV(M)"] = pd.to_numeric(df["ELEV(M)"], errors="coerce")
        return df

    def discover(self) -> pd.DataFrame:
        h = self._history()
        inbox = [self.scope.in_bbox(a, b) for a, b in zip(h["LAT"], h["LON"])]
        h = h[((h["STATE"] == self.scope.state_abbr) & (h["CTRY"] == "US")) | pd.Series(inbox, index=h.index)]
        h = h[h["LAT"].notna()]
        # GHCNh station list -> reference
        try:
            art = self.get(GHCNH_URL, kind="meta", refresh=True)
            gh = pd.read_csv(io.StringIO(art.read_text()), dtype=str)
            gh["LATITUDE"] = pd.to_numeric(gh["LATITUDE"], errors="coerce")
            gh["LONGITUDE"] = pd.to_numeric(gh["LONGITUDE"], errors="coerce")
            gin = [self.scope.in_bbox(a, b) for a, b in zip(gh["LATITUDE"], gh["LONGITUDE"])]
            gh = gh[(gh["STATE"] == self.scope.state_abbr) | pd.Series(gin, index=gh.index)]
            self.store.write_table(gh, "reference", self.name, "ghcnh_stations")
        except Exception as e:  # noqa: BLE001
            log.warning("GHCNh station list failed: %s", e)
        return pd.DataFrame(
            {
                "native_id": h["USAF"] + "-" + h["WBAN"],
                "name": h["STATION NAME"],
                "lat": h["LAT"],
                "lon": h["LON"],
                "elevation_m": h["ELEV(M)"].where(h["ELEV(M)"] > -900),
                "site_type": "met",
                "agency": "NOAA/ISD",
                "state": h["STATE"].where(h["STATE"].astype(str).str.len() == 2, None),
                "active": h["END"].astype(str).str[:4].astype(int) >= date.today().year - 1,
                "raw_metadata": [json.dumps({"icao": i, "begin": b, "end": e, "ctry": c})
                                 for i, b, e, c in zip(h["ICAO"].fillna(""), h["BEGIN"], h["END"], h["CTRY"].fillna(""))],
            }
        )

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
        jobs: list[tuple[str, int]] = []
        this_year = date.today().year
        for r in sites.itertuples(index=False):
            meta = json.loads(r.raw_metadata) if r.raw_metadata else {}
            b = int(str(meta.get("begin", "1941"))[:4])
            e = min(int(str(meta.get("end", this_year))[:4]), this_year)
            if since:
                b = max(b, since.year)
            for y in range(b, e + 1):
                jobs.append((r.native_id, y))

        def one(j) -> int:
            sid, y = j
            usaf, wban = sid.split("-")
            url = LITE_URL.format(year=y, usaf=usaf, wban=wban)
            # current + previous year files change; refresh them
            do_refresh = refresh or (y >= this_year - 1)
            try:
                art = self.get(url, kind="data", site_uid=self.uid(sid), window=(f"{y}-01-01", f"{y}-12-31"),
                               refresh=do_refresh)
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
            n = self.write_obs(df, tag=f"{sid}-{y}")
            self.ledger.set_rows(art.request_key, n)
            return n

        res = self.parallel(one, jobs, desc="station-years")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(jobs) - len(res)
        return summ

    def normalize(self, artifact) -> pd.DataFrame | None:
        raw = artifact.read_bytes()
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError):
            pass
        text = raw.decode("ascii", "replace")
        if not text.strip():
            return None
        df = pd.read_csv(io.StringIO(text), sep=r"\s+", header=None, names=COLS, dtype=float)
        sid = artifact.url.rsplit("/", 1)[-1].rsplit("-", 1)[0]
        ts = pd.to_datetime(
            dict(year=df["year"].astype(int), month=df["month"].astype(int), day=df["day"].astype(int),
                 hour=df["hour"].astype(int)), utc=True, errors="coerce")
        state_nm = True  # nominal NM standard offset for local reconstruction
        frames = []
        for col in ("temp", "wdir", "wspd", "precip1h"):
            v = df[col].where(df[col] > -9998)
            qual = None
            if col == "precip1h":
                trace = v == -1  # ISD-Lite encodes trace precipitation as -1
                qual = trace.map({True: "T", False: None})
                v = v.where(~trace, 0.0)
            sub = pd.DataFrame(
                {
                    "site_uid": self.uid(sid),
                    "datetime_utc": ts,
                    "value": v,
                    "qualifier": qual,
                    "source_param": col,
                    "utc_offset_min": -420 if state_nm else None,
                }
            )
            frames.append(sub[sub["value"].notna()])
        out = pd.concat(frames, ignore_index=True)
        return self.xw.apply(out, self.name)
