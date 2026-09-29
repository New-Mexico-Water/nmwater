"""Search and social-sharing metadata for the river pages.

- head_meta(): description, canonical URL, Open Graph and Twitter card tags, and JSON-LD (the page, its
  breadcrumb, and the river's data as a schema.org Dataset so dataset search engines can find the CSVs).
- social_card(): a 1200 x 630 PNG preview per river (state map with the river, its name and last week's
  status), rendered from SVG with rsvg-convert. Skipped, with a warning, where rsvg-convert is missing.
- sitemap() and robots(): for the site root.

Absolute URLs need the site's public address, `site.base_url` in config/river_reports.yaml. Until it is
set, pages still get their description, titles and JSON-LD, but no canonical link, og:url, og:image or
sitemap (the protocols require absolute URLs).
"""

from __future__ import annotations

import html
import json
import logging
import shutil
import subprocess
from pathlib import Path

from . import river_map as rm

log = logging.getLogger("nmwater.reports.river_share")
E = html.escape
DEFAULT_SITE = {"name": "New Mexico River Conditions", "base_url": None, "locale": "en_US", "twitter": None, "license": None}


def site_config(cfg: dict) -> dict:
    s = {**DEFAULT_SITE, **(cfg.get("site") or {})}
    if s["base_url"] and not s["base_url"].endswith("/"):
        s["base_url"] += "/"
    return s


def trim(text: str, n: int = 158) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def status_line(rows: list[dict]) -> str:
    """Last week's rating in words: one segment's class, or how many segments were below normal."""
    rated = [x for x in rows if x["cls"]]
    if not rated:
        return ""
    if len(rows) == 1:
        return f"Last week's flow: {rated[0]['cls']}."
    below = sum("below" in x["cls"] for x in rated)
    return (f"Last week {below} of {len(rated)} segments were below normal." if below
            else f"Last week none of its {len(rated)} rated segments was below normal.")


def description(river: str, rows: list[dict], tabs: list[str], as_of) -> str:
    what = ["weekly streamflow", "drying"]
    if "quality" in tabs:
        what.append("water temperature")
    if "watershed" in tabs:
        what.append("snowpack and drought")
    status = status_line(rows)
    when = f"Data through {as_of:%b} {as_of.day}, {as_of.year}."
    for n in range(len(what), 0, -1):                  # drop topics before dropping the status
        topics = what[0] if n == 1 else ", ".join(what[:n - 1]) + " and " + what[n - 1]
        text = f"The {river}, New Mexico: {topics} by watershed segment. {status} {when}".replace("  ", " ")
        if len(text) <= 160:
            return text
    return trim(text)


def head_meta(site: dict, *, title: str, desc: str, path: str, image: str | None, image_alt: str,
              jsonld: list[dict], kind: str = "website") -> str:
    """path: the page's path under the site root, e.g. 'rivers/rio-grande/'. image: path of the PNG."""
    base = site["base_url"]
    url = base + path if base else None
    img = base + image if base and image else None
    m = [f'<meta name="description" content="{E(desc)}">',
         '<meta name="robots" content="index, follow, max-image-preview:large">',
         '<meta name="theme-color" content="#f3f4f2" media="(prefers-color-scheme: light)">',
         '<meta name="theme-color" content="#121211" media="(prefers-color-scheme: dark)">',
         f'<meta property="og:type" content="{kind}">', f'<meta property="og:site_name" content="{E(site["name"])}">',
         f'<meta property="og:title" content="{E(title)}">', f'<meta property="og:description" content="{E(desc)}">',
         f'<meta property="og:locale" content="{E(site["locale"])}">',
         f'<meta name="twitter:card" content="{"summary_large_image" if img else "summary"}">',
         f'<meta name="twitter:title" content="{E(title)}">', f'<meta name="twitter:description" content="{E(desc)}">']
    if url:
        m += [f'<link rel="canonical" href="{E(url)}">', f'<meta property="og:url" content="{E(url)}">']
    if img:
        m += [f'<meta property="og:image" content="{E(img)}">', '<meta property="og:image:type" content="image/png">',
              '<meta property="og:image:width" content="1200">', '<meta property="og:image:height" content="630">',
              f'<meta property="og:image:alt" content="{E(image_alt)}">', f'<meta name="twitter:image" content="{E(img)}">',
              f'<meta name="twitter:image:alt" content="{E(image_alt)}">']
    if site.get("twitter"):
        m.append(f'<meta name="twitter:site" content="{E(site["twitter"])}">')
    ld = json.dumps({"@context": "https://schema.org", "@graph": jsonld}, ensure_ascii=False, separators=(",", ":"))
    m.append('<script type="application/ld+json">' + ld.replace("</", "<\\/") + "</script>")
    return "".join(m)


def river_jsonld(site: dict, *, river: str, slug: str, title: str, desc: str, bounds, first_year: int, as_of,
                 csvs: list[str], variables: list[str]) -> list[dict]:
    base = site["base_url"] or ""
    url = f"{base}rivers/{slug}/"
    index = f"{base}rivers/"
    page = {"@type": "WebPage", "@id": url + "#page", "url": url, "name": title, "description": desc, "inLanguage": "en-US",
            "dateModified": as_of.isoformat(), "isPartOf": {"@type": "WebSite", "name": site["name"], **({"url": base} if base else {})},
            "about": {"@type": "RiverBodyOfWater", "name": river, "containedInPlace": {"@type": "State", "name": "New Mexico"}},
            "breadcrumb": {"@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "All rivers", "item": index},
                {"@type": "ListItem", "position": 2, "name": river, "item": url}]}}
    w, s, e, n = bounds
    data = {"@type": "Dataset", "@id": url + "#data", "name": f"{river} streamflow and conditions by watershed segment",
            "description": trim(f"Weekly mean streamflow for the {river} in New Mexico by HUC8 watershed segment, merged from "
                                "USGS, Bureau of Reclamation, Army Corps of Engineers, State Engineer and other agency gauges, "
                                "with flow compared with the 1991-2020 normal and days of no flow each year.", 5000),
            "url": url, "isAccessibleForFree": True, "creator": {"@type": "Organization", "name": site["name"]},
            "temporalCoverage": f"{first_year}/{as_of.isoformat()}", "dateModified": as_of.isoformat(),
            "keywords": [river, "New Mexico", "streamflow", "discharge", "cfs", "drought", "river conditions", "HUC8"],
            "variableMeasured": variables,
            "spatialCoverage": {"@type": "Place", "name": f"{river}, New Mexico",
                                "geo": {"@type": "GeoShape", "box": f"{s:.4f} {w:.4f} {n:.4f} {e:.4f}"}},
            "distribution": [{"@type": "DataDownload", "encodingFormat": "text/csv", "name": c, "contentUrl": url + c} for c in csvs]}
    if site.get("license"):
        data["license"] = site["license"]
    return [page, data]


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


def sitemap(root: Path, site: dict, entries: list[dict], lastmod: str) -> None:
    """Write <root>/sitemap.xml for the rivers index and every river page (needs base_url)."""
    base = site["base_url"]
    if not base:
        return
    urls = [f"{base}rivers/"] + [f"{base}rivers/{e['slug']}/" for e in entries]
    body = "".join(f"<url><loc>{E(u)}</loc><lastmod>{lastmod}</lastmod></url>" for u in urls)
    (root / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
                                      f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>\n')


def robots(out: Path, site: dict) -> None:
    lines = ["User-agent: *", "Allow: /"]
    if site["base_url"]:
        lines.append(f"Sitemap: {site['base_url']}rivers/sitemap.xml")
    (out / "robots.txt").write_text("\n".join(lines) + "\n")
