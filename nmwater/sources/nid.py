"""National Inventory of Dams: the number the fill fraction is missing without it.

reservoir_storage (from usbr_hydrodata, usace_cwms, resopsus, usgs) is how much water is in a
reservoir at a point in time. It says nothing about how full that is, because "full" needs a
capacity to divide by, and none of the operational sources publish one - they publish storage,
not the tank size.

NID is the Army Corps' national dam registry: essentially every regulated dam in the country,
with three different capacity figures per dam (design/flood storage, maximum recorded storage,
and normal/conservation-pool storage), hazard classification, purpose, height, and completion
year. The gap between design and normal storage is itself informative: Abiquiu is rated at
1,369,000 acre-feet design capacity but normally sits around 170,000, because it is a flood-
control dam built to catch a flood, not to hold water - see docs/interpretation.md.

Fetched as a single national CSV (no per-state filtering available; ~67 MB, ~93,000 dams),
filtered here to New Mexico and the neighbouring states in scope. Each dam is spatially matched
to the nearest NHDPlus waterbody polygon within `nid_max_distance_m` (default 3000 m - dam
points and the reservoir centroid they hold back can be a real distance apart at a narrow
canyon dam like Abiquiu or Cochiti), which is what lets capacity join to the storage time series
through `site_waterbodies` without any name matching:

    observations (reservoir_storage) -> site_waterbodies.comid -> reservoir_capacity.comid

Source: https://nid.sec.usace.army.mil/api/nation/csv - public, no key. Verified 2026-09-13.
Depends on `nhdplus` having run first (waterbodies.gpkg) for the capacity-to-reach match; runs
fine without it and still writes dam sites and the capacity table, just without `comid`.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.nid")

BASE = "https://nid.sec.usace.army.mil/api/nation/csv"
CITATION = "U.S. Army Corps of Engineers, National Inventory of Dams, accessed 2026."

STATE_FULL = {
    "NM": "New Mexico", "CO": "Colorado", "TX": "Texas", "AZ": "Arizona", "OK": "Oklahoma",
    "UT": "Utah", "KS": "Kansas",
}
NUMERIC_COLS = [
    "NID Height (Ft)", "Dam Length (Ft)", "Volume (Cubic Yards)", "NID Storage (Acre-Ft)",
    "Max Storage (Acre-Ft)", "Normal Storage (Acre-Ft)", "Surface Area (Acres)",
    "Drainage Area (Sq Miles)", "Max Discharge (Cubic Ft/Second)",
]
CAPACITY_COLS = {
    "NID ID": "nid_id", "Dam Name": "dam_name", "State": "state", "River or Stream Name": "river",
    "Primary Purpose": "purpose", "Primary Dam Type": "dam_type", "Year Completed": "year_completed",
    "Hazard Potential Classification": "hazard_class", "Operational Status": "operational_status",
    "NID Storage (Acre-Ft)": "nid_storage_af", "Max Storage (Acre-Ft)": "max_storage_af",
    "Normal Storage (Acre-Ft)": "normal_storage_af", "Surface Area (Acres)": "surface_area_acres",
    "Drainage Area (Sq Miles)": "drainage_area_sqmi", "NID Height (Ft)": "height_ft",
}


@register
class NID(Source):
    name = "nid"
    agency = "US Army Corps of Engineers"
    description = "National Inventory of Dams: capacity and hazard class, matched to reservoir waterbodies"
    kinds = ("dams", "capacity")

    def _dir(self) -> Path:
        d = self.settings.grids_dir / "nid"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _load(self, refresh: bool) -> pd.DataFrame:
        dest = self._dir() / "nation.csv"
        if refresh or not dest.exists():
            art = self.get(BASE, kind="dams", refresh=refresh)
            dest.write_bytes(art.read_bytes())
        # First line is a "Data Last Updated:,<date>" banner, not the header.
        df = pd.read_csv(dest, skiprows=1, dtype=str, low_memory=False)
        for c in NUMERIC_COLS:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df

    def _in_scope(self, df: pd.DataFrame) -> pd.DataFrame:
        states = [STATE_FULL[self.scope.state_abbr]] + [STATE_FULL[s] for s in self.scope.neighbor_states
                                                         if s in STATE_FULL]
        return df[df["State"].isin(states)].copy()

    def discover(self) -> pd.DataFrame:
        df = self._in_scope(self._load(refresh=True))
        rows = []
        # to_dict("records") rather than itertuples(): several column names ("NID Storage
        # (Acre-Ft)") are not valid Python identifiers, and itertuples silently renames those
        # to positional fields (_0, _1, ...) instead of raising, which breaks name lookups.
        for r in df.to_dict("records"):
            lat, lon = r.get("Latitude"), r.get("Longitude")
            try:
                lat, lon = float(lat), float(lon)
            except (TypeError, ValueError):
                lat = lon = None
            rows.append({
                "native_id": str(r.get("NID ID")),
                "name": r.get("Dam Name"),
                "lat": lat, "lon": lon,
                "elevation_m": None,
                "site_type": "dam",
                "agency": "USACE (NID)",
                "state": next((k for k, v in STATE_FULL.items() if v == r.get("State")), r.get("State")),
                "drainage_area_km2": (r.get("Drainage Area (Sq Miles)") or None) and r["Drainage Area (Sq Miles)"] * 2.589988,
                "active": str(r.get("Operational Status") or "").strip().lower() != "breached",
                "raw_metadata": json.dumps({v: r.get(k) for k, v in CAPACITY_COLS.items()}, default=str),
            })
        return pd.DataFrame(rows)

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        df = self._in_scope(self._load(refresh=refresh))
        summ.n_requests += 1
        if site_ids:
            df = df[df["NID ID"].isin(site_ids)]
        if limit:
            df = df.head(limit)
        if df.empty:
            summ.notes.append("no dams in scope")
            return summ

        cap = df.rename(columns=CAPACITY_COLS)[list(CAPACITY_COLS.values()) + ["Latitude", "Longitude"]]
        cap = cap.rename(columns={"Latitude": "lat", "Longitude": "lon"})
        cap["lat"] = pd.to_numeric(cap["lat"], errors="coerce")
        cap["lon"] = pd.to_numeric(cap["lon"], errors="coerce")

        comid, dist = self._match_waterbodies(cap)
        cap["comid"] = comid
        cap["match_distance_m"] = dist

        self.store.write_table(cap, "reference", self.name, "reservoir_capacity")
        summ.n_rows = len(cap)
        matched = cap["comid"].notna().sum()
        summ.notes.append(f"{len(cap)} dams in scope; {matched} matched to a waterbody polygon")
        return summ

    def _match_waterbodies(self, cap: pd.DataFrame):
        """Nearest-waterbody match so capacity joins to storage observations via comid."""
        import geopandas as gpd

        gpkg = self.settings.grids_dir / "nhdplus" / "waterbodies.gpkg"
        n = len(cap)
        if not gpkg.exists():
            log.warning("nid: waterbodies.gpkg not found (run `nmwater fetch nhdplus --kind waterbodies` "
                        "first); capacity will have no comid")
            return [None] * n, [None] * n
        wb = gpd.read_file(gpkg, layer="waterbodies", columns=["comid"])
        if wb.empty:
            return [None] * n, [None] * n
        has_xy = cap["lat"].notna() & cap["lon"].notna()
        pts = gpd.GeoDataFrame(cap.loc[has_xy, []], geometry=gpd.points_from_xy(
            cap.loc[has_xy, "lon"], cap.loc[has_xy, "lat"]), crs=4326)
        max_m = float(self.opt("nid_max_distance_m", 3000))
        aea = 5070
        joined = gpd.sjoin_nearest(pts.to_crs(aea), wb.to_crs(aea), max_distance=max_m, distance_col="d")
        joined = joined[~joined.index.duplicated(keep="first")]
        comid = pd.Series(None, index=cap.index, dtype="object")
        dist = pd.Series(float("nan"), index=cap.index, dtype="float64")
        comid.loc[joined.index] = joined["comid"].values
        dist.loc[joined.index] = joined["d"].round(1).values
        return list(comid), list(dist)
