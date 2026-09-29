"""The river page's map: one SVG that shows where the river is in New Mexico and zooms to the river.

Drawn from data already in the archive, so the page needs no map service and works offline:
- New Mexico and its counties (TIGER), and the state's main rivers (NHDPlus, stream order 6 and up)
  for orientation in the state view;
- the river itself (NHDPlus flowlines with the river's GNIS id), its segments (the WBD HUC8 polygons
  the report uses, shaded in the chart colours), larger named tributaries, reservoirs on the river (NHD
  waterbodies), the largest urban areas in the segments (TIGER) and the report's gauges.

The whole map is drawn once in state coordinates. Two viewBoxes, the state and the river (padded to the
same shape, so the frame does not jump), are stored on the SVG; ZOOM_JS animates between them. Lines
keep their on-screen width (vector-effect), and points, labels and numbers sit in "pins" that the
script rescales so they stay the same size at any zoom. Without JavaScript the map shows the river view,
with a small state locator in the corner. A small river at state scale gets a ring and a label, so it
never becomes an unlabelled dot.

Coordinates are projected with a simple equirectangular projection scaled by the cosine of the map's
middle latitude, accurate to well under 1% across a state-sized map.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

KM_PER_MI = 1.609344
E = html.escape
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
        f = _read(grids / "nhdplus" / "flowlines.gpkg", where="streamorde >= 6", columns=["gnis_id", "gnis_name", "streamorde"])
        f = f[f["gnis_name"].fillna("").str.strip().ne("")]
        _cache["main_rivers"] = f[f.intersects(_cache["nm"].buffer(0.05))]
    return _cache["nm"], _cache["counties"], _cache["main_rivers"]


class Proj:
    """Equirectangular projection of lon/lat into an SVG box of the given width."""

    def __init__(self, bounds, width: float):
        self.x0, self.y0, self.x1, self.y1 = bounds
        self.k = math.cos(math.radians((self.y0 + self.y1) / 2))
        self.s = width / ((self.x1 - self.x0) * self.k)
        self.width, self.height = width, (self.y1 - self.y0) * self.s

    def xy(self, x, y):
        return (x - self.x0) * self.k * self.s, (self.y1 - y) * self.s

    def path(self, geom, tol_px: float = 0.6, nd: int = 1) -> str:
        """SVG path data; tol_px is the simplification tolerance in map units, nd the decimals."""
        if geom is None or geom.is_empty:
            return ""
        g = geom.simplify(tol_px / self.s, preserve_topology=True)
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

    from .river_watershed import huc8_polygons

    codes = [huc8_of[s] for s in segments if s in huc8_of]
    hucs = huc8_polygons(grids / "wbd", codes)
    hucs["segment"] = hucs["huc8"].map({v: k for k, v in huc8_of.items()})
    area = hucs.union_all()
    flo = _read(grids / "nhdplus" / "flowlines.gpkg", bbox=area.bounds)
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


def _pin(x: float, y: float, body: str, cls: str, k: float, title: str = "") -> str:
    """A point symbol drawn in screen pixels around (0, 0); ZOOM_JS keeps it the same size at any zoom."""
    t = f"<title>{E(title)}</title>" if title else ""
    return f'<g class="{cls}" data-pin="{x:.3f} {y:.3f}" transform="translate({x:.3f} {y:.3f}) scale({k:.4f})">{t}{body}</g>'


def zoom_map(gnis_id: str, label: str, segments: list[str], layers: Layers, gauges: pd.DataFrame,
             grids: Path, width: int = 600) -> str:
    """The map card's contents: view buttons, the zoomable SVG with its state inset, scale bar and key."""
    from shapely.geometry import box

    nm, counties, mains = state_layers(grids)
    ext = layers.ext
    b = nm.bounds
    full = (min(b[0], ext[0]) - 0.12, min(b[1], ext[1]) - 0.12, max(b[2], ext[2]) + 0.12, max(b[3], ext[3]) + 0.12)
    p = Proj(full, width)
    W, H = width, p.height
    aspect = H / W
    # the river view, padded to the frame's shape
    x0, y1 = p.xy(ext[0], ext[1])
    x1, y0 = p.xy(ext[2], ext[3])
    w, h = x1 - x0, y1 - y0
    w2, h2 = (h / aspect, h) if h / w > aspect else (w, w * aspect)
    rv = (x0 - (w2 - w) / 2, y0 - (h2 - h) / 2, w2, h2)
    zoom = rv[2] / W                                   # map units per screen pixel in the river view
    nd = max(1, math.ceil(-math.log10(zoom)) + 1) if zoom < 1 else 1
    fine = {"tol_px": 0.6 * zoom, "nd": nd}
    color = {s: f"var(--s{i + 1})" for i, s in enumerate(segments)}
    area = layers.hucs.union_all()
    towns = ", ".join(town_name(n) for n in layers.towns["NAME20"])
    desc = (f"New Mexico, with the {label}'s {len(segments)} watershed segment{'s' if len(segments) != 1 else ''} shaded "
            "and numbered upstream to downstream, the river and its gauges" + (f", and towns near it ({towns})" if towns else "") + ".")
    sv = f"0 0 {W} {H:.1f}"
    rvs = " ".join(f"{v:.3f}" for v in rv)
    L = [f'<svg class="zmap" viewBox="{rvs}" data-state="{sv}" data-river="{rvs}" data-view="river" '
         f'style="aspect-ratio:{W}/{H:.1f}" role="img" aria-labelledby="map-t map-d">'
         f'<title id="map-t">Map of the {E(label)} in New Mexico</title><desc id="map-d">{E(desc)}</desc>',
         f'<path class="z-county" d="{p.path(counties.boundary.union_all())}"/>',
         f'<path class="z-state" d="{p.path(nm.boundary, **fine)}"/>']
    for n, g in mains[mains["gnis_id"].astype(str) != str(gnis_id)].groupby("gnis_name"):
        L.append(f'<path class="z-main" d="{p.path(g.union_all())}"><title>{E(n)}</title></path>')
    for r in layers.hucs.itertuples():
        idx = segments.index(r.segment) + 1 if r.segment in segments else ""
        L.append(f'<path class="z-seg" style="--c:{color.get(r.segment, "var(--axis)")}" d="{p.path(r.geometry, **fine)}">'
                 f'<title>{idx}. {E(str(r.segment))} (HUC {E(r.huc8)})</title></path>')
    L.append('<g class="v-river">')
    for r in layers.trib.itertuples():
        L.append(f'<path class="z-trib" d="{p.path(r.geometry, **fine)}"><title>{E(r.gnis_name)}</title></path>')
    L.append("</g>")
    if layers.river is not None:
        d = p.path(layers.river, **fine)
        L.append(f'<path class="z-halo" d="{d}"/><path class="z-river" d="{d}"><title>{E(label)}</title></path>')
    L.append('<g class="v-river">')
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
    L.append('</g><g class="v-state">')
    for name, lon, lat in CITIES:
        x, y = p.xy(lon, lat)
        L.append(_pin(x, y, f'<circle r="2.2"/><text x="5" y="3.5">{name}</text>', "z-city", zoom))
    # a ring and a label when the river is small at state scale
    shape = layers.river if layers.river is not None else area
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
    L.append("</g></svg>")
    # corner locator for the river view, scale bar, buttons, key
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
    scale = (f'<div class="zscale" aria-hidden="true"><span style="width:{km / km_view * 100:.1f}%"></span>'
             f'{km} km ({km / KM_PER_MI:.0f} mi)</div>' if km / km_view < 0.5 else "")
    key = ('<ul class="maplegend" aria-label="Map key">' + "".join(
        f'<li><span class="n" style="--c:var(--s{i + 1})">{i + 1}</span>{E(sg)}</li>' for i, sg in enumerate(segments))
        + '<li><i></i>river and reservoirs</li><li><b class="g"></b>gauge</li></ul>')
    buttons = ('<div class="zbar" role="group" aria-label="Map view">'
               '<button type="button" data-view="state" aria-pressed="false">New Mexico</button>'
               '<button type="button" data-view="river" aria-pressed="true">The river</button></div>')
    return f'<div class="zwrap" data-view="river">{buttons}<div class="zframe">{"".join(L)}{inset}{scale}</div>{key}</div>'


