"""The reservoir part of the site data bundle (schema_version 2; schemas in docs/site-data/v2/).

reservoirs/
  index.json                       every reservoir: where it stands now, how that compares with the same date in other years, and its rivers
  <slug>/fill.json                 one reservoir: status, the last 365 days, the normal range for each date, the annual history, capacity
                                   eras and pools, provenance, the registry's hand-written guidance, and the dam's facts (dam_facts)
  <slug>/annual_fill.csv           the annual table (one row per year)
  <slug>/daily_last_365_days.csv   storage, capacity in force and percent for each of the last 365 days
  annual_fill_all.csv              every reservoir-year

The numbers are nmwater/derived/reservoirs.py's (capacity in force each day, per the operator's pools). What a number *means* depends on the
reservoir's role and how well its capacity is known, so each reservoir carries a `measure`:
  percent_full         storage / the primary pool (storage reservoirs, diversion forebays)
  conservation_pool    storage / the conservation pool; above 100% is flood water being held (flood-control dams)
  flood_pool_use       storage / the flood pool, normally near zero (dry dams: empty by design, a flood is the event)
  storage_only         acre-feet only: capacity is the owner-reported normal storage, which is not sediment-corrected (no percent, no rating)
A normal for the date (median and 10/25/75/90th percentiles of percent, by day of year, +/-3 days) is built from years whose capacity table is
`published_pool`, `current_table`, `high` or `medium` inside 1991-2020, and only when at least MIN_NORMAL_YEARS of them exist; otherwise there is none and
no rating. The rating uses the same percentile classes as flow and rain, with one floor: a reading within MATERIAL_POINTS percentage points of the
median is "normal" whatever its percentile (a dam held near one level, like Cochiti's, has a tight band in which a 3-point dip would otherwise rank as
"much below normal"). `class_floored` says when the floor changed the class.
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..derived import reservoirs as rv
from ..derived import river_normal as rn
from . import SCHEMA_VERSION
from .river import clean

log = logging.getLogger("nmwater.site.reservoirs")
BASELINE = (1991, 2020)
MIN_NORMAL_YEARS = 15
TRUSTED = {"published_pool", "current_table", "high", "medium"}
MATERIAL_POINTS = 5.0                # percentage points of the pool below which a difference from the median is not worth a rating
WINDOW_DAYS = 3                      # the normal for a date pools +/-3 days of every year
RECENT_DAYS = 365
STALE_DAYS = 14                      # a reservoir with no reading this recent is flagged
SOURCES = {"usbr_hydrodata": "Bureau of Reclamation HydroData", "usace_cwms": "U.S. Army Corps of Engineers CWMS", "usgs": "U.S. Geological Survey NWIS"}
ACRONYMS = {"USACE", "DOI", "BLM", "BIA", "NM", "USBR", "US", "ISC"}
SMALL = {"of", "and", "the", "for", "de"}


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


COLUMN_WORDS = {"days_flood_storage": "days in flood storage", "peak_flood_pct": "peak flood-pool percent"}


def public_text(s: str) -> str:
    """Registry guidance is written for the notes files next to the CSVs; for the public page, name columns in words and drop pointers to
    files and scripts. Markdown **bold** is kept (the website renders it)."""
    s = " ".join(str(s).split())
    for col, words in COLUMN_WORDS.items():
        s = s.replace(f"`{col}`", words).replace(col, words)
    s = re.sub(r"\s*See [\w./-]+\.md[^.]*\.", "", s)
    s = s.replace("The flood-pool columns carry the hydrological signal.", "The days spent in flood storage carry the hydrological signal.")
    return s.replace("`", "")


def public_list(items) -> list[str]:
    return [public_text(x) for x in items or [] if "scripts/" not in str(x) and ".md" not in str(x)]


def tidy_owner(v) -> str | None:
    if not v or (isinstance(v, float) and math.isnan(v)):
        return None
    words = []
    for i, w in enumerate(str(v).split()):
        if w.upper() in ACRONYMS:
            words.append(w.upper())
        elif w.lower() in SMALL and i:
            words.append(w.lower())
        else:
            words.append("-".join(p.capitalize() for p in w.split("-")))
    return " ".join(words)


def measure_of(role: str, confidence_last: str | None, overridden: bool) -> str:
    if overridden or confidence_last in ("owner_reported", "none", None):
        return "storage_only"
    if role == "dry_flood":
        return "flood_pool_use"
    if role == "flood_control":
        return "conservation_pool"
    return "percent_full"


def doy366(dates: pd.Series) -> np.ndarray:
    """Day of year on a leap-year calendar, so Feb 29 has its own day and Mar 1 is the same day every year."""
    return np.array([pd.Timestamp(2000, d.month, d.day).dayofyear for d in dates])


def normal_for_date(st: pd.DataFrame) -> dict | None:
    """Percentiles of percent-of-pool by day of year from trusted years in the baseline, or None when there are too few years."""
    ok = st[st.confidence.isin(TRUSTED) & st.date.dt.year.between(*BASELINE) & st.pct.notna()]
    years = sorted(ok.date.dt.year.unique())
    if len(years) < MIN_NORMAL_YEARS:
        return None
    M = np.full((len(years), 366), np.nan)
    ix = {y: i for i, y in enumerate(years)}
    for y, d, v in zip(ok.date.dt.year, doy366(ok.date), ok.pct):
        M[ix[y], d - 1] = v
    qs = (10, 25, 50, 75, 90)
    out = {f"p{q}": np.full(366, np.nan) for q in qs}
    samples: list[np.ndarray] = []
    for d in range(366):
        cols = [(d + k) % 366 for k in range(-WINDOW_DAYS, WINDOW_DAYS + 1)]
        w = M[:, cols]
        s = w[~np.isnan(w)]
        samples.append(s)
        if (~np.isnan(w)).any(axis=1).sum() >= MIN_NORMAL_YEARS:      # years with at least one value in the window
            for q in qs:
                out[f"p{q}"][d] = np.percentile(s, q)
        else:
            samples[-1] = np.array([])
    return {"years": [int(y) for y in years], "basis": f"{len(years)} years of {BASELINE[0]}-{BASELINE[1]} whose capacity figure is published, current-table, high- or medium-confidence", **out, "_samples": samples}


def dam_facts(nid_row: dict, cfg: dict) -> dict:
    def nv(c):
        v = nid_row.get(c)
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else v

    def num(c):
        try:
            return float(nv(c)) if nv(c) is not None else None
        except ValueError:
            return None
    return {"nid_id": cfg.get("nid_id"), "owner": tidy_owner(nv("Owner Names")), "owner_type": nv("Primary Owner Type"),
            "operated_with": nv("Federal Agency Involvement Operation"), "types": [x for x in str(nv("Dam Types") or "").split(";") if x],
            "purposes": [x for x in str(nv("Purposes") or "").split(";") if x], "year_completed": nv("Year Completed"),
            "height_ft": num("NID Height (Ft)"), "drainage_sq_mi": num("Drainage Area (Sq Miles)"), "hazard": nv("Hazard Potential Classification"),
            "nid_max_storage_af": num("NID Storage (Acre-Ft)"), "nid_normal_storage_af": num("Normal Storage (Acre-Ft)")}


def annual_arrays(a: pd.DataFrame) -> dict:
    cols = ["n_days", "capacity_confidence", "full_pool_af", "peak_af", "mean_af", "low_af", "peak_pct", "mean_pct", "low_pct", "peak_date",
            "flood_pool_af", "peak_flood_pct", "days_flood_storage", "inflow_af", "release_af", "peak_inflow_cfs", "peak_release_cfs"]
    out = {"years": [int(y) for y in a["year"]]}
    for c in cols:
        out[c] = [None if (isinstance(v, float) and math.isnan(v)) else v for v in (a[c].tolist() if c in a else [None] * len(a))]
    return out


def export_one(con, key: str, cfg: dict, nid: dict, out: Path, rivers: dict[str, str], csv: bool) -> tuple[dict, dict] | None:
    r = rv.build(con, key, cfg)
    st = r["stor"].copy()
    if st.empty:
        log.warning("%s: no storage record; skipped", key)
        return None
    st["date"] = pd.to_datetime(st["date"])
    slug = cfg["slug"]
    role = cfg["role"]
    overridden = bool(cfg.get("capacity_basis_override"))
    last = st.iloc[-1]
    measure = measure_of(role, last.get("confidence"), overridden)
    st["pct"] = np.where(st["primary_af"] > 0, 100 * st["stor"] / st["primary_af"], np.nan) if measure != "storage_only" else np.nan
    normal = normal_for_date(st) if measure in ("percent_full", "conservation_pool") else None

    latest = st.iloc[-1]
    d_now = int(doy366(pd.Series([latest.date]))[0])
    pct_now = None if pd.isna(latest.pct) else float(latest.pct)
    percentile = klass = None
    floored = False
    if normal is not None and pct_now is not None:
        sample = normal["_samples"][d_now - 1]
        if len(sample):
            percentile = rn.percentile_rank(np.asarray(sample), pct_now)
            klass = rn.classify(percentile)
            if klass != "normal" and abs(pct_now - float(normal["p50"][d_now - 1])) < MATERIAL_POINTS:
                klass, floored = "normal", True

    def at(delta_days: int):
        row = st[st.date <= latest.date - pd.Timedelta(days=delta_days)]
        return None if row.empty else row.iloc[-1]
    m30, m365 = at(30), at(365)
    this_year = st[st.date.dt.year == latest.date.year]
    status = {"latest_date": latest.date, "storage_af": float(latest.stor), "capacity_af": None if pd.isna(latest.primary_af) else float(latest.primary_af),
              "percent": pct_now, "percentile": percentile, "class": klass, "class_floored": floored, "normal_percent": None if normal is None else float(normal["p50"][d_now - 1]),
              "storage_30_days_ago_af": None if m30 is None else float(m30.stor), "percent_30_days_ago": None if m30 is None or pd.isna(m30.pct) else float(m30.pct),
              "storage_year_ago_af": None if m365 is None else float(m365.stor), "percent_year_ago": None if m365 is None or pd.isna(m365.pct) else float(m365.pct),
              "capacity_confidence": latest.get("confidence"), "days_in_flood_storage_this_year": int(this_year.flood_day.sum()) if role in ("flood_control", "dry_flood") else None,
              "above_conservation_pool_now": bool(latest.flood_day) if role == "flood_control" else None,
              "last_flood_storage_date": (lambda x: None if x.empty else x.date.max())(st[st.flood_day]) if role in ("flood_control", "dry_flood") else None}

    recent = st[st.date > latest.date - pd.Timedelta(days=RECENT_DAYS)].copy()
    rd = doy366(recent.date)
    fl = r["flows"]

    def series(name):
        f = fl.get(name)
        if f is None or not len(f):
            return [None] * len(recent)
        m = dict(zip(f.date, f.value))
        return [None if recent_d not in m else float(m[recent_d]) for recent_d in recent.date]
    daily = {"dates": list(recent.date), "storage_af": [float(x) for x in recent.stor], "capacity_af": [None if pd.isna(x) else float(x) for x in recent.primary_af],
             "percent": [None if pd.isna(x) else float(x) for x in recent.pct], "confidence": list(recent.confidence),
             "inflow_cfs": series("inflow"), "release_cfs": series("release")}
    if normal is not None:
        for q in (10, 25, 50, 75, 90):
            daily[f"normal_p{q}"] = [None if np.isnan(normal[f"p{q}"][d - 1]) else float(normal[f"p{q}"][d - 1]) for d in rd]

    pn = r["primary"]["pool"] if r["primary"] else None
    eras = []
    if len(r["eras"]) and pn:
        for e in r["eras"].itertuples():
            eras.append({"label": e.label, "first_date": e.first_date, "last_date": e.last_date, "capacity_af": getattr(e, f"{pn}_af"),
                         "confidence": getattr(e, f"{pn}_confidence"), "on_current_table": bool(e.on_current_table)})
    pools = [{"pool": p["pool"], "elev_ft": p["elev_ft"], "capacity_current_af": p.get("capacity_current_af"), "source": p["source"], "comment": p.get("comment")}
             for p in r["pools"]]
    n_days = st.groupby("site_uid").size().sort_values(ascending=False)
    used = [{"site": s, "agency": SOURCES.get(s.split(":")[0], s.split(":")[0]), "days": int(n)} for s, n in n_days.items()]
    rm = r["rating"]
    valid = r["valid"]
    nid_row = nid.get(cfg.get("nid_id"), {})
    fill = {
        "schema_version": SCHEMA_VERSION, "slug": slug, "key": key, "name": cfg["name"], "dam": cfg.get("dam"), "river": cfg.get("river"), "basin": cfg.get("basin"),
        "role": role, "measure": measure, "river_slugs": cfg.get("river_slugs") or [], "rivers": [{"slug": s, "name": rivers[s]} for s in (cfg.get("river_slugs") or []) if s in rivers],
        "record": {"first_date": st.date.min(), "last_date": latest.date, "years": int(st.date.dt.year.nunique())},
        "status": status,
        "daily": daily,
        "normal": None if normal is None else {"basis": normal["basis"], "years": normal["years"], "baseline": f"{BASELINE[0]}-{BASELINE[1]}", "window_days": WINDOW_DAYS},
        "annual": annual_arrays(r["annual"]),
        "capacity": {"basis": "owner_reported_normal_storage" if overridden else "operator_table", "primary_pool": pn, "pools": pools, "eras": eras,
                     "override": None if not overridden else {"af": float(cfg["capacity_basis_override"]["af"]), "source": cfg["capacity_basis_override"]["source"]},
                     "table": None if not rm else {"agency": rm.get("agency"), "effective_date": rm.get("effective_date"), "datum": rm.get("datum"), "description": rm.get("description")},
                     "validation": valid or None},
        "provenance": {"series": used, "credits": [{"name": SOURCES[s], "url": rv.SOURCE_CITES[s][1]} for s in rv.SOURCE_CITES if s in {u["site"].split(":")[0] for u in used}]},
        "guidance": {"summary": public_text(cfg.get("summary", "")), "uses": public_list(cfg.get("uses")), "cautions": public_list(cfg.get("cautions"))},
        "dam_facts": dam_facts(nid_row, cfg),
        "files": {"csv": ["annual_fill.csv", "daily_last_365_days.csv"] if csv else []},
    }
    d = out / "reservoirs" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "fill.json").write_text(json.dumps(clean(fill), separators=(",", ":"), allow_nan=False), encoding="utf-8")
    if csv:
        r["annual"].to_csv(d / "annual_fill.csv", index=False)
        pd.DataFrame({"date": recent.date.dt.strftime("%Y-%m-%d"), "storage_af": recent.stor.round(1), "capacity_af": recent.primary_af.round(1),
                      "percent": recent.pct.round(2), "capacity_confidence": recent.confidence}).to_csv(d / "daily_last_365_days.csv", index=False)
    row = {"slug": slug, "key": key, "name": cfg["name"], "dam": cfg.get("dam"), "basin": cfg.get("basin"), "role": role, "measure": measure,
           "rivers": fill["rivers"], "latest_date": latest.date, "storage_af": status["storage_af"], "capacity_af": status["capacity_af"], "percent": pct_now,
           "percentile": percentile, "class": klass, "class_floored": floored, "normal_percent": status["normal_percent"], "storage_30_days_ago_af": status["storage_30_days_ago_af"],
           "percent_30_days_ago": status["percent_30_days_ago"], "capacity_confidence": status["capacity_confidence"],
           "days_in_flood_storage_this_year": status["days_in_flood_storage_this_year"], "above_conservation_pool_now": status["above_conservation_pool_now"],
           "has_normal": normal is not None, "first_year": int(st.date.dt.year.min())}
    return row, r["annual"]


def export_reservoirs(con, out: Path, rivers_dir: Path, generated: str, csv: bool = True) -> dict | None:
    """Write <out>/reservoirs/ and return the manifest block, or None when the registry or catalog is not there."""
    try:
        reg = rv.load_registry()
        con.sql("SELECT 1 FROM reservoir_ratings LIMIT 1")
    except Exception as e:
        log.warning("no reservoir data (%s); no reservoir pages", e)
        return None
    nid = rv.load_nid()
    rivers = {}
    for p in sorted(rivers_dir.glob("*/summary.json")):
        s = json.loads(p.read_text())
        rivers[s["river"]["slug"]] = s["river"]["name"]
    for k, cfg in reg.items():
        for s in cfg.get("river_slugs") or []:
            if s not in rivers:
                log.warning("%s: river_slugs names %s, which is not in this bundle", k, s)
    rows, annual = [], []
    (out / "reservoirs").mkdir(parents=True, exist_ok=True)
    for key, cfg in reg.items():
        try:
            res = export_one(con, key, cfg, nid, out, rivers, csv)
        except Exception as e:                                   # one bad reservoir must not stop the rest
            log.exception("%s failed: %s", key, e)
            continue
        if res:
            rows.append(res[0])
            annual.append(res[1])
    if not rows:
        return None
    as_of = max(r["latest_date"] for r in rows)
    for r in rows:
        r["stale"] = bool((as_of - r["latest_date"]).days > STALE_DAYS)
    rows.sort(key=lambda r: r["name"])
    index = {"schema_version": SCHEMA_VERSION, "generated": generated, "as_of": as_of, "baseline": f"{BASELINE[0]}-{BASELINE[1]}", "min_normal_years": MIN_NORMAL_YEARS,
             "classes": [label for _, label in rn.CLASSES], "reservoirs": rows,
             "files": {"index": "index.json", "csv": ["annual_fill_all.csv"] if csv else []}}
    (out / "reservoirs" / "index.json").write_text(json.dumps(clean(index), separators=(",", ":"), allow_nan=False), encoding="utf-8")
    if csv:
        pd.concat(annual, ignore_index=True).to_csv(out / "reservoirs" / "annual_fill_all.csv", index=False)
    log.info("reservoirs: %d, latest data %s", len(rows), as_of.date())
    return {"path": "reservoirs/", "reservoirs": len(rows), "as_of": as_of, "files": index["files"]}
