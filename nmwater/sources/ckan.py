"""NM Water Data Catalog (CKAN) harvest + targeted resource downloads.

Verified 2026-09-12: the CKAN Action API works with a browser User-Agent
(current_package_list_with_resources -> 376 datasets); resource file downloads under
/dataset/.../download/... are behind a Cloudflare JS challenge (403) even with cookies, so they are
listed in docs/manual_downloads.md and ingested from data/manual/ckan/ when present.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

import pandas as pd

from ._manual import manual_dir, note_manual
from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.ckan")

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
TARGET_DATASETS = [
    "water-authority-asr-monitoring-wells", "wua-data-gap-well-groundwater-levels",
    "buckman-wells-water-level-monitoring-program", "northwest-well-and-city-wellfield-water-level-monitoring-program",
    "pecos_region_manual_groundwater_levels", "seven-rivers-monitoring-network", "groundwater-levels",
    "open-file-report-626", "pecos-valley-data-inventory", "data-dictionary-sdwis",
]
TARGET_TITLE_RE = re.compile(r"drying|river dry|mrgescp", re.IGNORECASE)
DATA_FORMATS = {"CSV", "XLSX", "XLS", "GEOJSON", "ZIP", "JSON", "MDB", "APPLICATION/MSACCESS"}


@register
class CKAN(Source):
    name = "ckan"
    agency = "NM Water Data Initiative"
    description = "NM Water Data Catalog metadata harvest and groundwater-level resource files"
    kinds = ("catalog", "resource")

    @property
    def base(self) -> str:
        return self.cfg.base_url or "https://catalog.newmexicowaterdata.org"

    def _packages(self, refresh: bool = True) -> list[dict]:
        out: list[dict] = []
        offset = 0
        while True:
            art = self.get(f"{self.base}/api/3/action/current_package_list_with_resources",
                           params={"limit": 400, "offset": offset}, kind="catalog", refresh=refresh,
                           headers={"User-Agent": UA}, key_extra=date.today().isoformat())
            d = art.read_json()
            res = d.get("result") or []
            out.extend(res)
            if len(res) < 400:
                break
            offset += 400
        return out

    def discover(self) -> pd.DataFrame:
        pk = self._packages()
        ds_rows, res_rows = [], []
        for p in pk:
            org = (p.get("organization") or {}).get("name")
            groups = ";".join(g.get("name", "") for g in p.get("groups") or [])
            ds_rows.append({"dataset_id": p.get("id"), "name": p.get("name"), "title": p.get("title"), "org": org,
                            "groups": groups, "notes": (p.get("notes") or "")[:2000], "metadata_modified": p.get("metadata_modified"),
                            "num_resources": p.get("num_resources"), "license": p.get("license_title"),
                            "contact": p.get("data_contact_email")})
            for r in p.get("resources") or []:
                res_rows.append({"dataset_name": p.get("name"), "resource_id": r.get("id"), "name": r.get("name"),
                                 "format": (r.get("format") or "").upper(), "url": r.get("url"),
                                 "last_modified": r.get("last_modified") or r.get("created"),
                                 "description": (r.get("description") or "")[:500]})
        self.store.write_table(pd.DataFrame(ds_rows), "reference", self.name, "ckan_datasets")
        self.store.write_table(pd.DataFrame(res_rows), "reference", self.name, "ckan_resources")
        log.info("ckan: %d datasets, %d resources harvested", len(ds_rows), len(res_rows))
        # sites come from ingested manual files (below); the harvest itself has no sites
        sites = self._sites_from_manual()
        return sites if sites is not None else pd.DataFrame(columns=["native_id", "name", "lat", "lon", "site_type",
                                                                     "agency", "state", "raw_metadata"])

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        pk = self._packages(refresh=False)
        targets = [p for p in pk if p.get("name") in TARGET_DATASETS or TARGET_TITLE_RE.search(p.get("title") or "")]
        mdir = manual_dir(self.settings.data_dir, self.name)
        jobs = []
        for p in targets:
            for r in p.get("resources") or []:
                fmt = (r.get("format") or "").upper()
                url = r.get("url") or ""
                if fmt in DATA_FORMATS and "/download/" in url:
                    jobs.append((p["name"], r))
        if limit:
            jobs = jobs[:limit]
        blocked = 0
        for dsname, r in jobs:
            url = r["url"]
            fname = url.rsplit("/", 1)[-1]
            local = mdir / fname
            if local.exists():
                continue
            try:
                art = self.get(url, kind="resource", refresh=refresh, headers={"User-Agent": UA})
                summ.n_requests += 1
                raw = art.read_bytes()
                if raw.lstrip()[:15].lower().startswith(b"<!doctype html") or b"Just a moment" in raw[:2000]:
                    raise RuntimeError("Cloudflare challenge")
                local.write_bytes(raw)
            except Exception as e:  # noqa: BLE001
                blocked += 1
                note_manual(self.name, url, fname, f"dataset {dsname}: {str(e)[:50]}")
        if blocked:
            summ.notes.append(f"{blocked} resource downloads blocked (Cloudflare); listed in docs/manual_downloads.md")
        # ingest whatever is in the manual folder
        n = 0
        for f in sorted(mdir.iterdir()):
            try:
                n += self._ingest_file(f, since)
            except Exception as e:  # noqa: BLE001
                summ.notes.append(f"{f.name}: not ingested ({str(e)[:80]})")
        summ.n_rows = n
        return summ

    # ---------------------------------------------------------------- manual-file ingestion (heuristic)
    def _read_any(self, f: Path) -> dict[str, pd.DataFrame]:
        suf = f.suffix.lower()
        if suf == ".csv":
            return {f.stem: pd.read_csv(f, dtype=str, low_memory=False)}
        if suf in (".xlsx", ".xls"):
            return {f"{f.stem}__{k}": v for k, v in pd.read_excel(f, sheet_name=None, dtype=str).items()}
        if suf == ".geojson":
            import geopandas as gpd

            return {f.stem: pd.DataFrame(gpd.read_file(f).drop(columns="geometry", errors="ignore"))}
        return {}

    def _ingest_file(self, f: Path, since: date | None) -> int:
        frames = self._read_any(f)
        total = 0
        for name, df in frames.items():
            if df is None or df.empty:
                continue
            df = df.dropna(how="all").dropna(axis=1, how="all")
            self.store.write_table(df.astype(str), "reference", self.name, f"manual_{_safe(name)}")
            obs = self._guess_observations(df, name)
            if obs is not None and len(obs):
                if since:
                    obs = obs[obs["datetime_utc"] >= pd.Timestamp(since, tz="UTC")]
                total += self.write_obs(obs, tag=_safe(name))
        return total

    def _guess_observations(self, df: pd.DataFrame, name: str) -> pd.DataFrame | None:
        cols = {c.lower().strip(): c for c in df.columns}
        dcol = next((cols[k] for k in cols if re.search(r"^(date|datetime|timestamp|measurement_?date|reading_?date|time)$", k)
                     or ("date" in k and "update" not in k)), None)
        idcol = next((cols[k] for k in cols if re.search(r"(well|site|point|station).*(id|name|number|no)$|^pointid$|^site_id$", k)), None)
        vmap = {}
        for k, c in cols.items():
            if re.search(r"depth.*water|dtw|depth_to_water|water_?level_?depth|depthtowater", k):
                vmap[c] = "gw_depth_to_water"
            elif re.search(r"(water|gw|groundwater).*(elev|elevation)|wl_?elev|elevation_?ft", k):
                vmap[c] = "gw_level_elevation"
        if not dcol or not vmap:
            return None
        frames = []
        for c, var in vmap.items():
            sub = pd.DataFrame({
                "site_uid": ("ckan:" + df[idcol].astype(str).str.strip()) if idcol else f"ckan:{_safe(name)}",
                "datetime_utc": pd.to_datetime(df[dcol], errors="coerce", utc=True),
                "value": pd.to_numeric(df[c], errors="coerce"),
                "variable": var, "unit": "ft", "source_param": c, "source_unit": "ft (assumed)",
                "statistic": "instantaneous", "interval": "irregular", "qualifier": f"manual file {name}",
                "utc_offset_min": None,
            })
            frames.append(sub[sub["value"].notna() & sub["datetime_utc"].notna()])
        out = pd.concat(frames, ignore_index=True)
        return out if len(out) else None

    def _sites_from_manual(self) -> pd.DataFrame | None:
        mdir = manual_dir(self.settings.data_dir, self.name)
        rows = []
        for f in sorted(mdir.iterdir()):
            try:
                frames = self._read_any(f)
            except Exception:  # noqa: BLE001
                continue
            for name, df in frames.items():
                cols = {c.lower().strip(): c for c in df.columns}
                idcol = next((cols[k] for k in cols if re.search(r"(well|site|point|station).*(id|name|number|no)$|^pointid$|^site_id$", k)), None)
                lat = next((cols[k] for k in cols if k in ("lat", "latitude", "y", "lat_dd")), None)
                lon = next((cols[k] for k in cols if k in ("lon", "long", "longitude", "x", "lon_dd", "lng")), None)
                if not idcol:
                    continue
                for r in df.drop_duplicates(idcol).to_dict("records"):
                    rows.append({"native_id": str(r[idcol]).strip(), "name": str(r[idcol]).strip(), "site_type": "well",
                                 "lat": pd.to_numeric(r.get(lat), errors="coerce") if lat else None,
                                 "lon": pd.to_numeric(r.get(lon), errors="coerce") if lon else None,
                                 "agency": "NM Water Data catalog (manual file)", "state": "NM",
                                 "raw_metadata": json.dumps({"file": f.name, "sheet": name})})
        return pd.DataFrame(rows).drop_duplicates("native_id") if rows else None


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", s)[:60]
