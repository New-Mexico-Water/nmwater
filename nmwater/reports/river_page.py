"""The river page: one HTML page per river with tabs (Overview with map and description, Flow, Compared
with normal, Drying, Temperature & salinity, Watershed, Data notes). Layout chosen 2026-09-28.

Called by nmwater.reports.river_flow.run for every river; charts come from templates/assets
(site.css, charts.js), shared by all pages from <out>/rivers/assets/. Each river folder gets
index.html, data.js (the chart data) and CSVs next to them.
"""

from __future__ import annotations

import html
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import river_flow as rf
from . import river_normal as rn
from . import river_quality as rq
from . import river_watershed as rw

log = logging.getLogger("nmwater.reports.river_samples")
ASSETS = Path(__file__).parent / "templates" / "assets"
CLASS_VARS = {"much below normal": "var(--c-much-below)", "below normal": "var(--c-below)", "normal": "var(--c-normal)",
              "above normal": "var(--c-above)", "much above normal": "var(--c-much-above)"}
FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" '
         'href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">')


def _ms(d) -> int:
    return int(pd.Timestamp(d).tz_localize(None).value // 1_000_000)


def _num(v, nd=1):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) or pd.isna(v) else round(float(v), nd)


def esc(s) -> str:
    return html.escape(str(s))


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


