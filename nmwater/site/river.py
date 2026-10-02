"""One river's files in the site data bundle (schema_version 1; schemas in docs/site-data/v1/).

rivers/<slug>/
  summary.json    the Overview: status per segment, description, cited background, acequias, reservoirs, water
                  users, the numbered source list, map description, page metadata
  map.svg         the zoomable map (two levels of detail; styled by the site's stylesheet)
  flow.json       weekly mean flow per segment for the whole record, and the gauges behind it
  normal.json     last 52 weeks rated against 1991-2020, per segment and per gauge   (when there is a baseline)
  drying.json     days a year any gauge in a segment read below 0.1 cfs
  quality.json    water temperature and salinity                                      (when there is data)
  watershed.json  daily and monthly precipitation, temperature, snowpack, drought     (when there is data)
  notes.json      data gaps and disparities: removed values, reviewed notes, automatic findings, gauges
  *.csv, notes.md the downloadable files; social.png the 1200x630 share image

Dates are ISO 8601 strings (`YYYY-MM-DD`), flows are cfs, precipitation inches, temperature degrees C. Series are
parallel arrays with null where there is no value.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ..derived import river_context as rc
from ..derived import river_flow as rf
from ..derived import river_geo as rm
from ..derived import river_normal as rn
from ..derived import river_facts as ro
from ..derived import river_table as rp
from . import geo, mapsvg, meta as rs
from . import social as social_card_mod
from . import SCHEMA_VERSION
from .sources import Sources

CLASSES = [label for _, label in rn.CLASSES]


def clean(o):
    """JSON-safe copy: NaN and inf become null, numpy and pandas scalars become Python ones."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not math.isfinite(float(o)) else round(float(o), 6)          # no 6.6049999999999995
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (pd.Timestamp, datetime)):
        return pd.Timestamp(o).date().isoformat()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if o is pd.NaT:
        return None
    return o


def iso(ms) -> str | None:
    """Epoch milliseconds (what the page code uses) as an ISO date."""
    return None if ms is None else pd.Timestamp(ms, unit="ms").date().isoformat()


def dump(path: Path, obj) -> int:
    text = json.dumps(clean(obj), separators=(",", ":"), allow_nan=False, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return len(text)


def _r(v, nd: int):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), nd)


def _color(i: int) -> int:
    return i + 1                                           # the site's --s1 ... --s8 series colours


# ---------------------------------------------------------------------------- the files
def flow_file(r: rf.RiverReport) -> dict:
    ex = rf.explorer_data(r)
    return {"schema_version": SCHEMA_VERSION, "river": r.river, "weeks": ex["keys"], "first_year": (ex["first"] + timedelta(days=6)).year,
            "last_week_end": ex["last"].isoformat(), "min_weeks": rf.MIN_WEEKS,
            "series": [{"name": s["name"], "color_index": _color(i), "v": s["v"], "n": s["n"]} for i, s in enumerate(ex["series"])],
            "gauges": rf._gauge_rows(r), "notes": list(r.notes), "dropped_segments": list(r.dropped_segments)}


def normal_file(b: rp.Bundle, data: dict) -> dict:
    n = data["normal"]
    return {"schema_version": SCHEMA_VERSION, "baseline": f"{rn.BASELINE[0]}-{rn.BASELINE[1]}", "classes": CLASSES,
            "weeks": [iso(w) for w in n["weeks"]],
            "segments": [{"name": name, "color_index": _color(i), "cls": n["cls"][i], "pct": n["pct"][i]}
                         for i, name in enumerate(n["rows"])],
            "gauges": n["gauges"]}


def drying_file(b: rp.Bundle, data: dict) -> dict:
    dr = data["drying"]
    return {"schema_version": SCHEMA_VERSION, "dry_cfs": rn.DRY_CFS, "normally_dry_share": rn.NORMALLY_DRY, "years": dr["years"],
            "as_of": b.r.as_of, "baseline": f"{rn.BASELINE[0]}-{rn.BASELINE[1]}",
            "segments": [{"name": s["name"], "color_index": _color(i), "dry_days": s["dry"], "days_with_data": s["days"],
                          "median": s["median"]} for i, s in enumerate(dr["segments"])],
            "left_out": [{"segment": seg, "gauges": gs} for seg, gs in b.dry.normally_dry.items() if seg]}


def quality_file(data: dict) -> dict:
    t, s = data["temp"], data["salinity"]
    return {"schema_version": SCHEMA_VERSION, "temperature": {"sites": t["sites"]},
            "salinity": {"segments": s["segments"], "decades": s["decades"], "median_uS_cm": s["median"], "n_days": s["n"]}}


