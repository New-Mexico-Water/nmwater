"""NOAA NCEI Global Historical Climatology Network hourly (GHCNh), the successor to ISD-Lite.

  stations:  https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/doc/ghcnh-station-list.csv
  inventory: .../hourly/doc/ghcnh-inventory.txt   (one line per station-year, report counts by month)
  data:      .../hourly/access/by-year/{YYYY}/parquet/GHCNh_{station}_{YYYY}.parquet
             one row per report (several per hour), timestamps UTC, SI units, and for every variable
             <var>, <var>_Measurement_Code, <var>_Quality_Code, <var>_Report_Type, <var>_Source_Code.

Why this exists: ISD-Lite stopped in 2025 (no 2026 files), and GHCNh carries the same airport/AWOS
network plus more (USCRN, RAWS-like, COOP hourly). See docs/sources.md and issue #39.

Reduction to one value per hour (keeps this comparable to ISD-Lite and keeps volume manageable):
  * instantaneous variables: the report nearest the top of the hour, labelled with that hour;
  * precipitation: reports within an hour are running totals since the last routine report, so the
    LAST report of each reporting period is the hourly total (GHCNh documentation, section IX).
Quality: GHCNh's own QC failures and "erroneous" codes are dropped; suspect values are kept and the
quality code is stored verbatim in `qualifier`.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import date

import httpx
import pandas as pd
import pyarrow.parquet as pq

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.noaa_ghcnh")

BASE = "https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly"
STATIONS_URL = f"{BASE}/doc/ghcnh-station-list.csv"
INVENTORY_URL = f"{BASE}/doc/ghcnh-inventory.txt"
DATA_URL = f"{BASE}/access/by-year/{{year}}/parquet/GHCNh_{{station}}_{{year}}.parquet"

# GHCNh columns that map to canonical variables (see catalog/crosswalk.d/noaa_ghcnh.csv)
VARIABLES = ("temperature", "wind_speed", "wind_direction", "precipitation", "relative_humidity", "snow_depth")

# Codes that mean "do not use". GHCNh applies one QC scheme (letters) to these six variables and
# passes through the legacy scheme (digits) for the rest; see the GHCNh documentation, Table 3.
ERRONEOUS = {"3", "7"}
GHCNH_QC_VARS = {"temperature", "dew_point_temperature", "station_level_pressure",
                 "sea_level_pressure", "wind_direction", "wind_speed"}
DROP_QUALITY = {
    # any letter code means a NOAA QC check failed (spike, streak, outlier, world record, ...)
    "precipitation": ERRONEOUS | set("XNYKGOZAMD"),  # failed checks, multi-hour accumulation, missing/deleted
    "relative_humidity": ERRONEOUS | set("fo"),      # derived from suspect inputs / out of range
    "snow_depth": ERRONEOUS,
}
FUTURE_SLACK = pd.Timedelta(days=1)
MAX_HOURLY_PRECIP_MM = 305.0  # about 12 in, near the world record for one hour; anything larger is an error


def _drop_mask(var: str, qc: pd.Series) -> pd.Series:
    q = qc.fillna("").astype(str).str.strip()
    if var in GHCNH_QC_VARS:
        return q.isin(ERRONEOUS) | q.str.fullmatch(r"[A-Za-z]")
    return q.isin(DROP_QUALITY.get(var, ERRONEOUS))


def _nearest_hour(sub: pd.DataFrame) -> pd.DataFrame:
    """One report per hour: the one closest to the top of the hour, labelled with that hour."""
    hour = sub["ts"].dt.round("h")
    sub = sub.assign(datetime_utc=hour, _d=(sub["ts"] - hour).abs())
    sub = sub.sort_values(["datetime_utc", "_d"], kind="stable")
    return sub.drop_duplicates("datetime_utc", keep="first")


def _hourly_precip(sub: pd.DataFrame) -> pd.DataFrame:
    """Last report of each reporting period, labelled with the routine report time rounded to the hour.

    Totals within a period are running totals that reset after the routine report (often at :52-:55),
    so a station's routine minute m is found from the data and reports are grouped as (m, m+60min].
    """
    if sub.empty:
        return sub
    routine = sub[sub["rtype"] == "FM15"] if (sub["rtype"] == "FM15").any() else sub
    m = int(routine["ts"].dt.minute.mode().iloc[0])
    key = (sub["ts"] - pd.Timedelta(minutes=m)).dt.ceil("h")
    sub = sub.assign(datetime_utc=key + pd.Timedelta(hours=1 if m >= 30 else 0))
    sub = sub.sort_values(["datetime_utc", "ts"], kind="stable")
    return sub.drop_duplicates("datetime_utc", keep="last")


@register
class NOAAGHCNh(Source):
    name = "noaa_ghcnh"
    agency = "NOAA NCEI"
    description = "GHCNh hourly surface weather (temperature, wind, humidity, hourly precipitation, snow depth), successor to ISD-Lite"

    def _inventory(self, ids: set[str]) -> dict[str, list[int]]:
        """Years with at least one report, per station (the file is ~90 MB; keep only wanted ids)."""
        art = self.get(INVENTORY_URL, kind="meta", refresh=True)
        years: dict[str, list[int]] = {}
        for line in art.read_text().splitlines()[1:]:
            parts = line.split()
            if len(parts) < 14 or parts[0] not in ids:
                continue
            if sum(int(x) for x in parts[2:14]) > 0:
                years.setdefault(parts[0], []).append(int(parts[1]))
        return years

    def discover(self) -> pd.DataFrame:
        art = self.get(STATIONS_URL, kind="meta", refresh=True)
        st = pd.read_csv(io.StringIO(art.read_text()), dtype=str)
        st["LATITUDE"] = pd.to_numeric(st["LATITUDE"], errors="coerce")
        st["LONGITUDE"] = pd.to_numeric(st["LONGITUDE"], errors="coerce")
        st["ELEVATION"] = pd.to_numeric(st["ELEVATION"], errors="coerce")
        inbox = [self.scope.in_bbox(a, b) for a, b in zip(st["LATITUDE"], st["LONGITUDE"])]
        st = st[pd.Series(inbox, index=st.index)]
        years = self._inventory(set(st["GHCN_ID"]))
        st = st[st["GHCN_ID"].isin(years)]
        this_year = date.today().year
        return pd.DataFrame(
            {
                "native_id": st["GHCN_ID"],
                "name": st["NAME"].str.strip(),
                "lat": st["LATITUDE"],
                "lon": st["LONGITUDE"],
                "elevation_m": st["ELEVATION"].where(st["ELEVATION"] > -900),
                "site_type": "met",
                "agency": "NOAA/GHCNh",
                "state": st["STATE"].where(st["STATE"].astype(str).str.len() == 2, None),
                "active": [max(years[i]) >= this_year - 1 for i in st["GHCN_ID"]],
                "raw_metadata": [
                    json.dumps({"icao": ic if isinstance(ic, str) else "", "wmo": w if isinstance(w, str) else "",
                                "first_year": min(years[i]), "last_year": max(years[i]), "years": sorted(years[i])})
                    for i, ic, w in zip(st["GHCN_ID"], st["ICAO"], st["WMO_ID"])
                ],
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
        this_year = date.today().year
        jobs: list[tuple[str, int]] = []
        for r in sites.itertuples(index=False):
            meta = json.loads(r.raw_metadata) if r.raw_metadata else {}
            ys = set(meta.get("years", []))
            if ys and max(ys) >= this_year - 1:
                ys |= {this_year - 1, this_year}  # the inventory is a snapshot; recent files keep growing
            if since:
                ys = {y for y in ys if y >= since.year}
            jobs += [(r.native_id, y) for y in sorted(ys)]

        def one(j) -> int:
            sid, y = j
            url = DATA_URL.format(year=y, station=sid)
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
        sid = artifact.url.rsplit("/", 1)[-1].split("_")[1]
        want = ["DATE"]
        for v in VARIABLES:
            want += [v, f"{v}_Quality_Code", f"{v}_Measurement_Code", f"{v}_Report_Type"]
        avail = set(pq.ParquetFile(io.BytesIO(artifact.read_bytes())).schema_arrow.names)
        raw = pd.read_parquet(io.BytesIO(artifact.read_bytes()), columns=[c for c in want if c in avail])
        if raw.empty:
            return None
        ts = pd.to_datetime(raw["DATE"], utc=True, errors="coerce")
        ok = ts.notna() & (ts <= pd.Timestamp.now(tz="UTC") + FUTURE_SLACK)
        frames = []
        for v in VARIABLES:
            if v not in raw.columns:
                continue
            val = pd.to_numeric(raw[v], errors="coerce")
            qc = raw.get(f"{v}_Quality_Code", pd.Series(None, index=raw.index))
            mc = raw.get(f"{v}_Measurement_Code", pd.Series(None, index=raw.index))
            keep = ok & val.notna() & ~_drop_mask(v, qc)
            if v == "wind_direction":
                keep &= val <= 360  # 999 marks a variable direction, not a bearing
            elif v == "precipitation":
                keep &= val <= MAX_HOURLY_PRECIP_MM
            if not keep.any():
                continue
            sub = pd.DataFrame({"ts": ts[keep], "value": val[keep], "qc": qc[keep].astype("object"),
                                "mc": mc[keep].astype("object"),
                                "rtype": raw.get(f"{v}_Report_Type", pd.Series(None, index=raw.index))[keep]})
            sub = _hourly_precip(sub) if v == "precipitation" else _nearest_hour(sub)
            trace = sub["mc"].astype(str).eq("T")
            qual = sub["qc"].where(sub["qc"].notna(), None).astype("object")
            if v == "precipitation":
                qual = qual.where(~trace, qual.fillna("").astype(str) + ",T").str.strip(",").replace("", None)
            frames.append(pd.DataFrame({
                "site_uid": self.uid(sid),
                "datetime_utc": sub["datetime_utc"],
                "value": sub["value"],
                "qualifier": qual,
                "source_param": v,
                "utc_offset_min": -420,
            }))
        if not frames:
            return None
        return self.xw.apply(pd.concat(frames, ignore_index=True), self.name)