# ---------------------------------------------------------------------------- sections
SECTION_JS = r"""
(function () {
  const D = window.RIVER, C = window.RiverCharts;
  const cls = {"much below normal": "var(--c-much-below)", "below normal": "var(--c-below)", "normal": "var(--c-normal)",
               "above normal": "var(--c-above)", "much above normal": "var(--c-much-above)"};
  const $ = id => document.getElementById(id);
  function opt(sel, items, label) { items.forEach((it, i) => { const o = document.createElement("option"); o.value = i; o.textContent = label(it); sel.appendChild(o); }); }
  // compared with normal
  if ($("n-strip")) {
    const N = D.normal;
    C.strip($("n-strip"), { rows: N.rows, x: N.weeks, cls: N.cls, pct: N.pct, classes: cls, label: "Flow compared with normal, by segment and week" });
    const sel = $("n-gauge"); opt(sel, N.gauges, g => g.segment + " · " + g.name);
    const draw = () => { const g = N.gauges[+sel.value];
      $("n-basis").textContent = "Normal: " + g.basis + ". Shaded: middle 50% and 10th-90th percentile of that week's flow in the normal period.";
      C.line($("n-bands"), { x: N.weeks, xType: "time", unit: "cfs", yMin: 0, label: "This year's weekly flow against normal",
        bands: [{ name: "10th-90th percentile", lo: g.p10, hi: g.p90, color: "var(--band-1)" }, { name: "25th-75th percentile", lo: g.p25, hi: g.p75, color: "var(--band-2)" }],
        series: [{ name: "Median", values: g.p50, color: "var(--text-muted)", dash: true, width: 1.5 }, { name: "This year", values: g.mean_cfs, color: "var(--s1)" }] }); };
    sel.addEventListener("change", draw); draw();
  }
  // drying
  if ($("dry-grid")) {
    const R = D.drying, box = $("dry-grid");
    R.segments.forEach(s => { const card = document.createElement("div"); card.className = "mini";
      const h = document.createElement("h3"); h.textContent = s.name; const p = document.createElement("p");
      p.textContent = s.median == null ? "No 1991-2020 normal." : "Normal (1991-2020 median): " + s.median + " days a year.";
      const c = document.createElement("div"); card.append(h, p, c); box.appendChild(card);
      if (!s.dry.some(v => v)) { c.className = "note"; c.textContent = "No dry days recorded since " + R.years[0] + "."; return; }
      C.bars(c, { x: R.years, xType: "year", values: s.dry, color: s.color, name: "Days any gauge was dry", unit: "days", height: 150,
        label: "Days each year any gauge was dry, " + s.name,
        ref: s.median == null ? null : { name: "Normal", values: R.years.map(() => s.median) }, extra: [{ name: "Days with data", values: s.days }] }); });
  }
  // temperature
  if ($("t-season")) {
    const T = D.temp.sites.filter(s => s.season), sel = $("t-site");
    if (!T.length) { $("t-season").textContent = "No water-temperature sensor with enough years."; }
    else { opt(sel, T, s => s.segment + " · " + s.name + " (" + s.years + " summers)");
      const doy = Array.from({ length: 365 }, (_, i) => i + 1);
      const draw = () => { const s = T[+sel.value];
        C.line($("t-season"), { x: doy, xType: "doy", unit: "°C", label: "Daily water temperature this year against other years",
          bands: [{ name: "10th-90th percentile, other years", lo: s.season.p10, hi: s.season.p90, color: "var(--band-1)" }],
          series: [{ name: "Median, other years", values: s.season.p50, color: "var(--text-muted)", dash: true, width: 1.5 }, { name: "This year", values: s.season.this_year, color: "var(--s2)" }] });
        C.line($("t-heat"), { x: s.heat_years, xType: "year", unit: "°C", label: "Hottest week each year",
          series: [{ name: s.metric, values: s.heat, color: "var(--s2)", points: true, joined: false }] }); };
      sel.addEventListener("change", draw); draw(); }
  }
  // salinity profile
  if ($("s-profile")) {
    const S = D.salinity;
    if (!S.decades.length) $("s-profile").textContent = "No conductance data."; else {
      const pal = ["var(--c-much-above)", "var(--s1)", "var(--s3)", "var(--s4)", "var(--s2)", "var(--c-much-below)"];
      const keep = S.decades.slice(-5);
      C.line($("s-profile"), { x: S.segments.map((_, i) => i), xType: "cat", xLabel: "Segment", labels: S.segments.map(s => s.replace(/^Rio Grande-/, "").replace(" Reservoir", "").replace("Upper Rio Grande", "Upper")), unit: "µS/cm",
        label: "Median specific conductance by segment and decade",
        series: keep.map((d, k) => ({ name: d + "s", values: S.median[S.decades.indexOf(d)], color: pal[k % pal.length], points: true, joined: true, width: 1.5 })) });
    }
  }
  // watershed
  if ($("w-seg")) {
    const W = D.watershed, sel = $("w-seg"); opt(sel, W.segments, s => s.name + " (HUC " + s.huc8 + ")");
    const draw = () => { const s = W.segments[+sel.value];
      const fD = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
      const fDY = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
      const note = $("w-cov"); note.hidden = !s.coverage_note; note.textContent = s.coverage_note || "";
      const rr = s.recent, sum = $("w-recent-sum"), dbox = $("w-daily");
      if (rr) {
        sum.textContent = "Last 30 days, " + fD.format(rr.start) + " to " + fDY.format(rr.last) + ": " + rr.total.toFixed(2) + " in"
          + (rr.pct_normal == null ? "" : ", " + rr.pct_normal + "% of normal (" + rr.normal.toFixed(2) + " in, the 1991-2020 median for these dates)")
          + (rr.percentile == null ? "" : "; wetter than " + rr.percentile + "% of those 30 years")
          + ". Wettest day: " + rr.wettest_in.toFixed(2) + " in, " + fDY.format(rr.wettest_date) + ".";
        C.bars(dbox, { x: s.daily_dates, xType: "time", values: s.daily, color: "var(--s1)", name: "Precipitation", unit: "in", height: 180,
          label: "Daily precipitation, last 90 days, " + s.name, tickFmt: m => fD.format(m) });
      } else { sum.textContent = s.coverage_note ? "" : "No daily precipitation for this watershed."; dbox.replaceChildren(); }
      const partial = s.month_complete || [];
      C.bars($("w-precip"), { x: s.months, xType: "time", xRes: "month", values: s.precip, color: "var(--s1)", name: "Precipitation", unit: "in",
        colorFn: (v, i) => partial.length && !partial[i] ? "var(--control)" : "var(--s1)", label: "Monthly precipitation, last 36 months, " + s.name,
        ref: { name: "Normal (1991-2020 median)", values: s.precip_normal }, height: 200, extra: s.month_days.length ? [{ name: "Days counted", values: s.month_days }] : undefined,
        tickFmt: m => new Intl.DateTimeFormat("en-US", { month: "short", year: "2-digit", timeZone: "UTC" }).format(m) });
      C.bars($("w-temp"), { x: s.tmonths, xType: "time", xRes: "month", values: s.tanom, colorFn: v => v >= 0 ? "var(--neg)" : "var(--pos)", name: "Difference from normal", unit: "°C", height: 200,
        tickFmt: m => new Intl.DateTimeFormat("en-US", { month: "short", year: "2-digit", timeZone: "UTC" }).format(m) });
      const pal = ["var(--s3)", "var(--s4)", "var(--s2)"];
      C.line($("w-swe"), { x: s.swe_weeks, xType: "cat", xLabel: "Week of the water year", labels: s.swe_weeks.map(w => new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" }).format(Date.UTC(2021, 9, 1) + w * 7 * 864e5)),
        unit: "in", yMin: 0, label: "Snow-water equivalent by week of the water year",
        series: [{ name: "Median 2004-2025", values: s.swe_median, color: "var(--text-muted)", dash: true, width: 1.5 }].concat(
          s.swe_winters.map((w, k) => ({ name: "Winter " + (w.wy - 1) + "-" + String(w.wy).slice(2), values: w.values, color: pal[k] }))) });
      C.line($("w-dsci"), { x: s.dsci_dates, xType: "time", unit: "DSCI", yMin: 0, label: "Drought Severity and Coverage Index",
        series: [{ name: "DSCI, basin HUC " + s.huc4, values: s.dsci, color: "var(--c-much-below)" }] }); };
    sel.addEventListener("change", draw); draw();
  }
})();
"""


