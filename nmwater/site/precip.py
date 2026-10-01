"""The precipitation part of the site data bundle (schema_version 1; schemas in docs/site-data/v1/).

precipitation/
  index.json            every watershed with recent rain over 7, 30 and 90 days against 1991-2020, coverage, and the rivers
                        whose pages use it; the statewide page is drawn from this
  map.svg               New Mexico with its watersheds that reach into it (clipped to the state), one <path data-huc8> each, with the rating of
                        each window in data-c7 / data-c30 / data-c90; styled by the site's stylesheet
  recent.csv            the same numbers as a table
  <huc8>/precip.json    one watershed: months, the last 90 days, the windows, coverage, rivers
  <huc8>/precip_daily_last_365_days.csv

Source: PRISM (daily from 1981, monthly before), averaged over each HUC8 (nmwater/derived/watershed_precip.py). A PRISM day
ends at 12:00 UTC on its date. Rain is inches of water (rain plus melted snow).
"""

from __future__ import annotations

import html
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ..reports import river_map as rm
from ..derived import river_normal as rn
from ..reports import river_page as rp
from ..derived import river_watershed as rw
from . import SCHEMA_VERSION
from .river import clean

log = logging.getLogger("nmwater.site.precip")
WINDOWS = (7, 30, 90)
NORMAL_YEARS = rw.NORMAL_YEARS
PARTIAL_BELOW = 0.95            # grid_fraction under this: averages over part of the watershed
MAP_WIDTH = 600
NOTE = "A PRISM day is the 24 hours ending at 12:00 UTC on its date (about 5 to 6 AM Mountain), so a day's value is mostly the previous day's rain."


def slug_of(cls: str | None) -> str:
    return (cls or "none").replace(" ", "-")


def window_stats(s: pd.Series, window: int, years: tuple[int, int] = NORMAL_YEARS) -> dict | None:
    """The last `window` days of s (a daily series, PRISM days) against the same calendar window in each year of the normal period."""
    if len(s) < window:
        return None
    last = s.index.max()
    now = s.iloc[-window:]
    total = float(now.sum())
    ref = []
    for y in range(years[0], years[1] + 1):
        end = pd.Timestamp(year=y, month=last.month, day=min(last.day, pd.Timestamp(year=y, month=last.month, day=1).days_in_month))
        w = s[end - pd.Timedelta(days=window - 1):end]
        if len(w) == window:
            ref.append(float(w.sum()))
    if len(ref) < 20:                                    # too few years to call it "normal"
        return {"window_start": last - pd.Timedelta(days=window - 1), "window_end": last, "total_in": total, "normal_in": None,
                "percent_of_normal": None, "percentile": None, "class": None, "wettest_date": now.idxmax(), "wettest_in": float(now.max())}
    ref_a = np.array(ref)
    normal = float(np.median(ref_a))
    pct = rn.percentile_rank(ref_a, total)
    wet = now.idxmax()
    return {"window_start": last - pd.Timedelta(days=window - 1), "window_end": last, "total_in": total, "normal_in": normal,
            "percent_of_normal": (total / normal * 100) if normal > 0 else None, "percentile": pct, "class": rn.classify(pct),
            "wettest_date": wet, "wettest_in": float(now.loc[wet])}


def river_links(rivers_dir: Path) -> dict[str, list[dict]]:
    """HUC8 -> the rivers (slug, name) whose segments sit in it, read from the river summaries already written."""
    out: dict[str, list[dict]] = {}
    for p in sorted(rivers_dir.glob("*/summary.json")):
        s = json.loads(p.read_text())
        for h in {x["huc8"] for x in s["segments"] if x.get("huc8")}:
            out.setdefault(h, []).append({"slug": s["river"]["slug"], "name": s["river"]["name"]})
    return {h: sorted(v, key=lambda r: r["name"]) for h, v in out.items()}


