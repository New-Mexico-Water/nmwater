"""USACE CWMS Data API (CDA), Albuquerque District (office SPA).

    {base}/locations?office=SPA                              -> all SPA locations (latin-1 JSON)
    {base}/catalog/TIMESERIES?office=SPA&page-size=20000&includeExtents=true
    {base}/timeseries?office=SPA&name=<tsid>&begin=&end=&unit=EN&page-size=-1

Time-series id: Location.Parameter.Type.Interval.Duration.Version, values [epoch_ms, value, quality].
Long windows fail with "Request is taking too long", so series are pulled in interval-dependent
windows (sub-hourly 1 yr, hourly 5 yr, daily 20 yr) that halve on that error.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.usace_cwms")

HDR = {"Accept": "application/json;version=2"}
MT = ZoneInfo("US/Mountain")

LOC_TYPE_MAP = {
    "stream gauge": "stream", "stream": "stream", "diversion gauge": "diversion", "corps reservoir": "reservoir",
    "reservoir gauge": "reservoir", "reservoir": "reservoir", "raws": "met", "weather station": "met",
    "snotel": "snow", "computed": "other", "basin": "area",
}
TYPE_STAT = {"inst": "instantaneous", "ave": "mean", "max": "max", "min": "min", "total": "total",
             "const": "other", "cum": "accumulated"}
INTERVAL_MAP = {"1minute": "instant", "5minutes": "5min", "15minutes": "15min", "30minutes": "30min",
                "1hour": "hourly", "~1hour": "hourly", "1day": "daily", "~1day": "daily", "1week": "weekly",
                "1month": "monthly", "~1month": "monthly", "1year": "annual", "~1year": "annual", "0": "irregular"}
WINDOW_DAYS = {"5min": 366, "15min": 366, "30min": 366, "instant": 366, "hourly": 5 * 366, "daily": 20 * 366,
               "weekly": 40 * 366, "monthly": 100 * 366, "annual": 200 * 366, "irregular": 20 * 366}
EARLIEST = date(1930, 1, 1)


def parse_tsid(tsid: str) -> dict[str, str]:
    parts = tsid.split(".")
    keys = ["location", "parameter", "type", "interval", "duration", "version"]
    d = dict(zip(keys, parts + [""] * (6 - len(parts))))
    return d


@register
class USACECWMS(Source):
    name = "usace_cwms"
    agency = "USACE"
    description = "CWMS Data API: SPA reservoirs, stream and MRGCD diversion gauges (1982-)"
    kinds = ("sites", "catalog", "data")

    @property
    def base(self) -> str:
        return self.cfg.base_url or "https://cwms-data.usace.army.mil/cwms-data"

    @property
    def office(self) -> str:
        return self.opt("office", "SPA")

    def _json(self, art):
        return json.loads(art.read_text(encoding="latin-1"))

    # ---------------------------------------------------------------- discovery
    def _locations(self, refresh: bool) -> list[dict]:
        art = self.get(f"{self.base}/locations", params={"office": self.office}, headers=HDR, kind="sites",
                       refresh=refresh)
        d = self._json(art)
        if isinstance(d, dict):
            d = d.get("locations", {}).get("locations", d.get("locations", []))
        return d

    def _catalog(self, refresh: bool) -> list[dict]:
        art = self.get(f"{self.base}/catalog/TIMESERIES",
                       params={"office": self.office, "page-size": 20000, "includeExtents": "true"},
                       headers=HDR, kind="catalog", refresh=refresh)
        d = self._json(art)
        return d.get("entries", [])

    def discover(self) -> pd.DataFrame:
        locs = self._locations(refresh=True)
        cat = self._catalog(refresh=True)
        by_loc: dict[str, list[dict]] = {}
        for e in cat:
            p = parse_tsid(e["name"])
            ext = (e.get("extents") or [{}])[0]
            by_loc.setdefault(p["location"], []).append(
                {"tsid": e["name"], "units": e.get("units"), "interval": e.get("interval"),
                 "earliest": ext.get("earliest-time"), "latest": ext.get("latest-time")}
            )
        # reference table of all series
        self.store.write_table(pd.DataFrame(
            [{"location": parse_tsid(e["name"])["location"], "tsid": e["name"], "units": e.get("units"),
              "interval": e.get("interval"), "time_zone": e.get("time-zone"),
              "earliest": (e.get("extents") or [{}])[0].get("earliest-time"),
              "latest": (e.get("extents") or [{}])[0].get("latest-time")} for e in cat]),
            "reference", self.name, "timeseries_catalog")
        rows = []
        for l in locs:
            lat, lon = l.get("latitude"), l.get("longitude")
            st = l.get("state-initial")
            if not (self.scope.in_bbox(lat, lon) or st in ("NM",) or l["name"] in by_loc):
                continue
            ltype = (l.get("location-type") or "").lower()
            stype = LOC_TYPE_MAP.get(ltype, "other")
            agency = "USACE"
            if ltype == "diversion gauge":
                agency = "MRGCD via USACE"
            elif ltype == "raws":
                agency = "RAWS via USACE"
            elif ltype == "snotel":
                agency = "NRCS via USACE"
            elev = l.get("elevation")
            if elev is not None and (l.get("elevation-units") or "m").lower() in ("ft", "feet"):
                elev = float(elev) * 0.3048
            rows.append({
                "native_id": l["name"],
                "name": l.get("public-name") or l.get("long-name") or l["name"],
                "lat": lat, "lon": lon, "elevation_m": elev, "site_type": stype, "agency": agency,
                "state": st, "active": l.get("active"),
                "raw_metadata": json.dumps({**{k: v for k, v in l.items() if k not in ("latitude", "longitude")},
                                            "series": by_loc.get(l["name"], [])}, default=str),
            })
        return pd.DataFrame(rows)

    # ---------------------------------------------------------------- fetch
    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        cat = self._catalog(refresh=bool(since) or refresh)
        series = []
        for e in cat:
            p = parse_tsid(e["name"])
            if site_ids and p["location"] not in site_ids:
                continue
            if self.xw.lookup(self.name, p["parameter"]) is None:
                continue
            ext = (e.get("extents") or [{}])[0]
            series.append((e["name"], p, ext.get("earliest-time"), ext.get("latest-time")))
        if limit:
            locs = []
            for s in series:
                if s[1]["location"] not in locs:
                    locs.append(s[1]["location"])
            keep = set(locs[:limit])
            series = [s for s in series if s[1]["location"] in keep]
        today = date.today() + timedelta(days=1)

        def one(s) -> int:
            tsid, p, earliest, latest = s
            interval = INTERVAL_MAP.get(p["interval"].lower(), "irregular")
            b = date.fromisoformat(earliest[:10]) if earliest else EARLIEST
            e = min(date.fromisoformat(latest[:10]) + timedelta(days=1), today) if latest else today
            if since:
                last = self.ledger.last_window_end(self.name, self.uid(p["location"]), tsid)
                b = max(b, since, date.fromisoformat(last[:10]) - timedelta(days=1) if last else b)
            total = 0
            cur = b
            win = WINDOW_DAYS.get(interval, 366)
            while cur < e:
                stop = min(cur + timedelta(days=win), e)
                total += self._fetch_window(tsid, p, cur, stop, interval, summ, refresh or bool(since))
                cur = stop
            return total

        res = self.parallel(one, series, desc="series")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(series) - len(res)
        return summ

    def _fetch_window(self, tsid, p, b: date, e: date, interval: str, summ: FetchSummary, refresh: bool) -> int:
        params = {"office": self.office, "name": tsid, "begin": f"{b.isoformat()}T00:00:00Z",
                  "end": f"{e.isoformat()}T00:00:00Z", "unit": "EN", "page-size": -1}
        url = f"{self.base}/timeseries"
        total = 0
        page = None
        for _ in range(500):
            if page:
                params = {**params, "page": page, "page-size": 20000}
            try:
                art = self.get(url, params=params, headers=HDR, kind="data", site_uid=self.uid(p["location"]),
                               variable=tsid, window=(b.isoformat(), e.isoformat()), refresh=refresh)
            except httpx.HTTPStatusError as ex:
                body = ex.response.text[:200] if ex.response is not None else ""
                if "taking too long" in body and (e - b).days > 30:
                    mid = b + (e - b) / 2
                    return (self._fetch_window(tsid, p, b, mid, interval, summ, refresh)
                            + self._fetch_window(tsid, p, mid, e, interval, summ, refresh))
                raise
            summ.n_requests += 1
            if art.from_cache:
                summ.n_cached += 1
            d = self._json(art)
            if "values" not in d:
                if "taking too long" in json.dumps(d) and (e - b).days > 30:
                    mid = b + (e - b) / 2
                    return (self._fetch_window(tsid, p, b, mid, interval, summ, True)
                            + self._fetch_window(tsid, p, mid, e, interval, summ, True))
                log.warning("%s: no values for %s %s..%s: %s", self.name, tsid, b, e, str(d)[:150])
                return total
            if not (art.from_cache and self.already_written(art)):
                df = self._normalize_json(d, p, interval)
                n = self.write_obs(df, tag=p["location"]) if df is not None else 0
                self.ledger.set_rows(art.request_key, n)
                total += n
            page = d.get("next-page")
            if not page:
                break
        return total

    def _normalize_json(self, d: dict, p: dict, interval: str) -> pd.DataFrame | None:
        vals = d.get("values") or []
        if not vals:
            return None
        arr = pd.DataFrame(vals, columns=["t", "value", "quality"][: len(vals[0])])
        ts = pd.to_datetime(arr["t"], unit="ms", utc=True)
        unit = d.get("units")
        if interval in ("daily", "weekly", "monthly", "annual"):
            local = ts.dt.tz_convert(MT)
            dt_utc = pd.to_datetime(local.dt.strftime("%Y-%m-%d"), utc=True)
            offset = None
        else:
            dt_utc = ts
            offset = ts.dt.tz_convert(MT).map(lambda x: int(x.utcoffset().total_seconds() // 60))
        out = pd.DataFrame({
            "site_uid": self.uid(p["location"]),
            "datetime_utc": dt_utc,
            "value": pd.to_numeric(arr["value"], errors="coerce"),
            "qualifier": (arr["quality"].astype(str) if "quality" in arr else "") + "|" + p.get("version", ""),
            "source_param": p["parameter"],
            "source_unit": unit,
            "statistic": TYPE_STAT.get(p["type"].lower()),
            "interval": interval,
            "utc_offset_min": offset,
        })
        out = out[out["value"].notna()]
        raw_values = out["value"].copy()
        out = self.xw.apply(out, self.name)
        # crosswalk factors assume EN units (unit=EN requested); if the API returned SI anyway, convert
        si = {"m": 3.28084, "cms": 35.3146667, "m3": 1 / 1233.48184, "mm": 1 / 25.4}
        if unit in si:
            out["value"] = out["value"] * si[unit]
        elif unit == "C" and p["parameter"].startswith("Temp"):
            out["value"] = raw_values.loc[out.index]  # already Celsius; undo the F->C crosswalk conversion
        return out

    def normalize(self, artifact) -> pd.DataFrame | None:
        if artifact.kind != "data":
            return None
        d = self._json(artifact)
        p = parse_tsid(d.get("name", artifact.params.get("name", "") if artifact.params else ""))
        interval = INTERVAL_MAP.get(p["interval"].lower(), "irregular")
        return self._normalize_json(d, p, interval)
