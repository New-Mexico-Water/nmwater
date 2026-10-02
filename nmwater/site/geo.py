"""Geometry for the website, as GeoJSON (RFC 7946: WGS84 longitude/latitude, exterior rings counter-clockwise).

nmwater prepares geometry (clipping, simplification, joining) because that needs the archive and the GIS libraries; the website projects and
draws it. Coordinates are rounded to 3 decimals (about 100 m) after simplifying, which is finer than any map the site draws.

geo/state.geojson         New Mexico's outline (one Feature, a Polygon or MultiPolygon)
geo/county-lines.geojson  the county boundaries inside the state, joined into long lines (one MultiLineString Feature)
geo/main-rivers.geojson   the state's main named rivers (stream order 6 and up), one MultiLineString per river, with gnis_id
geo/cities.geojson        the five cities every state map labels
rivers/<slug>/geo/        one river's map: state-view.geojson (coarse), river-view.geojson (fine, clipped, with points), bounds.json
"""

from __future__ import annotations

import json
from pathlib import Path

import shapely
from shapely.geometry import mapping
from shapely.geometry.polygon import orient

from ..derived import river_geo as rg
from . import SCHEMA_VERSION

DECIMALS = 3
SIMPLIFY_DEG = 0.004                    # about 0.5 px on a 600 px wide map of the state


def prepare(geom, tol: float = SIMPLIFY_DEG, topology: bool = True, decimals: int = DECIMALS):
    """Simplify, snap to the output grid, and wind polygons the GeoJSON way."""
    if geom is None or geom.is_empty:
        return None
    g = geom.simplify(tol, preserve_topology=topology)
    g = shapely.set_precision(g, 10 ** -decimals, mode="valid_output")
    if g.is_empty:
        return None
    if g.geom_type == "Polygon":
        return orient(g, 1.0)
    if g.geom_type == "MultiPolygon":
        return shapely.MultiPolygon([orient(p, 1.0) for p in g.geoms])
    return g


def feature(geom, tol: float = SIMPLIFY_DEG, decimals: int = DECIMALS, **props) -> dict | None:
    g = prepare(geom, tol, topology=geom.geom_type in ("Polygon", "MultiPolygon"), decimals=decimals)
    return None if g is None else {"type": "Feature", "properties": props, "geometry": mapping(g)}


def collection(features: list[dict | None]) -> dict:
    return {"type": "FeatureCollection", "features": [f for f in features if f]}


def bounds_of(fc: dict) -> list[float]:
    """[west, south, east, north] of a FeatureCollection."""
    xs, ys = [], []

    def walk(c):
        if c and isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for x in c:
                walk(x)
    for f in fc["features"]:
        walk(f["geometry"]["coordinates"])
    return [min(xs), min(ys), max(xs), max(ys)]


def write(path: Path, obj: dict) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, separators=(",", ":"), allow_nan=False)
    path.write_text(text, encoding="utf-8")
    return len(text)


def _lonlat_box(p, view: tuple, margin: float):
    """A lon/lat box covering a viewBox (map units) plus margin (a fraction of its size)."""
    from shapely.geometry import box

    x0, y0, w, h = view
    mx, my = w * margin, h * margin
    lon0, lat1 = p.inv(x0 - mx, y0 - my)
    lon1, lat0 = p.inv(x0 + w + mx, y0 + h + my)
    return box(min(lon0, lon1), min(lat0, lat1), max(lon0, lon1), max(lat0, lat1))


def river_view_frame(ext: tuple, state_bounds: tuple, width: int = 600):
    """The projection and river-view frame of the river map, as the website must recompute them (documented in docs/site-data):
    full = the state's bounds plus the river's extent plus 0.12 degrees; the river view is the extent's box widened to the full map's
    aspect ratio. Returns (Proj over full, viewBox (x, y, w, h) of the river view in map units)."""
    b = state_bounds
    full = (min(b[0], ext[0]) - 0.12, min(b[1], ext[1]) - 0.12, max(b[2], ext[2]) + 0.12, max(b[3], ext[3]) + 0.12)
    p = rg.Proj(full, width)
    aspect = p.height / width
    x0, y1 = p.xy(ext[0], ext[1])
    x1, y0 = p.xy(ext[2], ext[3])
    w, h = x1 - x0, y1 - y0
    w2, h2 = (h / aspect, h) if h / w > aspect else (w, w * aspect)
    return p, (x0 - (w2 - w) / 2, y0 - (h2 - h) / 2, w2, h2)


