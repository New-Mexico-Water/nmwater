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
    "1301": "Rio Grande Headwaters",
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
        except Exception as e:  # noqa: BLE001
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
