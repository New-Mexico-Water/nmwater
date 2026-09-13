"""NM Interstate Stream Commission Seven Rivers (Pecos) monitoring network API.

  getMonitoringPoints.ashx           -> 73 points (Artesian GW / Shallow GW / Unknown)
  getAnalytes.ashx                   -> 37 analytes
  getReadings.ashx?monitoringPointId=&analyteId=&start=<ms>&end=<ms>   (end must be in the past)
  getWaterLevels.ashx?id=<point id>&start=<ms>&end=<ms>  -> dateTime (ms), depthToWaterFeet, invalid, dry
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.isc_sevenrivers")


@register
class ISCSevenRivers(Source):
    name = "isc_sevenrivers"
    agency = "NM ISC"
    description = "ISC Seven Rivers (Pecos) well network: water levels + chemistry"
    kinds = ("sites", "levels", "chemistry")

    @property
    def base(self) -> str:
        return (self.cfg.base_url or "https://nmisc-wf.gladata.com/api").rstrip("/")

    def _points(self, refresh: bool = False) -> list[dict]:
        art = self.get(f"{self.base}/getMonitoringPoints.ashx", kind="sites", refresh=refresh)
        return art.read_json().get("data") or []

    def _analytes(self, refresh: bool = False) -> list[dict]:
        art = self.get(f"{self.base}/getAnalytes.ashx", kind="analytes", refresh=refresh)
        return art.read_json().get("data") or []

    def discover(self) -> pd.DataFrame:
        pts = self._points(refresh=True)
        an = self._analytes(refresh=True)
        self.store.write_table(pd.DataFrame(an), "reference", self.name, "analytes")
        rows = []
        for p in pts:
            rows.append({
                "native_id": str(p["id"]), "name": p.get("name"), "lat": p.get("latitude"), "lon": p.get("longitude"),
                "elevation_m": (p.get("groundSurfaceElevationFeet") or 0) * 0.3048 or None,
                "site_type": "well", "agency": "NM ISC", "state": "NM", "basin": "Lower Pecos",
                "aquifer": {"Artesian GW": "Roswell Artesian (San Andres)", "Shallow GW": "Pecos Valley alluvium"}.get(p.get("type")),
                "active": True, "raw_metadata": json.dumps(p, default=str),
            })
        return pd.DataFrame(rows)

    @staticmethod
    def _end_ms() -> int:
        return int((time.time() - 86400) * 1000)

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        kinds = opts.get("kinds") or ["levels", "chemistry"]
        summ = FetchSummary(self.name)
        pts = self._points()
        if site_ids:
            pts = [p for p in pts if str(p["id"]) in set(site_ids)]
        if limit:
            pts = pts[:limit]
        start_ms = int(pd.Timestamp(since).timestamp() * 1000) if since else 0
        end_ms = self._end_ms()
        # end in the request key changes daily -> tail refetch each day; acceptable (small).
        if "levels" in kinds:
            def lv(p) -> int:
                art = self.get(f"{self.base}/getWaterLevels.ashx",
                               params={"id": p["id"], "start": start_ms, "end": end_ms}, kind="levels",
                               site_uid=self.uid(str(p["id"])), variable="depthToWaterFeet", refresh=refresh)
                summ.n_requests += 1
                if art.from_cache:
                    summ.n_cached += 1
                    if self.already_written(art):
                        return 0
                df = self.normalize(art)
                n = self.write_obs(df, tag=f"wl-{p['id']}") if df is not None and len(df) else 0
                self.ledger.set_rows(art.request_key, n)
                return n
            res = self.parallel(lv, pts, desc="levels")
            summ.n_rows += int(sum(res))
            summ.n_errors += len(pts) - len(res)
        if "chemistry" in kinds:
            an = self._analytes()
            jobs = [(p, a) for p in pts for a in an]

            def ch(job) -> int:
                p, a = job
                art = self.get(f"{self.base}/getReadings.ashx",
                               params={"monitoringPointId": p["id"], "analyteId": a["id"], "start": start_ms,
                                       "end": end_ms}, kind="chemistry", site_uid=self.uid(str(p["id"])),
                               variable=str(a["id"]), refresh=refresh)
                summ.n_requests += 1
                if art.from_cache:
                    summ.n_cached += 1
                    if self.already_written(art):
                        return 0
                data = (art.read_json() or {}).get("data") or []
                if not data:
                    self.ledger.set_rows(art.request_key, 0)
                    return 0
                df = pd.DataFrame(data)
                df["site_uid"] = self.uid(str(p["id"]))
                df["analyte"] = a["name"]
                df["analyte_id"] = a["id"]
                tcol = next((c for c in df.columns if "date" in c.lower() or "time" in c.lower()), None)
                if tcol:
                    df["datetime_utc"] = pd.to_datetime(df[tcol], unit="ms", errors="coerce", utc=True)
                self.store.append_table(df, "waterquality", self.name, "readings", self.run_id)
                self.ledger.set_rows(art.request_key, len(df))
                return len(df)
            res = self.parallel(ch, jobs, desc="chemistry")
            summ.n_rows += int(sum(res))
            summ.n_errors += len(jobs) - len(res)
        return summ

    def normalize(self, artifact) -> pd.DataFrame | None:
        if artifact.kind != "levels":
            return None
        doc = artifact.read_json() or {}
        data = doc.get("data") or []
        if not data:
            return None
        pid = str((artifact.params or {}).get("id"))
        df = pd.DataFrame(data)
        out = pd.DataFrame({
            "site_uid": self.uid(pid),
            "datetime_utc": pd.to_datetime(df["dateTime"], unit="ms", errors="coerce", utc=True),
            "value": pd.to_numeric(df["depthToWaterFeet"], errors="coerce"),
            "source_param": "depthToWaterFeet",
            "source_unit": "ft",
            "qualifier": df.apply(lambda r: "|".join(k for k in ("invalid", "dry", "noMeasurementTaken") if r.get(k)) or None, axis=1),
            "utc_offset_min": None,
            "statistic": "instantaneous",
            "interval": "irregular",
        })
        out = out[out["value"].notna() & out["datetime_utc"].notna()]
        out = out[~out["qualifier"].fillna("").str.contains("invalid")]
        return self.xw.apply(out, self.name)