ZOOM_JS = r"""
(function () {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.querySelectorAll(".zwrap").forEach(w => {
    const svg = w.querySelector("svg.zmap"), btns = w.querySelectorAll("button[data-view]"), pins = svg.querySelectorAll("[data-pin]");
    const parse = s => s.split(" ").map(Number);
    let cur = parse(svg.getAttribute("viewBox")), touched = false, frame = 0;
    const scale = () => { const k = cur[2] / Math.max(1, svg.getBoundingClientRect().width);
      pins.forEach(p => p.setAttribute("transform", "translate(" + p.dataset.pin + ") scale(" + k.toFixed(5) + ")")); };
    const go = (to, animate) => {
      const a = cur.slice(), b = parse(svg.dataset[to]), t0 = performance.now(), dur = animate ? 700 : 0;
      w.dataset.view = svg.dataset.view = to;
      btns.forEach(x => x.setAttribute("aria-pressed", String(x.dataset.view === to)));
      cancelAnimationFrame(frame);
      const step = now => { const t = dur ? Math.min(1, (now - t0) / dur) : 1, e = t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
        cur = a.map((v, i) => v + (b[i] - v) * e); svg.setAttribute("viewBox", cur.map(v => v.toFixed(3)).join(" ")); scale();
        if (t < 1) frame = requestAnimationFrame(step); };
      frame = requestAnimationFrame(step);
    };
    btns.forEach(x => x.addEventListener("click", () => { touched = true; go(x.dataset.view, !reduce); }));
    new ResizeObserver(scale).observe(svg);
    // open on the state, then zoom to the river once; with reduced motion stay on the river view
    // (its corner locator shows where it is) and let the button show the state
    if (reduce) scale(); else { go("state", false); setTimeout(() => { if (!touched) go("river", true); }, 1200); }
  });
})();
"""

