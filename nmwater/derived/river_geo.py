"""Geometry for a river: its segments, tributaries, reservoirs, towns, gauges and the state layers, read from the archive.

No drawing here (nmwater/site/mapsvg.py draws, until the website draws from GeoJSON)."""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from pathlib import Path
import pandas as pd
from ..catalog import reach_fixes


KM_PER_MI = 1.609344


CITIES = (("Albuquerque", -106.65, 35.08), ("Santa Fe", -105.94, 35.69), ("Las Cruces", -106.76, 32.32),
          ("Roswell", -104.52, 33.39), ("Farmington", -108.21, 36.73))


_cache: dict = {}


@dataclass
class MapFacts:
    length_km: float = 0.0                 # river length inside the report's segments
    reservoirs: list[str] = field(default_factory=list)
    tributaries: list[str] = field(default_factory=list)
    towns: list[str] = field(default_factory=list)
    flows_into: str | None = None          # first named river downstream of the mapped reach
    drainage_km2: float | None = None      # drainage area at the most downstream mapped reach
    bounds: tuple | None = None            # the river view's extent (lon/lat)


@dataclass
class Layers:
    hucs: object                           # GeoDataFrame: huc8, segment, geometry
    river: object                          # shapely geometry or None
    trib: object                           # GeoDataFrame of tributary flowlines
    res: object                            # GeoDataFrame of reservoirs (NHD waterbodies)
    towns: object                          # GeoDataFrame of urban-area points labelled on the map
    all_towns: object                      # every urban area in the segments, largest first
    ext: tuple                             # river view bounds (lon/lat)


def _read(path: Path, **kw):
    import geopandas as gpd

    return gpd.read_file(path, **kw)


def state_layers(grids: Path):
    """(New Mexico polygon, NM counties, the state's main named rivers), read once per process."""
    if "nm" not in _cache:
        c = _read(grids / "tiger" / "county.gpkg").to_crs(4326)
        c = c[c["STATEFP"] == "35"]
        _cache["nm"] = c.union_all()
        _cache["counties"] = c
        import shapely

        _cache["county_lines"] = shapely.line_merge(c.boundary.union_all())    # long lines simplify; thousands of pieces do not
        f = reach_fixes.apply(_read(grids / "nhdplus" / "flowlines.gpkg", where="streamorde >= 6",
                                    columns=["comid", "gnis_id", "gnis_name", "streamorde"]))
        f = f[f["gnis_name"].fillna("").str.strip().ne("")]
        _cache["main_rivers"] = f[f.intersects(_cache["nm"].buffer(0.05))]
    return _cache["nm"], _cache["counties"], _cache["main_rivers"]


def county_lines(grids: Path):
    state_layers(grids)
    return _cache["county_lines"]


class Proj:
    """Equirectangular projection of lon/lat into an SVG box of the given width."""

    def __init__(self, bounds, width: float):
        self.x0, self.y0, self.x1, self.y1 = bounds
        self.k = math.cos(math.radians((self.y0 + self.y1) / 2))
        self.s = width / ((self.x1 - self.x0) * self.k)
        self.width, self.height = width, (self.y1 - self.y0) * self.s

    def xy(self, x, y):
        return (x - self.x0) * self.k * self.s, (self.y1 - y) * self.s

    def inv(self, px, py):
        """Map units back to (lon, lat)."""
        return px / (self.k * self.s) + self.x0, self.y1 - py / self.s

    def path(self, geom, tol_px: float = 0.6, nd: int = 1, topology: bool = True) -> str:
        """SVG path data; tol_px is the simplification tolerance in map units, nd the decimals. topology=False
        uses plain Douglas-Peucker, which may self-intersect: fine for faint lines, not for filled shapes."""
        if geom is None or geom.is_empty:
            return ""
        g = geom.simplify(tol_px / self.s, preserve_topology=topology)
        out = []

        def ring(coords, close):
            c = list(coords)
            if len(c) >= 2:
                out.append("M" + "L".join(f"{a:.{nd}f} {b:.{nd}f}" for a, b in (self.xy(x, y) for x, y, *_ in c)) + ("Z" if close else ""))

        def walk(g):
            t = g.geom_type
            if t == "Polygon":
                ring(g.exterior.coords, True)
                for i in g.interiors:
                    ring(i.coords, True)
            elif t in ("LineString", "LinearRing"):
                ring(g.coords, False)
            elif hasattr(g, "geoms"):
                for p in g.geoms:
                    walk(p)
        walk(g)
        return "".join(out)


