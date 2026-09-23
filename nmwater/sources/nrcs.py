"""NRCS AWDB REST API: SNOTEL, SCAN, snow courses, COOP/MPRC precipitation; forecasts.

    {base}/stations?stationTriplets=*:NM:*&returnStationElements=true&...   (no stateCds param!)
    {base}/stations?hucs=1301*,1408*                                         (CO headwater stations)
    {base}/data?stationTriplets=&elements=&duration=DAILY|HOURLY|SEMIMONTHLY|MONTHLY&beginDate=&endDate=&returnFlags=true
    {base}/forecasts?stationTriplets=...
Times are station local standard time (MST for this region); daily values are calendar dates.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta

import pandas as pd

from ..core.geo import basin_from_huc
from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.nrcs")

NET_TYPE = {"SNTL": "snow", "SNTLT": "snow", "SNOW": "snow", "SCAN": "met", "COOP": "met", "MPRC": "met",
            "MSNT": "snow", "CSCAN": "met", "USGS": "stream", "BOR": "reservoir"}
DUR_INTERVAL = {"DAILY": "daily", "HOURLY": "hourly", "SEMIMONTHLY": "semimonthly", "MONTHLY": "monthly",
                "WATER_YEAR": "water_year", "CALENDAR_YEAR": "annual"}
DEPTH_ELEMENTS = {"SMS", "STO", "SMV", "SMN", "SMX", "STV", "STN", "STX"}
MST_OFFSET_MIN = -420
DAILY_WINDOW_YEARS = 10
HOURLY_WINDOW_YEARS = 1


@register
class NRCS(Source):
    name = "nrcs"
    agency = "USDA NRCS"
    description = "AWDB SNOTEL/SCAN/snow course/COOP data and water supply forecasts (1971-)"
    kinds = ("sites", "daily", "hourly", "periodic", "forecasts")

    @property
    def base(self) -> str:
        return self.cfg.base_url or "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"

    # ---------------------------------------------------------------- discovery
    def _stations(self, params: dict, refresh: bool) -> list[dict]:
        base = {"returnStationElements": "true", "returnReservoirMetadata": "true",
                "returnForecastPointMetadata": "true", "activeOnly": "false"}
        base.update(params)
        art = self.get(f"{self.base}/stations", params=base, kind="sites", refresh=refresh)
        d = art.read_json()
        return d if isinstance(d, list) else []

    def _all_stations(self, refresh: bool) -> list[dict]:
        excl = set(self.opt("exclude_networks", ["USGS", "BOR"]))
        st = self._stations({"stationTriplets": f"*:{self.scope.state_abbr}:*"}, refresh)
        hucs = self.opt("extra_hucs", ["1301", "1408"])
        if hucs:
            st += self._stations({"hucs": ",".join(f"{h}*" for h in hucs)}, refresh)
        seen, out = set(), []
        for s in st:
            t = s["stationTriplet"]
            if t in seen or s.get("networkCode") in excl:
                continue
            seen.add(t)
            out.append(s)
        return out

    def discover(self) -> pd.DataFrame:
        st = self._all_stations(refresh=True)
        rows = []
        for s in st:
            huc = str(s.get("huc") or "")
            net = s.get("networkCode")
            rows.append({
                "native_id": s["stationTriplet"], "name": s.get("name"),
                "lat": s.get("latitude"), "lon": s.get("longitude"),
                "elevation_m": s["elevation"] * 0.3048 if s.get("elevation") is not None else None,
                "site_type": "reservoir" if s.get("reservoirMetadata") else NET_TYPE.get(net, "met"),
                "agency": f"NRCS {net}", "state": s.get("stateCode"),
                "huc8": huc[:8] or None, "basin": basin_from_huc(huc),
                "active": (s.get("endDate") or "2100") >= "2099",
                "raw_metadata": json.dumps({k: v for k, v in s.items() if k not in ("latitude", "longitude", "name")},
                                           default=str),
            })
        return pd.DataFrame(rows)

    # ---------------------------------------------------------------- fetch
    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        kinds = opts.get("kinds") or ["daily", "hourly", "periodic", "forecasts"]
        summ = FetchSummary(self.name)
        st = self._all_stations(refresh=False)
        if site_ids:
            st = [s for s in st if s["stationTriplet"] in site_ids]
        if limit:
            st = st[:limit]
        hourly_el = set(self.opt("hourly_elements", ["WTEQ", "SNWD", "PREC", "TOBS", "SMS", "STO", "PRCP"]))
        jobs = []
        for s in st:
            els = s.get("stationElements") or []
            by_dur: dict[str, dict] = {}
            for e in els:
                code = e["elementCode"]
                if self.xw.lookup(self.name, code) is None and self.xw.lookup(self.name, f"{code}:{e.get('heightDepth')}") is None:
                    continue
                d = e["durationName"]
                if d == "HOURLY" and code not in hourly_el:
                    continue
                if d not in DUR_INTERVAL:
                    continue
                b = by_dur.setdefault(d, {"codes": set(), "begin": "2100", "end": "1800"})
                b["codes"].add(code)
                b["begin"] = min(b["begin"], e.get("beginDate") or "2100")
                b["end"] = max(b["end"], e.get("endDate") or "1800")
            for d, b in by_dur.items():
                kind = {"DAILY": "daily", "HOURLY": "hourly"}.get(d, "periodic")
                if kind not in kinds:
                    continue
                jobs.append((s["stationTriplet"], d, sorted(b["codes"]), b["begin"][:10], b["end"][:10]))
        today = date.today()

        def one(j) -> int:
            trip, dur, codes, begin, end = j
            b = max(date.fromisoformat(begin), date(1900, 1, 1))
            e = min(date.fromisoformat(end), today)
            if since and not opts.get("revise"):   # manual --since: catch up; update: honour since
                last = self.ledger.last_window_end(self.name, self.uid(trip), dur)
                b = max(b, since, date.fromisoformat(last[:10]) if last else b)
            elif since:
                b = max(b, since)
            yrs = HOURLY_WINDOW_YEARS if dur == "HOURLY" else DAILY_WINDOW_YEARS
            total = 0
            cur = b
            while cur <= e:
                stop = min(date(cur.year + yrs, 1, 1) - timedelta(days=1), e)
                params = {"stationTriplets": trip, "elements": ",".join(codes), "duration": dur,
                          "beginDate": cur.isoformat(), "endDate": stop.isoformat(), "returnFlags": "true"}
                art = self.get(f"{self.base}/data", params=params, kind=dur.lower(), site_uid=self.uid(trip),
                               variable=dur, window=(cur.isoformat(), stop.isoformat()), refresh=refresh or bool(since))
                summ.n_requests += 1
                if art.from_cache:
                    summ.n_cached += 1
                if not (art.from_cache and self.already_written(art)):
                    df = self.normalize(art)
                    n = self.write_obs(df, tag=trip.replace(":", "_")) if df is not None else 0
                    self.ledger.set_rows(art.request_key, n)
                    total += n
                cur = stop + timedelta(days=1)
            return total

        res = self.parallel(one, jobs, desc="data")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(jobs) - len(res)
        if "forecasts" in kinds:
            summ.add(self.fetch_forecasts(st, refresh or bool(since)))
        return summ

    def fetch_forecasts(self, st: list[dict], refresh: bool) -> FetchSummary:
        summ = FetchSummary(self.name)
        fps = [s["stationTriplet"] for s in st if s.get("forecastPoint")]
        rows = []
        for i in range(0, len(fps), 10):
            batch = fps[i:i + 10]
            art = self.get(f"{self.base}/forecasts", params={"stationTriplets": ",".join(batch),
                           "beginPublicationDate": "1900-01-01"}, kind="forecasts", refresh=refresh)
            summ.n_requests += 1
            for fp in art.read_json() or []:
                for d in fp.get("data", []):
                    fv = d.get("forecastValues") or {}
                    rows.append({
                        "station_triplet": fp["stationTriplet"], "forecast_point": fp.get("forecastPointName"),
                        "element": d.get("elementCode"), "period_start": (d.get("forecastPeriod") or [None, None])[0],
                        "period_end": (d.get("forecastPeriod") or [None, None])[1], "status": d.get("forecastStatus"),
                        "issue_date": d.get("issueDate"), "publication_date": d.get("publicationDate"),
                        "period_normal": d.get("periodNormal"), "unit": d.get("unitCode"),
                        **{f"p{k}": v for k, v in fv.items()},
                    })
        if rows:
            self.store.write_table(pd.DataFrame(rows), "forecasts", self.name, "water_supply_forecasts")
            summ.n_rows = len(rows)
        return summ

    # ---------------------------------------------------------------- normalize
    def normalize(self, artifact) -> pd.DataFrame | None:
        if artifact.kind in ("sites", "forecasts"):
            return None
        d = artifact.read_json()
        if not isinstance(d, list):
            return None
        frames = []
        for st in d:
            trip = st["stationTriplet"]
            for block in st.get("data", []):
                se = block["stationElement"]
                code = se["elementCode"]
                depth = se.get("heightDepth")
                param = f"{code}:{depth}" if (code in DEPTH_ELEMENTS and depth is not None) else code
                dur = se.get("durationName", "DAILY")
                vals = block.get("values") or []
                if not vals:
                    continue
                v = pd.DataFrame(vals)
                # Period-start convention for aggregated durations
                if "date" in v.columns:
                    raw_ts = pd.to_datetime(v["date"], errors="coerce")
                elif "collectionDate" in v.columns:  # SEMIMONTHLY
                    raw_ts = pd.to_datetime(v["collectionDate"], errors="coerce")
                elif "month" in v.columns:  # MONTHLY
                    raw_ts = pd.to_datetime(v["year"].astype(str) + "-" + v["month"].astype(str).str.zfill(2) + "-01",
                                            errors="coerce")
                elif "year" in v.columns and dur == "WATER_YEAR":
                    raw_ts = pd.to_datetime((v["year"].astype(int) - 1).astype(str) + "-10-01", errors="coerce")
                elif "year" in v.columns:
                    raw_ts = pd.to_datetime(v["year"].astype(str) + "-01-01", errors="coerce")
                else:
                    continue
                if dur == "HOURLY":
                    ts = raw_ts.dt.tz_localize("UTC") - pd.Timedelta(minutes=MST_OFFSET_MIN)
                    offset = MST_OFFSET_MIN
                else:
                    ts = pd.to_datetime(raw_ts.dt.strftime("%Y-%m-%d"), utc=True)
                    offset = None
                frames.append(pd.DataFrame({
                    "site_uid": self.uid(trip), "datetime_utc": ts,
                    "value": pd.to_numeric(v["value"], errors="coerce"),
                    "qualifier": (v.get("qcFlag", pd.Series("", index=v.index)).fillna("").astype(str) + "|"
                                  + v.get("qaFlag", pd.Series("", index=v.index)).fillna("").astype(str)).str.strip("|"),
                    "source_param": param, "source_unit": se.get("storedUnitCode"),
                    "interval": DUR_INTERVAL.get(dur, "irregular"), "utc_offset_min": offset,
                }))
        if not frames:
            return None
        out = pd.concat(frames, ignore_index=True)
        out = out[out["value"].notna() & out["datetime_utc"].notna()]
        return self.xw.apply(out, self.name)