MAP_CSS = """
.zwrap .zbar { display: flex; gap: 6px; margin-bottom: 8px; }
.zwrap .zbar button { font: inherit; font-size: 0.84rem; border: 1px solid var(--control); background: var(--surface-1); color: var(--text-primary);
  border-radius: 4px; padding: 6px 12px; min-height: 32px; cursor: pointer; }
.zwrap .zbar button[aria-pressed="true"] { background: var(--text-primary); color: var(--surface-1); border-color: var(--text-primary); }
.zframe { position: relative; }
.zmap { display: block; width: 100%; height: auto; max-height: 78vh; background: var(--surface-1); }
.zmap path { vector-effect: non-scaling-stroke; }
.zmap .z-county { fill: none; stroke: var(--hair); stroke-width: .6; }
.zmap .z-state { fill: none; stroke: var(--text-muted); stroke-width: 1.3; stroke-dasharray: 5 3; }
.zmap .z-main { fill: none; stroke: var(--text-muted); stroke-width: 1; stroke-opacity: .6; }
.zmap .z-seg { fill: var(--c); fill-opacity: .16; stroke: var(--c); stroke-width: 1.2; }
.zmap .z-seg:hover { fill-opacity: .28; }
.zmap .z-trib { fill: none; stroke: var(--river); stroke-width: 1; stroke-opacity: .6; }
.zmap .z-halo { fill: none; stroke: var(--surface-1); stroke-width: 6; stroke-linecap: round; stroke-linejoin: round; }
.zmap .z-river { fill: none; stroke: var(--river); stroke-width: 2.8; stroke-linecap: round; stroke-linejoin: round; }
.zmap .z-res { fill: var(--river); stroke: var(--river); stroke-width: 2; }
.zmap text { font-family: var(--font-body); paint-order: stroke; stroke: var(--surface-1); stroke-width: 3px; stroke-linejoin: round; }
.zmap .z-reslabel text { fill: var(--river); font-size: 11px; font-style: italic; font-weight: 500; }
.zmap .z-town circle, .zmap .z-city circle { fill: var(--text-secondary); }
.zmap .z-town text, .zmap .z-city text { fill: var(--text-secondary); font-size: 11.5px; font-weight: 500; }
.zmap .z-gauge circle { fill: var(--c); stroke: var(--surface-1); stroke-width: 1.5; }
.zmap .z-gauge:hover circle { stroke: var(--text-primary); }
.zmap .z-num circle { fill: var(--surface-1); stroke: var(--c); stroke-width: 2.2; }
.zmap .z-num text { fill: var(--text-primary); font: 600 11px var(--font-num); stroke-width: 0; }
.zmap .z-callout circle { fill: none; stroke: var(--text-primary); stroke-width: 1.6; }
.zmap .z-callout path { stroke: var(--text-primary); stroke-width: 1.2; }
.zmap .z-callout text { fill: var(--text-primary); font-size: 13px; font-weight: 700; }
.zmap .v-state, .zmap .v-river { transition: opacity .3s; }
.zmap[data-view="river"] .v-state, .zmap[data-view="state"] .v-river { opacity: 0; visibility: hidden; }
.zinset { position: absolute; top: 8px; right: 8px; width: 84px; height: auto; background: var(--surface-1);
  border: 1px solid var(--hair); border-radius: 4px; padding: 3px; transition: opacity .3s; }
.zinset .i-state { fill: var(--grid); stroke: var(--text-muted); stroke-width: .8; }
.zinset .i-area { fill: var(--river); fill-opacity: .35; }
.zinset .i-river { fill: none; stroke: var(--river); stroke-width: 1.4; }
.zinset .i-box { fill: none; stroke: var(--text-primary); stroke-width: 1.2; }
.zscale { position: absolute; left: 10px; bottom: 8px; width: calc(100% - 20px); pointer-events: none; transition: opacity .3s;
  font: 11px var(--font-num); color: var(--text-secondary); }
.zscale span { display: block; height: 5px; border: 1.5px solid var(--text-secondary); border-top: 0; margin-bottom: 2px; }
.zwrap[data-view="state"] .zinset, .zwrap[data-view="state"] .zscale { opacity: 0; visibility: hidden; }
.maplegend { display: flex; flex-wrap: wrap; gap: 6px 14px; margin: 8px 0 0; padding: 0; list-style: none; font-size: 0.8rem; color: var(--text-secondary); }
.maplegend li { display: inline-flex; align-items: center; gap: 6px; }
.maplegend .n { display: inline-grid; place-items: center; width: 18px; height: 18px; border-radius: 50%; border: 2px solid var(--c);
  font: 600 10px var(--font-num); color: var(--text-primary); }
.maplegend i { display: inline-block; width: 16px; border-top: 2.5px solid var(--river); }
.maplegend b.g { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--text-secondary); }
@media (prefers-reduced-motion: reduce) { .zmap .v-state, .zmap .v-river, .zinset, .zscale { transition: none; } }
"""


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