def sec_normal(b: Bundle) -> str:
    return ('<h2>Flow compared with normal</h2>'
            '<p class="hint">Each week of the last year, rated against the same week in 1991-2020 (the standard 30-year normal). '
            'A segment\'s rating is the median of its gauges\' percentiles. Classes as used by USGS WaterWatch: below the 10th '
            'percentile is much below normal, 10th-24th below, 25th-75th normal, 76th-90th above, over the 90th much above.</p>'
            '<ul class="legend">' + "".join(f'<li><i class="box" style="--c:{v}"></i>{esc(k)}</li>' for k, v in CLASS_VARS.items())
            + '<li><i class="box" style="--c:var(--c-none)"></i>no rating</li></ul>'
            '<div id="n-strip"></div>'
            '<h3>One gauge against its normal range</h3><div class="controls"><label for="n-gauge">Gauge</label>'
            '<select id="n-gauge"></select></div><p class="hint" id="n-basis"></p><div id="n-bands"></div>')


def sec_drying(b: Bundle) -> str:
    left_out = "".join(f"<li><b>{esc(seg)}</b>: {esc(', '.join(gs))}</li>" for seg, gs in b.dry.normally_dry.items() if seg)
    note = ("" if not left_out else
            f'<div class="note">Left out of the counts because they are dry on at least {rn.NORMALLY_DRY:.0%} of their days '
            '(usually just below a dam or diversion that takes the flow): <ul>' + left_out + "</ul></div>")
    return ('<h2>Drying</h2>'
            f'<p class="hint">Days each year on which at least one of the segment\'s gauges read below {rn.DRY_CFS} cfs (no flow). '
            f'{b.r.as_of.year} is counted through {b.r.as_of:%b} {b.r.as_of.day}. The dashed line is the 1991-2020 median. '
            'Hover a bar for the number of days with data that year.</p>' + note + '<div class="grid3" id="dry-grid"></div>')


def sec_quality(b: Bundle) -> str:
    return ('<h2>Water temperature</h2>'
            '<p class="hint">From gauges with a temperature sensor and at least three full summers. The yearly measure is the '
            'hottest 7-day average of the daily maximum (7DADM), the usual measure of heat stress for fish; where a gauge '
            'publishes only daily means, the 7-day mean is used and labelled.</p>'
            '<div class="controls"><label for="t-site">Sensor</label><select id="t-site"></select></div>'
            '<div class="grid2"><div><h3>This year against other years</h3><div id="t-season"></div></div>'
            '<div><h3>Hottest week each year</h3><div id="t-heat"></div></div></div>'
            '<h2 style="margin-top:18px">Salinity (specific conductance)</h2>'
            '<p class="hint">Median specific conductance of all measurements (sensor days and field samples) by segment and decade, '
            'upstream to downstream. Higher means saltier water. Samples are occasional, so treat a decade with few values '
            'with care (counts are in the hover).</p><div id="s-profile"></div>')


def sec_watershed(b: Bundle) -> str:
    daily = b.precip is not None
    return ('<h2>Watershed conditions</h2>'
            '<p class="hint">Averaged over each segment\'s HUC8 watershed: precipitation and air temperature from PRISM (4 km; '
            'precipitation daily since 1981 and monthly before, temperature monthly), snow-water equivalent from SNODAS (weekly, '
            '1 km, from 2004), and drought from the US Drought Monitor\'s Drought Severity and Coverage Index for the larger HUC4 '
            'basin (0 = no drought, 500 = all of the basin in exceptional drought).</p>'
            '<div class="controls"><label for="w-seg">Segment</label><select id="w-seg"></select></div>'
            '<p class="note" id="w-cov" hidden></p>'
            + ('<h3>Recent rain</h3><p class="hint" id="w-recent-sum"></p><div id="w-daily"></div>'
               '<p class="hint">Each bar is a PRISM day: the 24 hours ending at 12:00 UTC (about 6 AM Mountain, 5 AM in winter) on '
               'the date shown, so a bar is mostly the rain of the day before. The newest days are provisional: PRISM revises '
               'about the latest six months. '
               '<a href="precip_daily_last_365_days.csv">Daily values for the last 365 days (CSV)</a>.</p>' if daily else
               '<p class="hint" id="w-recent-sum" hidden></p><div id="w-daily" hidden></div>')
            + '<div class="grid2"><div><h3>Precipitation by month</h3><div id="w-precip"></div>'
            + ('<p class="hint">Grey bar: the month so far, compared with the 1991-2020 median of the same days.</p>' if daily else '')
            + '</div><div><h3>Air temperature, difference from normal</h3><div id="w-temp"></div></div>'
            '<div><h3>Snowpack</h3><div id="w-swe"></div></div><div><h3>Drought</h3><div id="w-dsci"></div></div></div>')


