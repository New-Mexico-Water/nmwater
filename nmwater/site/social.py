"""The 1200x630 share image of a river (a PNG rendered from an SVG with rsvg-convert)."""

from __future__ import annotations

import logging
import html
import shutil
import subprocess
from pathlib import Path
from ..derived import river_geo as rm

from .meta import status_line

log = logging.getLogger("nmwater.site.social")


E = html.escape


# ---------------------------------------------------------------------------- social card
CARD = {"bg": "#f3f4f2", "ink": "#0b0b0b", "ink2": "#52514e", "hair": "#d4d3cd", "river": "#0b4f8a", "area": "#2a78d6",
        "below": "#c0392f", "above": "#1c5cab", "normal": "#77766f"}


def social_card(path: Path, *, river: str, site_name: str, rows: list[dict], layers: rm.Layers, grids: Path, as_of) -> bool:
    """Write a 1200x630 PNG. Returns False when it could not be rendered."""
    exe = shutil.which("rsvg-convert")
    if not exe:
        log.warning("rsvg-convert not found; no social image for %s", river)
        return False
    nm, counties, mains = rm.state_layers(grids)
    b = nm.bounds
    p = rm.Proj((b[0] - 0.1, b[1] - 0.1, b[2] + 0.1, b[3] + 0.1), 400)
    ox, oy = 760, (630 - p.height) / 2
    area = layers.hucs.union_all().intersection(nm.buffer(0.3))
    shape = layers.river if layers.river is not None else area
    rb = shape.bounds
    span = max((rb[2] - rb[0]) * p.k, rb[3] - rb[1]) * p.s
    ring = ""
    if span < 70:
        cx, cy = p.xy(shape.centroid.x, shape.centroid.y)
        ring = f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{max(12, span / 2 + 8):.1f}" fill="none" stroke="{CARD["ink"]}" stroke-width="3"/>'
    rated = [x for x in rows if x["cls"]]
    below = sum("below" in x["cls"] for x in rated)
    above = sum("above" in x["cls"] for x in rated)
    status = status_line(rows).rstrip(".") or f"{len(rows)} watershed segment{'s' if len(rows) != 1 else ''}"
    col = CARD["below"] if rated and below * 2 >= len(rated) else CARD["above"] if rated and above * 2 >= len(rated) else CARD["normal"]
    name = river
    size = 76 if len(name) <= 13 else 62 if len(name) <= 17 else 50 if len(name) <= 22 else 40
    font = "Public Sans, Inter, DejaVu Sans, Helvetica, Arial, sans-serif"
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">'
           f'<rect width="1200" height="630" fill="{CARD["bg"]}"/>'
           f'<rect x="0" y="0" width="1200" height="10" fill="{CARD["river"]}"/>'
           f'<text x="72" y="118" font-family="{font}" font-size="26" font-weight="600" fill="{CARD["ink2"]}" letter-spacing="1">'
           f'{E(site_name.upper())}</text>'
           f'<text x="72" y="{250 if size > 60 else 240}" font-family="{font}" font-size="{size}" font-weight="700" fill="{CARD["ink"]}">{E(name)}</text>'
           f'<text x="72" y="320" font-family="{font}" font-size="34" fill="{CARD["ink2"]}">River conditions in New Mexico</text>'
           f'<circle cx="84" cy="408" r="10" fill="{col}"/>'
           f'<text x="106" y="419" font-family="{font}" font-size="{28 if len(status) < 42 else 24}" font-weight="600" fill="{CARD["ink"]}">{E(status)}</text>'
           f'<text x="72" y="520" font-family="{font}" font-size="24" fill="{CARD["ink2"]}">Streamflow, drying, temperature, snowpack, drought</text>'
           f'<text x="72" y="556" font-family="{font}" font-size="24" fill="{CARD["ink2"]}">Data through {as_of:%b} {as_of.day}, {as_of.year}</text>'
           f'<g transform="translate({ox:.1f} {oy:.1f})">'
           f'<path d="{p.path(counties.boundary.union_all())}" fill="none" stroke="{CARD["hair"]}" stroke-width="1"/>'
           f'<path d="{p.path(mains.union_all().intersection(nm.buffer(0.05)))}" fill="none" stroke="{CARD["ink2"]}" stroke-opacity=".35" stroke-width="1.2"/>'
           f'<path d="{p.path(nm)}" fill="none" stroke="{CARD["ink2"]}" stroke-width="2"/>'
           f'<path d="{p.path(area)}" fill="{CARD["area"]}" fill-opacity=".28" stroke="{CARD["area"]}" stroke-width="1.5"/>'
           f'<path d="{p.path(layers.river)}" fill="none" stroke="{CARD["bg"]}" stroke-width="9" stroke-linecap="round" stroke-linejoin="round"/>'
           f'<path d="{p.path(layers.river)}" fill="none" stroke="{CARD["river"]}" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/>'
           f'{ring}</g></svg>')
    path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([exe, "-w", "1200", "-h", "630", "-f", "png", "-o", str(path)], input=svg.encode(), capture_output=True)
    if r.returncode != 0:
        log.warning("social image for %s failed: %s", river, r.stderr.decode()[:200])
        return False
    return True