def watershed_file(b: rp.Bundle, data: dict) -> dict:
    segs = []
    for i, w in enumerate(data["watershed"]["segments"]):
        h = w["huc8"]
        cov = (b.coverage or {}).get(h) or {}
        rc_ = w["recent"]
        segs.append({
            "name": w["name"], "color_index": _color(i), "huc8": h, "huc4": w["huc4"],
            "coverage": {"grid_fraction": cov.get("grid_fraction"), "has_precip": cov.get("has_precip"), "note": w["coverage_note"] or None},
            "precipitation": {"months": [iso(x) for x in w["months"]], "total_in": w["precip"], "normal_in": w["precip_normal"],
                              "days_counted": w["month_days"], "complete": w["month_complete"]},
            "daily_precipitation": {"dates": [iso(x) for x in w["daily_dates"]], "inches": w["daily"],
                                    "note": "A PRISM day is the 24 hours ending at 12:00 UTC on its date."},
            "recent": None if not rc_ else {"window_start": iso(rc_["start"]), "window_end": iso(rc_["last"]), "total_in": rc_["total"],
                                            "normal_in": rc_["normal"], "percent_of_normal": rc_["pct_normal"],
                                            "percentile": rc_["percentile"], "wettest_date": iso(rc_["wettest_date"]),
                                            "wettest_in": rc_["wettest_in"]},
            "temperature": {"months": [iso(x) for x in w["tmonths"]], "anomaly_c": w["tanom"]},
            "snow": {"weeks_of_water_year": w["swe_weeks"], "median_2004_2025_in": w["swe_median"],
                     "winters": [{"water_year": y["wy"], "inches": y["values"]} for y in w["swe_winters"]]},
            "drought": {"dates": [iso(x) for x in w["dsci_dates"]], "dsci": w["dsci"]}})
    return {"schema_version": SCHEMA_VERSION, "as_of": b.r.as_of, "segments": segs}


def notes_file(r: rf.RiverReport) -> dict:
    warn = [f for f in r.issues if f.severity == "warn"]
    return {"schema_version": SCHEMA_VERSION,
            "counts": {"removed_values": len(r.removed), "reviewed_notes": len(r.data_notes), "warnings": len(warn),
                       "context": len(r.issues) - len(warn)},
            "removed_values": r.removed, "reviewed_notes": list(r.data_notes),
            "findings": [f.as_dict() for f in r.issues], "gauges": rf._gauge_rows(r), "min_weeks": rf.MIN_WEEKS,
            "method_notes": list(r.notes)}


