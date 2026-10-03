"""The statewide part of the site data bundle (schema_version 2; schemas in docs/site-data/v2/).

statewide/
  index.json         one entry per metric: its title, unit, as-of date, headline figures and the file holding its history
  reservoirs.json    acre-feet held and percent full of groups of reservoirs, with history, a normal for the date, window changes, members and exclusions
  drought.json       the U.S. Drought Monitor's state series: percent of the state in D0-D4 and the DSCI, weekly since 2000, with changes and a percentile for the time of year
  temperature.json   PRISM statewide mean air temperature: windows against 1991-2020, the last 365 days, monthly since 1895, annual, and a cross-check against nClimGrid
  snowpack.json      SNODAS statewide snow water equivalent: now (or out of season), the same dates in earlier weeks, the last season's peak, the season so far against other years,
                     the peak of every season since 2004, and a cross-check against the SNOTEL stations
  (flow files follow in a later phase)
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
from ..derived import statewide_snow as sns
from ..derived import statewide_temperature as stt
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
    entry = {"id": "reservoirs", "title": "Reservoir storage", "file": "reservoirs.json", "as_of": pd.Timestamp(g["as_of"]).date().isoformat(), "unit": "acre-feet",
             "headline": {"storage_af": groups["all"]["storage_af"], "members": groups["all"]["members"], "supply_percent": g["percent"], "supply_storage_af": g["storage_af"],
                          "supply_capacity_af": g["capacity_af"], "supply_members": g["members"], "class": g["class"], "percentile": g["percentile"], "normal_storage_af": g["normal_storage_af"]},
             "sources": ["usbr_hydrodata", "usace_cwms", "usgs"]}
    return out, entry


def drought_file(con, generated: str, csv_dir: Path | None) -> tuple[dict, dict] | None:
    """(statewide/drought.json content, the index entry), or None when the catalog has no statewide drought series."""
    w = sw.drought_weekly(con)
    now = sw.drought_summary(w)
    if now is None:
        return None
    hist = {"dates": [d.date().isoformat() for d in w.index], "dsci": [float(v) for v in w["dsci"]], **{c: [float(v) for v in w[c]] for c in ("d0", "d1", "d2", "d3", "d4")}}
    out = {"schema_version": SCHEMA_VERSION, "id": "drought", "generated": generated, "as_of": now["as_of"], "site": sw.DROUGHT_SITE, "latest": now, "history": hist,
           "definitions": {"categories": "D0 abnormally dry, D1 moderate, D2 severe, D3 extreme, D4 exceptional drought. Each percent is the share of the state in that category or a worse one, so D0 is at least D1 and so on.",
                           "dsci": "Drought Severity and Coverage Index: the five cumulative percents added up, 0 (no part of the state abnormally dry) to 500 (all of it in exceptional drought).",
                           "percentile": f"Percent of the weeks within {sw.DROUGHT_PLUS_MINUS_DAYS} days of this time of year in {now['baseline']} (excluding this year) with a lower DSCI. The record is short, so this is not a 30-year normal."},
           "method": "Weekly statewide values published by the U.S. Drought Monitor (valid Tuesdays), stored unchanged; nothing is recomputed except the changes and the percentile."}
    if csv_dir is not None:
        pd.DataFrame({"valid_date": hist["dates"], "dsci": hist["dsci"], **{f"{c}_percent": hist[c] for c in ("d0", "d1", "d2", "d3", "d4")}}).to_csv(csv_dir / "drought_weekly.csv", index=False)
    entry = {"id": "drought", "title": "Drought", "file": "drought.json", "as_of": now["as_of"], "unit": "DSCI (0-500) and percent of the state",
             "headline": {"dsci": now["dsci"], "d0": now["d0"], "d1": now["d1"], "d2": now["d2"], "d3": now["d3"], "d4": now["d4"], "worst_category": now["worst_category"],
                          "percentile": now["percentile"]},
             "sources": ["usdm"]}
    return out, entry


def temperature_file(grids: Path, generated: str, csv_dir: Path | None) -> tuple[dict, dict] | None:
    """(statewide/temperature.json content, the index entry), or None when there are no PRISM temperature grids."""
    if not any((grids / "prism").glob("tmean_daily_*.nc")):
        return None
    poly = stt.nm_polygon(grids)
    daily, monthly, cover = stt.series(grids, poly)
    windows = [w for d in stt.WINDOWS if (w := stt.window_stats(daily, d))]
    if not windows:
        return None
    m = stt.monthly_anomaly(monthly)
    a = stt.annual_anomaly(m)
    rec = stt.recent_daily(daily)
    last_m = m.iloc[-1]
    iso = lambda ix: [d.date().isoformat() for d in ix]
    num = lambda v: [None if pd.isna(x) else round(float(x), 3) for x in v]
    asof = daily.index[-1].date().isoformat()
    out = {"schema_version": SCHEMA_VERSION, "id": "temperature", "generated": generated, "as_of": asof, "baseline": f"{stt.BASELINE[0]}-{stt.BASELINE[1]}",
           "coverage": round(cover, 4), "unit": "degC",
           "latest": {"windows": windows, "month": {"month": m.index[-1].date().isoformat(), "tmean_c": float(last_m["tmean_c"]), "normal_c": float(last_m["normal_c"]), "anomaly_c": float(last_m["anomaly_c"])}},
           "history": {"recent_daily": {"dates": iso(rec.index), "tmean_c": num(rec["tmean_c"]), "normal_c": num(rec["normal_c"])},
                       "monthly": {"dates": iso(m.index), "tmean_c": num(m["tmean_c"]), "normal_c": num(m["normal_c"]), "anomaly_c": num(m["anomaly_c"])},
                       "annual": {"years": [int(y) for y in a.index], "tmean_c": num(a["tmean_c"]), "anomaly_c": num(a["anomaly_c"])}},
           "crosscheck": stt.crosscheck(grids, poly, monthly),
           "definitions": {"windows": "Mean temperature over the last N days, against the mean of the same calendar days in each year of the baseline; the anomaly is the difference from that mean, the percentile ranks this window among the baseline years.",
                           "monthly": "Each month against the mean of the same calendar month in the baseline years. A year's anomaly is the mean of its twelve monthly anomalies; only complete years are listed.",
                           "area": "Every 4 km cell is weighted by the share of it inside New Mexico and by the cosine of its latitude."},
           "method": "PRISM Climate Group AN81 4 km mean temperature grids (a model estimate that interpolates station data; the newest days are provisional and PRISM revises about the latest six months), averaged over the state polygon."}
    if csv_dir is not None:
        pd.DataFrame({"month": iso(m.index), "tmean_c": num(m["tmean_c"]), "normal_1991_2020_c": num(m["normal_c"]), "anomaly_c": num(m["anomaly_c"])}).to_csv(csv_dir / "temperature_monthly.csv", index=False)
    head = {"as_of": asof, **{f"anomaly_{w['days']}_c": w["anomaly_c"] for w in windows}, **{f"class_{w['days']}": w["class"] for w in windows}}
    entry = {"id": "temperature", "title": "Air temperature", "file": "temperature.json", "as_of": asof, "unit": "degrees Celsius", "headline": head, "sources": ["prism"]}
    return out, entry


def snowpack_file(grids: Path, con, cache: Path, generated: str, csv_dir: Path | None) -> tuple[dict, dict] | None:
    """(statewide/snowpack.json content, the index entry), or None when there are no SNODAS grids."""
    if not any((grids / "snodas").glob("snodas_*.nc")):
        return None
    poly = stt.nm_polygon(grids)
    df = sns.daily(grids, poly, cache)
    df = df.astype({c: float for c in ("swe_in", "volume_af", "cover_pct", "valid_pct")})
    s = sns.summarise(df)
    if s is None:
        return None
    snotel = sns.snotel_percent_of_median(con, pd.DatetimeIndex(df["date"])) if con is not None else pd.Series(dtype=float)
    now = s["now"]
    out = {"schema_version": SCHEMA_VERSION, "id": "snowpack", "generated": generated, "as_of": s["as_of"], "baseline": s["baseline"] + " water years", "valid_pct": round(s["valid_pct"], 3),
           "min_median_in": s["min_median_in"], "latest": {"now": now, "compare": s["compare"], "last_season": s["last_season"]}, "history": s["history"],
           "crosscheck": sns.crosscheck(df, snotel) if len(snotel) else None,
           "definitions": {"swe": "Snow water equivalent: the depth of water the snow would make if melted, averaged over the whole state (inches) and summed over the state (acre-feet).",
                           "median": f"For a date, the median over the water years {s['baseline']} (October to September, named for the year they end) of the days within {sns.WINDOW_DAYS} days of the same calendar day; the season shown is left out.",
                           "percent_of_median": f"Mean SWE over that median. Stated only when the median is at least {sns.MIN_MEDIAN_IN} inch; otherwise the state is out of snow season and no percent is given.",
                           "cover": f"Share of the state with at least {int(sns.COVER_MM)} mm of SWE."},
           "method": "NOAA NOHRSC SNODAS 1 km daily snow model (assimilates satellite, airborne and ground observations: an estimate, not a measurement) averaged over the state polygon, each cell by its true area."}
    if csv_dir is not None:
        d = df[["date", "swe_in", "volume_af", "cover_pct"]].copy()
        d["date"] = d["date"].dt.date.astype(str)
        d.round({"swe_in": 5, "volume_af": 1, "cover_pct": 3}).to_csv(csv_dir / "snowpack_daily.csv", index=False)
    head = {"as_of": s["as_of"], "in_season": now["in_season"], "swe_in": now["swe_in"], "volume_af": now["volume_af"], "percent_of_median": now["percent_of_median"],
            "last_season_peak_in": s["last_season"]["peak_swe_in"], "last_season_percent_of_median_peak": s["last_season"]["percent_of_median_peak"]}
    entry = {"id": "snowpack", "title": "Snowpack", "file": "snowpack.json", "as_of": s["as_of"], "unit": "inches of snow water equivalent", "headline": head, "sources": ["snodas"]}
    return out, entry


def export_statewide(out: Path, frames: dict[str, pd.DataFrame], reg: dict, generated: str, csv: bool = True, con=None, grids: Path | None = None, cache: Path | None = None) -> dict | None:
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
    dr = drought_file(con, generated, base if csv else None) if con is not None else None
    if dr:
        content, entry = dr
        (base / "drought.json").write_text(json.dumps(clean(content), separators=(",", ":"), allow_nan=False), encoding="utf-8")
        metrics.append(entry)
        if csv:
            files["csv"].append("drought_weekly.csv")
    tp = temperature_file(grids, generated, base if csv else None) if grids is not None else None
    if tp:
        content, entry = tp
        (base / "temperature.json").write_text(json.dumps(clean(content), separators=(",", ":"), allow_nan=False), encoding="utf-8")
        metrics.append(entry)
        if csv:
            files["csv"].append("temperature_monthly.csv")
    sp = snowpack_file(grids, con, cache, generated, base if csv else None) if grids is not None and cache is not None else None
    if sp:
        content, entry = sp
        (base / "snowpack.json").write_text(json.dumps(clean(content), separators=(",", ":"), allow_nan=False), encoding="utf-8")
        metrics.append(entry)
        if csv:
            files["csv"].append("snowpack_daily.csv")
    if not metrics:
        return None
    as_of = max(pd.Timestamp(m["as_of"]).date().isoformat() for m in metrics)
    index = {"schema_version": SCHEMA_VERSION, "generated": generated, "as_of": as_of, "metrics": metrics, "files": {"index": "index.json", **files}}
    (base / "index.json").write_text(json.dumps(clean(index), separators=(",", ":"), allow_nan=False), encoding="utf-8")
    log.info("statewide: %s", ", ".join(m["id"] for m in metrics))
    return {"path": "statewide/", "metrics": [m["id"] for m in metrics], "as_of": as_of, "files": index["files"]}
