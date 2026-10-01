"""Facts about a river from the researched background files and the archive: cited research, acequias, per-segment facts.

Plain data; the Overview is rendered by the website."""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
import yaml
from . import river_context as rc
from . import river_geo as rm

log = logging.getLogger("nmwater.derived.river_facts")


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


def segment_facts(rows: list[dict], layers: rm.Layers, aqs: list[dict]) -> list[dict]:
    """Per segment, upstream to downstream: its status row plus the towns beside the river in it and its acequias."""
    towns = layers.all_towns
    if len(towns) and layers.river is not None:          # towns beside the river, not anywhere in a large watershed
        import warnings

        with warnings.catch_warnings():                  # degrees are fine for "within a few km"
            warnings.simplefilter("ignore", UserWarning)
            towns = towns[towns.distance(layers.river) < TOWN_DEG]
    out = []
    for x in rows:
        poly = layers.hucs.loc[layers.hucs["segment"] == x["segment"], "geometry"]
        tn = [] if poly.empty else [rm.town_name(n) for n in towns[towns.within(poly.iloc[0])]["NAME20"]][:4]
        out.append({**x, "towns": tn, "acequias": [a["name"] for a in aqs if a["segment"] == x["segment"]]})
    return out


def acequia_list(label: str, ctx: rc.Context, aq_db: dict) -> list[dict]:
    """Acequias along the river: those in the State Engineer's map within ACEQUIA_KM, plus those the
    governance research places on it. One entry per name: {name, segment, where, sources}."""
    out: dict[str, dict] = {}
    for seg, v in ctx.acequias.items():
        for n in v["names"]:
            out.setdefault(_norm(n), {"name": n, "segment": seg, "where": "", "sources": []})
    for a in governed_acequias(label, ctx, aq_db):
        e = out.setdefault(_norm(a["name"]), {"name": a["name"], "segment": None, "where": "", "sources": []})
        e["name"] = a["name"]                         # the acequia's own spelling
        e["where"] = ", ".join(x for x in (a.get("community"), f'{a["county"]} County' if a.get("county") else None)
                               if x and "(" not in str(x))
        e["sources"] = a.get("sources") or []
    return sorted(out.values(), key=lambda e: e["name"].lower())