def map_svg(wsheds: pd.DataFrame, stats: dict[str, dict], grids: Path, wbd_dir: Path) -> str:
    """New Mexico's watersheds, clipped to the state, as one SVG. Paths carry the rating of each window as data attributes."""
    nm, _counties, _mains = rm.state_layers(grids)
    p = rm.Proj(nm.bounds, MAP_WIDTH)
    polys = rw.huc8_polygons(wbd_dir, list(wsheds["huc8"]))
    names = dict(zip(wsheds["huc8"], wsheds["name"]))
    body = []
    for r in polys.itertuples():
        g = r.geometry.intersection(nm)
        d = p.path(g, tol_px=0.5)
        if not d:
            continue
        st = stats.get(r.huc8, {})
        attrs = "".join(f' data-c{w}="{slug_of(((st.get(str(w)) or {}).get("class")))}"' for w in WINDOWS)
        name = html.escape(names.get(r.huc8) or r.name, quote=True)
        body.append(f'<path class="ws" data-huc8="{r.huc8}" data-name="{name}"{attrs} d="{d}"><title>{name}</title></path>')
    counties = p.path(rm.county_lines(grids), tol_px=0.6, topology=False)
    outline = p.path(nm.boundary, tol_px=0.5)
    return (f'<svg class="pmap" viewBox="0 0 {p.width:.1f} {p.height:.1f}" style="aspect-ratio:{p.width:.1f}/{p.height:.1f}" role="img" '
            f'aria-labelledby="pm-t pm-d"><title id="pm-t">Map of New Mexico watersheds coloured by recent rain</title>'
            f'<desc id="pm-d">New Mexico divided into its {len(body)} HUC8 watersheds, each coloured by how its recent rain compares with 1991 to 2020. '
            f'The same numbers are in the table beside the map.</desc>'
            f'<path class="p-county" d="{counties}"/><g class="p-ws">{"".join(body)}</g><path class="p-state" d="{outline}"/></svg>')


