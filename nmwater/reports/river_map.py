"""A static, self-contained SVG map of a river and its segments, plus facts for its description.

Drawn from data already in the archive, so the page needs no map service and works offline:
- the river itself: NHDPlus v2 flowlines with the river's GNIS id;
- segments: the WBD HUC8 polygons the report uses, shaded in the same colours as the charts;
- context: New Mexico's outline (TIGER counties), the larger named tributaries (NHDPlus), reservoirs on
  the river (NHD waterbodies) and the largest urban areas in the segments (TIGER);
- gauges: the report's gauges, coloured by segment, with their names on hover.

Coordinates are projected with a simple equirectangular projection scaled by the cosine of the map's
middle latitude, which is accurate to well under 1% across a state-sized map.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

KM_PER_MI = 1.609344


@dataclass
class MapFacts:
    length_km: float = 0.0                 # river length inside the report's segments
    reservoirs: list[str] = field(default_factory=list)
    tributaries: list[str] = field(default_factory=list)
    towns: list[str] = field(default_factory=list)
    flows_into: str | None = None          # first named river downstream of the mapped reach
    drainage_km2: float | None = None      # drainage area at the most downstream mapped reach


def _read(path: Path, **kw):
    import geopandas as gpd

    return gpd.read_file(path, **kw)


def build(gnis_id: str, segments: list[str], huc8_of: dict[str, str], gauges: pd.DataFrame,
          grids: Path, width: int = 480) -> tuple[str, MapFacts]:
    """gauges: name, segment, lat, lon. Returns (svg markup, facts)."""
    import geopandas as gpd
    from shapely.geometry import box

    from .river_watershed import huc8_polygons

    codes = [huc8_of[s] for s in segments if s in huc8_of]
    hucs = huc8_polygons(grids / "wbd", codes)
    seg_of_huc = {v: k for k, v in huc8_of.items()}
    hucs["segment"] = hucs["huc8"].map(seg_of_huc)
    area = hucs.union_all()
    flo = _read(grids / "nhdplus" / "flowlines.gpkg", bbox=area.bounds)
    river = flo[flo["gnis_id"].astype(str) == str(gnis_id)]
    in_seg = river[river["huc8"].astype(str).isin(codes)]
    # zoom to the river inside its segments and its gauges (a short creek would be lost in a whole
    # watershed); the watershed shading is simply clipped by the view
    pieces = [in_seg.union_all()] if len(in_seg) else [area]
    g_ok = gauges.dropna(subset=["lat", "lon"])
    if len(g_ok):
        pieces.append(box(g_ok["lon"].min(), g_ok["lat"].min(), g_ok["lon"].max(), g_ok["lat"].max()))
    from shapely.ops import unary_union

    minx, miny, maxx, maxy = unary_union(pieces).bounds
    pad = max(maxx - minx, maxy - miny, 0.15) * 0.12
    ext = box(minx - pad, miny - pad, maxx + pad, maxy + pad)
    tol = (ext.bounds[2] - ext.bounds[0]) / width * 0.8
    view = ext
    facts = MapFacts(length_km=float(in_seg["lengthkm"].sum()))
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
    trib_names = (trib[trib["tonode"].isin(joins)].sort_values("totdasqkm", ascending=False)["gnis_name"]
                  .drop_duplicates().head(8).tolist())
    facts.tributaries = trib_names
    trib = trib[trib["gnis_name"].isin(trib_names) & trib.intersects(view)]

    wb = _read(grids / "nhdplus" / "waterbodies.gpkg", bbox=ext.bounds)
    wb = wb[wb["ftype"].isin(["Reservoir", "LakePond"]) & (wb["areasqkm"] > 0.5)]
    import warnings

    with warnings.catch_warnings():            # a 0.01-degree buffer in lat/lon is fine for "touches the river"
        warnings.simplefilter("ignore", UserWarning)
        near = river.buffer(0.01).union_all() if len(river) else None
    res = wb[wb.intersects(near)] if near is not None else wb.iloc[0:0]
    names = res.sort_values("areasqkm", ascending=False)["gnis_name"].dropna().astype(str).str.strip()
    facts.reservoirs = names[names != ""].drop_duplicates().head(8).tolist()

    counties = _read(grids / "tiger" / "county.gpkg")
    nm = counties[counties["STATEFP"] == "35"].to_crs(4326).union_all()
    uac = _read(grids / "tiger" / "uac.gpkg").to_crs(4326)
    pts = gpd.points_from_xy(uac["INTPTLON20"].astype(float), uac["INTPTLAT20"].astype(float), crs=4326)
    uac = uac.set_geometry(pts)
    uac = uac[uac.within(area) & uac.within(view)].sort_values("ALAND20", ascending=False).head(6)
    facts.towns = [n.split(",")[0].split("--")[0] for n in uac["NAME20"]]

    # ---- projection
    x0, y0, x1, y1 = ext.bounds
    k = math.cos(math.radians((y0 + y1) / 2))
    sx = width / ((x1 - x0) * k)
    height = int((y1 - y0) * sx) + 1

    def P(x, y):
        return (x - x0) * k * sx, (y1 - y) * sx

    def path(geom) -> str:
        if geom is None or geom.is_empty:
            return ""
        g = geom.simplify(tol, preserve_topology=True)
        out = []

        def ring(coords, close):
            c = list(coords)
            if len(c) < 2:
                return
            s = "M" + "L".join(f"{a:.1f} {b:.1f}" for a, b in (P(x, y) for x, y, *_ in c))
            out.append(s + ("Z" if close else ""))

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

    color = {s: f"var(--s{i + 1})" for i, s in enumerate(segments)}
    E = html.escape
    L = [f'<svg class="rmap" viewBox="0 0 {width} {height}" role="img" aria-label="Map of the river and its segments">']
    L.append(f'<path class="m-state" d="{path(nm.boundary.intersection(view))}"/>')
    hucs = hucs.assign(geometry=hucs.intersection(view))
    for r in hucs.itertuples():
        idx = segments.index(r.segment) + 1 if r.segment in segments else ""
        L.append(f'<path class="m-seg" style="--c:{color.get(r.segment, "var(--axis)")}" d="{path(r.geometry)}">'
                 f'<title>{idx}. {E(str(r.segment))} (HUC {E(r.huc8)})</title></path>')
    for r in trib.itertuples():
        L.append(f'<path class="m-trib" d="{path(r.geometry)}"><title>{E(r.gnis_name)}</title></path>')
    L.append(f'<path class="m-river" d="{path(river.union_all().intersection(view)) if len(river) else ""}"><title>river</title></path>')
    for r in res.itertuples():
        nm_ = str(r.gnis_name).strip() if isinstance(r.gnis_name, str) and r.gnis_name.strip() else "Unnamed reservoir"
        L.append(f'<path class="m-res" d="{path(r.geometry.buffer(0.004))}"><title>{E(nm_)}</title></path>')
    for r in res[res["gnis_name"].fillna("").str.strip().ne("") & (res["areasqkm"] > 5)].itertuples():
        c = r.geometry.representative_point()
        x, y = P(c.x, c.y)
        L.append(f'<text class="m-reslabel" x="{x + 8:.1f}" y="{y + 3.5:.1f}">{E(r.gnis_name.strip())}</text>')
    for r in uac.itertuples():
        x, y = P(r.geometry.x, r.geometry.y)
        name = r.NAME20.split(",")[0].split("--")[0]
        L.append(f'<g class="m-town"><circle cx="{x:.1f}" cy="{y:.1f}" r="2.5"/>'
                 f'<text x="{x + 5:.1f}" y="{y + 3.5:.1f}">{E(name)}</text></g>')
    for r in gauges.dropna(subset=["lat", "lon"]).itertuples():
        x, y = P(r.lon, r.lat)
        L.append(f'<circle class="m-gauge" cx="{x:.1f}" cy="{y:.1f}" r="4" style="--c:{color.get(r.segment, "var(--axis)")}">'
                 f'<title>{E(r.name)} ({E(str(r.segment))})</title></circle>')
    # segment numbers
    for r in hucs.itertuples():
        if r.segment not in segments:
            continue
        if r.geometry.is_empty:
            continue
        p = r.geometry.representative_point()
        x, y = P(p.x, p.y)
        L.append(f'<g class="m-label"><circle cx="{x:.1f}" cy="{y:.1f}" r="10" style="--c:{color[r.segment]}"/>'
                 f'<text x="{x:.1f}" y="{y + 4:.1f}" text-anchor="middle">{segments.index(r.segment) + 1}</text></g>')
    # scale bar (50 km or 20 km) and north marker
    km = 50 if (x1 - x0) * 111 * k > 300 else 20
    bar = km / (111.32 * k) * k * sx
    L.append(f'<g class="m-scale"><path d="M16 {height - 18}h{bar:.1f}"/><text x="16" y="{height - 24}">{km} km</text>'
             f'<text x="{width - 18}" y="22" text-anchor="middle">N</text><path d="M{width - 18} 28v18"/></g>')
    L.append("</svg>")
    return "".join(L), facts


def describe(label: str, facts: MapFacts, segments: list[str], n_gauges: int, first_year: int, as_of_year: int) -> str:
    """One factual paragraph generated from the archive."""
    parts = []
    mi = facts.length_km / KM_PER_MI
    if len(segments) == 1:
        parts.append(f"This report follows {mi:,.0f} miles ({facts.length_km:,.0f} km) of the {label}, all within the "
                     f"{segments[0]} watershed segment.")
    else:
        parts.append(f"This report follows {mi:,.0f} miles ({facts.length_km:,.0f} km) of the {label} across "
                     f"{len(segments)} watershed segments, from {segments[0]} downstream to {segments[-1]}.")
    if facts.drainage_km2:
        parts.append(f"At the lower end of the report the river drains about {facts.drainage_km2 / 2.58999:,.0f} square miles.")
    if facts.flows_into:
        parts.append(f"Downstream of the mapped reach it flows into the {facts.flows_into}.")
    if facts.tributaries:
        parts.append("Named tributaries joining it here include the " + _list(facts.tributaries[:5]) + ".")
    if facts.reservoirs:
        parts.append("Reservoirs on this reach: " + _list(facts.reservoirs[:6]) + ".")
    if facts.towns:
        parts.append("Largest urban areas near it: " + _list(facts.towns[:5]) + ".")
    parts.append(f"{n_gauges} gauge{'s' if n_gauges != 1 else ''} with at least 26 weeks of daily flow cover "
                 f"{first_year} to {as_of_year}.")
    return " ".join(parts)


def _list(xs: list[str]) -> str:
    xs = [x for x in xs if x]
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


MAP_CSS = """
.rmap { display: block; width: 100%; height: auto; max-height: 82vh; margin-inline: auto; }
.rmap .m-state { fill: none; stroke: var(--axis); stroke-width: 1; stroke-dasharray: 4 3; }
.rmap .m-seg { fill: var(--c); fill-opacity: .13; stroke: var(--c); stroke-width: 1; stroke-opacity: .6; }
.rmap .m-seg:hover { fill-opacity: .26; }
.rmap .m-trib { fill: none; stroke: var(--river); stroke-width: .9; stroke-opacity: .55; }
.rmap .m-river { fill: none; stroke: var(--river); stroke-width: 2.6; stroke-linejoin: round; stroke-linecap: round; }
.rmap .m-res { fill: var(--river); stroke: var(--surface-1); stroke-width: 1; }
.rmap .m-reslabel { fill: var(--river); font: italic 500 10.5px var(--font-body); paint-order: stroke; stroke: var(--surface-1); stroke-width: 3px; }
.rmap .m-town circle { fill: var(--text-secondary); }
.rmap .m-town text { fill: var(--text-secondary); font: 500 11px var(--font-body); paint-order: stroke; stroke: var(--surface-1); stroke-width: 3px; }
.rmap .m-gauge { fill: var(--c); stroke: var(--surface-1); stroke-width: 1.5; }
.rmap .m-gauge:hover { stroke: var(--text-primary); }
.rmap .m-label circle { fill: var(--surface-1); stroke: var(--c); stroke-width: 2; }
.rmap .m-label text { fill: var(--text-primary); font: 600 11px var(--font-num); }
.rmap .m-scale path { stroke: var(--text-secondary); stroke-width: 1.5; fill: none; }
.rmap .m-scale text { fill: var(--text-secondary); font: 11px var(--font-num); }
"""