def summary_file(b: rp.Bundle, data: dict, rows: list[dict], con, gauges: pd.DataFrame, grids: Path, root: Path,
                 background: dict | None, tabs: list[str], generated: str, site_name: str, files: dict) -> tuple[dict, rm.MapParts, rm.Layers]:
    r = b.r
    layers, facts = rm.collect(r.gnis_id, r.segments, b.huc8_of, gauges, grids)
    mp = mapsvg.zoom_map_parts(r.gnis_id, r.river, r.segments, layers, gauges, grids)
    ctx = rc.build(r.river, r.gnis_id, r.segments, b.huc8_of, con, root)
    res = ro.load_research(root, r.slug)
    aq_db = ro.load_acequias(root)
    table = res.get("sources") or {}
    aq_table = aq_db.get("sources") or {}
    src = Sources()
    first = (pd.Timestamp(min(r.seg_all["week_start"])) + pd.Timedelta(days=6)).year
    auto = mapsvg.describe(r.river, facts, r.segments, len(r.elig), first, r.as_of.year)
    background_refs: list[str] = []
    if res.get("summary"):
        summary_items = src.statements(res["summary"], table)
    elif background and background.get("text"):
        summary_items = [{"text": " ".join(str(background["text"]).split()), "sources": []}]
        background_refs = [str(x) for x in background.get("sources") or []]
    else:
        summary_items = []
    culture = src.statements(res.get("culture"), table)
    aqs = ro.acequia_list(r.river, ctx, aq_db)
    acequias = [{"name": a["name"], "where": a["where"] or None, "segment": a["segment"], "sources": src.ids(a["sources"], aq_table)}
                for a in aqs]
    notes = res.get("reservoirs_notes") or []
    reservoirs = []
    for x in ctx.reservoirs:
        word = x["name"].lower().split(" ")[0]
        n = next((y for y in notes if str(y.get("name", "")).lower().split(" ")[0] == word), None)
        reservoirs.append({"name": x["name"], "dam": x.get("dam") if x.get("dam") != x["name"] else None, "year": x.get("year"),
                           "purpose": ro.ROLE.get(x.get("role"), x.get("purpose") or "") or None, "storage_af": x.get("storage_af"),
                           "note": None if not n else {"text": " ".join(str(n["text"]).split()), "sources": src.ids(n.get("sources"), table)}})
    seg_rows = ro.segment_facts(rows, layers, aqs)
    segments = []
    for i, x in enumerate(seg_rows):
        segments.append({"index": i + 1, "name": x["segment"], "color_index": _color(i), "huc8": b.huc8_of.get(x["segment"]),
                         "flow_cfs": _r(x["flow"], 1), "class": x["cls"], "percentile": _r(x["pct"], 1),
                         "last_52_weeks_cfs": [_r(v, 1) for v in x["spark"]],
                         "dry_days_this_year": x["dry"], "dry_days_normal": x["dry_median"],
                         "water_temp_c": _r(x["temp"], 1), "water_temp_site": x["temp_site"],
                         "conductance_uS_cm": None if not x["sc"] else x["sc"][0], "conductance_decade": None if not x["sc"] else x["sc"][1],
                         "precip_3_months_percent_of_normal": _r(x["precip3"], 0), "dsci": x["dsci"], "huc4": x["huc4"],
                         "towns": x["towns"], "acequias": x["acequias"]})
    rated = [x for x in rows if x["cls"]]
    below = [x for x in rated if "below" in x["cls"]]
    last = r.weeks52[-1] + timedelta(days=6)
    desc = rs.description(r.river, rows, tabs, last)
    variables = ["streamflow (discharge), cubic feet per second", "flow percentile against 1991-2020"]
    if "quality" in tabs:
        variables += ["water temperature", "specific conductance"]
    if "watershed" in tabs:
        variables += ["precipitation", "snow-water equivalent", "Drought Severity and Coverage Index"]
    out = {
        "schema_version": SCHEMA_VERSION,
        "river": {"name": r.river, "slug": r.slug, "gnis_id": r.gnis_id},
        "generated": generated, "data_through": last, "tabs": tabs, "files": files,
        "description": {"auto": auto, "background": summary_items, "background_references": background_refs},
        "status": {"segments_rated": len(rated), "segments_below_normal": len(below), "segments": len(rows)},
        "segments": segments,
        "facts": {"length_km": facts.length_km, "drainage_km2": facts.drainage_km2, "flows_into": facts.flows_into,
                  "tributaries": facts.tributaries, "reservoirs": facts.reservoirs, "towns": facts.towns,
                  "gauges": len(r.elig), "first_year": first, "as_of_year": r.as_of.year},
        "culture": culture, "habitat": src.statements(res.get("habitats"), table), "users": src.statements(res.get("users"), table),
        "acequias": {"items": acequias, "map_distance_km": rc.ACEQUIA_KM,
                     "note": "From the State Engineer's acequia map, which covers mainly northern New Mexico, and cited records of acequia governance."},
        "reservoirs": reservoirs,
        "water_use": ctx.water_use or None,
        "irrigation_districts": [{"name": d["name"], "acres": d["acres"]} for d in ctx.districts],
        "water_systems": ctx.water_systems,
        "sources": src.items,
        "map": {"file": "map.svg", "title": mp.title, "description": mp.desc, "view_state": mp.view_state, "view_river": mp.view_river,
                "inset_svg": mp.inset, "scale": mp.scale, "key": [{"index": i + 1, "name": s} for i, s in enumerate(r.segments)]},
        "meta": {"title": f"{r.river}: river conditions in New Mexico", "description": desc, "social_image": "social.png",
                 "site_name": site_name,
                 "dataset": {"name": f"{r.river} streamflow and conditions by watershed segment", "first_year": first,
                             "through": last, "keywords": [r.river, "New Mexico", "streamflow", "discharge", "cfs", "drought",
                                                          "river conditions", "HUC8"],
                             "variables": variables, "bounds": {"west": facts.bounds[0], "south": facts.bounds[1],
                                                                "east": facts.bounds[2], "north": facts.bounds[3]}}},
    }
    return out, mp, layers


