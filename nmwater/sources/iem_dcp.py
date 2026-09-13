"""Iowa Environmental Mesonet archive of the NWS HADS/SHEF DCP feed for New Mexico (NM_DCP network).

  stations: https://mesonet.agron.iastate.edu/geojson/network/NM_DCP.geojson  (archive_begin/end per site)
  data:     https://mesonet.agron.iastate.edu/cgi-bin/request/hads.py?network=NM_DCP&stations=SID
                &sts=YYYY-MM-DDT00:00Z&ets=...&format=csv
            columns: station, utc_valid, then one column per SHEF code (e.g. HGIRGZ, QRIRGZ, PPDRZZ, TAIRZXZ)
SHEF code = PE(2) + duration(1) + type/source(2) + extremum(1) [+ probability]. Values are in SHEF
standard units (HG ft, QR kcfs, PC/PP in, TA F, HP ft, LS kaf, SD/SW/SF in).
"""

from __future__ import annotations

import io
import json
import logging
from datetime import date, timedelta

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.iem_dcp")

GEOJSON = "https://mesonet.agron.iastate.edu/geojson/network/NM_DCP.geojson"
HADS = "https://mesonet.agron.iastate.edu/cgi-bin/request/hads.py"
DUR_INTERVAL = {"I": "instant", "H": "hourly", "D": "daily", "M": "monthly", "Q": "irregular",
                "T": "irregular", "U": "irregular"}
EXT_STAT = {"X": "max", "N": "min", "Z": None, "D": None}
# PEs that are interval totals
TOTAL_PE = {"PP", "PC", "EP", "SF"}


@register
class IEMDCP(Source):
    name = "iem_dcp"
    agency = "IEM (NWS HADS/SHEF mirror)"
    description = "IEM archive of NWS DCP/SHEF observations for NM gauges (2002-)"

    def discover(self) -> pd.DataFrame:
        art = self.get(GEOJSON, kind="meta", refresh=True)
        d = art.read_json()
        rows = []
        for f in d.get("features", []):
            p = f.get("properties", {})
            lon, lat = (f.get("geometry") or {}).get("coordinates", [None, None])[:2]
            rows.append(
                {
                    "native_id": p.get("sid") or f.get("id"),
                    "name": p.get("sname"),
                    "lat": lat,
                    "lon": lon,
                    "elevation_m": p.get("elevation"),
                    "site_type": "other",
                    "agency": "NWS/HADS via IEM",
                    "state": p.get("state"),
                    "active": bool(p.get("online")),
                    "raw_metadata": json.dumps(
                        {k: p.get(k) for k in ("archive_begin", "archive_end", "time_domain", "wfo", "tzname",
                                               "climate_site", "ncdc81", "ncei91", "county", "ugc_county")}
                        | {"note": "NWS LID; USGS DCPs overlap the usgs source (link via nwps usgsId)"}
                    ),
                }
            )
        return pd.DataFrame(rows)

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
        today = date.today()
        span = int(self.opt("window_years", 2))
        jobs: list[tuple[str, date, date]] = []
        for r in sites.itertuples(index=False):
            meta = json.loads(r.raw_metadata) if r.raw_metadata else {}
            b = meta.get("archive_begin")
            e = meta.get("archive_end")
            b = date.fromisoformat(b) if b else date(2002, 1, 1)
            e = date.fromisoformat(e) if e else today
            e = min(e, today)
            if since:
                last = self.ledger.last_window_end(self.name, self.uid(r.native_id))
                b = max(b, since, date.fromisoformat(last[:10]) if last else b)
            cur = b
            while cur <= e:
                stop = min(date(cur.year + span, 1, 1) - timedelta(days=1), e)
                jobs.append((r.native_id, cur, stop))
                cur = stop + timedelta(days=1)

        def one(j) -> int:
            sid, a, b = j
            params = {"network": "NM_DCP", "stations": sid, "sts": f"{a.isoformat()}T00:00Z",
                      "ets": f"{b.isoformat()}T23:59Z", "format": "csv"}
            art = self.get(HADS, params=params, kind="data", site_uid=self.uid(sid),
                           window=(a.isoformat(), b.isoformat()), refresh=refresh or b >= today - timedelta(days=7))
            summ.n_requests += 1
            if art.from_cache:
                summ.n_cached += 1
                if self.already_written(art):
                    return 0
            df = self.normalize(art)
            if df is None or df.empty:
                return 0
            n = self.write_obs(df, tag=f"{sid}-{a.year}")
            self.ledger.set_rows(art.request_key, n)
            return n

        res = self.parallel(one, jobs, desc="station-windows")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(jobs) - len(res)
        return summ

    def normalize(self, artifact) -> pd.DataFrame | None:
        text = artifact.read_text()
        if not text.startswith("station,"):
            return None
        df = pd.read_csv(io.StringIO(text), dtype=str)
        if df.empty:
            return None
        ts = pd.to_datetime(df["utc_valid"], errors="coerce", utc=True)
        frames = []
        for col in df.columns:
            if col in ("station", "utc_valid") or len(col) < 6:
                continue
            pe, dur, ext = col[:2], col[2], col[5] if len(col) > 5 else "Z"
            interval = DUR_INTERVAL.get(dur, "irregular")
            stat = EXT_STAT.get(ext)
            if stat is None:
                stat = "total" if pe in TOTAL_PE and dur != "I" else ("accumulated" if pe == "PC" else "instantaneous")
            if interval == "instant":
                interval = "15min" if pe in ("HG", "QR", "HP", "LS", "PC") else "irregular"
            v = pd.to_numeric(df[col], errors="coerce")
            sub = pd.DataFrame(
                {
                    "site_uid": self.name + ":" + df["station"],
                    "datetime_utc": ts,
                    "value": v,
                    "qualifier": col,
                    "source_param": pe,
                    "statistic": stat,
                    "interval": interval,
                    "utc_offset_min": -420,
                }
            )
            frames.append(sub[sub["value"].notna()])
        if not frames:
            return None
        out = pd.concat(frames, ignore_index=True)
        # daily-duration values: keep the SHEF valid time (usually 12Z) but tag interval daily
        return self.xw.apply(out, self.name)
