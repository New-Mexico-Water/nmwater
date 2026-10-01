"""The river map as an SVG (zoomable, two levels of detail) and its prose description. Presentation that still ships in the bundle; to be replaced by GeoJSON the website draws (see docs/site-data)."""

from __future__ import annotations
import html
import math
from dataclasses import dataclass
from pathlib import Path
import pandas as pd

from ..derived.river_geo import CITIES, KM_PER_MI, Layers, MapFacts, Proj, county_lines, state_layers, town_name


E = html.escape


def _pin(x: float, y: float, body: str, cls: str, k: float, title: str = "") -> str:
    """A point symbol drawn in screen pixels around (0, 0); ZOOM_JS keeps it the same size at any zoom."""
    t = f"<title>{E(title)}</title>" if title else ""
    return f'<g class="{cls}" data-pin="{x:.3f} {y:.3f}" transform="translate({x:.3f} {y:.3f}) scale({k:.4f})">{t}{body}</g>'


@dataclass
class MapParts:
    """The zoomable map as separate pieces, so a page can lay out its own buttons, key and scale."""
    svg: str                      # <svg class="zmap" ...>: both views, drawn in two levels of detail
    inset: str                    # corner locator for the river view (state outline, watershed, river, box)
    scale: dict | None            # {"km", "miles", "width_pct"}: a scale bar for the river view, or None
    title: str
    desc: str
    view_state: str               # viewBox of the whole-state view
    view_river: str               # viewBox of the river view
    segments: list[str]           # segment names, in numbering order


def _lonlat_box(p: Proj, view: tuple, margin: float):
    """A lon/lat box covering a viewBox (map units) plus margin (a fraction of its size)."""
    from shapely.geometry import box

    x0, y0, w, h = view
    mx, my = w * margin, h * margin
    lon0, lat1 = p.inv(x0 - mx, y0 - my)
    lon1, lat0 = p.inv(x0 + w + mx, y0 + h + my)
    return box(min(lon0, lon1), min(lat0, lat1), max(lon0, lon1), max(lat0, lat1))