# ---------------------------------------------------------------------------- one river
def export_river(con, r: rf.RiverReport, river_name: str, grids: Path, cache: Path, root: Path, d: Path, background: dict | None,
                 generated: str, site_name: str, social: bool = True, csv: bool = True, license_url: str | None = None) -> dict:
    """Write one river's files into d and return its manifest entry. csv=False leaves out the CSV downloads."""
    d.mkdir(parents=True, exist_ok=True)
    b = rp.extend(con, r, river_name, grids, cache)
    if csv:
        if not b.normal.segments.empty:
            b.normal.segments.round({"pct": 1}).to_csv(d / "normal_last_52_weeks_by_segment.csv", index=False)
        b.dry.by_segment.to_csv(d / "drying_by_year.csv", index=False)
        rf.write_csvs(d, r)
        rp.write_precip_csv(d, b)
    rf.write_notes(d / "notes.md", r, generated)
    data = rp.page_data(b)
    rows = rp.overview_rows(b, data)
    show = rp.has_tabs(data)
    tabs = ["overview", "flow"] + (["normal"] if show["normal"] else []) + ["drying"] + (["quality"] if show["quality"] else []) \
        + (["watershed"] if show["watershed"] else []) + ["notes"]
    files = {"summary": "summary.json", "map": "map.svg", "flow": "flow.json", "drying": "drying.json", "notes": "notes.json"}
    if show["normal"]:
        files["normal"] = "normal.json"
    if show["quality"]:
        files["quality"] = "quality.json"
    if show["watershed"]:
        files["watershed"] = "watershed.json"
    csvs = sorted(p.name for p in d.glob("*.csv"))
    files["csv"] = csvs
    files["notes_md"] = "notes.md"
    files["geo"] = ["geo/state-view.geojson", "geo/river-view.geojson", "geo/bounds.json"]
    gauges = rp.gauge_points(con, b)
    summary, mp, layers = summary_file(b, data, rows, con, gauges, grids, root, background, tabs, generated, site_name, files)
    if license_url:
        summary["meta"]["dataset"]["license"] = license_url
    if social:
        ok = social_card_mod.social_card(d / "social.png", river=r.river, site_name=site_name, rows=rows, layers=layers, grids=grids,
                            as_of=r.weeks52[-1] + timedelta(days=6))
        if not ok:
            summary["meta"]["social_image"] = None
    else:
        summary["meta"]["social_image"] = None
    sizes = {"summary.json": dump(d / "summary.json", summary)}
    (d / "map.svg").write_text(mp.svg, encoding="utf-8")
    sizes["map.svg"] = len(mp.svg)
    parts = geo.river_geo(r.gnis_id, r.river, r.segments, layers, gauges, grids)
    for name, obj in parts.items():
        sizes[f"geo/{name}"] = geo.write(d / "geo" / (f"{name}.json" if name == "bounds" else f"{name}.geojson"), obj)
    sizes["flow.json"] = dump(d / "flow.json", flow_file(r))
    sizes["drying.json"] = dump(d / "drying.json", drying_file(b, data))
    sizes["notes.json"] = dump(d / "notes.json", notes_file(r))
    if show["normal"]:
        sizes["normal.json"] = dump(d / "normal.json", normal_file(b, data))
    if show["quality"]:
        sizes["quality.json"] = dump(d / "quality.json", quality_file(data))
    if show["watershed"]:
        sizes["watershed.json"] = dump(d / "watershed.json", watershed_file(b, data))
    with_data = r.seg_all.groupby("segment")["week_start"].agg(["min", "max"])
    first_week, last_week = min(with_data["min"]), max(with_data["max"])
    below = [x for x in rows if x["cls"] in ("much below normal", "below normal")]
    rated = [x for x in rows if x["cls"]]
    return {"slug": r.slug, "name": r.river, "path": f"rivers/{r.slug}/", "segments": len(r.segments), "gauges": len(r.elig),
            "first_year": (pd.Timestamp(first_week) + pd.Timedelta(days=6)).year, "last_week": str(last_week), "tabs": tabs,
            "reporting": bool(len(r.seg52) and max(pd.to_datetime(r.seg52["week_start"])) >= pd.Timestamp(r.weeks52[-4])),
            "last_52_mean_cfs": None if r.seg52.empty else round(float(r.seg52["mean_cfs"].mean()), 1),
            "segments_rated": len(rated), "segments_below_normal": len(below),
            "issues_warn": sum(f.severity == "warn" for f in r.issues), "removed_values": len(r.removed),
            "description": summary["meta"]["description"], "social_image": summary["meta"]["social_image"],
            "bytes": sizes, "generated": datetime.now(UTC).isoformat(timespec="seconds")}
