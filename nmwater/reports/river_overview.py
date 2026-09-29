"""The Overview tab of a river page (layout "B", chosen 2026-09-28): the river told from the headwaters
down, with cited background, beside a map that zooms between New Mexico and the river.

Two kinds of content, always labelled as such:
- counted from the archive (river_context.py, river_map.py): reservoirs, irrigation districts, public
  water systems, county water use, acequias in the State Engineer's acequia map, towns and gauges;
- cited background (config/river_context/<slug>.yaml, config/acequia_governance.yaml): researched
  statements, each with numbered sources that were checked by a reviewer before use.

Acequias are presented under Culture: they are community-governed irrigation systems, political
subdivisions of the state with elected commissioners and a mayordomo, and centuries-old institutions,
not just ditches. They also appear, briefly, among the river's water users.
"""

from __future__ import annotations

import html
import logging
import re
import unicodedata
from pathlib import Path

import pandas as pd
import yaml

from . import river_context as rc
from . import river_map as rm

E = html.escape
log = logging.getLogger("nmwater.reports.river_overview")
TOWN_DEG = 0.06          # about 5-6 km: a town "on" the river in the segment list
ROLE = {"storage": "water storage", "flood_control": "flood control", "dry_flood": "flood control (normally dry)",
        "diversion": "irrigation diversion"}


UNCHECKED = "# Not yet checked by a reviewer"


def load_research(root: Path, slug: str) -> dict:
    """A river's cited background, or {} until a reviewer has checked it against its sources (the file
    still carries the UNCHECKED line)."""
    p = root / "config" / "river_context" / f"{slug}.yaml"
    if not p.exists():
        return {}
    text = p.read_text()
    if UNCHECKED in text:
        log.info("%s: background not yet checked by a reviewer; left out", p.name)
        return {}
    return yaml.safe_load(text) or {}


def load_acequias(root: Path) -> dict:
    p = root / "config" / "acequia_governance.yaml"
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


class Cites:
    """One numbered source list for the page, shared by every research file it draws on."""

    def __init__(self):
        self.items: list[dict] = []
        self._by_url: dict[str, int] = {}

    def refs(self, ids, sources: dict) -> str:
        out = []
        for i in ids or []:
            s = sources.get(i) or sources.get(str(i)) or sources.get(int(i) if str(i).isdigit() else i)
            if not s or not s.get("url"):
                continue
            n = self._by_url.get(s["url"])
            if n is None:
                self.items.append(s)
                n = self._by_url[s["url"]] = len(self.items)
            who = ", ".join(x for x in (s.get("title"), s.get("publisher")) if x)
            out.append(f'<a class="cite" href="{E(s["url"])}" target="_blank" rel="noopener"><span class="vh">source </span>{n}'
                       f'<span class="vh">: {E(who)} (opens in a new tab)</span></a>')
        return "".join(out)

    def html(self) -> str:
        if not self.items:
            return ""
        lis = "".join(f'<li id="src-{i + 1}"><a href="{E(s["url"])}" target="_blank" rel="noopener">{E(s.get("title") or s["url"])}'
                      f'<span class="vh"> (opens in a new tab)</span></a>{", " + E(s["publisher"]) if s.get("publisher") else ""}'
                      f' <span class="when">accessed {E(str(s.get("accessed", "")))}</span></li>' for i, s in enumerate(self.items))
        return (f'<section class="card" aria-labelledby="h-sources"><h2 id="h-sources">Sources</h2>'
                '<p class="hint">Background statements on this tab come from these pages and were checked against them. '
                f'Everything else is counted from the archive\'s data, as labelled.</p><ol class="sources">{lis}</ol></section>')


def _items(sec: list, sources: dict, cites: Cites) -> str:
    return "<ul class='facts'>" + "".join(f"<li>{E(x['text'])}{cites.refs(x.get('sources'), sources)}</li>" for x in sec) + "</ul>"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", " ", s)
    return " ".join(w for w in s.split() if w not in {"acequia", "de", "del", "la", "las", "los", "el", "y", "community", "ditch"})