def export_precip(con, out: Path, rivers_dir: Path, grids: Path, wbd_dir: Path, generated: str, csv: bool = True) -> dict | None:
    """Write <out>/precipitation/ and return the manifest block, or None when the catalog has no watershed precipitation."""
    try:
        con.sql("SELECT 1 FROM watershed_precip LIMIT 1")
    except Exception:
        log.warning("the catalog has no watershed_precip view; no precipitation pages (nmwater watershed-precip, then rebuild the catalog)")
        return None
    ws = con.sql("SELECT huc8, name, states, area_km2, nm_fraction, grid_fraction FROM watersheds WHERE has_precip ORDER BY huc8").df()
    ws["grid_fraction"] = ws["grid_fraction"].clip(upper=1.0)         # the cell raster can overshoot by a hair (1.0001)
    codes = list(ws["huc8"])
    series, cov = rw.precip_summary(con, codes) or ({}, {})
    daily = con.sql("SELECT huc8, date::DATE AS date, precip_in FROM watershed_precip WHERE interval = 'daily' AND date >= DATE '1990-01-01' "
                    "AND huc8 IN (" + ",".join(f"'{c}'" for c in codes) + ") ORDER BY huc8, date").df()
    daily["date"] = pd.to_datetime(daily["date"])
    links = river_links(rivers_dir)
    base = out / "precipitation"
    base.mkdir(parents=True, exist_ok=True)

    rows, stats_by = [], {}
    for h, g in daily.groupby("huc8"):
        s = g.set_index("date")["precip_in"]
        stats = {str(w): window_stats(s, w) for w in WINDOWS}
        stats_by[h] = stats
    as_of = daily["date"].max()
    csv_rows = []
    for w_ in ws.itertuples():
        h = w_.huc8
        if h not in series:
            continue
        sr, stats = series[h], stats_by.get(h, {})
        d = base / h
        d.mkdir(exist_ok=True)
        c = cov.get(h) or {"grid_fraction": float(w_.grid_fraction), "has_precip": True, "states": str(w_.states)}
        note = rp.coverage_note(c) or None
        one = {"schema_version": SCHEMA_VERSION, "huc8": h, "name": w_.name, "states": str(w_.states).split(","), "area_km2": float(w_.area_km2),
               "nm_fraction": float(w_.nm_fraction), "grid_fraction": float(w_.grid_fraction), "partial": bool(w_.grid_fraction < PARTIAL_BELOW),
               "coverage_note": note, "as_of": as_of, "baseline": f"{NORMAL_YEARS[0]}-{NORMAL_YEARS[1]}", "stats": stats,
               "months": [m for m in sr["months"]], "total_in": sr["month_total"], "normal_in": sr["month_normal"], "days_counted": sr["month_days"],
               "complete": sr["month_complete"], "daily": {"dates": sr["daily_dates"], "inches": sr["daily"], "note": NOTE},
               "rivers": links.get(h, [])}
        (d / "precip.json").write_text(json.dumps(clean(one), separators=(",", ":"), allow_nan=False), encoding="utf-8")
        if csv:
            ds = pd.DataFrame({"date": sr["year_dates"], "precip_in": np.round(sr["year_daily"], 4)})
            ds["period_start_utc"] = (ds["date"] - pd.Timedelta(days=1)).dt.strftime("%Y-%m-%dT12:00:00Z")
            ds["period_end_utc"] = ds["date"].dt.strftime("%Y-%m-%dT12:00:00Z")
            ds.assign(date=ds["date"].dt.strftime("%Y-%m-%d")).to_csv(d / "precip_daily_last_365_days.csv", index=False)
        rows.append({"huc8": h, "name": w_.name, "states": str(w_.states).split(","), "area_km2": float(w_.area_km2), "nm_fraction": float(w_.nm_fraction),
                     "grid_fraction": float(w_.grid_fraction), "partial": bool(w_.grid_fraction < PARTIAL_BELOW), "coverage_note": note,
                     "stats": stats, "rivers": links.get(h, [])})
        for w in WINDOWS:
            st = stats.get(str(w))
            if st:
                csv_rows.append({"huc8": h, "name": w_.name, "window_days": w, "start": st["window_start"].date(), "end": st["window_end"].date(),
                                 "total_in": round(st["total_in"], 3), "normal_in": None if st["normal_in"] is None else round(st["normal_in"], 3),
                                 "percent_of_normal": None if st["percent_of_normal"] is None else round(st["percent_of_normal"]),
                                 "percentile": None if st["percentile"] is None else round(st["percentile"]), "rating": st["class"] or "",
                                 "wettest_date": st["wettest_date"].date(), "wettest_in": round(st["wettest_in"], 3), "grid_fraction": round(float(w_.grid_fraction), 3)})
    (base / "map.svg").write_text(map_svg(ws[ws["huc8"].isin([r["huc8"] for r in rows])], {r["huc8"]: r["stats"] for r in rows}, grids, wbd_dir), encoding="utf-8")
    files = {"index": "index.json", "map": "map.svg", "csv": []}
    if csv:
        pd.DataFrame(csv_rows).to_csv(base / "recent.csv", index=False)
        files["csv"] = ["recent.csv"]
    index = {"schema_version": SCHEMA_VERSION, "generated": generated, "as_of": as_of, "windows": list(WINDOWS), "baseline": f"{NORMAL_YEARS[0]}-{NORMAL_YEARS[1]}",
             "classes": [label for _, label in rn.CLASSES], "note": NOTE, "watersheds": rows, "files": files}
    (base / "index.json").write_text(json.dumps(clean(index), separators=(",", ":"), allow_nan=False), encoding="utf-8")
    log.info("precipitation: %d watersheds, data through %s", len(rows), as_of.date())
    return {"path": "precipitation/", "watersheds": len(rows), "as_of": as_of, "windows": list(WINDOWS), "files": files}
