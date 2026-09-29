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
FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="stylesheet" '
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
    return Bundle(r, normal, dry, quality, clim, drought, huc8_of)


# ---------------------------------------------------------------------------- data for the pages
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
            "months": [_ms(x) for x in p["date"]], "precip": [_num(v, 2) for v in p["value"]], "precip_normal": [_num(v, 2) for v in p["normal"]],
            "tmonths": [_ms(x) for x in t["date"]], "tanom": [_num(v, 1) for v in t["anomaly"]],
            "swe_weeks": wk, "swe_median": [_num(med.get(w), 1) for w in wk],
            "swe_winters": [{"wy": int(y), "values": [_num(sw[(sw["wy"] == y) & (sw["dwy"] == w)]["value"].mean(), 1) for w in wk]} for y in winters],
            "dsci_dates": [_ms(x) for x in dd["date"]], "dsci": [_num(v, 0) for v in dd["dsci"]]})
    return {"normal": n, "drying": dr, "temp": temp, "salinity": sal, "watershed": ws, "colors": colors,
            "as_of": r.as_of.isoformat()}


# ---------------------------------------------------------------------------- overview
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
        p3 = [v for v in ws["precip"][-3:] if v is not None]
        n3 = [v for v in ws["precip_normal"][-3:] if v is not None]
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


def sparkline(vals: list) -> str:
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return ""
    lo, hi = min(v), max(v)
    w, h = 120, 26
    pts, pen, d = len(vals), False, ""
    for i, x in enumerate(vals):
        if x is None:
            pen = False
            continue
        px = i / (pts - 1) * (w - 2) + 1
        py = h - 2 - (0 if hi == lo else (x - lo) / (hi - lo)) * (h - 4)
        d += f"{'L' if pen else 'M'}{px:.1f} {py:.1f}"
        pen = True
    return f'<svg class="spark" viewBox="0 0 {w} {h}" aria-hidden="true"><path d="{d}"/></svg>'