def _base(label: str) -> str:
    return _norm(label.split(" (")[0].removesuffix(" River").removesuffix(" Creek"))


def governed_acequias(label: str, ctx: rc.Context, db: dict) -> list[dict]:
    """Acequias in the governance research that are on this river: the source names the river as their
    stream, or the name matches an acequia the State Engineer maps along the river. Either way the county
    must be one the river crosses, since names like Acequia Madre repeat across the state."""
    counties = {c.lower() for c in (ctx.water_use.get("counties") or [])}
    base = _base(label)
    mapped = {_norm(n) for a in ctx.acequias.values() for n in a["names"]}
    out = []
    for a in db.get("acequias") or []:
        county = str(a.get("county") or "").lower().replace(" county", "").strip()
        if counties and county and county not in counties:
            continue
        stream = _norm(str(a.get("stream") or "").removesuffix(" River").removesuffix(" river"))
        on_stream = bool(stream and base and (stream == base or stream.startswith(base + " ")))
        if stream and not on_stream:            # the source puts it on another stream
            continue
        if on_stream or (_norm(a.get("name", "")) in mapped and county):
            out.append(a)
    return out


# ---------------------------------------------------------------------------- cards
def about(b, facts: rm.MapFacts, res: dict, sources: dict, cites: Cites, background: dict | None) -> str:
    first = (pd.Timestamp(min(b.r.seg_all["week_start"])) + pd.Timedelta(days=6)).year   # first week can start in late December
    auto = rm.describe(b.r.river, facts, b.r.segments, len(b.r.elig), first, b.r.as_of.year)
    out = []
    if res.get("summary"):
        out.append("<p>" + " ".join(E(x["text"]) for x in res["summary"]) + cites.refs(res.get("summary_sources"), sources) + "</p>")
    elif background and background.get("text"):
        out.append("<p>" + E(" ".join(str(background["text"]).split())) + "</p>")
        if background.get("sources"):
            out.append(f'<p class="src">Background from general references: {E("; ".join(background["sources"]))}.</p>')
    out.append(f'<p>{E(auto)}</p><p class="src">From the archive: NHDPlus river network, WBD watersheds, NHD waterbodies, '
               "TIGER urban areas and the report's gauges.</p>")
    return f'<section class="card about" aria-labelledby="h-about"><h2 id="h-about">About the {E(b.r.river)}</h2>{"".join(out)}</section>'


def journey(rows: list[dict], layers: rm.Layers, ctx: rc.Context) -> str:
    towns = layers.all_towns
    if len(towns) and layers.river is not None:          # towns beside the river, not anywhere in a large watershed
        import warnings

        with warnings.catch_warnings():                  # degrees are fine for "within a few km"
            warnings.simplefilter("ignore", UserWarning)
            towns = towns[towns.distance(layers.river) < TOWN_DEG]
    legs = []
    for i, x in enumerate(rows):
        seg = x["segment"]
        poly = layers.hucs.loc[layers.hucs["segment"] == seg, "geometry"]
        tn = [] if poly.empty else [rm.town_name(n) for n in towns[towns.within(poly.iloc[0])]["NAME20"]][:4]
        aq = ctx.acequias.get(seg)
        chip = "" if not x["cls"] else f' <span class="st {x["cls"].replace(" ", "-")}">{E(x["cls"])}</span>'
        flow = "No data last week" if x["flow"] is None else f'{x["flow"]:,.0f} cfs last week'
        dry = "" if not x["dry"] else f', {x["dry"]} dry day{"s" if x["dry"] != 1 else ""} this year'
        legs.append(
            f'<li class="leg" style="--c:var(--s{i + 1})"><span class="num" aria-hidden="true">{i + 1}</span>'
            f'<h3><span class="vh">Segment {i + 1}: </span>{E(seg)}</h3><p>{flow}{chip}{dry}.</p>'
            + (f"<p>Towns: {E(', '.join(tn))}.</p>" if tn else "")
            + (f'<p>Acequias: {aq["count"]} in the State Engineer\'s map along this reach'
               + (f', including {E(", ".join(aq["names"][:4]))}' if aq["names"] else "") + '. <a href="#acequias">About acequias</a></p>'
               if aq else "")
            + "</li>")
    return ('<section class="card" aria-labelledby="h-journey"><h2 id="h-journey">From the headwaters down</h2>'
            '<p class="hint">Each watershed segment in order, with last week\'s flow rated against the same week in 1991-2020.</p>'
            f'<ol class="journey">{"".join(legs)}</ol></section>')


