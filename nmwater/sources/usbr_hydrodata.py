"""Bureau of Reclamation Upper Colorado HydroData: flat CSV per site/datatype.

    {base}/reservoir_data/meta.csv, {base}/gage_data/meta.csv
    {base}/{category}/{site_id}/csv/{datatype_id}.csv   -> "datetime,<name>" whole period of record
"""

from __future__ import annotations

import io
import json
from datetime import date

import pandas as pd

from ..core.geo import basin_from_huc
from .base import FetchSummary, Source, register

CATEGORIES = ("reservoir_data", "gage_data")


@register
class USBRHydroData(Source):
    name = "usbr_hydrodata"
    agency = "USBR"
    description = "Reclamation UC HydroData daily reservoir ops and gauge flows (1899-)"

    def _meta(self, category: str, refresh: bool = False) -> pd.DataFrame:
        art = self.get(f"{self.cfg.base_url}/{category}/meta.csv", kind="meta", refresh=refresh)
        df = pd.read_csv(io.BytesIO(art.read_bytes()), dtype=str, low_memory=False)
        cols = []
        for c in df.columns:
            c = c.strip().lower()
            for pre in ("site_metadata.", "datatype_metadata."):
                if c.startswith(pre):
                    c = c[len(pre):]
            cols.append(c)
        df.columns = cols
        df = df.loc[:, ~df.columns.duplicated()]
        if "hydrologic_unit" in df.columns:
            df["hydrologic_unit"] = df["hydrologic_unit"].fillna("").astype(str).str.replace(r"\.0$", "", regex=True)
        df["category"] = category
        return df

    def _select_nm(self, meta: pd.DataFrame) -> pd.DataFrame:
        name = meta.get("site_common_name", pd.Series("", index=meta.index)).fillna("")
        lat = pd.to_numeric(meta.get("lat"), errors="coerce")
        lon = pd.to_numeric(meta.get("longi"), errors="coerce")
        huc = meta.get("hydrologic_unit", pd.Series("", index=meta.index)).fillna("").astype(str)
        usgs = meta.get("usgs_id", pd.Series("", index=meta.index)).fillna("").astype(str).str.strip()
        allow = set(self.scope.allow_sites.get("usgs", []))
        state_id = meta.get("state_id", pd.Series("", index=meta.index)).fillna("").astype(str)
        m_name = name.str.contains(r",\s*NM\b|\bNM\b", regex=True)
        m_bbox = pd.Series([self.scope.in_bbox(a, b) for a, b in zip(lat, lon)], index=meta.index)
        m_huc = huc.str[:4].isin(set(self.scope.huc4))
        m_allow = usgs.isin(allow)
        m_state = state_id == "31"  # HDB internal state_id for New Mexico (observed on Brantley, Heron)
        return meta[m_name | m_bbox | m_huc | m_allow | m_state]

    def discover(self) -> pd.DataFrame:
        frames = [self._select_nm(self._meta(c, refresh=True)) for c in CATEGORIES]
        meta = pd.concat(frames, ignore_index=True)
        sites = []
        for sid, g in meta.groupby("site_id"):
            r = g.iloc[0]
            huc = str(r.get("hydrologic_unit") or "") or None
            stype = "reservoir" if r["category"] == "reservoir_data" else "stream"
            nm = str(r.get("site_common_name") or "")
            if any(k in nm.lower() for k in ("canal", "ditch", "conveyance", "drain")):
                stype = "canal"
            sites.append(
                {
                    "native_id": str(sid),
                    "name": nm,
                    "lat": pd.to_numeric(r.get("lat"), errors="coerce"),
                    "lon": pd.to_numeric(r.get("longi"), errors="coerce"),
                    "elevation_m": pd.to_numeric(r.get("elevation"), errors="coerce") * 0.3048
                    if "elevation" in r else None,
                    "site_type": stype,
                    "agency": "USBR",
                    "state": "NM" if (", NM" in nm or " NM" in nm or str(r.get("state_id") or "") == "31") else None,
                    "huc8": huc[:8] if huc and len(huc) >= 8 else None,
                    "basin": basin_from_huc(huc),
                    "active": True,
                    "raw_metadata": json.dumps(
                        {
                            "category": r["category"],
                            "usgs_id": r.get("usgs_id"),
                            "nws_code": r.get("nws_code"),
                            "shef_code": r.get("shef_code"),
                            "datatypes": sorted(set(g["datatype_id"].astype(str))),
                            "datatype_names": sorted(set(g.get("datatype_common_name", pd.Series(dtype=str)).astype(str))),
                        },
                        default=str,
                    ),
                }
            )
        return pd.DataFrame(sites)

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        meta = pd.concat([self._select_nm(self._meta(c)) for c in CATEGORIES], ignore_index=True)
        if site_ids:
            meta = meta[meta["site_id"].astype(str).isin([str(s) for s in site_ids])]
        if limit:
            keep = meta["site_id"].drop_duplicates().head(limit)
            meta = meta[meta["site_id"].isin(keep)]
        # Only series with a crosswalk mapping
        meta = meta[[self.xw.lookup(self.name, str(d)) is not None for d in meta["datatype_id"].astype(str)]]
        # Whole-POR files are small; re-download when incremental or refresh requested.
        do_refresh = refresh or since is not None

        def one(row) -> int:
            cat, sid, dt = row["category"], str(row["site_id"]), str(row["datatype_id"])
            url = f"{self.cfg.base_url}/{cat}/{sid}/csv/{dt}.csv"
            art = self.get(url, kind="data", site_uid=self.uid(sid), variable=dt, refresh=do_refresh,
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
            n = self.write_obs(df, tag=f"{sid}-{dt}")
            self.ledger.set_rows(art.request_key, n)
            return n

        rows = [r for _, r in meta.iterrows()]
        results = self.parallel(one, rows, desc="series")
        summ.n_rows = int(sum(results))
        summ.n_errors = len(rows) - len(results)
        return summ

    def normalize(self, artifact) -> pd.DataFrame | None:
        text = artifact.read_text()
        if not text.strip() or text.lstrip().startswith("<"):
            return None
        df = pd.read_csv(io.StringIO(text))
        if df.shape[1] < 2 or "datetime" not in df.columns[0].lower():
            return None
        url_parts = artifact.url.rstrip("/").split("/")
        dt_id = url_parts[-1].replace(".csv", "")
        sid = url_parts[-3]
        vcol = df.columns[1]
        out = pd.DataFrame(
            {
                "site_uid": self.uid(sid),
                "datetime_utc": pd.to_datetime(df.iloc[:, 0], errors="coerce", utc=True),
                "value": pd.to_numeric(df[vcol], errors="coerce"),
                "source_param": dt_id,
                "source_unit": None,
                "qualifier": None,
                "utc_offset_min": None,
            }
        )
        out = out[out["value"].notna() & out["datetime_utc"].notna()]
        # HDB missing-value sentinels seen in the data (-901 on storage/elevation series)
        out = out[~out["value"].isin([-901.0, -999.0, -9999.0, -99999.0, -998877.0])]
        out = self.xw.apply(out, self.name)
        out["interval"] = out["interval"].fillna("daily")
        return out
