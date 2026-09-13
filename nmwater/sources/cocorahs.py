"""CoCoRaHS volunteer daily precipitation/snow for New Mexico via the CSV export.

  https://data.cocorahs.org/cocorahs/export/exportreports.aspx?ReportType=Daily&dtf=1&Format=CSV&State=NM
      &ReportDateType=reportdate&StartDate=M/D/YYYY&EndDate=M/D/YYYY&TimesInGMT=False
Monthly windows (verified: a 31-day range returns all days). Sites are derived from the rows.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import date, timedelta

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.cocorahs")

EXPORT = "https://data.cocorahs.org/cocorahs/export/exportreports.aspx"
PARAMS = ["TotalPrecipAmt", "NewSnowDepth", "NewSnowSWE", "TotalSnowDepth", "TotalSnowSWE"]


def _month_windows(start: date, end: date):
    cur = date(start.year, start.month, 1)
    while cur <= end:
        nxt = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
        yield max(cur, start), min(nxt - timedelta(days=1), end)
        cur = nxt


@register
class CoCoRaHS(Source):
    name = "cocorahs"
    agency = "CoCoRaHS"
    description = "CoCoRaHS volunteer daily precipitation and snow reports, NM (2005-)"

    def _window(self, a: date, b: date, refresh: bool):
        params = {
            "ReportType": "Daily", "dtf": "1", "Format": "CSV", "State": self.scope.state_abbr,
            "ReportDateType": "reportdate", "StartDate": f"{a.month}/{a.day}/{a.year}",
            "EndDate": f"{b.month}/{b.day}/{b.year}", "TimesInGMT": "False",
        }
        return self.get(EXPORT, params=params, kind="data", window=(a.isoformat(), b.isoformat()), refresh=refresh)

    def _parse(self, artifact) -> pd.DataFrame | None:
        text = artifact.read_text()
        if not text.startswith("ObservationDate"):
            return None
        df = pd.read_csv(io.StringIO(text), dtype=str, skipinitialspace=True)
        if df.empty:
            return None
        df.columns = [c.strip() for c in df.columns]
        for c in df.columns:
            df[c] = df[c].astype(str).str.strip()
        return df

    def _sites_from(self, df: pd.DataFrame) -> pd.DataFrame:
        g = df.sort_values("ObservationDate").groupby("StationNumber").agg(
            name=("StationName", "last"), lat=("Latitude", "last"), lon=("Longitude", "last"),
            first=("ObservationDate", "min"), last=("ObservationDate", "max"))
        g = g.reset_index()
        return pd.DataFrame(
            {
                "native_id": g["StationNumber"],
                "name": g["name"],
                "lat": pd.to_numeric(g["lat"], errors="coerce"),
                "lon": pd.to_numeric(g["lon"], errors="coerce"),
                "site_type": "met",
                "agency": "CoCoRaHS",
                "state": g["StationNumber"].str[:2],
                "raw_metadata": [json.dumps({"first": a, "last": b}) for a, b in zip(g["first"], g["last"])],
            }
        )

    def _merge_sites(self, new: pd.DataFrame) -> int:
        old = self.sites()
        if not old.empty:
            keep = [c for c in new.columns if c in old.columns]
            merged = pd.concat([old[keep], new[keep]], ignore_index=True)
            # keep newest metadata but earliest 'first' date
            merged = merged.drop_duplicates("native_id", keep="last")
        else:
            merged = new
        return self.store.write_sites(merged, self.name)

    def discover(self) -> pd.DataFrame:
        end = date.today()
        start = end - timedelta(days=90)
        frames = []
        for a, b in _month_windows(start, end):
            df = self._parse(self._window(a, b, refresh=True))
            if df is not None:
                frames.append(df)
        if not frames:
            return self.sites()
        new = self._sites_from(pd.concat(frames, ignore_index=True))
        old = self.sites()
        if old.empty:
            return new
        return pd.concat([old, new], ignore_index=True).drop_duplicates("native_id", keep="last")

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        start = date.fromisoformat(str(self.opt("start_date", "2005-01-01")))
        if since:
            start = max(start, since)
        end = date.today()
        windows = list(_month_windows(start, end))
        if limit:
            windows = windows[-limit:]
        recent = end - timedelta(days=62)
        all_sites = []

        def one(w) -> int:
            a, b = w
            art = self._window(a, b, refresh=refresh or b >= recent)
            summ.n_requests += 1
            if art.from_cache:
                summ.n_cached += 1
            df = self._parse(art)
            if df is None:
                return 0
            all_sites.append(self._sites_from(df))
            if self.already_written(art):
                return 0
            obs = self._obs(df)
            if site_ids:
                obs = obs[obs["site_uid"].isin([self.uid(s) for s in site_ids])]
            n = self.write_obs(obs, tag=f"{a:%Y%m}")
            self.ledger.set_rows(art.request_key, n)
            return n

        res = self.parallel(one, windows, desc="months")
        if all_sites:
            self._merge_sites(pd.concat(all_sites, ignore_index=True).drop_duplicates("native_id", keep="last"))
        summ.n_rows = int(sum(res))
        summ.n_errors = len(windows) - len(res)
        return summ

    def _obs(self, df: pd.DataFrame) -> pd.DataFrame:
        frames = []
        ts = pd.to_datetime(df["ObservationDate"], errors="coerce", utc=True)
        for p in PARAMS:
            if p not in df.columns:
                continue
            raw = df[p].str.strip().str.upper()
            trace = raw == "T"
            val = pd.to_numeric(raw.where(~trace, "0"), errors="coerce")
            sub = pd.DataFrame(
                {
                    "site_uid": self.name + ":" + df["StationNumber"],
                    "datetime_utc": ts,
                    "value": val,
                    "qualifier": trace.map({True: "T", False: None}),
                    "source_param": p,
                    "utc_offset_min": None,
                }
            )
            frames.append(sub[sub["value"].notna()])
        out = pd.concat(frames, ignore_index=True)
        out = self.xw.apply(out, self.name)
        out["interval"] = out["interval"].fillna("daily")
        return out

    def normalize(self, artifact) -> pd.DataFrame | None:
        df = self._parse(artifact)
        return self._obs(df) if df is not None else None