def culture(label: str, ctx: rc.Context, res: dict, sources: dict, aq_db: dict, cites: Cites) -> str:
    aq_sources = {str(k): v for k, v in (aq_db.get("sources") or {}).items()}
    general = [x for x in res.get("culture") or [] if "acequia" not in x["text"].lower()]
    river_aq = [x for x in res.get("culture") or [] if "acequia" in x["text"].lower()]
    parts = [_items(general, sources, cites)] if general else []
    gov = governed_acequias(label, ctx, aq_db)
    if not (ctx.acequias or gov or river_aq):         # no acequias known on this river: no acequia section
        return "" if not parts else f'<section class="card" aria-labelledby="h-culture"><h2 id="h-culture">Culture</h2>{"".join(parts)}</section>'
    # acequias: what they are, then this river's
    a = ['<h3 class="h3" id="acequias">Acequias</h3>']
    if aq_db.get("general"):
        a.append(_items(aq_db["general"][:4], aq_sources, cites))
    if river_aq:
        a.append(_items(river_aq, sources, cites))
    if gov:
        lis = "".join(f'<li><b>{E(x["name"])}</b>{", " + E(x["community"]) if x.get("community") else ""}'
                      f'{", " + E(x["county"]) + " County" if x.get("county") else ""}: {E(x.get("evidence", ""))}'
                      f'{cites.refs(x.get("sources"), aq_sources)}</li>' for x in gov)
        a.append(f'<p>Acequias on this river with published evidence of their own governance (elected commissioners, a mayordomo, '
                 f'filings as a political subdivision or membership of a regional association):</p><ul class="facts">{lis}</ul>')
    if ctx.acequias:
        rows = "".join(f'<li>{E(seg)}: {v["count"]}' + (f' ({E(", ".join(v["names"]))})' if v["names"] else "") + "</li>"
                       for seg, v in ctx.acequias.items())
        total = sum(v["count"] for v in ctx.acequias.values())
        a.append(f'<details><summary>{total} acequia{"s" if total != 1 else ""} in the State Engineer\'s acequia map within '
                 f'{rc.ACEQUIA_KM:g} km of the river</summary><ul class="facts">{rows}</ul></details>')
    a.append('<p class="src">The State Engineer\'s acequia map covers mainly northern New Mexico, and not every acequia has '
             'published records, so a river with none listed here may still have active acequias.</p>')
    parts.append("".join(a))
    return f'<section class="card" aria-labelledby="h-culture"><h2 id="h-culture">Culture</h2>{"".join(parts)}</section>'


def habitat(res: dict, sources: dict, cites: Cites) -> str:
    if not res.get("habitats"):
        return ""
    return (f'<section class="card" aria-labelledby="h-habitat"><h2 id="h-habitat">Habitat</h2>'
            f'{_items(res["habitats"], sources, cites)}</section>')