# ---------------------------------------------------------------------------- flow explorer (existing page)
def explorer_parts(b: Bundle, tmp: Path) -> tuple[str, str, str, str]:
    """(style, markup, script) of the existing flow explorer, filled with this river's data."""
    f = tmp / "explorer.html"
    rf.write_explorer(f, b.r, "sample")
    t = f.read_text()
    style = t[t.index("<style>") + 7:t.index("</style>")]
    a = t.index('<section class="card" aria-labelledby="ch">')
    z = t.index('<section class="card" aria-labelledby="issues"')
    markup = t[a:z]
    script = t[t.index("<script>") + 8:t.rindex("</script>")]
    issues = t[z:t.index("</section>", z) + 10]
    return style, markup, script, issues


def head(title: str, extra_style: str = "", css_href: str = "assets/site.css", meta: str = "", icon_href: str = "assets/icon.svg") -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width, initial-scale=1"><title>{esc(title)}</title>{meta}'
            f'<link rel="icon" href="{icon_href}" type="image/svg+xml">{FONTS}'
            f'<link rel="stylesheet" href="{css_href}">' + (f"<style>{extra_style}</style>" if extra_style else "")
            + '</head><body><a class="skip" href="#main">Skip to content</a>')


TABS_JS = r"""
(function () {
  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  const show = (id, focus, keepHash) => {
    tabs.forEach(t => { const on = t.dataset.tab === id; t.setAttribute("aria-selected", String(on)); t.tabIndex = on ? 0 : -1;
      document.getElementById("panel-" + t.dataset.tab).hidden = !on; if (on && focus) t.focus(); });
    if (!keepHash && location.hash.slice(1) !== id) history.replaceState(null, "", "#" + id);
  };
  // a hash naming a tab opens it; a hash naming something inside a panel (e.g. #acequias) opens that panel and scrolls to it
  const route = () => { const h = decodeURIComponent(location.hash.slice(1));
    if (tabs.some(t => t.dataset.tab === h)) { show(h); return true; }
    const el = h && document.getElementById(h), p = el && el.closest('[role="tabpanel"]');
    if (p) { show(p.id.replace("panel-", ""), false, true); el.scrollIntoView(); return true; }
    return false; };
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => show(t.dataset.tab));
    t.addEventListener("keydown", ev => {
      const k = ev.key; let j = i;
      if (k === "ArrowRight") j = (i + 1) % tabs.length; else if (k === "ArrowLeft") j = (i - 1 + tabs.length) % tabs.length;
      else if (k === "Home") j = 0; else if (k === "End") j = tabs.length - 1; else return;
      ev.preventDefault(); show(tabs[j].dataset.tab, true);
    });
  });
  document.querySelectorAll("[data-goto]").forEach(a => a.addEventListener("click", ev => { ev.preventDefault(); show(a.dataset.goto); window.scrollTo(0, 0); }));
  window.addEventListener("hashchange", route);
  if (!route()) show(tabs[0].dataset.tab, false, !location.hash);
})();
"""


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


