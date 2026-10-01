"""Facts about a river's reservoirs, irrigation, acequias and water users, counted from the archive.

Everything here comes from layers already in the archive, so it is sourced and repeatable:
- reservoirs: the curated registry (catalog/reservoirs.yaml, matched by river name) and the National
  Inventory of Dams (reservoir_capacity) for dams within ~1 km of the river with at least 500 acre-feet
  of normal storage;
- acequias: OSE's acequia layer (acequias.parquet), acequias with any part within ACEQUIA_KM of the
  river, counted per segment. The layer mapped northern New Mexico's acequias; many valleys (the
  middle Rio Grande's MRGCD canals, most of the Pecos) are not in it, so a zero means "not mapped";
- irrigation districts: OSE's district layer, districts within DISTRICT_KM of the river;
- water use: OSE Water Use by Categories 2020, surface-water withdrawals by category in the counties
  the river's segments cross (county totals, not the river alone, and labelled that way);
- public water systems: OSE's layer, systems whose service area is within PWS_KM of the river.
Distances are approximate (degrees converted at the river's latitude).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from ..catalog import reach_fixes

ACEQUIA_KM = 1.5        # acequias run beside their river; wider picks up the next valley's
DISTRICT_KM = 3.0
PWS_KM = 2.0
DAM_KM = 1.0
MIN_DAM_AF = 500.0
WU_CATEGORIES = {  # surface-water columns of OSE water use 2020 (acre-feet)
    "Irrigated agriculture": "Irrigated_argriculture_surface",
    "Public water supply": "Public_water_supply_from_surfac",
    "Reservoir evaporation": "Reservoir_evaporation_withdrawa",
    "Livestock": "Livestock__self_supplied__surfa",
    "Commercial": "Commercial__self_supplied__surf",
    "Industrial": "Industrial__self_supplied__surf",
    "Mining": "Mining__self_supplied__surface",
    "Power": "Power__self_supplied__surface_w",
    "Domestic": "Domestic__self_supplied__surfac",
}


@dataclass
class Context:
    reservoirs: list[dict] = field(default_factory=list)     # name, dam, year, purpose, type, storage_af, source
    acequias: dict = field(default_factory=dict)             # segment -> {"count", "names"}
    districts: list[dict] = field(default_factory=list)      # name, acres
    water_use: dict = field(default_factory=dict)            # {"counties": [...], "by_category": {cat: af}, "total_af"}
    water_systems: list[str] = field(default_factory=list)


def _deg(km: float, lat: float) -> float:
    return km / (111.32 * math.cos(math.radians(lat)))


def _wkt_frame(path: Path, cols: list[str]):
    import geopandas as gpd
    from shapely import wkt

    d = pd.read_parquet(path, columns=[*cols, "geometry_wkt"]).dropna(subset=["geometry_wkt"])
    return gpd.GeoDataFrame(d.drop(columns="geometry_wkt"), geometry=[wkt.loads(x) for x in d["geometry_wkt"]], crs=4326)


def build(river_label: str, gnis_id: str, segments: list[str], huc8_of: dict[str, str], con, root: Path) -> Context:
    import geopandas as gpd

    from .river_watershed import huc8_polygons

    grids = root / "data" / "grids"
    ose = root / "data" / "parquet" / "reference" / "source=ose_arcgis"
    hucs = huc8_polygons(grids / "wbd", [huc8_of[s] for s in segments if s in huc8_of])
    hucs["segment"] = hucs["huc8"].map({v: k for k, v in huc8_of.items()})
    area = hucs.union_all()
    flo = reach_fixes.apply(gpd.read_file(grids / "nhdplus" / "flowlines.gpkg", bbox=area.bounds))
    river = flo[(flo["gnis_id"].astype(str) == str(gnis_id))]
    river = river[river.intersects(area)]
    ctx = Context()
    if river.empty:
        return ctx
    line = river.union_all()
    lat = line.centroid.y

    # reservoirs: registry entries on this river, then NID dams on the main stem
    reg = yaml.safe_load((root / "catalog" / "reservoirs.yaml").read_text()) or {}
    reg = reg.get("reservoirs", reg)
    name_l = river_label.split(" (")[0].lower()
    base = name_l.removesuffix(" river").strip()          # "pecos river" -> "pecos"; NID writes "Rio Grande River"
    for key, r in (reg.items() if isinstance(reg, dict) else []):
        # the registry's river field starts with the river it is on ("Jemez River, above its confluence
        # with the Rio Grande" is on the Jemez, not the Rio Grande)
        if isinstance(r, dict) and str(r.get("river", "")).lower().startswith(base):
            ctx.reservoirs.append({"name": r.get("name", key), "dam": r.get("dam"), "source": "registry",
                                   "key": key, "role": r.get("role")})
    nid = con.sql("SELECT dam_name, river, purpose, dam_type, year_completed, normal_storage_af, nid_storage_af, lat, lon "
                  "FROM reservoir_capacity WHERE lat IS NOT NULL").df()
    nid = gpd.GeoDataFrame(nid, geometry=gpd.points_from_xy(nid["lon"], nid["lat"]), crs=4326)
    on_river = nid["river"].fillna("").str.lower().str.startswith(base)      # not "Tr-Rio Grande" or an arroyo
    near = nid[on_river & nid.within(line.buffer(_deg(DAM_KM, lat)))
               & (nid[["normal_storage_af", "nid_storage_af"]].max(axis=1) >= MIN_DAM_AF)
               & ~nid["dam_name"].str.contains(r"\bdike\b", case=False, na=False)]
    have = {str(r.get("dam") or "").lower() for r in ctx.reservoirs}
    for r in near.sort_values("nid_storage_af", ascending=False).itertuples():
        nm_ = str(r.dam_name).lower()
        match = next((x for x in ctx.reservoirs if x.get("dam") and str(x["dam"]).lower().startswith(nm_)), None)
        rec = {"dam": r.dam_name, "year": None if pd.isna(r.year_completed) else int(r.year_completed),
               "purpose": r.purpose, "type": r.dam_type, "storage_af": None if pd.isna(r.nid_storage_af) else float(r.nid_storage_af)}
        if match:
            match.update(rec)
        elif nm_ not in have:
            ctx.reservoirs.append({"name": r.dam_name, "source": "NID", **rec})

    # acequias per segment
    aq = _wkt_frame(ose / "acequias.parquet", ["cnvy_name"])
    aq = aq[aq.intersects(line.buffer(_deg(ACEQUIA_KM, lat)))]
    for s in segments:
        poly = hucs.loc[hucs["segment"] == s, "geometry"]
        if poly.empty:
            continue
        a = aq[aq.intersects(poly.iloc[0])]
        names = sorted({n.strip().title() for n in a["cnvy_name"].dropna() if n.strip() and n.strip().upper() != "UNKNOWN"})
        if len(a):
            ctx.acequias[s] = {"count": len(names) or len(a), "names": names}

    # irrigation districts
    dist = _wkt_frame(ose / "irrigation_districts.parquet", ["District", "ACRES"])
    dist = dist[dist.intersects(line.buffer(_deg(DISTRICT_KM, lat)))]
    for n, d in dist.groupby("District"):
        ctx.districts.append({"name": n, "acres": float(d["ACRES"].sum())})
    ctx.districts.sort(key=lambda x: -x["acres"])

    # water use in the counties the segments cross (surface water, 2020)
    counties = gpd.read_file(grids / "tiger" / "county.gpkg").to_crs(4326)
    counties = counties[(counties["STATEFP"] == "35") & counties.intersects(line)]
    wu = pd.read_parquet(ose / "water_use_2020_county.parquet")
    wu = wu[wu["Name_of_the_county_in_New_Mexic"].str.strip().str.lower().isin(counties["NAME"].str.lower())]
    if len(wu):
        by = {cat: float(pd.to_numeric(wu[col], errors="coerce").fillna(0).sum()) for cat, col in WU_CATEGORIES.items()}
        by = {k: v for k, v in sorted(by.items(), key=lambda kv: -kv[1]) if v > 0}
        ctx.water_use = {"counties": sorted(wu["Name_of_the_county_in_New_Mexic"].str.strip()), "by_category": by,
                         "total_af": sum(by.values()), "year": 2020}

    # public water systems near the river
    pws = _wkt_frame(ose / "public_water_systems.parquet", ["PublicSystemName", "Sys_Type", "Shape__Area"])
    pws = pws[pws.intersects(line.buffer(_deg(PWS_KM, lat)))]
    ctx.water_systems = [n.strip() for n in pws.sort_values("Shape__Area", ascending=False)["PublicSystemName"].dropna().head(8)]
    return ctx
