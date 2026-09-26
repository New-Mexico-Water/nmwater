"""OSE/ISC Real-Time Water Measurement Information System (meas.ose.state.nm.us).

296 state-operated stations (2012-). The site page sets a JSESSIONID; exports are then plain
GETs of ReportProxy (the browser POSTs Validator.jsp first, which is not required):

  ReportProxy?id=<id>&type=<S|G>&&sDate=MM/DD/YYYY&eDate=MM/DD/YYYY&dischargeData=davg|dtot|mtot&rptFormat=CSV&sort=asc
  ReportProxy?id=<id>&type=<S|G>&rawData=dischargeheight&&sDate=..&eDate=..&rptFormat=CSV&sort=asc

Surface (S) sites report discharge in cfs and gage height in ft; metered wells (G) report
pumping discharge in gpm and depth to water (ft bgs) as "Gage Height".
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import date, timedelta

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.ose_meas")

LINK_RE = re.compile(
    r'<a href="site\.jsp\?id=(\d+)&status=([A-Z])&type=([A-Z])&dist=(\d+)&basin=([^"]+)"[^>]*>(.*?)</a>', re.S
)
DISTRICTS = {"1": "Albuquerque", "2": "Roswell", "3": "Deming", "4": "Las Cruces", "5": "Aztec", "6": "Santa Fe", "7": "Cimarron"}
MIN_DATE = date(2011, 1, 1)


def _norm(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _dms(v) -> float | None:
    """'35° 49' 16.069" N' -> 35.8211; west/south negative."""
    m = re.match(r"\s*(\d+)\D+(\d+)\D+([\d.]+)\D*([NSEW])", str(v or ""))
    if not m:
        return None
    x = int(m.group(1)) + int(m.group(2)) / 60 + float(m.group(3)) / 3600
    return -x if m.group(4) in "SW" else x


def _rtm_coords(r: dict) -> tuple[float | None, float | None]:
    """Decimal degrees, else the DMS strings, else UTM zone 13N (NAD83) northing/easting."""
    lat, lon = r.get("lat_ddd"), r.get("long_ddd")
    if pd.notna(lat) and pd.notna(lon):
        return float(lat), float(lon)
    lat, lon = _dms(r.get("Latitude")), _dms(r.get("Longitude"))
    if lat is not None and lon is not None:
        return lat, lon
    n, e = r.get("Northing"), r.get("Easting")
    if pd.notna(n) and pd.notna(e):
        from pyproj import Transformer
        lon, lat = Transformer.from_crs(26913, 4326, always_xy=True).transform(float(e), float(n))
        return lat, lon
    return None, None


def _rtm_index(path) -> tuple[dict, dict]:
    """OSE real-time meters keyed by Station_ID, plus by name for meters without one (unique names only)."""
    by_id: dict = {}
    names: dict = {}
    if not path.exists():
        return by_id, {}
    for r in pd.read_parquet(path).to_dict("records"):
        lat, lon = _rtm_coords(r)
        if lat is None:
            continue
        val = (lat, lon, r.get("Ditch_Name"), r.get("River_src"))
        sid = r.get("Station_ID")
        if pd.notna(sid) and sid not in ("", 0):
            by_id[str(int(sid))] = val
        else:
            for k in {_norm(r.get("Gauge_name")), _norm(r.get("Ditch_Name"))} - {""}:
                names.setdefault(k, []).append(val)
    by_name = {k: v[0] for k, v in names.items() if len(v) == 1}
    return by_id, by_name


