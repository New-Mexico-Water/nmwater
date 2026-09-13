"""NM Bureau of Geology & Mineral Resources Aquifer Mapping Program API.

Production host https://waterdata.nmt.edu/latest was unreachable from this machine
(TCP timeouts on 80/443); the App Engine instance
https://ampapidev-dot-waterdatainitiative-271000.appspot.com/latest serves the same FastAPI
app (OpenAPI 3.1 verified) and is used as the fallback. The dev host is slow (~7 s per 50
locations), so discovery tiles the state bbox with WKT polygons.

Endpoints used: /locations (GeoJSON), /waterlevels/manual, /waterlevels/continuous (paged),
/waterchemistry, /collaborative_network/locations, /collaborative_network/waterlevels/csv.
"""

from __future__ import annotations

import io
import json
import logging
from datetime import date

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.nmbgmr")

FALLBACK = "https://ampapidev-dot-waterdatainitiative-271000.appspot.com/latest"
SITE_TYPES = {
    "Groundwater other than spring (well)": "well", "Spring": "spring", "Stream": "stream",
    "Ephemeral stream": "stream", "Perennial stream": "stream", "Diversion": "diversion",
    "Lake, pond or reservoir": "reservoir", "Wastewater outfall": "outfall", "Meteorological": "met",
}


