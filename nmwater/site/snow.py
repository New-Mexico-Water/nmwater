"""The snow part of the site data bundle (schema_version 2; schemas in docs/site-data/v2/).

snow/
  index.json            every watershed and every mountain range with water in the snow, snow depth and new snow against other winters, and every SNOTEL station's latest readings;
                        the Snow page and its map are drawn from this
  ranges.geojson        the mountain ranges, drawn where snow is usual (the watersheds' shapes are precipitation/watersheds.geojson: they are the same HUC8 polygons)
  recent.csv            the same numbers as a table
  <unit>/snow.json      one watershed (by HUC8) or one range: this winter and last winter day by day, the usual range, the ratings, the stations in it

Sources: NOAA SNODAS (water in the snow and depth, a model; averaged over each unit by nmwater/derived/watershed_snow.py) and USDA NRCS SNOTEL stations (readings, and a new-snow
estimate from depth and water rises: nmwater/derived/snotel_snow.py). Ratings compare a value with the same date in water years 2004-2025 (nmwater/derived/snow_normal.py).
A station's day D is NRCS's daily value dated D, which the archive shows is the reading at local midnight that ENDS day D (07:00 UTC on D+1, all year); until it arrives, the hourly reading at 07:00 UTC on D+1. New snow dated D is the rise over the local day D. Days run 1 October to 30 September with Feb 29 left out: day 0 is 1 October.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from ..derived import river_normal as rn
from ..derived import snotel_snow as ss
from ..derived import snow_normal as sn
from . import SCHEMA_VERSION, geo
from .precip import river_links
from .river import clean

log = logging.getLogger("nmwater.site.snow")

PARTIAL_BELOW = 0.95                        # grid_fraction under this: the average covers part of the watershed
RANGE_NEAR_DEG = 0.04                       # a station within about 4 km of a range belongs to it
SERIES_FROM = "2003-09-01"
MODEL_NOTE = ("SNODAS is a 1 km snow model, an estimate and not a measurement; its figure for a day is for that day and is published the next morning. "
              "Narrow ranges are smoothed by it and read low.")
NEW_NOTE = ("New snow is estimated from SNOTEL snow depth and snow-water readings: a day counts when depth rises by at least 1 inch and the water reading rises by at least 0.1 inch. "
            "Snow settles, so it undercounts, and only places with a snow station have a value.")
DEFINITIONS = {
    "swe": "Water in the snow: the depth of water the snow would make if melted, averaged over the place (inches, SNODAS).",
    "depth": "Snow depth: how deep the snow lies, averaged over the place (inches, SNODAS).",
    "new24": "New snow yesterday, the last complete day, at the place's snow stations (inches of snow, estimated).",
    "new7": "New snow in the last 7 days at the place's snow stations (inches of snow, estimated).",
    "newseason": "New snow since 1 October at the place's snow stations (inches of snow, estimated).",
}
UNITS = {"swe": "in", "depth": "in", "new24": "in", "new7": "in", "newseason": "in"}


def _read_units(parquet_dir: Path):
    d = parquet_dir / "derived"
    units_file, daily_dir = d / "watershed_snow_units.parquet", d / "watershed_snow"
    if not units_file.exists() or not daily_dir.exists() or not any(daily_dir.rglob("*.parquet")):
        return None
    return pd.read_parquet(units_file), pd.read_parquet(daily_dir)


def _stations(con) -> pd.DataFrame:
    return con.sql("SELECT site_uid AS id, name, huc8, lat, lon, round(elevation_m * 3.28084)::INT AS elev_ft FROM sites "
                   "WHERE source = 'nrcs' AND state = 'NM' AND site_uid LIKE '%:SNTL' ORDER BY site_uid").df()


def _daily(con, ids: list[str], variable: str) -> dict[str, pd.Series]:
    """One value per station and day D: NRCS's own daily value dated D, or until it arrives the hourly reading at 07:00 UTC on D+1. Both are the reading at the local midnight that ends
    day D (checked against the hourly series: the daily rows equal the 07:00 UTC readings of the next UTC day exactly, in every month), so a rise dated D is the snow of the local day D."""
    q = ",".join("'" + i + "'" for i in ids)
    d = con.sql(f"SELECT site_uid, CASE WHEN interval = 'hourly' THEN (datetime_utc - INTERVAL 1 DAY)::DATE ELSE datetime_utc::DATE END AS d, "
                f"coalesce(max(value) FILTER (WHERE interval = 'daily'), max(value) FILTER (WHERE interval = 'hourly' AND hour(datetime_utc) = 7)) AS v "
                f"FROM observations_clean WHERE variable = '{variable}' AND site_uid IN ({q}) "
                f"AND datetime_utc >= TIMESTAMPTZ '{SERIES_FROM} 00:00:00+00' GROUP BY 1, 2").df().dropna(subset=["v"])
    d["d"] = pd.to_datetime(d["d"])
    return {k: g.set_index("d")["v"].sort_index() for k, g in d.groupby("site_uid")}


def _station_range(lon: float, lat: float, ranges: dict) -> str | None:
    from shapely.geometry import Point

    p = Point(lon, lat)
    best = min(((g.distance(p), rid) for rid, g in ranges.items()), default=(1e9, None))
    return best[1] if best[0] < RANGE_NEAR_DEG else None


def _val(s: pd.Series | None, day: pd.Timestamp, back: int = 5) -> float | None:
    """The reading on `day`, or the latest one in the `back` days before it (a station can miss a day)."""
    if s is None:
        return None
    for k in range(back + 1):
        v = s.get(day - pd.Timedelta(days=k), np.nan)
        if pd.notna(v):
            return float(v)
    return None


def _pct(r: dict) -> int | None:
    return None if r["pct"] is None else round(r["pct"])


def station_state(con, unit_ids: list[str], range_shapes: dict, fallback_day):
    """The stations with their daily series and new-snow estimates, the newest station day, and which stations belong to which watershed and range.
    Returns (stations, depth_by, swe_by, new_by, station_day, by_watershed, by_range); a unit id appears in both maps, with the stations that fit it."""
    st = _stations(con)
    depth_by = _daily(con, list(st["id"]), "snow_depth")
    swe_by = _daily(con, list(st["id"]), "swe")
    new_by = {i: ss.new_snow_daily(depth_by[i], swe_by.get(i)) for i in st["id"] if i in depth_by}
    st["range"] = [_station_range(r.lon, r.lat, range_shapes) for r in st.itertuples()]
    stn_day = pd.Timestamp(max((s.index.max() for s in depth_by.values()), default=fallback_day))
    by_ws = {u: [i for i, h in zip(st["id"], st["huc8"]) if h == u and i in new_by] for u in unit_ids}
    by_rg = {u: [i for i, r in zip(st["id"], st["range"]) if r == u and i in new_by] for u in unit_ids}
    return st, depth_by, swe_by, new_by, stn_day, by_ws, by_rg


def export_snow(con, out: Path, rivers_dir: Path, parquet_dir: Path, generated: str, csv: bool = True) -> dict | None:
    """Write <out>/snow/ and return the manifest block, or None when the derived SNODAS series do not exist yet (nmwater watershed-snow)."""
    got = _read_units(parquet_dir)
    if got is None:
        log.warning("no watershed snow series; no snow pages (run nmwater watershed-snow)")
        return None
    units, daily = got
    daily["date"] = pd.to_datetime(daily["date"])
    model_day = daily["date"].max()
    wy, i_now = sn.day_index(model_day) or sn.day_index(model_day - pd.Timedelta(days=1))
    base = out / "snow"
    base.mkdir(parents=True, exist_ok=True)

    from shapely.geometry import shape

    cached = json.loads((parquet_dir / "derived" / "snow_ranges.geojson").read_text())["features"]
    range_shapes = {f["properties"]["id"]: shape(f["geometry"]) for f in cached}
    ranges_fc = geo.collection([geo.feature(range_shapes[f["properties"]["id"]], **f["properties"]) for f in cached])
    geo.write(base / "ranges.geojson", ranges_fc)

    ids = list(units["unit"])
    piv = {k: daily.pivot(index="date", columns="unit", values=col).reindex(columns=ids) for k, col in (("swe", "swe_in"), ("depth", "depth_in"))}
    years = range(sn.BASELINE[0], wy + 1)
    mats = {k: sn.season_matrix(piv[k].index, piv[k].to_numpy(), years) for k in piv}
    col = {u: i for i, u in enumerate(ids)}

    # ---- stations
    st, depth_by, swe_by, new_by, stn_day, by_ws, by_rg = station_state(con, ids, range_shapes, model_day)

    def new_ratings(u: str, kind: str) -> dict:
        members = (by_rg if units.loc[units["unit"] == u, "kind"].iloc[0] == "range" else by_ws)[u]
        return ss.rate_new_snow({m: new_by[m] for m in members}, stn_day, kind) if members else {"now": None, "usual": None, "pct": None, "cls": None, "little": False, "n": 0, "stations": 0}

    def ratings(u: str) -> dict:
        r = {}
        for k in ("swe", "depth"):
            r[k] = sn.rate(piv[k][u].get(model_day), sn.pool(mats[k], col[u], i_now), k)
        for kind in ss.KINDS:
            r[kind] = new_ratings(u, kind)
        return r

    links = river_links(rivers_dir)
    rows, csv_rows = [], []
    for u in units.itertuples():
        rt = ratings(u.unit)
        members = (by_rg if u.kind == "range" else by_ws)[u.unit]
        feats = ranges_fc["features"]
        props = next((f["properties"] for f in feats if f["properties"]["id"] == u.unit), {}) if u.kind == "range" else {}
        stn_rows = [{"id": s.id, "name": s.name, "elev_ft": int(s.elev_ft), "swe": _val(swe_by.get(s.id), stn_day), "depth": _val(depth_by.get(s.id), stn_day),
                     **{k: (None if pd.isna(ss.window_total(new_by[s.id], stn_day, k)) else round(float(ss.window_total(new_by[s.id], stn_day, k)), 2)) for k in ss.KINDS}}
                    for s in st.itertuples() if s.id in members]
        series = {k: {"this": [None if np.isnan(v) else round(float(v), 3) for v in mats[k].get(wy, np.full((len(ids), sn.DAYS), np.nan))[col[u.unit]]],
                      "last": [None if np.isnan(v) else round(float(v), 3) for v in mats[k].get(wy - 1, np.full((len(ids), sn.DAYS), np.nan))[col[u.unit]]]} for k in ("swe", "depth")}
        one = {"schema_version": SCHEMA_VERSION, "unit": u.unit, "kind": u.kind, "name": u.name, "water_year": int(wy), "model_day": model_day,
               "day_index": int(i_now), "stations_day": stn_day, "baseline": f"{sn.BASELINE[0]}-{sn.BASELINE[1]}", "series": series,
               "band": {k: sn.band(mats[k], col[u.unit]) for k in ("swe", "depth")}, "ratings": rt, "stations": stn_rows}
        if u.kind == "watershed":
            one.update({"grid_fraction": None if pd.isna(u.grid_fraction) else float(u.grid_fraction), "nm_fraction": float(u.nm_fraction), "rivers": links.get(u.unit, [])})
        else:
            one.update({"note": props.get("note", ""), "cells": int(props.get("cells", 0)), "model_smooths": bool(props.get("model_smooths", False))})
        d = base / u.unit
        d.mkdir(exist_ok=True)
        (d / "snow.json").write_text(json.dumps(clean(one), separators=(",", ":"), allow_nan=False), encoding="utf-8")
        head = {"unit": u.unit, "kind": u.kind, "name": u.name, "ratings": rt, "stations": len(members)}
        if u.kind == "watershed":
            head.update({"nm_fraction": float(u.nm_fraction), "partial": bool(pd.notna(u.grid_fraction) and u.grid_fraction < PARTIAL_BELOW), "rivers": links.get(u.unit, [])})
        else:
            head.update({"note": props.get("note", ""), "model_smooths": bool(props.get("model_smooths", False))})
        rows.append(head)
        csv_rows.append({"unit": u.unit, "kind": u.kind, "name": u.name, "model_day": model_day.date(), "stations_day": stn_day.date(),
                         **{f"{k}_{f}": (None if rt[k][f] is None else rt[k][f]) for k in sn.MEASURES for f in ("now", "usual", "pct", "cls")}})
    stations = [{"id": s.id, "name": s.name, "lat": round(float(s.lat), 4), "lon": round(float(s.lon), 4), "elev_ft": int(s.elev_ft), "watershed": s.huc8 if s.huc8 in col else None,
                 "range": s.range, "swe": _val(swe_by.get(s.id), stn_day), "depth": _val(depth_by.get(s.id), stn_day),
                 **{k: (None if s.id not in new_by or pd.isna(ss.window_total(new_by[s.id], stn_day, k)) else round(float(ss.window_total(new_by[s.id], stn_day, k)), 2)) for k in ss.KINDS}}
                for s in st.itertuples()]
    files = {"index": "index.json", "ranges": "ranges.geojson", "csv": []}
    if csv:
        pd.DataFrame(csv_rows).to_csv(base / "recent.csv", index=False)
        files["csv"] = ["recent.csv"]
    index = {"schema_version": SCHEMA_VERSION, "generated": generated, "model_day": str(model_day.date()), "stations_day": str(stn_day.date()), "baseline": f"{sn.BASELINE[0]}-{sn.BASELINE[1]}",
             "measures": list(sn.MEASURES), "definitions": DEFINITIONS, "units": UNITS, "classes": [label for _, label in rn.CLASSES],
             "thresholds": {"little_in": sn.LITTLE_IN, "min_usual_in": sn.MIN_USUAL_IN, "percent_from_in": sn.PERCENT_FROM_IN},
             "notes": {"model": MODEL_NOTE, "new_snow": NEW_NOTE},
             "watersheds": [r for r in rows if r["kind"] == "watershed"], "ranges": [r for r in rows if r["kind"] == "range"], "stations": stations, "files": files}
    (base / "index.json").write_text(json.dumps(clean(index), separators=(",", ":"), allow_nan=False), encoding="utf-8")
    log.info("snow: %d watersheds, %d ranges, %d stations, model day %s, stations %s", len(index["watersheds"]), len(index["ranges"]), len(stations), model_day.date(), stn_day.date())
    return {"path": "snow/", "watersheds": len(index["watersheds"]), "ranges": len(index["ranges"]), "stations": len(stations), "model_day": str(model_day.date()), "stations_day": str(stn_day.date()),
            "measures": list(sn.MEASURES), "files": files}