@register
class OSEMeas(Source):
    name = "ose_meas"
    agency = "NM OSE/ISC"
    description = "OSE real-time water measurement system: 296 stream/ditch/well stations (2012-)"
    kinds = ("daily", "monthly", "raw")

    @property
    def base(self) -> str:
        return (self.cfg.base_url or "https://meas.ose.state.nm.us").rstrip("/")

    def _home(self, refresh: bool = True) -> str:
        # site.jsp?id=1 renders the full station menu and sets the session cookie on the shared client
        art = self.get(f"{self.base}/site.jsp", params={"id": 1}, kind="page", refresh=refresh)
        return art.read_text(errors="replace")

    def _stations(self, refresh: bool = True) -> pd.DataFrame:
        html = self._home(refresh=refresh)
        rows = {}
        for sid, status, typ, dist, basin, inner in LINK_RE.findall(html):
            name = " ".join(re.sub(r"<[^>]+>", " ", inner).split())
            rows.setdefault(sid, {"id": sid, "status": status, "type": typ, "dist": dist, "basin": basin.strip(), "name": name})
        return pd.DataFrame(list(rows.values()))

    def discover(self) -> pd.DataFrame:
        st = self._stations(refresh=True)
        self.store.write_table(st, "reference", self.name, "stations")
        by_id, by_name = _rtm_index(self.store.root / "reference" / "source=ose_arcgis" / "real_time_meters.parquet")
        rows = []
        for r in st.to_dict("records"):
            hit = by_id.get(str(r["id"]))
            how = "rtm_station_id" if hit else None
            if hit is None and _norm(r["name"]) in by_name:
                hit, how = by_name[_norm(r["name"])], "rtm_unique_name"
            lat, lon, ditch, river = hit or (None, None, None, None)
            how = how if lat is not None else None
            rows.append({
                "native_id": r["id"], "name": r["name"], "lat": lat, "lon": lon,
                "site_type": "well" if r["type"] == "G" else "diversion" if any(k in r["name"].lower() for k in ("ditch", "acequia", "canal", "lateral", "flume", "drain")) else "stream",
                "agency": "NM OSE", "state": "NM", "basin": r["basin"], "active": r["status"] == "Y",
                "raw_metadata": json.dumps({"district": DISTRICTS.get(r["dist"], r["dist"]), "type": r["type"],
                                            "rtm_ditch_name": ditch, "rtm_river_src": river, "coord_source": how,
                                            "url": f"{self.base}/site.jsp?id={r['id']}&status={r['status']}&type={r['type']}&dist={r['dist']}"}),
            })
        return pd.DataFrame(rows)

    @staticmethod
    def _fmt(d: date) -> str:
        return d.strftime("%m/%d/%Y")

    def _windows(self, since: date | None, years: int = 1) -> list[tuple[date, date]]:
        start = max(since or MIN_DATE, MIN_DATE)
        today = date.today()
        out = []
        cur = start
        while cur <= today:
            end = min(date(cur.year + years - 1, 12, 31), today)
            out.append((cur, end))
            cur = end + timedelta(days=1)
        return out

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        kinds = opts.get("kinds") or ["daily", "monthly"]
        summ = FetchSummary(self.name)
        st = self._stations(refresh=True)  # also establishes the session cookie
        if site_ids:
            st = st[st["id"].isin([str(s) for s in site_ids])]
        if limit:
            st = st.head(limit)
        jobs = []
        today = date.today()
        for r in st.to_dict("records"):
            for b, e in self._windows(since):
                # the window containing today is refetched each run; closed windows are cached
                fresh = refresh or e >= today
                if "daily" in kinds:
                    for dd in (["davg", "dtot"] if r["type"] == "S" else ["davg"]):
                        jobs.append((r, dd, None, b, e, fresh))
                if "raw" in kinds:
                    jobs.append((r, None, "dischargeheight", b, e, fresh))
            if "monthly" in kinds:
                b, e = max(since or MIN_DATE, MIN_DATE), today
                jobs.append((r, "mtot", None, b, e, True))

        def one(job) -> int:
            r, dd, raw, b, e, fresh = job
            q = f"id={r['id']}&type={r['type']}&"
            if raw:
                q += f"rawData={raw}&"
            q += f"&sDate={self._fmt(b)}&eDate={self._fmt(e)}"
            if dd:
                q += f"&dischargeData={dd}"
            q += "&rptFormat=CSV&sort=asc"
            art = self.get(f"{self.base}/ReportProxy?{q}", kind="raw" if raw else ("monthly" if dd == "mtot" else "daily"),
                           site_uid=self.uid(r["id"]), variable=raw or dd, window=(b.isoformat(), e.isoformat()),
                           refresh=fresh)
            summ.n_requests += 1
            if art.from_cache:
                summ.n_cached += 1
                if self.already_written(art):
                    return 0
            df = self.normalize(art)
            n = self.write_obs(df, tag=f"{r['id']}-{raw or dd}") if df is not None and len(df) else 0
            self.ledger.set_rows(art.request_key, n)
            return n

        res = self.parallel(one, jobs, desc="reports")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(jobs) - len(res)
        return summ

    def normalize(self, artifact) -> pd.DataFrame | None:
        text = artifact.read_text(errors="replace")
        if "no data available" in text.lower() or text.lstrip().startswith("<"):
            return None
        m = re.search(r"[?&]id=(\d+)&type=([A-Z])", artifact.url)
        if not m:
            return None
        sid, typ = m.group(1), m.group(2)
        lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("--")]
        if len(lines) < 2:
            return None
        df = pd.read_csv(io.StringIO("\n".join(lines)), dtype=str)
        tcol = df.columns[0]
        frames = []
        for col in df.columns[1:]:
            cl = col.lower()
            unit = re.search(r"\((.*?)\)", col)
            unit = unit.group(1).strip() if unit else None
            if "dischargeavg" in cl.replace(" ", ""):
                param = "DischargeAvg|" + ("gpm" if typ == "G" else "cfs")
                interval, stat = "daily", "mean"
            elif "dischargetotal" in cl.replace(" ", ""):
                param = "DischargeTotal|" + ("well" if typ == "G" else "surface")
                interval = "monthly" if tcol.lower().startswith("month") else "daily"
                stat = "total"
            elif cl.startswith("discharge"):
                param = "Discharge|" + ("gpm" if typ == "G" else "cfs")
                interval, stat = "instant", "instantaneous"
            elif "gage height" in cl:
                param = "GageHeight|" + ("ft bgs" if typ == "G" else "ft")
                interval, stat = "instant", "instantaneous"
            else:
                continue
            if tcol.lower().startswith("month"):
                ts = pd.to_datetime(df[tcol], format="%m/%Y", errors="coerce", utc=True)
            elif tcol.lower().startswith("day"):
                ts = pd.to_datetime(df[tcol], format="%m/%d/%Y", errors="coerce", utc=True)
            else:
                # timestamps are local Mountain time; convert to UTC and keep the offset
                loc = pd.to_datetime(df[tcol], format="%m/%d/%Y %H:%M", errors="coerce")
                loc = loc.dt.tz_localize("America/Denver", ambiguous="NaT", nonexistent="NaT")
                ts = loc.dt.tz_convert("UTC")
            vals = pd.to_numeric(df[col].replace({"NR": None, "nr": None, "": None}), errors="coerce")
            sub = pd.DataFrame({
                "site_uid": self.uid(sid), "datetime_utc": ts, "value": vals, "source_param": param,
                "source_unit": unit, "qualifier": "provisional", "utc_offset_min": None,
                "statistic": stat, "interval": interval,
            })
            if interval == "instant":
                sub["utc_offset_min"] = (loc.dt.tz_convert("UTC").dt.tz_localize(None) - loc.dt.tz_localize(None)).dt.total_seconds().div(-60).astype("Int64")
            frames.append(sub[sub["value"].notna() & sub["datetime_utc"].notna()])
        if not frames:
            return None
        out = pd.concat(frames, ignore_index=True)
        return self.xw.apply(out, self.name)
