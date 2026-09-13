"""Synoptic Data (MesoWest) API: RAWS fire-weather stations, DOT road-weather, and other
mesonets reporting in and around New Mexico.

  GET /v2/stations/metadata?state=NM&complete=1&sensorvars=1&token=...
  GET /v2/stations/timeseries?stid=...&start=YYYYmmddHHMM&end=...&token=...&units=english&obtimezone=utc
  GET /v2/networks?token=...

Hard limit: 100,000 station-hours per timeseries request, so windows are sized from the number
of stations in the batch. Requires SYNOPTIC_TOKEN (free research tier at customer.synopticdata.com);
the source skips cleanly when the token is absent.

The ZiaMet/NMSU network is excluded by default because nmwater/sources/ziamet.py pulls it at
native 5-minute resolution directly from NMSU; set `exclude_networks: []` in config to include it.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.synoptic")

BASE = "https://api.synopticdata.com/v2"
# Synoptic variable name -> source_param in catalog/crosswalk.d/synoptic.csv
VARS = {
    "air_temp": "air_temp",
    "relative_humidity": "relative_humidity",
    "wind_speed": "wind_speed",
    "wind_direction": "wind_direction",
    "wind_gust": "wind_gust",
    "solar_radiation": "solar_radiation",
    "precip_accum": "precip_accum",
    "precip_accum_since_local_midnight": "precip_since_midnight",
    "snow_depth": "snow_depth",
    "snow_water_equiv": "snow_water_equiv",
    "soil_moisture": "soil_moisture",
    "soil_temp": "soil_temp",
    "dew_point_temperature": "dew_point",
}
ACCUM_VARS = {"precip_accum", "precip_since_midnight"}
MAX_STATION_HOURS = 100_000


@register
class Synoptic(Source):
    name = "synoptic"
    agency = "Synoptic Data / MesoWest"
    description = "RAWS and other NM mesonet stations (token required)"
    requires_tokens = ("SYNOPTIC_TOKEN",)
    kinds = ("networks", "sites", "data")

    @property
    def token(self) -> str:
        return self.settings.tokens["SYNOPTIC_TOKEN"]

    def _networks(self) -> dict[str, str]:
        art = self.get(f"{BASE}/networks", params={"token": self.token}, kind="networks")
        d = art.read_json()
        return {str(n["ID"]): n.get("SHORTNAME") or n.get("NAME") or "" for n in d.get("MNET", [])}

    def discover(self) -> pd.DataFrame:
        self.check_tokens()
        nets = self._networks()
        w, s, e, n = self.scope.bbox_buffered
        frames = []
        for params in (
            {"state": "NM", "complete": 1, "sensorvars": 1, "status": "active"},
            {"state": "NM", "complete": 1, "sensorvars": 1, "status": "inactive"},
            {"bbox": f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}", "complete": 1, "sensorvars": 1},
        ):
            try:
                art = self.get(f"{BASE}/stations/metadata", params={**params, "token": self.token},
                               kind="sites", refresh=True)
                frames.extend(art.read_json().get("STATION", []) or [])
            except Exception as ex:  # noqa: BLE001
                log.warning("synoptic metadata %s: %s", params.get("status") or "bbox", str(ex)[:120])
        if not frames:
            return pd.DataFrame()
        seen, rows = set(), []
        excl = set(self.opt("exclude_networks", ["ZIAMET", "NMSU"]))
        for st in frames:
            stid = st.get("STID")
            if not stid or stid in seen:
                continue
            seen.add(stid)
            net = nets.get(str(st.get("MNET_ID")), "")
            if net.upper() in excl:
                continue
            lat, lon = st.get("LATITUDE"), st.get("LONGITUDE")
            try:
                lat, lon = float(lat), float(lon)
            except (TypeError, ValueError):
                lat = lon = None
            if not self.scope.in_bbox(lat, lon):
                continue
            elev = st.get("ELEVATION")
            rows.append(
                {
                    "native_id": stid,
                    "name": st.get("NAME"),
                    "lat": lat,
                    "lon": lon,
                    "elevation_m": float(elev) * 0.3048 if elev not in (None, "") else None,
                    "site_type": "met",
                    "agency": net or "Synoptic",
                    "state": st.get("STATE"),
                    "county_fips": None,
                    "active": str(st.get("STATUS", "")).upper() == "ACTIVE",
                    "raw_metadata": json.dumps(
                        {
                            "mnet_id": st.get("MNET_ID"), "network": net,
                            "period_of_record": st.get("PERIOD_OF_RECORD"),
                            "sensor_variables": list((st.get("SENSOR_VARIABLES") or {}).keys()),
                            "timezone": st.get("TIMEZONE"), "nwszone": st.get("NWSZONE"),
                        },
                        default=str,
                    ),
                }
            )
        return pd.DataFrame(rows)

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        self.check_tokens()
        summ = FetchSummary(self.name)
        sites = self.sites()
        if sites.empty:
            summ.notes.append("run `nmwater discover synoptic` first")
            return summ
        if site_ids:
            sites = sites[sites["native_id"].isin(site_ids)]
        if limit:
            sites = sites.head(limit)
        batch_n = int(self.opt("batch_stations", 20))
        ids = list(sites["native_id"])
        # window length so that stations x hours stays under the API cap
        win_hours = max(24, MAX_STATION_HOURS // max(1, batch_n))
        win = timedelta(hours=win_hours)
        today = datetime.now(timezone.utc)
        jobs = []
        for i in range(0, len(ids), batch_n):
            batch = ids[i:i + batch_n]
            por_start = self._batch_start(sites, batch, since)
            cur = por_start
            while cur < today:
                stop = min(cur + win, today)
                jobs.append((batch, cur, stop))
                cur = stop
        log.info("synoptic: %d windows over %d stations", len(jobs), len(ids))

        def one(job) -> int:
            batch, b, e = job
            params = {
                "stid": ",".join(batch), "start": b.strftime("%Y%m%d%H%M"), "end": e.strftime("%Y%m%d%H%M"),
                "token": self.token, "units": "english", "obtimezone": "utc",
                "vars": ",".join(VARS), "output": "json",
            }
            art = self.get(f"{BASE}/stations/timeseries", params=params, kind="data",
                           window=(b.date().isoformat(), e.date().isoformat()), refresh=refresh)
            summ.n_requests += 1
            if art.from_cache:
                summ.n_cached += 1
                if self.already_written(art):
                    return 0
            df = self.normalize(art)
            n = self.write_obs(df, tag=f"{batch[0]}-{b:%Y%m%d}") if df is not None else 0
            self.ledger.set_rows(art.request_key, n)
            return n

        res = self.parallel(one, jobs, desc="timeseries")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(jobs) - len(res)
        return summ

    def _batch_start(self, sites: pd.DataFrame, batch: list[str], since: date | None) -> datetime:
        if since is not None:
            return datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
        starts = []
        for meta in sites.loc[sites["native_id"].isin(batch), "raw_metadata"]:
            try:
                por = (json.loads(meta) or {}).get("period_of_record") or {}
                s = por.get("start")
                if s:
                    starts.append(pd.to_datetime(s, utc=True).to_pydatetime())
            except Exception:  # noqa: BLE001
                continue
        return min(starts) if starts else datetime(1997, 1, 1, tzinfo=timezone.utc)

    def normalize(self, artifact) -> pd.DataFrame | None:
        d = artifact.read_json()
        stations = d.get("STATION") or []
        frames = []
        for st in stations:
            stid = st.get("STID")
            obs = st.get("OBSERVATIONS") or {}
            times = obs.get("date_time")
            if not stid or not times:
                continue
            ts = pd.to_datetime(pd.Series(times), errors="coerce", utc=True)
            for key, series in obs.items():
                if key == "date_time" or not isinstance(series, list):
                    continue
                base = key.rsplit("_set_", 1)[0]
                param = VARS.get(base)
                if not param or len(series) != len(ts):
                    continue
                vals = pd.to_numeric(pd.Series(series), errors="coerce")
                if base in ACCUM_VARS:
                    # accumulators reset at station resets/midnight; difference and drop resets
                    vals = vals.diff()
                    vals = vals.where(vals >= 0)
                sub = pd.DataFrame(
                    {
                        "site_uid": self.uid(stid),
                        "datetime_utc": ts,
                        "value": vals,
                        "qualifier": key,
                        "source_param": param,
                        "statistic": None,
                        "interval": None,
                        "utc_offset_min": None,
                    }
                )
                frames.append(sub[sub["value"].notna() & sub["datetime_utc"].notna()])
        if not frames:
            return None
        return self.xw.apply(pd.concat(frames, ignore_index=True), self.name)
