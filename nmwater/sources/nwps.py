"""NWS National Water Prediction Service API: gauge list (NWS LID <-> USGS id crosswalk),
30-day observed stage/flow, and official forecasts.

  https://api.water.noaa.gov/nwps/v1/gauges            (all ~12,900 gauges; filtered client-side)
  https://api.water.noaa.gov/nwps/v1/gauges/{lid}/stageflow
"""

from __future__ import annotations

import json
import logging
from datetime import date

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.nwps")

BASE = "https://api.water.noaa.gov/nwps/v1"


@register
class NWPS(Source):
    name = "nwps"
    agency = "NOAA NWS"
    description = "NWPS gauges (NWS LID/USGS crosswalk), 30-day obs, and forecasts for NM"

    def _gauges(self) -> list[dict]:
        w, s, e, n = self.scope.bbox_buffered
        # bbox-limited request first (smaller); fall back to the full list
        for params in ({"bbox.xmin": w, "bbox.ymin": s, "bbox.xmax": e, "bbox.ymax": n}, None):
            try:
                art = self.get(f"{BASE}/gauges", params=params, kind="meta", refresh=True)
                d = art.read_json()
                g = d.get("gauges", d if isinstance(d, list) else [])
                if g:
                    return g
            except Exception as ex:  # noqa: BLE001
                log.warning("nwps gauges request failed (%s): %s", params, ex)
        return []

    def discover(self) -> pd.DataFrame:
        rows = []
        for g in self._gauges():
            lat, lon = g.get("latitude"), g.get("longitude")
            st = (g.get("state") or {}).get("abbreviation")
            if st != self.scope.state_abbr and not self.scope.in_bbox(lat, lon):
                continue
            rows.append(
                {
                    "native_id": g.get("lid"),
                    "name": g.get("name"),
                    "lat": lat,
                    "lon": lon,
                    "site_type": "stream",
                    "agency": "NWS",
                    "state": st,
                    "active": True,
                    "raw_metadata": json.dumps(
                        {
                            "usgsId": g.get("usgsId"),
                            "rfc": (g.get("rfc") or {}).get("abbreviation"),
                            "wfo": (g.get("wfo") or {}).get("abbreviation"),
                            "reachId": g.get("reachId"),
                            "pedts": g.get("pedts"),
                            "flood": g.get("flood"),
                        },
                        default=str,
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
        today = date.today().isoformat()

        def one(lid: str) -> int:
            # observations roll daily: always refresh, key by day so each day's snapshot is archived
            art = self.get(f"{BASE}/gauges/{lid}/stageflow", kind="data", site_uid=self.uid(lid),
                           refresh=True, key_extra=today, window=(today, today))
            summ.n_requests += 1
            d = art.read_json()
            obs = self._series(lid, d.get("observed"), "observed")
            n = self.write_obs(obs, tag=lid) if obs is not None else 0
            fc = self._series(lid, d.get("forecast"), "forecast", raw=True)
            if fc is not None and len(fc):
                self.store.append_table(fc, "forecasts", self.name, "stageflow_forecast", self.run_id)
            self.ledger.set_rows(art.request_key, n)
            return n

        ids = list(sites["native_id"])
        res = self.parallel(one, ids, desc="gauges")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(ids) - len(res)
        return summ

    def _series(self, lid: str, block: dict | None, kind: str, raw: bool = False) -> pd.DataFrame | None:
        if not block or not block.get("data"):
            return None
        data = pd.DataFrame(block["data"])
        if data.empty or "validTime" not in data.columns:
            return None
        ts = pd.to_datetime(data["validTime"], errors="coerce", utc=True)
        p_name = (block.get("primaryName") or "").lower()
        s_name = (block.get("secondaryName") or "").lower()
        p_unit = block.get("primaryUnits") or ""
        s_unit = block.get("secondaryUnits") or ""
        if raw:
            out = pd.DataFrame(
                {
                    "site_uid": self.uid(lid), "lid": lid, "kind": kind, "valid_time": ts,
                    "generated_time": pd.to_datetime(data.get("generatedTime"), errors="coerce", utc=True),
                    "issued_time": block.get("issuedTime"), "pedts": block.get("pedts"),
                    "primary_name": p_name, "primary_units": p_unit,
                    "primary": pd.to_numeric(data.get("primary"), errors="coerce"),
                    "secondary_name": s_name, "secondary_units": s_unit,
                    "secondary": pd.to_numeric(data.get("secondary"), errors="coerce"),
                }
            )
            return out
        frames = []
        for col, nm, unit in (("primary", p_name, p_unit), ("secondary", s_name, s_unit)):
            if col not in data.columns:
                continue
            param = f"{nm}:{unit}".lower()
            sub = pd.DataFrame(
                {
                    "site_uid": self.uid(lid),
                    "datetime_utc": ts,
                    "value": pd.to_numeric(data[col], errors="coerce"),
                    "qualifier": "nwps:" + str(block.get("pedts") or ""),
                    "source_param": param,
                    "statistic": "instantaneous",
                    "interval": "15min",
                    "utc_offset_min": -420,
                }
            )
            # NWPS encodes missing as -999 (kcfs: -999000 after conversion would leak through)
            frames.append(sub[sub["value"].notna() & (sub["value"] > -998)])
        if not frames:
            return None
        out = pd.concat(frames, ignore_index=True)
        return self.xw.apply(out, self.name)

    def normalize(self, artifact) -> pd.DataFrame | None:
        lid = artifact.url.rstrip("/").split("/")[-2]
        d = artifact.read_json()
        return self._series(lid, d.get("observed"), "observed")
