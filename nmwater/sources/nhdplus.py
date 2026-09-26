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

Reservoirs and lakes are also pulled, from the companion `nhdwaterbody` layer: Elephant Butte,
Cochiti, Navajo, Heron and the rest come back as actual polygons with a COMID, a GNIS name, and
a surface area, not just the point gauges the station sources already carry. Reservoir and lake
sites are snapped to the waterbody they sit in or nearest to, the same way stream sites are
snapped to a reach.

Source: pynhd (HyRiver stack) against the USGS Water Mission Area GeoServer
(api.water.usgs.gov/geoserver/wmadata/ows). Public, no key. Verified 2026-09-13: the WFS layer
does not accept `huc8` as a CQL filter property ("Illegal property name"), so flowlines are
queried by intersecting geometry against each HUC8 polygon instead, which is also more correct
at boundaries than a HUC8 attribute would be.
"""

from __future__ import annotations

import logging
import re
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
WATERBODY_KEEP_COLS = ["comid", "gnis_id", "gnis_name", "areasqkm", "elevation", "reachcode",
                       "ftype", "fcode", "onoffnet"]
# Max distance (m) to associate a reservoir/lake site with a waterbody polygon it does not
# fall inside - dam-crest and outlet gauges commonly sit just outside the digitized shoreline.
WATERBODY_MAX_DISTANCE_M = 2000.0



def _name(v) -> str | None:
    """NHD leaves unnamed reaches as ' ' or NaN; normalise both to None. Some NHDPlus v2 names carry
    '¿' where the GNIS name has 'ñ' (Ca¿ones Creek); inside a word '¿' can only be that corruption."""
    if not isinstance(v, str) or not v.strip():
        return None
    return re.sub(r"(?<=\w)¿(?=\w)", "ñ", v.strip())


def downstream_names(a: pd.DataFrame, max_steps: int = 2000) -> dict[int, tuple[str | None, int | None]]:
    """For every unnamed reach, the GNIS name of the first named reach downstream and how many
    reaches away it is. Follows tonode -> fromnode, taking the main path at divergences
    (divergence 2 marks the minor branch). Closed basins and reaches that leave the extract
    end without a name."""
    a = a.sort_values("divergence", key=lambda d: (d == 2).astype(int))   # main path first
    nxt = a.drop_duplicates("fromnode").set_index("fromnode")["comid"].to_dict()
    # plain dict: assigning None back into a pandas string column turns it into NaN again
    name = {c: _name(g) for c, g in zip(a["comid"], a["gnis_name"])}
    to = dict(zip(a["comid"], a["tonode"]))
    memo: dict[int, tuple[str | None, int | None]] = {}
    for c in [c for c, g in name.items() if g is None]:
        path, cur, hit = [], c, (None, None)
        while len(path) < max_steps:
            if cur in memo:
                hit = memo[cur]
                break
            path.append(cur)
            cur = nxt.get(to.get(cur))
            if cur is None or cur in path:
                break
            if name.get(cur):
                hit = (name[cur], 0)
                break
        # path[i] is len(path)-i reaches from the hit (plus the hit's own distance)
        for i, p in enumerate(path):
            memo[p] = (hit[0], None if hit[0] is None else len(path) - i + (hit[1] or 0))
    return {int(k): v for k, v in memo.items()}

@register
class NHDPlus(Source):
    name = "nhdplus"
    agency = "USGS / EPA"
    description = "NHDPlus v2 medium-resolution flowlines, joined to sites via nearest-reach snap"
    kinds = ("flowlines", "site_reaches", "waterbodies", "site_waterbodies")

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
        summ = FetchSummary(self.name)
        wanted = set(opts.get("kinds") or self.kinds)
        h8 = self._huc8_in_scope()
        if site_ids:
            want = {s.split(":")[-1] for s in site_ids}
            h8 = h8[h8["huc8"].isin(want)]
        if limit:
            h8 = h8.head(limit)

        if "flowlines" in wanted or "site_reaches" in wanted:
            gpkg = self._fetch_layer(h8, refresh, summ, layer="flowlines",
                                     water_data="nhdflowline_network", keep_cols=KEEP_COLS,
                                     label="reaches", attr_table="flowline_attributes")
            if "site_reaches" in wanted:
                n = self._snap_sites(gpkg)
                summ.notes.append(f"{n} sites snapped to a flowline reach")

        if "waterbodies" in wanted or "site_waterbodies" in wanted:
            gpkg = self._fetch_layer(h8, refresh, summ, layer="waterbodies",
                                     water_data="nhdwaterbody", keep_cols=WATERBODY_KEEP_COLS,
                                     label="waterbodies", attr_table="waterbody_attributes")
            if "site_waterbodies" in wanted:
                n = self._snap_waterbody_sites(gpkg)
                summ.notes.append(f"{n} sites matched to a waterbody polygon")
        return summ

    def _fetch_layer(self, h8: pd.DataFrame, refresh: bool, summ: FetchSummary, *, layer: str,
                     water_data: str, keep_cols: list[str], label: str, attr_table: str) -> Path:
        """Shared HUC8-by-HUC8 fetcher for both the flowline and waterbody NHDPlus layers."""
        import geopandas as gpd
        import pyogrio
        from pynhd import WaterData

        gpkg = self._dir() / f"{layer}.gpkg"
        already = set()
        if gpkg.exists() and not refresh:
            try:
                existing = pyogrio.read_dataframe(gpkg, layer=layer, columns=["huc8"])
                already = set(existing["huc8"].astype(str).unique())
            except Exception:
                already = set()

        wd = WaterData(water_data)
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
                feat = wd.bygeom(geom_row.geometry, geo_crs=geom_row.crs)
                summ.n_requests += 1
            except Exception as ex:
                summ.n_errors += 1
                summ.notes.append(f"{layer} {huc8}: {str(ex)[:120]}")
                log.warning("nhdplus %s %s failed: %s", layer, huc8, str(ex)[:200])
                continue
            if feat is None or feat.empty:
                continue
            cols = [c for c in keep_cols if c in feat.columns]
            feat = feat[[*cols, "geometry"]].copy()
            feat["huc8"] = huc8
            feat["huc8_name"] = row.name
            frames.append(feat)
            n_done += 1
            if n_done % 20 == 0:
                log.info("nhdplus %s: %d/%d HUC8s fetched", layer, n_done, len(h8))

        if frames:
            new = pd.concat(frames, ignore_index=True)
            new = gpd.GeoDataFrame(new, geometry="geometry", crs=4326)
            mode = "a" if gpkg.exists() and not refresh else "w"
            new.to_file(gpkg, driver="GPKG", layer=layer, mode=mode)
            summ.n_rows += len(new)
            summ.notes.append(f"{len(new)} {label} across {n_done} HUC8s -> {gpkg.name}")
            attrs = pd.DataFrame(new.drop(columns=["geometry"])).drop_duplicates("comid")
            self.store.write_table(attrs, "reference", self.name, attr_table)
        return gpkg

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
        # OSE points of diversion are typed "diversion" unless known to be wells, but most carry no
        # surface/ground-water code; snap only those marked surface water ("S") so a well is never
        # labelled with the river it happens to be near.
        pod = sites["site_uid"].str.startswith("ose_arcgis:pod:")
        surface = sites["raw_metadata"].fillna("").str.contains('"grnd_wtr_s": "S"', regex=False)
        sites = sites[~pod | surface]
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
        names = [_name(n) for n in joined["gnis_name"]]
        down = self._downstream_names(gpkg)
        river = [n if n else down.get(int(c), (None, None))[0] for n, c in zip(names, joined["comid"])]
        steps = [0 if n else down.get(int(c), (None, None))[1] for n, c in zip(names, joined["comid"])]
        out = pd.DataFrame({
            "site_uid": joined["site_uid"].values,
            "comid": joined["comid"].values,
            "gnis_name": names,
            # the reach's own name, else the first named river downstream (an unnamed tributary or
            # ditch is labelled with what it drains to); river_steps = reaches walked, 0 = own name
            "river_name": river,
            "river_steps": pd.array(steps, dtype="Int64"),
            "streamorde": joined["streamorde"].values,
            "totdasqkm": joined["totdasqkm"].values,
            "huc8": joined["huc8"].values,
            "snap_distance_m": joined["snap_distance_m"].round(1).values,
        }).drop_duplicates("site_uid")
        self.store.write_table(out, "reference", self.name, "site_reaches")
        return len(out)

    def _downstream_names(self, gpkg: Path) -> dict[int, tuple[str | None, int | None]]:
        import geopandas as gpd

        a = gpd.read_file(gpkg, layer="flowlines", columns=["comid", "gnis_name", "fromnode", "tonode", "divergence"],
                          ignore_geometry=True)
        return downstream_names(a)

    def _snap_waterbody_sites(self, gpkg: Path) -> int:
        """Match reservoir/lake sites to the waterbody polygon they fall inside, falling back to
        the nearest one within WATERBODY_MAX_DISTANCE_M for dam-crest and outlet gauges that sit
        just outside the digitized shoreline. `match_type` records which rule fired, so a
        consumer can tell a point genuinely inside Elephant Butte from one merely near it."""
        import geopandas as gpd

        if not gpkg.exists():
            return 0
        sites = self.store.read_sites()
        sites = sites[sites["site_type"].isin(["reservoir", "lake"])
                      & sites["lat"].notna() & sites["lon"].notna()]
        if sites.empty:
            return 0
        wb = gpd.read_file(gpkg, layer="waterbodies",
                           columns=["comid", "gnis_name", "areasqkm", "ftype", "huc8", "huc8_name"])
        wb = wb[wb["ftype"].isin(["LakePond", "Reservoir"])]
        if wb.empty:
            return 0
        pts = gpd.GeoDataFrame(sites[["site_uid"]], geometry=gpd.points_from_xy(sites["lon"], sites["lat"]), crs=4326)
        aea = 5070
        pts_p, wb_p = pts.to_crs(aea), wb.to_crs(aea)

        within = gpd.sjoin(pts_p, wb_p, how="inner", predicate="within")
        within_ids = set(within["site_uid"]) if len(within) else set()
        remaining = pts_p[~pts_p["site_uid"].isin(within_ids)]
        nearest = (gpd.sjoin_nearest(remaining, wb_p, max_distance=WATERBODY_MAX_DISTANCE_M,
                                     distance_col="distance_m")
                   if len(remaining) else remaining.assign(distance_m=pd.Series(dtype=float)))

        rows = []
        if len(within):
            rows.append(pd.DataFrame({
                "site_uid": within["site_uid"].values, "comid": within["comid"].values,
                "gnis_name": within["gnis_name"].values, "areasqkm": within["areasqkm"].values,
                "huc8": within["huc8"].values, "match_type": "within", "distance_m": 0.0,
            }))
        if len(nearest):
            rows.append(pd.DataFrame({
                "site_uid": nearest["site_uid"].values, "comid": nearest["comid"].values,
                "gnis_name": nearest["gnis_name"].values, "areasqkm": nearest["areasqkm"].values,
                "huc8": nearest["huc8"].values, "match_type": "nearest",
                "distance_m": nearest["distance_m"].round(1).values,
            }))
        if not rows:
            return 0
        out = pd.concat(rows, ignore_index=True).drop_duplicates("site_uid")
        self.store.write_table(out, "reference", self.name, "site_waterbodies")
        return len(out)