def reservoirs(ctx: rc.Context, res: dict, sources: dict, cites: Cites) -> str:
    notes = res.get("reservoirs_notes") or []
    if not ctx.reservoirs:
        body = ('<p class="hint">No reservoir with 500 acre-feet or more of storage on this river in the National '
                "Inventory of Dams.</p>")
    else:
        rows = []
        for r in ctx.reservoirs:
            role = ROLE.get(r.get("role"), r.get("purpose") or "")
            first = r["name"].lower().split(" ")[0]
            n = next((x for x in notes if str(x.get("name", "")).lower().split(" ")[0] == first), None)
            rows.append(f'<tr><th scope="row">{E(r["name"])}{"<small>" + E(r["dam"]) + "</small>" if r.get("dam") and r["dam"] != r["name"] else ""}</th>'
                        f'<td class="n">{r.get("year") or ""}</td><td>{E(role)}</td>'
                        f'<td class="n">{"" if not r.get("storage_af") else f"{r["storage_af"]:,.0f}"}</td></tr>'
                        + (f'<tr class="sub"><td colspan="4">{E(n["text"])}{cites.refs(n.get("sources"), sources)}</td></tr>' if n else ""))
        body = ('<div class="scroll"><table class="facts-t"><caption class="vh">Reservoirs on the river</caption><thead><tr>'
                '<th scope="col">Reservoir</th><th scope="col">Completed</th><th scope="col">Main purpose</th>'
                '<th scope="col">Storage, acre-ft</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"
                '<p class="src">From the archive: National Inventory of Dams (USACE) and the reservoir registry.</p>')
    return f'<section class="card" aria-labelledby="h-res"><h2 id="h-res">Reservoirs</h2>{body}</section>'


def users(ctx: rc.Context, res: dict, sources: dict, cites: Cites) -> str:
    wu = ctx.water_use
    parts = []
    if res.get("users"):
        parts.append(_items(res["users"], sources, cites))
    if wu:
        tot = wu["total_af"]
        top = list(wu["by_category"].items())[:5]
        rows = "".join(f'<tr><th scope="row">{E(k)}</th><td class="bar-c"><i style="width:{v / tot * 100:.1f}%"></i></td>'
                       f'<td class="n">{v / tot:.0%}</td><td class="n">{v:,.0f}</td></tr>' for k, v in top)
        parts.append(f'<h3 class="h3">Surface water withdrawn, {wu["year"]}</h3>'
                     f'<p class="hint">In the counties this river crosses ({E(", ".join(wu["counties"]))}): {tot:,.0f} acre-feet. '
                     'County totals include other streams.</p>'
                     '<div class="scroll"><table class="bars-t"><caption class="vh">Surface-water withdrawals by use</caption>'
                     '<thead><tr><th scope="col">Use</th><th scope="col"><span class="vh">Share (bar)</span></th>'
                     f'<th scope="col">Share</th><th scope="col">Acre-ft</th></tr></thead><tbody>{rows}</tbody></table></div>'
                     '<p class="src">State Engineer, New Mexico Water Use by Categories 2020.</p>')
    if ctx.districts:
        parts.append('<h3 class="h3">Irrigation districts along the river</h3><ul class="facts">' + "".join(
            f"<li>{E(d['name'])} <small>({d['acres']:,.0f} acres mapped)</small></li>" for d in ctx.districts[:5])
            + "</ul><p class='src'>State Engineer's irrigation districts layer.</p>")
    if ctx.acequias:
        total = sum(v["count"] for v in ctx.acequias.values())
        parts.append(f'<p>{total} acequia{"s" if total != 1 else ""} also take water from this river. '
                     '<a href="#acequias">More under Culture</a>.</p>')
    if ctx.water_systems:
        parts.append('<h3 class="h3">Public water systems beside the river</h3><p>' + E(", ".join(ctx.water_systems[:6]))
                     + "</p><p class='src'>State Engineer's public water systems layer (service areas within 2 km).</p>")
    if not parts:
        return ""
    return f'<section class="card" aria-labelledby="h-users"><h2 id="h-users">Water users</h2>{"".join(parts)}</section>'


