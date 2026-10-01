"""The data behind one river's pages: segment and gauge tables, the Overview rows, precipitation and coverage blocks.

Plain data (dicts and DataFrames); nothing here writes HTML. nmwater/site/river.py turns it into the bundle's JSON files."""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd
from ..derived import river_flow as rf
from ..derived import river_normal as rn
from ..derived import river_quality as rq
from ..derived import river_watershed as rw


def _ms(d) -> int:
    return int(pd.Timestamp(d).tz_localize(None).value // 1_000_000)


def _num(v, nd=1):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) or pd.isna(v) else round(float(v), nd)


@dataclass
class Bundle:
    r: rf.RiverReport
    normal: rn.NormalResult
    dry: rn.DryingResult
    quality: rq.QualityResult
    clim: pd.DataFrame
    drought: pd.DataFrame
    huc8_of: dict
    precip: dict | None = None           # rw.precip_summary: per-HUC8 daily and monthly precipitation, or None
    coverage: dict | None = None         # per-HUC8 grid_fraction, has_precip, nm_fraction


def extend(con, r: rf.RiverReport, river_name: str, grids: Path, cache: Path) -> Bundle:
    """Add flow-vs-normal, drying, quality and watershed data to an already built river report."""
    seg_of = dict(zip(r.gauges["gauge"], r.gauges["segment"]))
    normal = rn.flow_vs_normal(r.gw_all, seg_of, r.elig, r.weeks52)
    dry = rn.drying(r.chosen, seg_of, r.elig, r.as_of)
    river = rf.River(river_name, r.gnis_id, r.river)
    sites = rq.read_quality_sites(con, river, r.segments)
    quality = rq.summarise(rq.read_quality(con, sites), sites, r.segments, r.as_of.year)
    hmap = con.execute("SELECT r.huc8_name, mode(r.huc8) FROM river_segments r JOIN site_reaches sr USING (site_uid) "
                       "JOIN flowlines f ON f.comid = sr.comid WHERE f.gnis_id = ? GROUP BY 1", [r.gnis_id]).fetchall()
    huc8_of = {n: h for n, h in hmap if n in r.segments and h}
    codes = sorted(set(huc8_of.values()))
    clim = rw.update_climate(grids, grids / "wbd", cache, codes) if codes else pd.DataFrame(
        columns=["huc8", "variable", "date", "value"])
    drought = rw.drought(con, codes) if codes else pd.DataFrame(columns=["huc8", "huc4", "date", "dsci"])
    ps = rw.precip_summary(con, codes) if codes else None
    return Bundle(r, normal, dry, quality, clim, drought, huc8_of, *(ps or (None, None)))


# ---------------------------------------------------------------------------- data for the pages
def coverage_note(cov: dict | None) -> str:
    """Words for a watershed the precipitation grid covers only partly, or not at all."""
    if not cov:
        return ""
    gf = cov["grid_fraction"]
    why = ("the grid is cut at the New Mexico border" + (", and PRISM has no data in Mexico" if "MX" in cov.get("states", "") else ""))
    if not cov["has_precip"]:
        return f"Precipitation is not shown for this watershed: only {gf:.0%} of it lies inside the precipitation grid ({why})."
    if gf < 0.95:
        return f"The precipitation grid covers {gf:.0%} of this watershed ({why}), so the values are averages over that part only."
    return ""