def collect(gnis_id: str, segments: list[str], huc8_of: dict[str, str], gauges: pd.DataFrame,
            grids: Path) -> tuple[Layers, MapFacts]:
    """The river's map layers and the facts the description is written from. gauges: name, segment, lat, lon."""
    import warnings

    import geopandas as gpd
    from shapely.geometry import box
    from shapely.ops import unary_union

    from ..derived.river_watershed import huc8_polygons

    codes = [huc8_of[s] for s in segments if s in huc8_of]
    hucs = huc8_polygons(grids / "wbd", codes)
    hucs["segment"] = hucs["huc8"].map({v: k for k, v in huc8_of.items()})
    area = hucs.union_all()
    flo = reach_fixes.apply(_read(grids / "nhdplus" / "flowlines.gpkg", bbox=area.bounds))
    river = flo[flo["gnis_id"].astype(str) == str(gnis_id)]
    in_seg = river[river["huc8"].astype(str).isin(codes)]
    # the river view: the river inside its segments and its gauges (a short creek would be lost in a whole
    # watershed); the watershed shading is simply cut by the frame
    pieces = [in_seg.union_all()] if len(in_seg) else [area]
    g_ok = gauges.dropna(subset=["lat", "lon"])
    if len(g_ok):
        pieces.append(box(g_ok["lon"].min(), g_ok["lat"].min(), g_ok["lon"].max(), g_ok["lat"].max()))
    minx, miny, maxx, maxy = unary_union(pieces).bounds
    pad = max(maxx - minx, maxy - miny, 0.15) * 0.12
    ext = box(minx - pad, miny - pad, maxx + pad, maxy + pad)
    facts = MapFacts(length_km=float(in_seg["lengthkm"].sum()), bounds=ext.bounds)
    if len(in_seg):
        low = in_seg.loc[in_seg["hydroseq"].idxmin()]
        facts.drainage_km2 = float(low["totdasqkm"]) if pd.notna(low["totdasqkm"]) else None
        nxt = flo[flo["fromnode"] == low["tonode"]]
        seen = 0
        while len(nxt) and seen < 200:
            r = nxt.iloc[0]
            if str(r["gnis_id"]) != str(gnis_id) and isinstance(r["gnis_name"], str) and r["gnis_name"].strip():
                facts.flows_into = r["gnis_name"].strip()
                break
            nxt = flo[flo["fromnode"] == r["tonode"]]
            seen += 1
    # named tributaries joining the river inside the segments (a few, by size)
    top_order = int(river["streamorde"].max()) if len(river) else 0
    trib = flo[(flo["gnis_id"].astype(str) != str(gnis_id)) & flo["gnis_name"].fillna("").str.strip().ne("")
               & (flo["streamorde"] >= max(3, top_order - 3))]
    trib = trib[trib.intersects(area)]
    joins = set(river["fromnode"])
    facts.tributaries = (trib[trib["tonode"].isin(joins)].sort_values("totdasqkm", ascending=False)["gnis_name"]
                         .drop_duplicates().head(8).tolist())
    trib = trib[trib["gnis_name"].isin(facts.tributaries) & trib.intersects(ext)]

    wb = _read(grids / "nhdplus" / "waterbodies.gpkg", bbox=ext.bounds)
    wb = wb[wb["ftype"].isin(["Reservoir", "LakePond"]) & (wb["areasqkm"] > 0.5)]
    with warnings.catch_warnings():            # a 0.01-degree buffer in lat/lon is fine for "touches the river"
        warnings.simplefilter("ignore", UserWarning)
        near = river.buffer(0.01).union_all() if len(river) else None
    res = wb[wb.intersects(near)] if near is not None else wb.iloc[0:0]
    names = res.sort_values("areasqkm", ascending=False)["gnis_name"].dropna().astype(str).str.strip()
    facts.reservoirs = names[names != ""].drop_duplicates().head(8).tolist()

    uac = _read(grids / "tiger" / "uac.gpkg").to_crs(4326)
    uac = uac.set_geometry(gpd.points_from_xy(uac["INTPTLON20"].astype(float), uac["INTPTLAT20"].astype(float), crs=4326))
    every = uac[uac.within(area)].sort_values("ALAND20", ascending=False)
    uac = every[every.within(ext)].head(6)
    facts.towns = [town_name(n) for n in uac["NAME20"]]
    return Layers(hucs, river.union_all() if len(river) else None, trib, res, uac, every, ext.bounds), facts


def town_name(name20: str) -> str:
    return name20.split(",")[0].split("--")[0]