def river_geo(gnis_id: str, label: str, segments: list[str], layers, gauges, grids: Path, width: int = 600) -> dict[str, dict]:
    """The geometry of one river's map: geo/state-view.geojson (coarse, whole extent), geo/river-view.geojson (fine, clipped to the
    frame plus a margin, with points) and geo/bounds.json. Features carry `kind` and what the website needs to draw and label them."""
    import math

    from shapely.geometry import Point, box

    nm, _counties, mains = rg.state_layers(grids)
    ext = layers.ext
    p, rv = river_view_frame(ext, nm.bounds, width)
    zoom = rv[2] / width                                   # map units per screen pixel in the river view
    fine = zoom / p.s * 0.6                                # simplification tolerance in degrees: 0.6 px of the river view
    coarse = 0.6 / p.s
    nd_fine = max(3, math.ceil(-math.log10(fine)) + 1)
    clip = _lonlat_box(p, rv, 0.15)
    others = mains[mains["gnis_id"].astype(str) != str(gnis_id)]
    cl = rg.county_lines(grids)
    seg_no = {s: i + 1 for i, s in enumerate(segments)}

    state_view = [feature(r.geometry, coarse, 3, kind="segment", index=seg_no.get(r.segment), segment=str(r.segment), huc8=r.huc8) for r in layers.hucs.itertuples()]
    if layers.river is not None:
        state_view.append(feature(layers.river, coarse, 3, kind="river", name=label))

    def f(geom, **props):
        return feature(geom, fine, nd_fine, **props)

    view = [f(nm.boundary.intersection(clip), kind="state-boundary"), f(cl.intersection(clip), kind="county-lines")]
    for n, g in others[others.intersects(clip)].groupby("gnis_name"):
        view.append(f(g.union_all().intersection(clip), kind="main-river", name=str(n)))
    for r in layers.hucs.itertuples():
        view.append(f(r.geometry.intersection(clip), kind="segment", index=seg_no.get(r.segment), segment=str(r.segment), huc8=r.huc8))
    for r in layers.trib.itertuples():
        view.append(f(r.geometry.intersection(clip), kind="tributary", name=str(r.gnis_name)))
    if layers.river is not None:
        view.append(f(layers.river.intersection(clip), kind="river", name=label))
    for r in layers.res.itertuples():
        nm_ = str(r.gnis_name).strip() if isinstance(r.gnis_name, str) and r.gnis_name.strip() else None
        view.append(f(r.geometry, kind="reservoir", name=nm_, area_km2=round(float(r.areasqkm), 2)))
    for r in layers.res[layers.res["gnis_name"].fillna("").str.strip().ne("") & (layers.res["areasqkm"] > 5)].itertuples():
        view.append(f(r.geometry.representative_point(), kind="reservoir-label", name=r.gnis_name.strip()))
    for r in layers.towns.itertuples():
        view.append(f(Point(r.geometry.x, r.geometry.y), kind="town", name=rg.town_name(r.NAME20)))
    for r in gauges.dropna(subset=["lat", "lon"]).itertuples():
        view.append(f(Point(r.lon, r.lat), kind="gauge", name=str(r.name), segment=str(r.segment), index=seg_no.get(r.segment)))
    for r in layers.hucs.itertuples():
        g = r.geometry.intersection(box(*ext))
        if r.segment in seg_no and not g.is_empty:
            view.append(f(g.representative_point(), kind="segment-label", index=seg_no[r.segment], segment=str(r.segment)))
    sb = nm.bounds                                         # the state's exact bounds: the frame is computed from these, not from simplified shapes
    bounds = {"schema_version": SCHEMA_VERSION, "extent": {"west": ext[0], "south": ext[1], "east": ext[2], "north": ext[3]},
              "state_bounds": {"west": sb[0], "south": sb[1], "east": sb[2], "north": sb[3]}, "segments": list(segments)}
    return {"state-view": collection(state_view), "river-view": collection(view), "bounds": bounds}


def write_shared(out: Path, grids: Path) -> dict:
    """geo/state.geojson and geo/county-lines.geojson; returns the manifest's `geo` block."""
    nm, _counties, _mains = rg.state_layers(grids)
    state = collection([feature(nm, name="New Mexico")])
    lines = collection([feature(rg.county_lines(grids), name="County boundaries")])
    write(out / "geo" / "state.geojson", state)
    write(out / "geo" / "county-lines.geojson", lines)
    w, s, e, n = nm.bounds                               # exact, not the simplified outline's: frames are computed from these
    nm_, _c, mains = rg.state_layers(grids)
    rivers = collection([feature(g.union_all(), SIMPLIFY_DEG, 3, kind="main-river", name=str(n), gnis_id=str(gid))
                         for (n, gid), g in mains.groupby(["gnis_name", "gnis_id"])])
    cities = collection([{"type": "Feature", "properties": {"kind": "city", "name": name}, "geometry": {"type": "Point", "coordinates": [lon, lat]}} for name, lon, lat in rg.CITIES])
    write(out / "geo" / "main-rivers.geojson", rivers)
    write(out / "geo" / "cities.geojson", cities)
    return {"state": "geo/state.geojson", "county_lines": "geo/county-lines.geojson", "main_rivers": "geo/main-rivers.geojson", "cities": "geo/cities.geojson", "bounds": {"west": w, "south": s, "east": e, "north": n}}