def precip_block(b: Bundle, h: str | None, old: pd.DataFrame) -> dict:
    """Precipitation fields of one segment: from the daily dataset when the catalog has it, else the monthly cache."""
    cov = (b.coverage or {}).get(h)
    base = {"coverage_note": coverage_note(cov), "daily_dates": [], "daily": [], "recent": None, "month_days": [],
            "month_complete": []}
    if b.precip is None:                                       # catalog without watershed_precip: monthly cache only
        return {**base, "months": [_ms(x) for x in old["date"]], "precip": [_num(v, 2) for v in old["value"]],
                "precip_normal": [_num(v, 2) for v in old["normal"]],
                "coverage_note": "Daily precipitation is not available in this catalog."}
    x = b.precip.get(h)
    if not x:
        return {**base, "months": [], "precip": [], "precip_normal": []}
    rc = x["recent"]
    return {**base,
            "months": [_ms(t) for t in x["months"]], "precip": [_num(v, 2) for v in x["month_total"]],
            "precip_normal": [_num(v, 2) for v in x["month_normal"]], "month_days": x["month_days"],
            "month_complete": x["month_complete"],
            "daily_dates": [_ms(t) for t in x["daily_dates"]], "daily": [_num(v, 3) for v in x["daily"]],
            "recent": {"last": _ms(rc["last"]), "start": _ms(rc["window_start"]), "total": _num(rc["total"], 2),
                       "normal": _num(rc["normal"], 2), "pct_normal": _num(rc["pct_normal"], 0),
                       "percentile": _num(rc["percentile"], 0), "wettest_date": _ms(rc["wettest_date"]),
                       "wettest_in": _num(rc["wettest_in"], 2)}}