def panel(b, rows: list[dict], con, gauges: pd.DataFrame, grids: Path, root: Path, background: dict | None) -> tuple[str, dict]:
    """The Overview tab's markup, and facts other parts of the page use (for the description and social card)."""
    layers, facts = rm.collect(b.r.gnis_id, b.r.segments, b.huc8_of, gauges, grids)
    ctx = rc.build(b.r.river, b.r.gnis_id, b.r.segments, b.huc8_of, con, root)
    res = load_research(root, b.r.slug)
    sources = {str(k): v for k, v in (res.get("sources") or {}).items()}
    cites = Cites()
    left = [about(b, facts, res, sources, cites, background), journey(rows, layers, ctx),
            culture(b.r.river, ctx, res, sources, load_acequias(root), cites), habitat(res, sources, cites),
            reservoirs(ctx, res, sources, cites), users(ctx, res, sources, cites)]
    zmap = rm.zoom_map(b.r.gnis_id, b.r.river, b.r.segments, layers, gauges, grids)
    # the map comes first in the source so the focus order matches phones, where it sits on top
    body = (f'<div class="two"><div class="stick"><section class="card" aria-labelledby="h-map"><h2 id="h-map">Where it is</h2>'
            f'<p class="hint">Opens on New Mexico and zooms to the river. Segments are the HUC8 watersheds the gauges sit in, '
            f'numbered upstream to downstream; hover a gauge for its name.</p>{zmap}</section></div>'
            f'<div class="col">{"".join(x for x in left if x)}</div></div>{cites.html()}')
    summary = " ".join(x["text"] for x in res.get("summary") or []) or (background or {}).get("text") or ""
    return body, {"facts": facts, "layers": layers, "summary": " ".join(str(summary).split()), "ctx": ctx}


CSS = """
.two { display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr); gap: 18px; align-items: start; }
.two .col { grid-column: 1; grid-row: 1; display: flex; flex-direction: column; gap: 18px; min-width: 0; }
.two .stick { grid-column: 2; grid-row: 1; position: sticky; top: 64px; min-width: 0; }
@media (max-width: 860px) { .two { grid-template-columns: 1fr; } .two .col, .two .stick { grid-column: 1; grid-row: auto; } .two .stick { position: static; } }
.cite { font: 600 0.7rem var(--font-num); vertical-align: super; margin-left: 2px; text-decoration: none; line-height: 1;
  border: 1px solid var(--control); border-radius: 3px; padding: 0 3px; }
ul.facts { margin: 0; padding-left: 1.1em; display: flex; flex-direction: column; gap: 6px; max-width: 72ch; }
.card .h3 { font-size: 0.92rem; text-transform: none; letter-spacing: 0; color: var(--text-primary); margin: 16px 0 6px; }
.src { color: var(--text-muted); font-size: 0.78rem; margin: 6px 0 0; }
.facts-t th[scope="row"] { font-size: 0.84rem; text-transform: none; letter-spacing: 0; color: var(--text-primary); font-weight: 500; }
.facts-t td, .facts-t th { white-space: normal; vertical-align: top; }
.facts-t small { display: block; color: var(--text-muted); font-weight: 400; }
.facts-t tr.sub td { color: var(--text-secondary); font-size: 0.8rem; text-align: left; border-bottom-style: dashed; }
.bars-t th[scope="row"] { text-transform: none; letter-spacing: 0; font-size: 0.82rem; color: var(--text-primary); font-weight: 400; white-space: normal; }
.bars-t td.bar-c { width: 45%; } .bars-t td.bar-c i { display: block; height: 10px; background: var(--s1); border-radius: 2px; min-width: 1px; }
.journey { list-style: none; margin: 0; padding: 0; }
.leg { position: relative; padding: 2px 0 18px 30px; border-left: 3px solid var(--c); margin-left: 12px; }
.leg:last-child { border-left-color: transparent; padding-bottom: 0; }
.leg .num { position: absolute; left: -15px; top: 0; width: 26px; height: 26px; border-radius: 50%; display: grid; place-items: center;
  background: var(--surface-1); border: 2px solid var(--c); font: 600 12px var(--font-num); }
.leg h3 { margin: 2px 0 6px; text-transform: none; letter-spacing: 0; font-size: 1rem; color: var(--text-primary); }
.leg p { margin: 4px 0; max-width: 70ch; }
ol.sources { padding-left: 1.6em; margin: 0; display: flex; flex-direction: column; gap: 4px; font-size: 0.84rem; }
details summary { cursor: pointer; }
"""
