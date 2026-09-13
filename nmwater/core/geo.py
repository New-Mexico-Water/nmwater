"""Geographic helpers: bbox tests, basin naming from HUC codes, and (once WBD is
downloaded in Phase 3/4) point-in-HUC lookups."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import pandas as pd

log = logging.getLogger("nmwater.geo")

# HUC4/HUC6-level basin names relevant to the New Mexico system view.
BASIN_BY_HUC_PREFIX = {
    "1301": "Rio Grande Headwaters",   # CO
    "1302": "Rio Grande-Elephant Butte",
    "1303": "Rio Grande-Mimbres",      # closed + Mimbres
    "1304": "Rio Grande-Amistad",      # TX below Fort Quitman
    "1305": "Rio Grande Closed Basins",
    "1306": "Upper Pecos",
    "1307": "Lower Pecos",
    "1308": "Rio Grande-Amistad (TX)",
    "1408": "San Juan",
    "1502": "Little Colorado",
    "1504": "Upper Gila",
    "1508": "Sonora (Animas Valley)",
    "1108": "Upper Canadian",
    "1109": "Lower Canadian",
    "1104": "Upper Cimarron",
    "1112": "North Fork Red",
    "1203": "Brazos Headwaters",
    "1205": "Colorado Headwaters (TX)",
}


def basin_from_huc(huc: str | None) -> str | None:
    if not huc:
        return None
    huc = str(huc)
    for n in (4,):
        if huc[:n] in BASIN_BY_HUC_PREFIX:
            return BASIN_BY_HUC_PREFIX[huc[:n]]
    if huc[:2] == "13":
        return "Rio Grande"
    if huc[:2] == "14":
        return "Upper Colorado"
    if huc[:2] == "15":
        return "Lower Colorado"
    if huc[:2] == "11":
        return "Arkansas-White-Red"
    if huc[:2] == "12":
        return "Texas-Gulf"
    return None


@lru_cache(maxsize=1)
def _wbd(huc_level: int, grids_dir: str):
    """Load WBD HUC polygons from data/grids/wbd if present (GeoPackage per HU2)."""
    try:
        import geopandas as gpd
    except ImportError:  # pragma: no cover
        return None
    d = Path(grids_dir) / "wbd"
    if not d.exists():
        return None
    frames = []
    for gpkg in sorted(d.glob("WBD_*_HU2_GPKG.gpkg")) + sorted(d.glob("*.gpkg")):
        layer = f"WBDHU{huc_level}"
        try:
            frames.append(gpd.read_file(gpkg, layer=layer, columns=[f"huc{huc_level}", "name"]))
        except Exception as e:
            log.debug("WBD read failed %s: %s", gpkg, e)
    if not frames:
        return None
    import pandas as _pd

    g = _pd.concat(frames, ignore_index=True)
    return g.to_crs(4326) if g.crs else g


def assign_hucs(sites: pd.DataFrame, grids_dir: Path) -> pd.DataFrame:
    """Fill huc8/huc12 by spatial join where missing. No-op if WBD not downloaded yet."""
    try:
        import geopandas as gpd
    except ImportError:
        return sites
    out = sites.copy()
    pts = out[out["lat"].notna() & out["lon"].notna()]
    if pts.empty:
        return out
    gpts = gpd.GeoDataFrame(pts[["site_uid"]], geometry=gpd.points_from_xy(pts["lon"], pts["lat"]), crs=4326)
    for level in (8, 12):
        col = f"huc{level}"
        wbd = _wbd(level, str(grids_dir))
        if wbd is None:
            continue
        joined = gpd.sjoin(gpts, wbd[[col, "geometry"]], how="left", predicate="within")
        joined = joined.drop_duplicates("site_uid").set_index("site_uid")[col]
        if col not in out.columns:
            out[col] = None
        mask = out[col].isna()
        out.loc[mask, col] = out.loc[mask, "site_uid"].map(joined)
    if "basin" in out.columns:
        mask = out["basin"].isna()
        out.loc[mask, "basin"] = out.loc[mask, "huc8"].map(basin_from_huc)
    else:
        out["basin"] = out["huc8"].map(basin_from_huc)
    return out


# --- Administrative geography (Census TIGER/Line) ---------------------------------
# Watersheds say where water comes from; these say who uses it. Both partitions are kept
# because the interesting questions sit where they disagree. See docs/interpretation.md.
TIGER_LAYERS = {
    # layer: (region_type, id column, name column)
    "place": ("place", "GEOID", "NAMELSAD"),
    "county": ("county", "GEOID", "NAMELSAD"),
    "cousub": ("county_subdivision", "GEOID", "NAMELSAD"),
    "tract": ("tract", "GEOID", "NAMELSAD"),
    "bg": ("block_group", "GEOID", "NAMELSAD"),
    "aiannh": ("tribal_area", "GEOID", "NAMELSAD"),
    "uac": ("urban_area", "GEOID20", "NAMELSAD20"),
}


def assign_regions(sites: pd.DataFrame, grids_dir: Path, layers: list[str] | None = None) -> pd.DataFrame:
    """Point-in-polygon every located site against the TIGER layers on disk.

    Returns a long frame (site_uid, region_type, region_id, region_name). Long rather than wide
    because a site sits in several nested regions at once, and most sites sit in none of the
    small ones: 99% of New Mexico's area is outside any incorporated place.
    """
    empty = pd.DataFrame(columns=["site_uid", "region_type", "region_id", "region_name"])
    try:
        import geopandas as gpd
    except ImportError:  # pragma: no cover
        return empty
    tdir = Path(grids_dir) / "tiger"
    if not tdir.exists():
        log.info("no TIGER boundaries yet; run `nmwater fetch tiger`")
        return empty
    pts = sites[sites["lat"].notna() & sites["lon"].notna()]
    if pts.empty:
        return empty
    gpts = gpd.GeoDataFrame(pts[["site_uid"]].copy(),
                            geometry=gpd.points_from_xy(pts["lon"], pts["lat"]), crs=4326)
    out = []
    for layer in (layers or TIGER_LAYERS):
        rtype, idcol, namecol = TIGER_LAYERS[layer]
        gpkg = tdir / f"{layer}.gpkg"
        if not gpkg.exists():
            continue
        try:
            poly = gpd.read_file(gpkg, layer=layer)
            if poly.crs is not None and poly.crs.to_epsg() != 4326:
                poly = poly.to_crs(4326)
            cols = [c for c in (idcol, namecol) if c in poly.columns]
            joined = gpd.sjoin(gpts, poly[cols + ["geometry"]], how="inner", predicate="within")
            if joined.empty:
                continue
            out.append(pd.DataFrame({
                "site_uid": joined["site_uid"].values,
                "region_type": rtype,
                "region_id": joined[idcol].values if idcol in joined else None,
                "region_name": joined[namecol].values if namecol in joined else None,
            }))
            log.info("regions: %d sites in %s", len(joined), rtype)
        except Exception as e:
            log.warning("region join failed for %s: %s", layer, e)
    if not out:
        return empty
    return pd.concat(out, ignore_index=True).drop_duplicates()
