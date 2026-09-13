"""NHDPlus v2 flowlines: the river network itself, not just where things sit on it.

Everything else in this archive locates a site by point (lat/lon) or by area (a HUC polygon,
a Census place). Neither says whether one gauge is upstream of another, how far apart they are
along the channel, or what the named stream actually is. NHDPlus v2 is the medium-resolution
national stream network - each reach carries a COMID, a named-stream identifier (GNIS), a
Strahler stream order, cumulative drainage area, and topology (fromnode/tonode, hydroseq) that
lets you trace a path.

COMID is also the join key the National Water Model uses for `feature_id` (v2, not v3 - see the
gotcha below), so pulling this now is what makes that dataset usable later without redoing the
network build.

Fetched by HUC8, geometry-clipped to the WBD polygons `wbd` already downloaded (so this source
depends on `wbd` having run first). Only the network-flagged flowlines are kept
(`nhdflowline_network`); non-network artificial paths through waterbodies come along too since
they carry the through-flow for named rivers like the Rio Grande where it widens into a
reservoir.

Source: pynhd (HyRiver stack) against the USGS Water Mission Area GeoServer
(api.water.usgs.gov/geoserver/wmadata/ows). Public, no key. Verified 2026-09-13: the WFS layer
does not accept `huc8` as a CQL filter property ("Illegal property name"), so flowlines are
queried by intersecting geometry against each HUC8 polygon instead, which is also more correct
at boundaries than a HUC8 attribute would be.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.nhdplus")

CITATION = ("U.S. Geological Survey / U.S. EPA, National Hydrography Dataset Plus Version 2 "
            "(NHDPlus V2), medium resolution, accessed 2026 via api.water.usgs.gov.")
# Columns kept from the WaterData response; the rest (dates, admin fields) are dropped.
KEEP_COLS = [
    "comid", "gnis_id", "gnis_name", "reachcode", "ftype", "fcode", "lengthkm",
    "streamorde", "streamcalc", "streamleve", "arbolatesu", "totdasqkm", "divergence",
    "fromnode", "tonode", "hydroseq", "levelpathi", "pathlength", "terminalpa", "startflag",
]


@register
class NHDPlus(Source):
    name = "nhdplus"
    agency = "USGS / EPA"
    description = "NHDPlus v2 medium-resolution flowlines, joined to sites via nearest-reach snap"
    kinds = ("flowlines", "site_reaches")

    def _dir(self) -> Path:
        d = self.settings.grids_dir / "nhdplus"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _huc8_in_scope(self) -> pd.DataFrame:
        p = self.store.root / "reference" / "source=wbd" / "huc8_in_scope.parquet"
        if not p.exists():
            raise RuntimeError("run `nmwater fetch wbd` first (need HUC8 polygons to clip flowlines to)")
        return pd.read_parquet(p)

    def discover(self) -> pd.DataFrame:
        h8 = self._huc8_in_scope()
        return pd.DataFrame(
            [{"native_id": f"huc8:{r.huc8}", "name": f"NHDPlus flowlines, {r.name}", "site_type": "area",
              "agency": "USGS", "active": True, "raw_metadata": "{}"} for r in h8.itertuples()]
        )

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        import geopandas as gpd
        import pyogrio
        from pynhd import WaterData

        summ = FetchSummary(self.name)
        h8 = self._huc8_in_scope()
        if site_ids:
            want = {s.split(":")[-1] for s in site_ids}
            h8 = h8[h8["huc8"].isin(want)]
        if limit:
            h8 = h8.head(limit)

        gpkg = self._dir() / "flowlines.gpkg"
        already = set()
        if gpkg.exists() and not refresh:
            try:
                existing = pyogrio.read_dataframe(gpkg, layer="flowlines", columns=["huc8"])
                already = set(existing["huc8"].astype(str).unique())
            except Exception:
                already = set()

        wd = WaterData("nhdflowline_network")
        frames = []
        n_done = 0
        for row in h8.itertuples():
            huc8 = str(row.huc8)
            if huc8 in already:
                summ.n_cached += 1
                continue
            try:
                geom_row = self._huc8_geometry(huc8)
                if geom_row is None:
                    summ.notes.append(f"{huc8}: no polygon found in WBD, skipped")
                    continue
                flo = wd.bygeom(geom_row.geometry, geo_crs=geom_row.crs)
                summ.n_requests += 1
            except Exception as ex:
                summ.n_errors += 1
                summ.notes.append(f"{huc8}: {str(ex)[:120]}")
                log.warning("nhdplus %s failed: %s", huc8, str(ex)[:200])
                continue
            if flo is None or flo.empty:
                continue
            cols = [c for c in KEEP_COLS if c in flo.columns]
            flo = flo[[*cols, "geometry"]].copy()
            flo["huc8"] = huc8
            flo["huc8_name"] = row.name
            frames.append(flo)
            n_done += 1
            if n_done % 20 == 0:
                log.info("nhdplus: %d/%d HUC8s fetched", n_done, len(h8))

        if frames:
            new = pd.concat(frames, ignore_index=True)
            new = gpd.GeoDataFrame(new, geometry="geometry", crs=4326)
            mode = "a" if gpkg.exists() and not refresh else "w"
            new.to_file(gpkg, driver="GPKG", layer="flowlines", mode=mode)
            summ.n_rows += len(new)
            summ.notes.append(f"{len(new)} reaches across {n_done} HUC8s -> {gpkg.name}")
            attrs = pd.DataFrame(new.drop(columns=["geometry"])).drop_duplicates("comid")
            self.store.write_table(attrs, "reference", self.name, "flowline_attributes")

        # Snap stream/canal/diversion sites to their nearest reach.
        if "site_reaches" in (opts.get("kinds") or self.kinds):
            n = self._snap_sites(gpkg)
            summ.notes.append(f"{n} sites snapped to a flowline reach")
        return summ

    def _huc8_geometry(self, huc8: str):
        import geopandas as gpd

        for region_gpkg in sorted((self.settings.grids_dir / "wbd").glob("WBD_*_HU2_GPKG.gpkg")):
            try:
                df = gpd.read_file(region_gpkg, layer="WBDHU8", where=f"huc8 = '{huc8}'")
            except Exception:
                continue
            if len(df):
                row = df.iloc[0]
                row["crs"] = df.crs
                return row
        return None

    def _snap_sites(self, gpkg: Path, max_distance_m: float = 500.0) -> int:
        """Nearest-reach join for point sites of stream-like type. Distance in metres via a local
        equal-area projection; anything farther than max_distance_m is left unassigned rather than
        forced onto the wrong tributary."""
        import geopandas as gpd

        if not gpkg.exists():
            return 0
        sites = self.store.read_sites()
        sites = sites[sites["site_type"].isin(["stream", "canal", "diversion", "return_flow"])
                      & sites["lat"].notna() & sites["lon"].notna()]
        if sites.empty:
            return 0
        flo = gpd.read_file(gpkg, layer="flowlines", columns=["comid", "gnis_name", "streamorde",
                                                               "totdasqkm", "huc8", "huc8_name"])
        if flo.empty:
            return 0
        pts = gpd.GeoDataFrame(sites[["site_uid"]], geometry=gpd.points_from_xy(sites["lon"], sites["lat"]), crs=4326)
        aea = 5070  # CONUS Albers Equal Area: metres, good enough for a nearest-reach snap
        pts_p, flo_p = pts.to_crs(aea), flo.to_crs(aea)
        joined = gpd.sjoin_nearest(pts_p, flo_p, max_distance=max_distance_m, distance_col="snap_distance_m")
        if joined.empty:
            return 0
        out = pd.DataFrame({
            "site_uid": joined["site_uid"].values,
            "comid": joined["comid"].values,
            "gnis_name": joined["gnis_name"].values,
            "streamorde": joined["streamorde"].values,
            "totdasqkm": joined["totdasqkm"].values,
            "huc8": joined["huc8"].values,
            "snap_distance_m": joined["snap_distance_m"].round(1).values,
        }).drop_duplicates("site_uid")
        self.store.write_table(out, "reference", self.name, "site_reaches")
        return len(out)
