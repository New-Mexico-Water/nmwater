"""NOAA NCEI GHCN-Daily: per-station CSV (values in tenths for PRCP/TMAX/TMIN/... as in .dly).

  stations:  https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt   (fixed width)
  inventory: https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-inventory.txt  (ID LAT LON ELEM FIRST LAST)
  data:      https://www.ncei.noaa.gov/data/global-historical-climatology-network-daily/access/{ID}.csv
"""

from __future__ import annotations

import io
import json
import logging
from datetime import date

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.noaa_ghcnd")

STATIONS_URL = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt"
INVENTORY_URL = "https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-inventory.txt"
DATA_URL = "https://www.ncei.noaa.gov/data/global-historical-climatology-network-daily/access/{sid}.csv"
ELEMENTS = ["PRCP", "SNOW", "SNWD", "TMAX", "TMIN", "TAVG", "TOBS", "WESD", "AWND"]


@register
class NOAAGHCND(Source):
    name = "noaa_ghcnd"
    agency = "NOAA NCEI"
    description = "GHCN-Daily station precipitation, snow, temperature (1849-) for NM + border"

    def _stations(self, refresh: bool = True) -> pd.DataFrame:
        art = self.get(STATIONS_URL, kind="meta", refresh=refresh)
        rows = []
        for ln in art.read_text().splitlines():
            if len(ln) < 40:
                continue
            rows.append(
                {
                    "native_id": ln[0:11].strip(),
                    "lat": _f(ln[12:20]),
                    "lon": _f(ln[21:30]),
                    "elev": _f(ln[31:37]),
                    "state": ln[38:40].strip() or None,
                    "name": ln[41:71].strip(),
                    "gsn": ln[72:75].strip(),
                    "hcn_crn": ln[76:79].strip(),
                    "wmo": ln[80:85].strip() if len(ln) >= 85 else "",
                }
            )
        return pd.DataFrame(rows)

    def _inventory(self, refresh: bool = True) -> pd.DataFrame:
        art = self.get(INVENTORY_URL, kind="meta", refresh=refresh)
        df = pd.read_csv(io.StringIO(art.read_text()), sep=r"\s+", header=None,
                         names=["native_id", "lat", "lon", "elem", "first", "last"], dtype=str)
        df["first"] = pd.to_numeric(df["first"], errors="coerce")
        df["last"] = pd.to_numeric(df["last"], errors="coerce")
        return df

    def discover(self) -> pd.DataFrame:
        st = self._stations()
        inbox = [self.scope.in_bbox(a, b) for a, b in zip(st["lat"], st["lon"])]
        st = st[(st["state"] == self.scope.state_abbr) | pd.Series(inbox, index=st.index)]
        inv = self._inventory()
        inv = inv[inv["native_id"].isin(set(st["native_id"]))]
        inv_map: dict[str, dict] = {}
        for sid, g in inv.groupby("native_id"):
            inv_map[sid] = {r.elem: [int(r.first), int(r.last)] for r in g.itertuples(index=False)
                            if pd.notna(r.first)}
        return pd.DataFrame(
            {
                "native_id": st["native_id"],
                "name": st["name"],
                "lat": st["lat"],
                "lon": st["lon"],
                "elevation_m": st["elev"].where(st["elev"] > -900),
                "site_type": "met",
                "agency": "NOAA/GHCN",
                "state": st["state"],
                "active": [max((v[1] for v in inv_map.get(s, {}).values()), default=0) >= date.today().year - 1
                           for s in st["native_id"]],
                "raw_metadata": [
                    json.dumps({"gsn": g, "hcn_crn": h, "wmo": w, "inventory": inv_map.get(s, {})})
                    for s, g, h, w in zip(st["native_id"], st["gsn"], st["hcn_crn"], st["wmo"])
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
        if since is not None:
            def _last(meta: str) -> int:
                try:
                    inv = json.loads(meta).get("inventory", {})
                    return max((v[1] for v in inv.values()), default=0)
                except Exception:
                    return 9999
            sites = sites[[_last(m) >= since.year for m in sites["raw_metadata"]]]
        if limit:
            sites = sites.head(limit)
        do_refresh = refresh or since is not None

        def one(sid: str) -> int:
            art = self.get(DATA_URL.format(sid=sid), kind="data", site_uid=self.uid(sid), refresh=do_refresh,
                           window=("1800-01-01", date.today().isoformat()))
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
            n = self.write_obs(df, tag=sid)
            self.ledger.set_rows(art.request_key, n)
            return n

        ids = list(sites["native_id"])
        res = self.parallel(one, ids, desc="stations")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(ids) - len(res)
        return summ

    def normalize(self, artifact) -> pd.DataFrame | None:
        text = artifact.read_text()
        if not text.strip() or not text.startswith('"STATION"'):
            return None
        df = pd.read_csv(io.StringIO(text), dtype=str)
        if df.empty:
            return None
        sid = df["STATION"].iloc[0]
        frames = []
        for el in ELEMENTS:
            if el not in df.columns:
                continue
            val = pd.to_numeric(df[el].str.strip(), errors="coerce")
            attr = df[el + "_ATTRIBUTES"] if el + "_ATTRIBUTES" in df.columns else None
            sub = pd.DataFrame(
                {
                    "site_uid": self.uid(sid),
                    "datetime_utc": pd.to_datetime(df["DATE"], errors="coerce", utc=True),
                    "value": val,
                    "qualifier": attr.fillna("").str.strip() if attr is not None else None,
                    "source_param": el,
                    "utc_offset_min": None,
                }
            )
            frames.append(sub[sub["value"].notna()])
        if not frames:
            return None
        out = pd.concat(frames, ignore_index=True)
        out = self.xw.apply(out, self.name)
        out["interval"] = out["interval"].fillna("daily")
        return out


def _f(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None
