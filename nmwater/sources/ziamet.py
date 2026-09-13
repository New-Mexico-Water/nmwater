"""ZiaMet / New Mexico Climate Center (NMSU) mesonet.

Hosts: duststorm.nmsu.edu (primary), nmcc.nmsu.edu, weather2.nmsu.edu, weather.nmsu.edu,
ziamet.org - the same Django app; the NMSU server is intermittently unreachable, so the
module rotates hosts on connection failure.

Kinds:
  sites  - /ziamet/stations/ table (name, lat, lon, elevation ft) + station page details
  feed   - /ziamet/station/feed/sfwx/std/csv/iu/5/<sid>/ : 5-minute raw CSV, rolling window
           (header row + units row). Archived on every run with a timestamped key.
  daily  - POST forms /ziamet/station/{sfwx,dret,tmps}/dly/<sid>/ (daily surface weather,
           reference ET, soil temperature). CSRF token is sent as the X-CSRFToken header so
           the archived request key stays stable across sessions.
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import UTC, date, datetime

import httpx
import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.ziamet")

HOSTS = ["https://duststorm.nmsu.edu", "https://nmcc.nmsu.edu", "https://weather2.nmsu.edu",
         "https://weather.nmsu.edu"]
LOCAL_TZ = "America/Denver"

# feed column header -> source_param (units row is checked at parse time)
FEED_COLS = {
    "air temperature": "feed_airtemp",
    "dewpoint": "feed_dewpoint",
    "rh": "feed_rh",
    "3m max wind speed": "feed_wsmax",
    "3m mean wind speed": "feed_wsmean",
    "3m wind direction": "feed_wdir",
    "rain": "feed_rain",
    "solar radiation": "feed_solar",
}
# daily product column header (lowercased, units stripped) -> source_param
DAILY_COLS = {
    "max air temperature": "dly_tmax", "maximum air temperature": "dly_tmax", "air temperature max": "dly_tmax",
    "min air temperature": "dly_tmin", "minimum air temperature": "dly_tmin", "air temperature min": "dly_tmin",
    "mean air temperature": "dly_tavg", "average air temperature": "dly_tavg", "air temperature mean": "dly_tavg",
    "air temperature": "dly_tavg",
    "rain": "dly_rain", "precipitation": "dly_rain", "total rain": "dly_rain",
    "rh": "dly_rh", "mean rh": "dly_rh", "relative humidity": "dly_rh", "average rh": "dly_rh",
    "max rh": "dly_rhmax", "min rh": "dly_rhmin",
    "mean wind speed": "dly_wsmean", "3m mean wind speed": "dly_wsmean", "wind speed": "dly_wsmean",
    "max wind speed": "dly_wsmax", "3m max wind speed": "dly_wsmax",
    "wind direction": "dly_wdir", "3m wind direction": "dly_wdir",
    "solar radiation": "dly_solar", "total solar radiation": "dly_solar",
    "reference et": "dly_eto", "eto": "dly_eto", "grass reference et": "dly_eto", "et grass": "dly_eto",
    "etr": "dly_etr", "alfalfa reference et": "dly_etr", "et alfalfa": "dly_etr",
    "penman-monteith eto": "dly_eto", "penman-monteith etr": "dly_etr",
}
SOIL_RE = re.compile(r"soil\s*temp[a-z]*\s*(?:at\s*)?(\d+)\s*(in|cm|\")?", re.I)


def _strip_units(h: str) -> tuple[str, str | None]:
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", h.strip())
    if m:
        return m.group(1).strip().lower(), m.group(2).strip()
    return h.strip().lower(), None


@register
class ZiaMet(Source):
    name = "ziamet"
    agency = "NMSU NM Climate Center"
    description = "ZiaMet mesonet: 5-minute raw feed + daily weather/ET/soil products, 214 stations"
    kinds = ("sites", "feed", "daily")

    def __init__(self, ctx):
        super().__init__(ctx)
        hosts = [self.cfg.base_url] if self.cfg.base_url else []
        self.hosts = hosts + [h for h in HOSTS if h not in hosts]
        self._host_idx = 0

    # ------------------------------------------------------------------ host rotation
    def _get(self, path: str, **kw):
        last: Exception | None = None
        for _ in range(len(self.hosts)):
            host = self.hosts[self._host_idx % len(self.hosts)]
            try:
                return self.get(f"{host}{path}", **kw)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last = e
                log.warning("ziamet host %s unreachable (%s); rotating", host, type(e).__name__)
                self._host_idx += 1
        raise last or RuntimeError("no ZiaMet host reachable")

    @property
    def host(self) -> str:
        return self.hosts[self._host_idx % len(self.hosts)]

    # ------------------------------------------------------------------ sites
    def discover(self) -> pd.DataFrame:
        art = self._get("/ziamet/stations/", kind="sites", refresh=True)
        html = art.read_text()
        rows = []
        for m in re.finditer(
            r'<tr>\s*<td><a href="/ziamet/station/([a-z0-9]+)/">([^<]*)</a></td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>',
            html,
        ):
            sid, name, lat, lon, elev = m.groups()

            def f(x):
                try:
                    return float(x)
                except ValueError:
                    return None

            rows.append({
                "native_id": sid, "name": name.strip(), "lat": f(lat), "lon": f(lon),
                "elevation_m": (f(elev) * 0.3048) if f(elev) is not None else None,
                "site_type": "met", "agency": "NMSU", "state": "NM", "active": True,
                "raw_metadata": json.dumps({"station_page": f"{self.host}/ziamet/station/{sid}/",
                                            "feed_5min": f"{self.host}/ziamet/station/feed/sfwx/std/csv/iu/5/{sid}/"}),
            })
        if not rows:
            raise RuntimeError("no stations parsed from /ziamet/stations/")
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ fetch
    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        kinds = opts.get("kinds") or ["feed", "daily"]
        summ = FetchSummary(self.name)
        sites = self.sites()
        if sites.empty:
            sites = self.discover()
            self.store.write_sites(sites, self.name)
        sids = list(sites["native_id"])
        if site_ids:
            sids = [s for s in sids if s in site_ids]
        if limit:
            sids = sids[:limit]
        if "feed" in kinds:
            summ.add(self._fetch_feed(sids))
        if "daily" in kinds:
            summ.add(self._fetch_daily(sids, since, refresh))
        return summ

    # ---- 5-minute feed (rolling window) ------------------------------------------------
    def _fetch_feed(self, sids: list[str]) -> FetchSummary:
        summ = FetchSummary(self.name)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H")
        interval = int(self.opt("feed_interval_min", 5))

        def one(sid: str) -> int:
            art = self._get(f"/ziamet/station/feed/sfwx/std/csv/iu/{interval}/{sid}/", kind="feed",
                            site_uid=self.uid(sid), refresh=True, key_extra=f"run={stamp}")
            summ.n_requests += 1
            df = self._parse_feed(art.read_text(), sid, f"{interval}min")
            n = self.write_obs(df, tag=f"feed-{sid}") if df is not None else 0
            self.ledger.set_rows(art.request_key, n)
            return n

        res = self.parallel(one, sids, desc="feed")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(sids) - len(res)
        return summ

    def _parse_feed(self, text: str, sid: str, interval: str) -> pd.DataFrame | None:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) < 3 or not lines[0].lower().startswith("date"):
            return None
        header = [h.strip() for h in lines[0].split(",")]
        units = [u.strip() for u in lines[1].split(",")]
        df = pd.read_csv(io.StringIO("\n".join(lines[2:])), header=None, names=header, dtype=str)
        local = pd.to_datetime(df["Date"].str.strip() + " " + df["Time"].str.strip(), errors="coerce")
        local = local.dt.tz_localize(LOCAL_TZ, ambiguous="NaT", nonexistent="NaT")
        utc = local.dt.tz_convert("UTC")
        offset = (local.dt.tz_localize(None) - utc.dt.tz_localize(None)).dt.total_seconds() / 60
        frames = []
        for i, h in enumerate(header):
            p = FEED_COLS.get(h.lower())
            if not p:
                continue
            frames.append(pd.DataFrame({
                "site_uid": self.uid(sid), "datetime_utc": utc, "utc_offset_min": offset,
                "value": pd.to_numeric(df[h], errors="coerce"), "source_param": p,
                "source_unit": units[i] if i < len(units) else None, "interval": interval,
            }))
        if not frames:
            return None
        out = pd.concat(frames, ignore_index=True)
        out = out[out["value"].notna() & out["datetime_utc"].notna()]
        return self.xw.apply(out, self.name)

    # ---- daily products via POST forms ---------------------------------------------------
    def _csrf(self, path: str) -> str | None:
        art = self._get(path, kind="form", refresh=True, key_extra=datetime.now(UTC).strftime("%Y%m%d"))
        m = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', art.read_text())
        if m:
            return m.group(1)
        jar = self.http.client(self.name).cookies
        return jar.get("csrftoken")

    def _fetch_daily(self, sids: list[str], since: date | None, refresh: bool) -> FetchSummary:
        summ = FetchSummary(self.name)
        products = self.opt("daily_products", ["sfwx", "dret", "tmps"])
        start_year = int(self.opt("start_year", 1995))
        today = date.today()
        y0 = max(start_year, since.year) if since else start_year
        jobs = [(sid, prod, y) for sid in sids for prod in products for y in range(y0, today.year + 1)]

        def one(job) -> int:
            sid, prod, y = job
            path = f"/ziamet/station/{prod}/dly/{sid}/"
            token = self._csrf(path)
            s = date(y, 1, 1) if not (since and y == since.year) else since
            e = min(date(y, 12, 31), today)
            data = {"sid": sid, "sdate": s.isoformat(), "edate": e.isoformat(), "dtype": "dly",
                    "output": "CSV", "units": "English"}
            for k, v in (self.opt("form_extra") or {}).items():
                data[k] = v
            headers = {"Referer": f"{self.host}{path}", "X-CSRFToken": token or ""}
            # current-year windows are re-fetched; completed years come from the archive
            art = self._get(path, kind="daily", method="POST", data=data, headers=headers,
                            site_uid=self.uid(sid), variable=prod, window=(s.isoformat(), e.isoformat()),
                            refresh=refresh or e == today)
            summ.n_requests += 1
            if art.from_cache:
                summ.n_cached += 1
                if self.already_written(art):
                    return 0
            df = self._parse_daily(art.read_text(), sid, prod)
            n = self.write_obs(df, tag=f"{prod}-{sid}-{y}") if df is not None else 0
            self.ledger.set_rows(art.request_key, n)
            return n

        res = self.parallel(one, jobs, desc="daily")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(jobs) - len(res)
        return summ

    def _parse_daily(self, text: str, sid: str, prod: str) -> pd.DataFrame | None:
        if "<html" in text[:500].lower():
            # form re-rendered (validation error) or no data
            return None
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) < 2:
            return None
        # find header line (first line starting with Date)
        hi = next((i for i, ln in enumerate(lines) if ln.lower().startswith("date")), None)
        if hi is None:
            return None
        header = [h.strip() for h in lines[hi].split(",")]
        units_row = None
        body_start = hi + 1
        if body_start < len(lines):
            probe = lines[body_start].split(",")
            if probe and not re.match(r"^\d{4}-\d{2}-\d{2}", probe[0].strip()):
                units_row = [u.strip() for u in probe]
                body_start += 1
        df = pd.read_csv(io.StringIO("\n".join(lines[body_start:])), header=None, names=header, dtype=str)
        ts = pd.to_datetime(df["Date"].str.strip(), errors="coerce").dt.tz_localize("UTC")
        frames = []
        for i, h in enumerate(header):
            base, unit_in_hdr = _strip_units(h)
            unit = unit_in_hdr or (units_row[i] if units_row and i < len(units_row) else None)
            p = DAILY_COLS.get(base)
            sm = SOIL_RE.match(base)
            if p is None and sm:
                p = f"dly_soilt_{sm.group(1)}{(sm.group(2) or 'in').replace(chr(34), 'in')}"
            if p is None:
                continue
            # unit-sensitive params: pick the metric/english variant by the units label
            if p in ("dly_tmax", "dly_tmin", "dly_tavg") and unit and unit.strip().upper().startswith("C"):
                p = p + "_c"
            if p == "dly_rain" and unit and unit.lower().startswith("mm"):
                p = "dly_rain_mm"
            if p in ("dly_eto", "dly_etr") and unit and unit.lower().startswith("mm"):
                p = p + "_mm"
            if p.startswith("dly_soilt_") and unit and unit.strip().upper().startswith("C"):
                p = p + "_c"
            frames.append(pd.DataFrame({
                "site_uid": self.uid(sid), "datetime_utc": ts, "utc_offset_min": None,
                "value": pd.to_numeric(df[h], errors="coerce"), "source_param": p,
                "source_unit": unit, "interval": "daily",
            }))
        if not frames:
            return None
        out = pd.concat(frames, ignore_index=True)
        out = out[out["value"].notna() & out["datetime_utc"].notna()]
        out = self.xw.apply(out, self.name, keep_unmapped=False)
        unmapped = [c for c in header if _strip_units(c)[0] not in DAILY_COLS and not SOIL_RE.match(_strip_units(c)[0])
                    and c.lower() not in ("date", "time")]
        if unmapped:
            log.debug("ziamet %s/%s unmapped columns: %s", sid, prod, unmapped)
        return out

    def normalize(self, artifact) -> pd.DataFrame | None:
        m = re.search(r"/ziamet/station/(?:feed/sfwx/std/csv/iu/(\d+)/)?(?:(\w+)/dly/)?([a-z0-9]+)/?$", artifact.url)
        if not m:
            return None
        interval, prod, sid = m.groups()
        if artifact.kind == "feed":
            return self._parse_feed(artifact.read_text(), sid, f"{interval or 5}min")
        if artifact.kind == "daily":
            return self._parse_daily(artifact.read_text(), sid, prod or "sfwx")
        return None

    def reprocess(self, kind: str | None = None) -> int:
        return super().reprocess(kind="feed") + super().reprocess(kind="daily")