def zoom_map_parts(gnis_id: str, label: str, segments: list[str], layers: Layers, gauges: pd.DataFrame,
                   grids: Path, width: int = 600) -> MapParts:
    """One SVG in state coordinates with two views (state, river) and two levels of detail.

    The state view uses coarse shapes (about 0.6 px at state scale); the river view uses fine shapes clipped to
    the frame plus a margin, so detail far from the river is never drawn. Each level sits in its own group
    (v-state, v-river) and the stylesheet shows the one that matches the current view."""
    from shapely.geometry import box

    nm, counties, mains = state_layers(grids)
    ext = layers.ext
    b = nm.bounds
    full = (min(b[0], ext[0]) - 0.12, min(b[1], ext[1]) - 0.12, max(b[2], ext[2]) + 0.12, max(b[3], ext[3]) + 0.12)
    p = Proj(full, width)
    W, H = width, p.height
    aspect = H / W
    x0, y1 = p.xy(ext[0], ext[1])
    x1, y0 = p.xy(ext[2], ext[3])
    w, h = x1 - x0, y1 - y0
    w2, h2 = (h / aspect, h) if h / w > aspect else (w, w * aspect)
    rv = (x0 - (w2 - w) / 2, y0 - (h2 - h) / 2, w2, h2)
    zoom = rv[2] / W                                   # map units per screen pixel in the river view
    nd = max(1, math.ceil(-math.log10(zoom)) + 1) if zoom < 1 else 1
    fine = {"tol_px": 0.6 * zoom, "nd": nd}
    coarse = {"tol_px": 0.6, "nd": 1}
    clip = _lonlat_box(p, rv, 0.15)
    color = {s: f"var(--s{i + 1})" for i, s in enumerate(segments)}
    area = layers.hucs.union_all()
    towns = ", ".join(town_name(n) for n in layers.towns["NAME20"])
    title = f"Map of the {label} in New Mexico"
    desc = (f"New Mexico, with the {label}'s {len(segments)} watershed segment{'s' if len(segments) != 1 else ''} shaded "
            "and numbered upstream to downstream, the river and its gauges" + (f", and towns near it ({towns})" if towns else "") + ".")
    sv = f"0 0 {W} {H:.1f}"
    rvs = " ".join(f"{v:.3f}" for v in rv)
    others = mains[mains["gnis_id"].astype(str) != str(gnis_id)]
    cl = county_lines(grids)
    L = [f'<svg class="zmap" viewBox="{rvs}" data-state="{sv}" data-river="{rvs}" data-view="river" '
         f'style="aspect-ratio:{W}/{H:.1f}" role="img" aria-labelledby="map-t map-d">'
         f'<title id="map-t">{E(title)}</title><desc id="map-d">{E(desc)}</desc>']
    # ---- state view: coarse shapes
    L.append('<g class="v-state">')
    L.append(f'<path class="z-county" d="{p.path(cl, topology=False, **coarse)}"/>')
    L.append(f'<path class="z-state" d="{p.path(nm.boundary, **coarse)}"/>')
    for n, g in others.groupby("gnis_name"):
        L.append(f'<path class="z-main" d="{p.path(g.union_all(), **coarse)}"><title>{E(n)}</title></path>')
    for r in layers.hucs.itertuples():
        idx = segments.index(r.segment) + 1 if r.segment in segments else ""
        L.append(f'<path class="z-seg" style="--c:{color.get(r.segment, "var(--axis)")}" d="{p.path(r.geometry, **coarse)}">'
                 f'<title>{idx}. {E(str(r.segment))} (HUC {E(r.huc8)})</title></path>')
    if layers.river is not None:
        d = p.path(layers.river, **coarse)
        L.append(f'<path class="z-halo" d="{d}"/><path class="z-river" d="{d}"><title>{E(label)}</title></path>')
    for name, lon, lat in CITIES:
        x, y = p.xy(lon, lat)
        L.append(_pin(x, y, f'<circle r="2.2"/><text x="5" y="3.5">{name}</text>', "z-city", zoom))
    shape = layers.river if layers.river is not None else area          # a ring and a label when the river is small
    rb = shape.bounds
    span = max((rb[2] - rb[0]) * p.k, rb[3] - rb[1]) * p.s
    if span < W * 0.18:
        c = shape.centroid
        cx, cy = p.xy(c.x, c.y)
        right = cx < W * 0.6
        ring = max(9.0, span / 2 + 5)
        sx = 1 if right else -1
        L.append(_pin(cx, cy, f'<circle r="{ring:.1f}"/><path d="M{sx * ring * 0.7:.1f} {-ring * 0.7:.1f}L{sx * 56} -34"/>'
                              f'<text x="{sx * 60}" y="-30" text-anchor="{"start" if right else "end"}">{E(label)}</text>',
                      "z-callout", zoom))
    L.append("</g>")
    # ---- river view: fine shapes, clipped to the frame plus a margin
    L.append('<g class="v-river">')
    L.append(f'<path class="z-county" d="{p.path(cl.intersection(clip), topology=False, **fine)}"/>')
    L.append(f'<path class="z-state" d="{p.path(nm.boundary.intersection(clip), **fine)}"/>')
    for n, g in others[others.intersects(clip)].groupby("gnis_name"):
        L.append(f'<path class="z-main" d="{p.path(g.union_all().intersection(clip), **fine)}"><title>{E(n)}</title></path>')
    for r in layers.hucs.itertuples():
        idx = segments.index(r.segment) + 1 if r.segment in segments else ""
        L.append(f'<path class="z-seg" style="--c:{color.get(r.segment, "var(--axis)")}" d="{p.path(r.geometry.intersection(clip), **fine)}">'
                 f'<title>{idx}. {E(str(r.segment))} (HUC {E(r.huc8)})</title></path>')
    for r in layers.trib.itertuples():
        L.append(f'<path class="z-trib" d="{p.path(r.geometry.intersection(clip), **fine)}"><title>{E(r.gnis_name)}</title></path>')
    if layers.river is not None:
        d = p.path(layers.river.intersection(clip), **fine)
        L.append(f'<path class="z-halo" d="{d}"/><path class="z-river" d="{d}"><title>{E(label)}</title></path>')
    for r in layers.res.itertuples():
        nm_ = str(r.gnis_name).strip() if isinstance(r.gnis_name, str) and r.gnis_name.strip() else "Unnamed reservoir"
        L.append(f'<path class="z-res" d="{p.path(r.geometry, **fine)}"><title>{E(nm_)}</title></path>')
    for r in layers.res[layers.res["gnis_name"].fillna("").str.strip().ne("") & (layers.res["areasqkm"] > 5)].itertuples():
        c = r.geometry.representative_point()
        x, y = p.xy(c.x, c.y)
        L.append(_pin(x, y, f'<text x="8" y="3.5">{E(r.gnis_name.strip())}</text>', "z-reslabel", zoom))
    for r in layers.towns.itertuples():
        x, y = p.xy(r.geometry.x, r.geometry.y)
        L.append(_pin(x, y, f'<circle r="2.5"/><text x="5" y="3.5">{E(town_name(r.NAME20))}</text>', "z-town", zoom))
    for r in gauges.dropna(subset=["lat", "lon"]).itertuples():
        x, y = p.xy(r.lon, r.lat)
        L.append(_pin(x, y, f'<circle r="4" style="--c:{color.get(r.segment, "var(--axis)")}"/>', "z-gauge", zoom,
                      f"{r.name} ({r.segment})"))
    for r in layers.hucs.itertuples():
        g = r.geometry.intersection(box(*ext))
        if r.segment not in segments or g.is_empty:
            continue
        c = g.representative_point()
        x, y = p.xy(c.x, c.y)
        L.append(_pin(x, y, f'<circle r="10" style="--c:{color[r.segment]}"/><text y="4" text-anchor="middle">'
                            f"{segments.index(r.segment) + 1}</text>", "z-num", zoom))
    L.append("</g></svg>")
    loc_w = 84
    lp = Proj((b[0] - 0.1, b[1] - 0.1, b[2] + 0.1, b[3] + 0.1), loc_w)
    lx0, ly1 = lp.xy(ext[0], ext[1])
    lx1, ly0 = lp.xy(ext[2], ext[3])
    inset = (f'<svg class="zinset" viewBox="0 0 {loc_w} {lp.height:.0f}" aria-hidden="true" focusable="false">'
             f'<path class="i-state" d="{lp.path(nm)}"/><path class="i-area" d="{lp.path(area.intersection(nm.buffer(0.3)))}"/>'
             f'<path class="i-river" d="{lp.path(layers.river)}"/>'
             f'<rect class="i-box" x="{lx0:.1f}" y="{ly0:.1f}" width="{max(3, lx1 - lx0):.1f}" height="{max(3, ly1 - ly0):.1f}"/></svg>')
    km_view = rv[2] / (p.s / 111.32)
    km = next((v for v in (1, 2, 5, 10, 20, 50, 100, 200) if v / km_view >= 0.12), 200)
    scale = {"km": km, "miles": round(km / KM_PER_MI), "width_pct": round(km / km_view * 100, 1)} if km / km_view < 0.5 else None
    return MapParts("".join(L), inset, scale, title, desc, sv, rvs, list(segments))


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