def render(con, b: Bundle, d: Path, grids: Path, background: dict | None, generated: str,
           site: dict | None = None, root: Path | None = None) -> dict:
    """Write <d>/index.html, <d>/data.js and <d>/social.png for one river. Returns extra manifest fields."""
    from ..core.config import PROJECT_ROOT
    from . import river_map as rm
    from . import river_overview as ro
    from . import river_share as rs

    site = site or rs.site_config({})
    root = root or PROJECT_ROOT
    data = page_data(b)
    rows = overview_rows(b, data)
    show = has_tabs(data)
    last = b.r.weeks52[-1] + timedelta(days=6)
    tmp = d / ".tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    ex_style, ex_markup, ex_script, ex_issues = explorer_parts(b, tmp)
    shutil.rmtree(tmp)
    write_precip_csv(d, b)
    gauges = gauge_points(con, b)
    overview, ov = ro.panel(b, rows, con, gauges, grids, root, background)
    tabs = [("overview", "Overview", overview), ("flow", "Flow", ex_markup)]
    if show["normal"]:
        tabs.append(("normal", "Compared with normal", f'<section class="card">{sec_normal(b)}</section>'))
    tabs.append(("drying", "Drying", f'<section class="card">{sec_drying(b)}</section>'))
    if show["quality"]:
        tabs.append(("quality", "Temperature & salinity", f'<section class="card">{sec_quality(b)}</section>'))
    if show["watershed"]:
        tabs.append(("watershed", "Watershed", f'<section class="card">{sec_watershed(b)}</section>'))
    tabs.append(("notes", "Data notes", ex_issues))
    (d / "data.js").write_text("window.RIVER = " + json.dumps(data, separators=(",", ":")) + ";")
    tablist = ('<div class="tabs sticky" role="tablist" aria-label="Report sections">' + "".join(
        f'<button type="button" role="tab" id="tab-{k}" data-tab="{k}" aria-controls="panel-{k}">{esc(t)}</button>'
        for k, t, _ in tabs) + "</div>")
    panels = "".join(f'<div role="tabpanel" id="panel-{k}" aria-labelledby="tab-{k}" tabindex="0" hidden>{body}</div>'
                     for k, _, body in tabs)
    # search and sharing
    river = b.r.river
    title = f"{river}: river conditions in New Mexico"
    tab_keys = [k for k, _, _ in tabs]
    desc = rs.description(river, rows, tab_keys, last)
    has_card = rs.social_card(d / "social.png", river=river, site_name=site["name"], rows=rows, layers=ov["layers"],
                              grids=grids, as_of=last)
    first = (pd.Timestamp(min(b.r.seg_all["week_start"])) + pd.Timedelta(days=6)).year
    csvs = sorted(p.name for p in d.glob("*.csv"))
    variables = ["streamflow (discharge), cubic feet per second", "flow percentile against 1991-2020"]
    if show["quality"]:
        variables += ["water temperature", "specific conductance"]
    if show["watershed"]:
        variables += ["precipitation", "snow-water equivalent", "Drought Severity and Coverage Index"]
    meta = rs.head_meta(site, title=title, desc=desc, path=f"rivers/{b.r.slug}/",
                        image=f"rivers/{b.r.slug}/social.png" if has_card else None,
                        image_alt=f"Map of New Mexico with the {river} and its watershed highlighted, and last week's flow status.",
                        jsonld=rs.river_jsonld(site, river=river, slug=b.r.slug, title=title, desc=desc,
                                               bounds=ov["facts"].bounds, first_year=first, as_of=last, csvs=csvs,
                                               variables=variables))
    (d / "index.html").write_text("".join([
        head(f"{title} | {site['name']}", ex_style + rm.MAP_CSS + ro.CSS, css_href="../assets/site.css", meta=meta,
             icon_href="../assets/icon.svg"),
        '<div class="wrap"><header><nav aria-label="Breadcrumb"><p class="crumb"><a href="../index.html">All rivers</a></p></nav>',
        f"<h1>{esc(river)}</h1>",
        f'<p class="sub">Streamflow, conditions and background by watershed segment. Data through {last:%b} {last.day}, '
        f'{last.year}; generated {esc(generated)}.</p>',
        f'</header><main id="main" tabindex="-1">{tablist}{panels}</main>',
        f'<footer class="foot"><p>{esc(site["name"])}. Built from public data by the New Mexico water data archive; '
        'each tab says where its numbers come from. Data files for this river are listed under Flow, How to read this.</p></footer>',
        "</div>",
        '<script src="data.js"></script><script src="../assets/charts.js"></script>',
        f"<script>{ex_script}</script><script>{SECTION_JS}</script><script>{TABS_JS}</script>"
        f"<script>{rm.ZOOM_JS}</script></body></html>"]))
    below = [x for x in rows if x["cls"] in ("much below normal", "below normal")]
    rated = [x for x in rows if x["cls"]]
    return {"tabs": tab_keys, "segments_rated": len(rated), "segments_below_normal": len(below),
            "dry_days_this_year": {x["segment"]: x["dry"] for x in rows}, "description": desc}


def copy_assets(root: Path) -> None:
    (root / "assets").mkdir(parents=True, exist_ok=True)
    for f in ("site.css", "charts.js", "icon.svg"):
        shutil.copy(ASSETS / f, root / "assets" / f)