def page_data(b: Bundle) -> dict:
    r, segs = b.r, b.r.segments
    colors = {s: f"var(--s{i + 1})" for i, s in enumerate(segs)}
    weeks = [pd.Timestamp(w) for w in r.weeks52]
    # compared with normal
    n = {"weeks": [_ms(w) for w in weeks], "rows": segs, "cls": [], "pct": [], "gauges": []}
    s = b.normal.segments
    if s.empty:
        s = pd.DataFrame(columns=["segment", "week_start", "pct", "cls", "n_gauges"])
    for seg in segs:
        d = s[s["segment"] == seg].set_index("week_start")
        n["cls"].append([d["cls"].get(w) if w in d.index else None for w in weeks])
        n["pct"].append([_num(d["pct"].get(w), 0) if w in d.index else None for w in weeks])
    ng = b.normal.gauges if not b.normal.gauges.empty else pd.DataFrame(
        columns=["gauge", "segment", "week_start", "basis", "mean_cfs", "p10", "p25", "p50", "p75", "p90", "cls"])
    for (gname, seg), d in ng.groupby(["gauge", "segment"], sort=False):
        d = d.set_index("week_start").reindex(weeks)
        n["gauges"].append({"name": gname, "segment": seg, "basis": str(d["basis"].dropna().iloc[0]) if d["basis"].notna().any() else "",
                            **{k: [_num(v) for v in d[k]] for k in ("mean_cfs", "p10", "p25", "p50", "p75", "p90")},
                            "cls": [c if isinstance(c, str) else None for c in d["cls"]]})
    n["gauges"] = [g for g in n["gauges"] if any(v is not None for v in g["mean_cfs"])]
    n["gauges"].sort(key=lambda g: (segs.index(g["segment"]), g["name"]))
    # drying
    y0, y1 = 1991, r.as_of.year
    dr = {"years": list(range(y0, y1 + 1)), "segments": []}
    for seg in segs:
        d = b.dry.by_segment[b.dry.by_segment["segment"] == seg].set_index("year")
        base = d.loc[(d.index >= 1991) & (d.index <= 2020), "any_dry_days"]
        dr["segments"].append({"name": seg, "color": colors[seg],
                               "dry": [int(d["any_dry_days"].get(y)) if y in d.index else None for y in dr["years"]],
                               "days": [int(d["days_with_data"].get(y)) if y in d.index else None for y in dr["years"]],
                               "median": _num(base.median(), 0) if len(base) else None})
    # temperature and salinity
    q = b.quality
    temp = {"sites": []}
    if not q.sensors.empty:
        for x in q.sensors.sort_values(["segment", "years"], ascending=[True, False]).itertuples():
            sd = q.season[q.season["site"] == x.site] if not q.season.empty else pd.DataFrame()
            hd = q.heat[q.heat["site"] == x.site]
            temp["sites"].append({"name": rf.nice_name(x.site), "segment": x.segment, "stat": x.stat, "years": int(x.years),
                                  "season": None if sd.empty else {k: [_num(v) for v in sd[k]] for k in ("p10", "p50", "p90", "this_year")},
                                  "heat_years": [int(v) for v in hd["year"]], "heat": [_num(v) for v in hd["value"]],
                                  "metric": hd["metric"].iloc[0] if len(hd) else ""})
        temp["sites"].sort(key=lambda t: (segs.index(t["segment"]) if t["segment"] in segs else 99, -t["years"]))
    decades = sorted(q.salinity_profile["decade"].unique()) if not q.salinity_profile.empty else []
    sal = {"segments": segs, "decades": [int(x) for x in decades], "median": [], "n": []}
    for dcd in decades:
        d = q.salinity_profile[q.salinity_profile["decade"] == dcd].set_index("segment")
        sal["median"].append([_num(d["median"].get(sg), 0) if sg in d.index else None for sg in segs])
        sal["n"].append([int(d["n_days"].get(sg)) if sg in d.index else 0 for sg in segs])
    # watershed
    ws = {"segments": []}
    pv = rw.monthly_vs_normal(b.clim, "precip_in")
    tv = rw.monthly_vs_normal(b.clim, "air_temp_c")
    swe = b.clim[b.clim["variable"] == "swe_in"].copy()
    swe["date"] = pd.to_datetime(swe["date"])
    start = (pd.Timestamp(r.as_of) - pd.DateOffset(months=36)).replace(day=1)
    for seg in segs:
        h = b.huc8_of.get(seg)
        p = pv[(pv["huc8"] == h) & (pv["date"] >= start)]
        pr = precip_block(b, h, p)
        t = tv[(tv["huc8"] == h) & (tv["date"] >= start)]
        sw = swe[swe["huc8"] == h].copy()
        # snow: by day of the water year (Oct 1 = 0), last three winters and the 2004-2025 median
        sw["wy"] = sw["date"].dt.year + (sw["date"].dt.month >= 10)
        sw["dwy"] = (sw["date"] - pd.to_datetime((sw["wy"] - 1).astype(str) + "-10-01")).dt.days // 7
        med = sw[(sw["wy"] >= 2004) & (sw["wy"] <= 2025)].groupby("dwy")["value"].median()
        winters = sorted(sw["wy"].unique())[-3:]
        wk = list(range(0, 39))
        dd = b.drought[b.drought["huc8"] == h].copy()
        dd["date"] = pd.to_datetime(dd["date"])
        dd = dd[dd["date"] >= pd.Timestamp(r.as_of) - pd.DateOffset(years=10)].sort_values("date")
        ws["segments"].append({
            "name": seg, "huc8": h, "huc4": h[:4] if h else "",
            **pr,
            "tmonths": [_ms(x) for x in t["date"]], "tanom": [_num(v, 1) for v in t["anomaly"]],
            "swe_weeks": wk, "swe_median": [_num(med.get(w), 1) for w in wk],
            "swe_winters": [{"wy": int(y), "values": [_num(sw[(sw["wy"] == y) & (sw["dwy"] == w)]["value"].mean(), 1) for w in wk]} for y in winters],
            "dsci_dates": [_ms(x) for x in dd["date"]], "dsci": [_num(v, 0) for v in dd["dsci"]]})
    return {"normal": n, "drying": dr, "temp": temp, "salinity": sal, "watershed": ws, "colors": colors,
            "as_of": r.as_of.isoformat()}


# ---------------------------------------------------------------------------- overview
def write_precip_csv(d: Path, b: Bundle) -> None:
    """precip_daily_last_365_days.csv: one row per segment and PRISM day, with the period each value covers."""
    if b.precip is None:
        return
    rows = []
    for seg in b.r.segments:
        h = b.huc8_of.get(seg)
        x = (b.precip or {}).get(h)
        if not x:
            continue
        for t, v in zip(x["year_dates"], x["year_daily"]):
            end = pd.Timestamp(t) + pd.Timedelta(hours=12)
            rows.append({"segment": seg, "huc8": h, "date": pd.Timestamp(t).date().isoformat(), "precip_in": round(v, 4),
                         "period_start_utc": (end - pd.Timedelta(days=1)).strftime("%Y-%m-%d %H:%M"),
                         "period_end_utc": end.strftime("%Y-%m-%d %H:%M")})
    if rows:
        pd.DataFrame(rows).to_csv(d / "precip_daily_last_365_days.csv", index=False)


