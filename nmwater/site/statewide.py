"""The statewide part of the site data bundle (schema_version 2; schemas in docs/site-data/v2/).

statewide/
  index.json         one entry per metric: its title, unit, as-of date, headline figures and the file holding its history
  reservoirs.json    acre-feet held and percent full of groups of reservoirs, with history, a normal for the date, window changes, members and exclusions
  (drought, temperature, snowpack and flow files follow in later phases)
  *.csv              the histories as tables

Definitions and exclusions are in nmwater/derived/statewide.py and docs/statewide.md.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ..derived import statewide as sw
from . import SCHEMA_VERSION
from .river import clean

log = logging.getLogger("nmwater.site.statewide")

RESERVOIR_EXCLUSIONS = [
    "Reservoirs in Colorado, Texas and Arizona that matter to New Mexico (Platoro, Rio Grande Reservoir, Lake Meredith, Red Bluff and others): not in the registry.",
    "Smaller New Mexico reservoirs not in the registry (for example Storrie Lake, Morgan Lake, Stubblefield, Farmington Lake) and mine and industrial tailings ponds.",
    "Water in storage is not all New Mexico's: San Juan-Chama water in Heron, El Vado and Abiquiu belongs to its contractors, and Elephant Butte and Caballo store Rio Grande Project water allocated to irrigation districts in New Mexico and Texas and to Mexico.",
]


def _weekly(s: pd.DataFrame, since: int | None) -> pd.DataFrame:
    """The last day of each week, from the first year every member reports."""
    d = s[s["complete"]]
    if since:
        d = d[d.index.year >= since]
    return d.resample("W-SUN").last().dropna(subset=["storage_af"])


def reservoirs_file(frames: dict[str, pd.DataFrame], reg: dict, generated: str, csv_dir: Path | None) -> tuple[dict, dict] | None:
    """(statewide/reservoirs.json content, the index entry), or None when there are no reservoirs."""
    if not frames:
        return None
    members: dict[str, list[str]] = {g: [] for g in sw.GROUPS}
    for k, f in frames.items():
        members[sw.GROUP_OF_MEASURE[f["measure"].iloc[-1]]].append(k)
    members = {g: sorted(ks) for g, ks in members.items()}
    all_keys = sorted(frames)
    series = {g: sw.group_series(frames, ks) for g, ks in members.items() if ks}
    series["all"] = sw.group_series(frames, all_keys)
    groups, hist = {}, {}
    for g, s in series.items():
        ks = all_keys if g == "all" else members[g]
        asof = sw.latest_complete(s)
        if asof is None:
            continue
        pct_group = g in sw.PERCENT_GROUPS
        row = s.loc[asof]
        changes = [c for d in sw.WINDOWS if (c := sw.window_change(s, asof, d, pct_group))]
        entry = {"members": len(ks), "keys": ks, "as_of": asof, "storage_af": float(row["storage_af"]), "capacity_af": float(row["capacity_af"]) if pct_group else None,
                 "percent": float(100 * row["storage_af"] / row["capacity_af"]) if pct_group and row["capacity_af"] else None,
                 "stale": sw.stale_members(frames, ks, asof), "changes": changes,
                 "shares": sorted(({"key": k, "slug": frames[k]["slug"].iloc[-1], "name": reg[k]["name"], "storage_af": float(frames[k].sort_values("date")["stor"].iloc[-1])} for k in ks),
                                  key=lambda x: -x["storage_af"])}
        for x in entry["shares"]:
            x["share_percent"] = round(100 * x["storage_af"] / entry["storage_af"], 1) if entry["storage_af"] else None
        if g in ("supply", "all"):
            floor = None if not entry["capacity_af"] else sw.MATERIAL_POINTS / 100 * entry["capacity_af"]
            normal, pctile, klass, floored, median = sw.storage_normal(s, floor)
            entry.update(normal=None if normal is None else {"years": normal["years"], "basis": f"{len(normal['years'])} years of {sw.BASELINE[0]}-{sw.BASELINE[1]} in which every member reports",
                                                             "baseline": f"{sw.BASELINE[0]}-{sw.BASELINE[1]}", "window_days": 3, "of": "acre-feet held"},
                         normal_storage_af=median, percentile=pctile, **{"class": klass}, class_floored=floored)
            if g == "supply" and normal is not None:
                rec = s[s.index > asof - pd.Timedelta(days=365)]
                rd = sw.doy366(pd.Series(rec.index))
                hist["recent_normal"] = {f"normal_p{q}": [None if np.isnan(normal[f"p{q}"][i - 1]) else float(normal[f"p{q}"][i - 1]) for i in rd] for q in (10, 25, 50, 75, 90)}
        groups[g] = entry
        w = _weekly(s, sw.HISTORY_FROM.get(g))
        hist[g] = {"dates": [d.date().isoformat() for d in w.index], "storage_af": [float(v) for v in w["storage_af"]],
                   "capacity_af": [None if (not pct_group or pd.isna(v)) else float(v) for v in w["capacity_af"]],
                   "members": [int(v) for v in w["members"]]}
    if "supply" not in groups:
        return None
    sup = series["supply"]
    rec = sup[(sup.index > groups["supply"]["as_of"] - pd.Timedelta(days=365)) & (sup.index <= groups["supply"]["as_of"])]
    recent = {"dates": [d.date().isoformat() for d in rec.index], "storage_af": [None if pd.isna(v) else float(v) for v in rec["storage_af"]],
              "capacity_af": [None if pd.isna(v) else float(v) for v in rec["capacity_af"]],
              "percent": [None if (pd.isna(a) or not b) else float(100 * a / b) for a, b in zip(rec["storage_af"], rec["capacity_af"])], **hist.pop("recent_normal", {})}
    out = {"schema_version": SCHEMA_VERSION, "id": "reservoirs", "generated": generated, "as_of": groups["supply"]["as_of"], "groups": groups,
           "history": {k: v for k, v in hist.items()}, "recent_supply": recent, "ffill_days": sw.FFILL_DAYS, "stale_days": sw.STALE_DAYS,
           "definitions": {"supply": "Storage reservoirs and forebays with an operator-table capacity and a real full pool; percent full is storage divided by full-pool capacity in force that day, summed over the group.",
                           "flood_control": "Flood-control lakes: percent of the conservation pool (above 100% is flood water being held back).",
                           "dry": "Dry flood-control dams, empty by design: acre-feet only.",
                           "owner_reported": "Reservoirs whose only capacity is the owner's estimate of normal storage: acre-feet only, never a percent.",
                           "all": "Every registry reservoir: acre-feet only (a sum of different pools has no meaningful percent)."},
           "exclusions": RESERVOIR_EXCLUSIONS}
    if csv_dir is not None:
        wk = pd.DataFrame({"week_ending": hist["supply"]["dates"], "storage_af": hist["supply"]["storage_af"], "capacity_af": hist["supply"]["capacity_af"],
                           "members": hist["supply"]["members"]})
        wk["percent"] = (100 * wk["storage_af"] / wk["capacity_af"]).round(2)
        wk.to_csv(csv_dir / "reservoirs_supply_weekly.csv", index=False)
    g = groups["supply"]
    entry = {"id": "reservoirs", "title": "Reservoir storage", "file": "reservoirs.json", "as_of": g["as_of"], "unit": "acre-feet",
             "headline": {"storage_af": groups["all"]["storage_af"], "members": groups["all"]["members"], "supply_percent": g["percent"], "supply_storage_af": g["storage_af"],
                          "supply_capacity_af": g["capacity_af"], "supply_members": g["members"], "class": g["class"], "percentile": g["percentile"], "normal_storage_af": g["normal_storage_af"]},
             "sources": ["usbr_hydrodata", "usace_cwms", "usgs"]}
    return out, entry


def export_statewide(out: Path, frames: dict[str, pd.DataFrame], reg: dict, generated: str, csv: bool = True) -> dict | None:
    """Write <out>/statewide/ and return the manifest block, or None when there is nothing to say."""
    base = out / "statewide"
    base.mkdir(parents=True, exist_ok=True)
    metrics, files = [], {"csv": []}
    r = reservoirs_file(frames, reg, generated, base if csv else None)
    if r:
        content, entry = r
        (base / "reservoirs.json").write_text(json.dumps(clean(content), separators=(",", ":"), allow_nan=False), encoding="utf-8")
        metrics.append(entry)
        if csv:
            files["csv"].append("reservoirs_supply_weekly.csv")
    if not metrics:
        return None
    as_of = max(m["as_of"] for m in metrics)
    index = {"schema_version": SCHEMA_VERSION, "generated": generated, "as_of": as_of, "metrics": metrics, "files": {"index": "index.json", **files}}
    (base / "index.json").write_text(json.dumps(clean(index), separators=(",", ":"), allow_nan=False), encoding="utf-8")
    log.info("statewide: %s", ", ".join(m["id"] for m in metrics))
    return {"path": "statewide/", "metrics": [m["id"] for m in metrics], "as_of": as_of, "files": index["files"]}