def status_table(rows: list[dict], links: dict | None = None) -> str:
    def st(c, p):
        if not c:
            return '<span class="st none">no rating</span>'
        return f'<span class="st {c.replace(" ", "-")}">{esc(c)}</span> <small>{p:.0f}th</small>'
    L = ['<div class="scroll"><table class="status"><thead><tr><th>Segment, upstream to downstream</th><th>Flow last week</th>'
         '<th>Compared with normal</th><th>Last 52 weeks</th><th>Dry days this year</th><th>Water temp, latest</th>'
         '<th>Salinity, latest decade</th><th>Precip, last 3 months</th></tr></thead><tbody>']
    for x in rows:
        L.append(
            f'<tr><td><span class="key" style="--c:{x["color"]}"></span>{esc(x["segment"])}</td>'
            f'<td class="n">{"" if x["flow"] is None else f"{x["flow"]:,.0f} cfs"}</td>'
            f'<td>{st(x["cls"], x["pct"] if x["pct"] is not None else 0)}</td>'
            f'<td>{sparkline(x["spark"])}</td>'
            f'<td class="n">{"" if x["dry"] is None else x["dry"]}'
            f'{"" if x["dry_median"] is None else f" <small>normal {x["dry_median"]:.0f}</small>"}</td>'
            f'<td class="n">{"" if x["temp"] is None else f"{x["temp"]:.1f} °C"}</td>'
            f'<td class="n">{"" if not x["sc"] else f"{x["sc"][0]:,.0f} <small>µS/cm, {x["sc"][1]}s</small>"}</td>'
            f'<td class="n">{"" if x["precip3"] is None else f"{x["precip3"]:.0f}% <small>of normal</small>"}</td></tr>')
    L.append("</tbody></table></div>")
    return "".join(L)


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
      const h = document.createElement("h4"); h.textContent = s.name; const p = document.createElement("p");
      p.textContent = s.median == null ? "No 1991-2020 normal." : "Normal (1991-2020 median): " + s.median + " days a year.";
      const c = document.createElement("div"); card.append(h, p, c); box.appendChild(card);
      if (!s.dry.some(v => v)) { c.className = "note"; c.textContent = "No dry days recorded since " + R.years[0] + "."; return; }
      C.bars(c, { x: R.years, xType: "year", values: s.dry, color: s.color, name: "Days any gauge was dry", unit: "days", height: 150,
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
      C.line($("s-profile"), { x: S.segments.map((_, i) => i), xType: "cat", labels: S.segments.map(s => s.replace(/^Rio Grande-/, "").replace(" Reservoir", "").replace("Upper Rio Grande", "Upper")), unit: "µS/cm",
        label: "Median specific conductance by segment and decade",
        series: keep.map((d, k) => ({ name: d + "s", values: S.median[S.decades.indexOf(d)], color: pal[k % pal.length], points: true, joined: true, width: 1.5 })) });
    }
  }
  // watershed
  if ($("w-seg")) {
    const W = D.watershed, sel = $("w-seg"); opt(sel, W.segments, s => s.name + " (HUC " + s.huc8 + ")");
    const draw = () => { const s = W.segments[+sel.value];
      C.bars($("w-precip"), { x: s.months, xType: "time", xRes: "month", values: s.precip, color: "var(--s1)", name: "Precipitation", unit: "in",
        ref: { name: "Normal (1991-2020 median)", values: s.precip_normal }, height: 200, tickFmt: m => new Intl.DateTimeFormat("en-US", { month: "short", year: "2-digit", timeZone: "UTC" }).format(m) });
      C.bars($("w-temp"), { x: s.tmonths, xType: "time", xRes: "month", values: s.tanom, colorFn: v => v >= 0 ? "var(--neg)" : "var(--pos)", name: "Difference from normal", unit: "°C", height: 200,
        tickFmt: m => new Intl.DateTimeFormat("en-US", { month: "short", year: "2-digit", timeZone: "UTC" }).format(m) });
      const pal = ["var(--s3)", "var(--s4)", "var(--s2)"];
      C.line($("w-swe"), { x: s.swe_weeks, xType: "cat", labels: s.swe_weeks.map(w => new Intl.DateTimeFormat("en-US", { month: "short", timeZone: "UTC" }).format(Date.UTC(2021, 9, 1) + w * 7 * 864e5)),
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
    return ('<h2>Watershed conditions</h2>'
            '<p class="hint">Averaged over each segment\'s HUC8 watershed: precipitation and air temperature from PRISM (monthly, '
            '4 km), snow-water equivalent from SNODAS (weekly, 1 km, from 2004), and drought from the US Drought Monitor\'s '
            'Drought Severity and Coverage Index for the larger HUC4 basin (0 = no drought, 500 = all of the basin in '
            'exceptional drought).</p>'
            '<div class="controls"><label for="w-seg">Segment</label><select id="w-seg"></select></div>'
            '<div class="grid2"><div><h3>Precipitation by month</h3><div id="w-precip"></div></div>'
            '<div><h3>Air temperature, difference from normal</h3><div id="w-temp"></div></div>'
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


def head(title: str, extra_style: str = "", css_href: str = "assets/site.css") -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width, initial-scale=1"><title>{esc(title)}</title>{FONTS}'
            f'<link rel="stylesheet" href="{css_href}">' + (f"<style>{extra_style}</style>" if extra_style else "")
            + "</head><body>")


TABS_JS = r"""
(function () {
  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  const show = (id, focus) => {
    tabs.forEach(t => { const on = t.dataset.tab === id; t.setAttribute("aria-selected", String(on)); t.tabIndex = on ? 0 : -1;
      document.getElementById("panel-" + t.dataset.tab).hidden = !on; if (on && focus) t.focus(); });
    if (location.hash.slice(1) !== id) history.replaceState(null, "", "#" + id);
  };
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
  window.addEventListener("hashchange", () => { const h = location.hash.slice(1); if (tabs.some(t => t.dataset.tab === h)) show(h); });
  const h = location.hash.slice(1);
  show(tabs.some(t => t.dataset.tab === h) ? h : tabs[0].dataset.tab);
})();
"""


def gauge_points(con, b: Bundle) -> pd.DataFrame:
    """One point per eligible gauge: name, segment, lat, lon (from the first copy with coordinates)."""
    g = b.r.gauges[b.r.gauges["gauge"].isin(b.r.elig)]
    ids = ",".join("'" + u.replace("'", "''") + "'" for u in g["site_uid"])
    ll = con.sql(f"SELECT site_uid, lat, lon FROM sites WHERE site_uid IN ({ids})").df()
    g = g.merge(ll, on="site_uid").dropna(subset=["lat", "lon"]).drop_duplicates("gauge")
    return g.rename(columns={"gauge": "name"})[["name", "segment", "lat", "lon"]]


def overview_panel(b: Bundle, rows: list[dict], con, grids: Path, background: dict | None) -> str:
    from . import river_map as rm

    river_id = b.r.gnis_id
    svg, facts = rm.build(river_id, b.r.segments, b.huc8_of, gauge_points(con, b), grids)
    first = (pd.Timestamp(min(b.r.seg_all["week_start"])) + pd.Timedelta(days=6)).year   # the first week can start in late December
    auto = rm.describe(b.r.river, facts, b.r.segments, len(b.r.elig), first, b.r.as_of.year)
    about = []
    if background and background.get("text"):
        for para in str(background["text"]).split("\n\n"):
            if para.strip():
                about.append(f"<p>{esc(' '.join(para.split()))}</p>")
        src = background.get("sources") or []
        about.append(f'<p class="src">Background from general references{": " + esc("; ".join(src)) if src else ""}. '
                     "Not derived from the archive's data.</p>")
    about.append(f"<p>{esc(auto)}</p><p class=\"src\">Generated from the archive: NHDPlus river network, WBD watersheds, "
                 "NHD waterbodies, TIGER urban areas and the report's gauges.</p>")
    legend = ('<ul class="maplegend">' + "".join(
        f'<li><span class="n" style="--c:var(--s{i + 1})">{i + 1}</span>{esc(sg)}</li>' for i, sg in enumerate(b.r.segments))
        + '<li><i></i>river and reservoirs</li></ul>')
    def chip(c, p):
        return ('<span class="st none">no rating</span>' if not c else
                f'<span class="st {c.replace(" ", "-")}">{esc(c)}</span> <small>{p:.0f}th percentile</small>')
    now = ('<section class="card"><h2>Segments now</h2><p class="hint">Last week\'s mean flow, rated against the same week '
           'in 1991-2020.</p><table class="status"><tbody>' + "".join(
               f'<tr><td><span class="n" style="--c:var(--s{i + 1})">{i + 1}</span> {esc(x["segment"])}</td>'
               f'<td class="n">{"" if x["flow"] is None else f"{x["flow"]:,.0f} cfs"}</td>'
               f'<td>{chip(x["cls"], x["pct"] or 0)}</td></tr>' for i, x in enumerate(rows))
           + '</tbody></table><p class="hint" style="margin:10px 0 0">More per segment below, and in the other tabs.</p></section>')
    return (f'<div class="overview-grid"><div style="display:flex;flex-direction:column;gap:18px">'
            f'<section class="card about"><h2>About the {esc(b.r.river)}</h2>{"".join(about)}</section>{now}</div>'
            f'<section class="card"><h2>Map</h2><p class="hint">Segments are the HUC8 watersheds the gauges sit in, '
            f'numbered upstream to downstream. Dots are gauges; hover for names. Dashed line: New Mexico.</p>{svg}{legend}</section></div>'
            f'<section class="card"><h2>Where each segment stands, last week</h2><p class="hint">Upstream to downstream. '
            f'Open a tab above for detail.</p>{status_table(rows)}</section>')


def has_tabs(data: dict) -> dict[str, bool]:
    return {"normal": bool(data["normal"]["gauges"]),
            "quality": bool([t for t in data["temp"]["sites"] if t["season"]]) or bool(data["salinity"]["decades"]),
            "watershed": any(w["precip"] for w in data["watershed"]["segments"])}


def render(con, b: Bundle, d: Path, grids: Path, background: dict | None, generated: str) -> dict:
    """Write <d>/index.html and <d>/data.js for one river. Returns extra manifest fields."""
    from .river_map import MAP_CSS

    data = page_data(b)
    rows = overview_rows(b, data)
    show = has_tabs(data)
    last = b.r.weeks52[-1] + timedelta(days=6)
    tmp = d / ".tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    ex_style, ex_markup, ex_script, ex_issues = explorer_parts(b, tmp)
    shutil.rmtree(tmp)
    tabs = [("overview", "Overview", overview_panel(b, rows, con, grids, background)),
            ("flow", "Flow", ex_markup)]
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
    (d / "index.html").write_text("".join([
        head(f"{b.r.river} River Report" if "river" not in b.r.river.lower() else f"{b.r.river} Report",
             ex_style + MAP_CSS, css_href="../assets/site.css"),
        '<div class="wrap"><header><p class="crumb"><a href="../index.html">All rivers</a></p>',
        f"<h1>{esc(b.r.river)}</h1>",
        f'<p class="sub">Streamflow, conditions and data notes by watershed segment. Data through {last:%b} {last.day}, '
        f'{last.year}; generated {esc(generated)}.</p>',
        "</header>", tablist, panels, "</div>",
        '<script src="data.js"></script><script src="../assets/charts.js"></script>',
        f"<script>{ex_script}</script><script>{SECTION_JS}</script><script>{TABS_JS}</script></body></html>"]))
    below = [x for x in rows if x["cls"] in ("much below normal", "below normal")]
    rated = [x for x in rows if x["cls"]]
    return {"tabs": [k for k, _, _ in tabs], "segments_rated": len(rated), "segments_below_normal": len(below),
            "dry_days_this_year": {x["segment"]: x["dry"] for x in rows}}


def copy_assets(root: Path) -> None:
    (root / "assets").mkdir(parents=True, exist_ok=True)
    for f in ("site.css", "charts.js"):
        shutil.copy(ASSETS / f, root / "assets" / f)