def overview_rows(b: Bundle, data: dict) -> list[dict]:
    r = b.r
    rows = []
    last_w = pd.Timestamp(r.weeks52[-1])
    for i, seg in enumerate(r.segments):
        sw = r.seg52[(r.seg52["segment"] == seg)]
        cur = sw[pd.to_datetime(sw["week_start"]) == last_w]
        ns = b.normal.segments
        nsr = ns[(ns["segment"] == seg) & (pd.to_datetime(ns["week_start"]) == last_w)] if not ns.empty else ns
        dry = next(d for d in data["drying"]["segments"] if d["name"] == seg)
        temps = [t for t in data["temp"]["sites"] if t["segment"] == seg and t["season"]]
        tnow = None
        if temps:
            s_ = temps[0]["season"]["this_year"]
            vals = [v for v in s_ if v is not None]
            tnow = vals[-1] if vals else None
        sal = data["salinity"]
        sc = None
        if sal["decades"]:
            col = sal["segments"].index(seg)
            for k in range(len(sal["decades"]) - 1, -1, -1):
                if sal["median"][k][col] is not None:
                    sc = (sal["median"][k][col], sal["decades"][k])
                    break
        ws = next(w for w in data["watershed"]["segments"] if w["name"] == seg)
        done = [i for i, c in enumerate(ws["month_complete"] or [True] * len(ws["precip"])) if c][-3:]
        p3 = [ws["precip"][i] for i in done if ws["precip"][i] is not None]
        n3 = [ws["precip_normal"][i] for i in done if ws["precip_normal"][i] is not None]
        rows.append({"segment": seg, "color": f"var(--s{i + 1})",
                     "flow": None if cur.empty else float(cur["mean_cfs"].iloc[0]),
                     "cls": None if nsr.empty else nsr["cls"].iloc[0], "pct": None if nsr.empty else float(nsr["pct"].iloc[0]),
                     "spark": [None if pd.isna(v) else float(v) for v in
                               sw.set_index(pd.to_datetime(sw["week_start"]))["mean_cfs"].reindex(pd.to_datetime(pd.Series(r.weeks52)))],
                     "dry": dry["dry"][-1], "dry_median": dry["median"], "temp": tnow,
                     "temp_site": temps[0]["name"] if temps else None, "sc": sc,
                     "precip3": sum(p3) / sum(n3) * 100 if p3 and n3 and sum(n3) > 0 else None,
                     "dsci": ws["dsci"][-1] if ws["dsci"] else None, "huc4": ws["huc4"]})
    return rows


def gauge_points(con, b: Bundle) -> pd.DataFrame:
    """One point per eligible gauge: name, segment, lat, lon (from the first copy with coordinates)."""
    g = b.r.gauges[b.r.gauges["gauge"].isin(b.r.elig)]
    ids = ",".join("'" + u.replace("'", "''") + "'" for u in g["site_uid"])
    ll = con.sql(f"SELECT site_uid, lat, lon FROM sites WHERE site_uid IN ({ids})").df()
    g = g.merge(ll, on="site_uid").dropna(subset=["lat", "lon"]).drop_duplicates("gauge")
    return g.rename(columns={"gauge": "name"})[["name", "segment", "lat", "lon"]]


def has_tabs(data: dict) -> dict[str, bool]:
    return {"normal": bool(data["normal"]["gauges"]),
            "quality": bool([t for t in data["temp"]["sites"] if t["season"]]) or bool(data["salinity"]["decades"]),
            "watershed": any(w["precip"] for w in data["watershed"]["segments"])}