@register
class NMBGMR(Source):
    name = "nmbgmr"
    agency = "NMBGMR"
    description = "NMBGMR Aquifer Mapping Program: manual + continuous water levels, chemistry, Healy network"
    kinds = ("sites", "manual", "continuous", "chemistry", "collabnet")

    def __init__(self, ctx):
        super().__init__(ctx)
        self._base: str | None = None

    @property
    def base(self) -> str:
        if self._base is None:
            primary = (self.cfg.base_url or "https://waterdata.nmt.edu/latest").rstrip("/")
            self._base = primary
            try:
                art = self.get(f"{primary}/collaborative_network/stats", kind="probe", refresh=True)
                art.read_json()
            except Exception as e:  # noqa: BLE001
                log.warning("nmbgmr: %s unreachable (%s); using %s", primary, str(e)[:80], FALLBACK)
                self._base = FALLBACK
        return self._base

    # -- discovery: WKT tiles over the buffered bbox ----------------------------------
    def _tiles(self, step: float | None = None) -> list[str]:
        step = step or float(self.opt("tile_deg", 0.5))
        w, s, e, n = self.scope.bbox
        tiles = []
        y = s
        while y < n:
            x = w
            while x < e:
                x2, y2 = min(x + step, e), min(y + step, n)
                tiles.append(f"POLYGON(({x:.3f} {y:.3f},{x2:.3f} {y:.3f},{x2:.3f} {y2:.3f},{x:.3f} {y2:.3f},{x:.3f} {y:.3f}))")
                x += step
            y += step
        return tiles

    def _locations(self, refresh: bool = True, limit_tiles: int | None = None) -> list[dict]:
        feats: dict[str, dict] = {}
        tiles = self._tiles()
        if limit_tiles:
            tiles = tiles[:limit_tiles]

        def one(wkt: str) -> list[dict]:
            art = self.get(f"{self.base}/locations", params={"wkt": wkt}, kind="sites", refresh=refresh)
            return (art.read_json() or {}).get("features") or []

        for chunk in self.parallel(one, tiles, desc="location tiles"):
            for f in chunk:
                pid = (f.get("properties") or {}).get("point_id")
                if pid:
                    feats[pid] = f
        return list(feats.values())

    def discover(self) -> pd.DataFrame:
        feats = self._locations(refresh=True, limit_tiles=self.opt("limit_tiles"))
        rows = []
        for f in feats:
            p = f.get("properties") or {}
            thing = (p.get("thing") or {}).get("properties") or {}
            coords = (f.get("geometry") or {}).get("coordinates") or [None, None]
            wd = thing.get("well_depth_ftbgs")
            rows.append({
                "native_id": p["point_id"], "name": p.get("site_names") or p.get("point_id"),
                "lat": coords[1] if len(coords) > 1 else None, "lon": coords[0] if coords else None,
                "elevation_m": float(p["elevation_ft"]) * 0.3048 if p.get("elevation_ft") else None,
                "site_type": SITE_TYPES.get(p.get("site_type"), "other"), "agency": "NMBGMR",
                "state": p.get("state") or "NM", "well_depth_m": float(wd) * 0.3048 if wd else None,
                "aquifer": thing.get("formation"), "active": bool(thing.get("monitoring_status")) if thing.get("monitoring_status") is not None else None,
                "raw_metadata": json.dumps({"usgs_site_id": p.get("site_id"), "alternate_site_id": p.get("alternate_site_id"),
                                            "county": p.get("county"), "altitude_datum": p.get("altitude_datum"),
                                            "ose_well_id": thing.get("ose_well_id"), "ose_welltag_id": thing.get("ose_welltag_id"),
                                            "data_source": thing.get("data_source"), "public_release": p.get("public_release"),
                                            "thing": thing}, default=str),
            })
        # Healy collaborative network locations (subset) as a reference table
        try:
            art = self.get(f"{self.base}/collaborative_network/locations", kind="collabnet", refresh=True)
            cn = (art.read_json() or {}).get("features") or []
            self.store.write_table(pd.DataFrame([{**(x.get("properties") or {}), "lon": (x.get("geometry") or {}).get("coordinates", [None, None])[0],
                                                  "lat": (x.get("geometry") or {}).get("coordinates", [None, None])[1]} for x in cn]).drop(columns=["thing"], errors="ignore"),
                                   "reference", self.name, "collabnet_locations")
        except Exception as e:  # noqa: BLE001
            log.warning("collabnet locations failed: %s", e)
        return pd.DataFrame(rows)

    # -- fetch ---------------------------------------------------------------------------
    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        kinds = opts.get("kinds") or ["collabnet", "manual", "continuous", "chemistry"]
        summ = FetchSummary(self.name)
        if "collabnet" in kinds and not site_ids:
            art = self.get(f"{self.base}/collaborative_network/waterlevels/csv", kind="collabnet_csv", refresh=True)
            summ.n_requests += 1
            df = self._normalize_collab(art.read_text())
            summ.n_rows += self.write_obs(df, tag="collabnet") if df is not None else 0
        sites = self.sites()
        pids = list(sites["native_id"]) if len(sites) else []
        if site_ids:
            pids = [p for p in pids if p in set(site_ids)] or list(site_ids)
        if limit:
            pids = pids[:limit]
        size = int(self.opt("page_size", 5000))

        def levels(job) -> int:
            pid, kind = job
            total = 0
            page = 1
            while page < 500:
                art = self.get(f"{self.base}/waterlevels/{kind}", params={"pointid": pid, "size": size, "page": page,
                                                                        "sort_datetime": "asc"},
                               kind=kind, site_uid=self.uid(pid), refresh=refresh or (since is not None))
                summ.n_requests += 1
                doc = art.read_json() or {}
                items = doc.get("items") or []
                if art.from_cache:
                    summ.n_cached += 1
                    if self.already_written(art):
                        if int(doc.get("pages") or 1) <= page:
                            break
                        page += 1
                        continue
                df = self._normalize_levels(items, pid, kind)
                if since is not None and df is not None:
                    df = df[df["datetime_utc"] >= pd.Timestamp(since, tz="UTC")]
                n = self.write_obs(df, tag=f"{kind}-{pid}") if df is not None and len(df) else 0
                self.ledger.set_rows(art.request_key, n)
                total += n
                if int(doc.get("pages") or 1) <= page:
                    break
                page += 1
            return total

        jobs = [(pid, k) for pid in pids for k in ("manual", "continuous") if k in kinds]
        res = self.parallel(levels, jobs, desc="waterlevels")
        summ.n_rows += int(sum(res))
        summ.n_errors += len(jobs) - len(res)

        if "chemistry" in kinds:
            def chem(pid) -> int:
                art = self.get(f"{self.base}/waterchemistry", params={"pointid": pid}, kind="chemistry",
                               site_uid=self.uid(pid), refresh=refresh)
                summ.n_requests += 1
                if art.from_cache:
                    summ.n_cached += 1
                    if self.already_written(art):
                        return 0
                doc = art.read_json() or {}
                rows = []
                for point, analytes in doc.items():
                    for aname, recs in (analytes or {}).items():
                        for r in recs:
                            info = r.get("info") or {}
                            rows.append({
                                "site_uid": self.uid(point), "analyte": aname, "analyte_symbol": r.get("Analyte"),
                                "value": r.get("SampleValue"), "unit": r.get("Units"),
                                "datetime_utc": pd.to_datetime(info.get("CollectionDate"), errors="coerce", utc=True),
                                "sample_point_id": r.get("SamplePointID"), "method": r.get("AnalysisMethod"),
                                "agency": info.get("AnalysesAgency"), "data_quality": info.get("DataQuality"),
                                "sample_type": info.get("SampleTypeMeaning"), "raw": json.dumps(r, default=str),
                            })
                if not rows:
                    self.ledger.set_rows(art.request_key, 0)
                    return 0
                df = pd.DataFrame(rows)
                self.store.append_table(df, "waterquality", self.name, "chemistry", self.run_id)
                self.ledger.set_rows(art.request_key, len(df))
                return len(df)
            res = self.parallel(chem, pids, desc="chemistry")
            summ.n_rows += int(sum(res))
            summ.n_errors += len(pids) - len(res)
        return summ

    def _normalize_levels(self, items: list[dict], pid: str, kind: str) -> pd.DataFrame | None:
        if not items:
            return None
        df = pd.DataFrame(items)
        d = df["DateMeasured"].astype(str)
        t = df["TimeMeasured"].fillna("").astype(str) if "TimeMeasured" in df.columns else pd.Series("", index=df.index)
        has_time = t.str.len() > 0
        loc = pd.to_datetime((d + " " + t).str.strip(), errors="coerce")
        ts = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
        if has_time.any():
            l2 = loc[has_time].dt.tz_localize("America/Denver", ambiguous="NaT", nonexistent="NaT")
            ts.loc[has_time] = l2.dt.tz_convert("UTC")
        ts.loc[~has_time] = pd.to_datetime(d[~has_time], errors="coerce").dt.tz_localize("UTC")
        out = pd.DataFrame({
            "site_uid": self.uid(pid), "datetime_utc": ts,
            "value": pd.to_numeric(df.get("DepthToWaterBGS"), errors="coerce"),
            "source_param": "DepthToWaterBGS", "source_unit": "ft",
            "qualifier": (df.get("MeasurementMethod").fillna("") + "|" + df.get("LevelStatus").fillna("")).str.strip("|") if "MeasurementMethod" in df.columns else None,
            "utc_offset_min": None, "statistic": "instantaneous",
            "interval": "irregular" if kind == "manual" else "instant",
        })
        out = out[out["value"].notna() & out["datetime_utc"].notna()]
        return self.xw.apply(out, self.name)

    def _normalize_collab(self, text: str) -> pd.DataFrame | None:
        df = pd.read_csv(io.StringIO(text), dtype=str)
        if df.empty:
            return None
        col = next((c for c in df.columns if c.startswith("DepthToWaterBelowGroundSurface")), None)
        if not col:
            return None
        out = pd.DataFrame({
            "site_uid": "nmbgmr:" + df["PointID"].astype(str),
            "datetime_utc": pd.to_datetime(df["DateMeasured"], errors="coerce", utc=True),
            "value": pd.to_numeric(df[col], errors="coerce"),
            "source_param": "DepthToWaterBGS", "source_unit": "ft",
            "qualifier": (df.get("MeasurementMethod", "").fillna("") + "|" + df.get("MeasuringAgency", "").fillna("")).str.strip("|"),
            "utc_offset_min": None, "statistic": "instantaneous", "interval": "irregular",
        })
        out = out[out["value"].notna() & out["datetime_utc"].notna()].drop_duplicates()
        return self.xw.apply(out, self.name)
