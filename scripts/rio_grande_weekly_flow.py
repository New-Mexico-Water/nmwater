"""Weekly average streamflow (cfs) for each New Mexico segment of the Rio Grande, last 52 weeks.

A segment is the HUC8 watershed a river gauge sits in (the `river_segments` view). Each physical
gauge is published by several agencies, so copies are merged and counted once. A segment's value
for a week is the mean of its gauges' weekly means; the by-gauge table is written beside it so the
gauges behind every number can be seen.

    uv run python scripts/rio_grande_weekly_flow.py            (or: just report-rio-grande-flow)
    uv run python scripts/rio_grande_weekly_flow.py --weeks 26 --no-notes

Writes
    reports/rio_grande_weekly_flow_by_segment.csv   segment, week_start, mean_cfs, n_gauges, min, max
    reports/rio_grande_weekly_flow_by_gauge.csv     segment, gauge, source_used, week_start, mean_cfs, n_days
    reports/rio_grande_flow_copy_agreement.csv      lower-priority copies compared with the one used
    reports/rio_grande_weekly_flow_chart.html       self-contained line chart of the segment weeks
    reports/rio_grande_flow_all_weeks_by_segment.csv  the same segment weeks for the whole record (1889 on)
    reports/rio_grande_flow_explorer.html           interactive page over the whole record: segment
                                                    toggles, time-range presets, custom dates,
                                                    weekly/monthly/yearly means (open in a browser)
    docs/reports/rio-grande-weekly-flow.md          notes: method, gauges, agreement, caveats (generated)

Reads only the DuckDB catalog; no network. Needs a catalog build after `nmwater update`.

Method
  1. Gauges: sites on the Rio Grande itself (river_name = 'Rio Grande', river_method = 'snap',
     stream sites, not canals or diversions, with "Rio Grande" in the gauge name) in the six New
     Mexico segments.
  2. Copies of one gauge (USGS, USBR HydroData, USACE CWMS, Colorado DWR) are merged with
     site_links (same sensor or colocated within 250 m).
  3. Daily mean per copy: the source's own daily mean where it has one, else the mean of its
     sub-daily readings over the America/Denver day (at least half the expected readings). Daily
     and sub-daily rows are never averaged together; that halves a USGS mean when its 15-minute
     record covers only part of the year.
  4. One value per gauge and day, from the first copy in PRIORITY that has one.
  5. Weeks run Monday to Sunday. A gauge-week is the mean of its daily means when at least
     MIN_DAYS of 7 exist. A segment-week is the mean of its gauges' values that week.
  6. Only gauges with at least MIN_WEEKS reported weeks count in a segment mean; shorter records
     (new gauges, the 30-day NWPS feed) stay in the by-gauge file, flagged, so the number of gauges
     behind a segment does not jump around from week to week.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nmwater.reports.river_flow import (  # noqa: E402
    DISAGREE_PCT,
    MIN_DAYS,
    MIN_WEEKS,
    PCT_MIN_CFS,
    PRIORITY,
    copy_agreement,
    eligible_gauges,
    gauge_weekly,
    group_copies,
    pick_copy,
    read_daily,
    week_starts,
)
from nmwater.reports.river_flow import segment_weekly as _segment_weekly  # noqa: E402

# upstream to downstream
SEGMENTS = ["Upper Rio Grande", "Rio Grande-Santa Fe", "Rio Grande-Albuquerque",
            "Elephant Butte Reservoir", "Caballo", "El Paso-Las Cruces"]
# ---------------------------------------------------------------------------- pure pieces
# Shared with every river's report: nmwater/reports/river_flow.py.
def segment_weekly(gw: pd.DataFrame, segment_of: dict[str, str]) -> pd.DataFrame:
    """Mean of the gauges' weekly means per segment, in SEGMENTS order."""
    return _segment_weekly(gw, segment_of, SEGMENTS)


# ---------------------------------------------------------------------------- catalog reads
def read_gauges(con) -> pd.DataFrame:
    seg = ",".join(f"'{s}'" for s in SEGMENTS)
    return con.sql(f"""
        SELECT r.site_uid, r.huc8_name AS segment, s.source, s.name, s.state, r.totdasqkm
        FROM river_segments r JOIN sites s USING (site_uid)
        WHERE r.river_name = 'Rio Grande' AND r.river_method = 'snap' AND r.site_type = 'stream'
          AND r.huc8_name IN ({seg}) AND coalesce(s.state, 'NM') NOT IN ('CO', 'TX')
          AND s.name ILIKE '%rio grande%'      -- drains and ditches that snap to the river are not river gauges
          AND s.site_uid IN (SELECT site_uid FROM site_variables WHERE variable = 'discharge')""").df()


# ---------------------------------------------------------------------------- notes
def _fmt(x, nd=0) -> str:
    return "" if pd.isna(x) else f"{x:,.{nd}f}"


def write_notes(path: Path, weeks, gauges, gw, seg, agree, span, elig) -> None:
    first, last = weeks[0], weeks[-1] + timedelta(days=6)
    L = ["# Rio Grande weekly streamflow by segment", "",
         f"Weekly mean discharge in cubic feet per second (cfs) for each New Mexico segment of the Rio "
         f"Grande, {first:%b %-d, %Y} to {last:%b %-d, %Y} ({len(weeks)} Monday-to-Sunday weeks). "
         "Generated by `scripts/rio_grande_weekly_flow.py`; do not edit by hand.", "",
         "Files: `reports/rio_grande_weekly_flow_by_segment.csv`, "
         "`reports/rio_grande_weekly_flow_by_gauge.csv`, `reports/rio_grande_flow_copy_agreement.csv`, and a "
         "self-contained chart, `reports/rio_grande_weekly_flow_chart.html`. For the whole record (1889 on) "
         "with segment toggles, time ranges and weekly/monthly/yearly means, open "
         "`reports/rio_grande_flow_explorer.html` in a browser; its data is "
         "`reports/rio_grande_flow_all_weeks_by_segment.csv`.", "",
         "## Summary", "",
         "| Segment | Gauges | Mean cfs, year | Weeks reported | Highest week (cfs) | Week starting | Lowest week (cfs) | Week starting |",
         "|---|---|---|---|---|---|---|---|"]
    for s in SEGMENTS:
        d = seg[seg["segment"] == s]
        if d.empty:
            L.append(f"| {s} | 0 | | 0 | | | | |")
            continue
        hi, lo = d.loc[d["mean_cfs"].idxmax()], d.loc[d["mean_cfs"].idxmin()]
        n_g = len(set(gauges[gauges["segment"] == s]["gauge"]) & elig)
        L.append(f"| {s} | {n_g} | {_fmt(d['mean_cfs'].mean())} | {len(d)} | {_fmt(hi['mean_cfs'])} | "
                 f"{hi['week_start']} | {_fmt(lo['mean_cfs'])} | {lo['week_start']} |")
    L += ["", "The year mean is the mean of the weekly segment values that were reported, so a segment with "
          "missing weeks is averaged over the weeks it has.", "",
          "## Method", "",
          "1. **Segments** are the HUC8 watersheds the gauges sit in (the catalog's `river_segments` view), "
          "listed upstream to downstream. HUC8s are watershed units, not the Upper/Middle/Lower Rio Grande "
          "convention.",
          "2. **Gauges** are stream sites on the Rio Grande itself (not canals, diversions or tributaries), "
          "in the six New Mexico segments.",
          "3. **Copies of one gauge** (USGS, USBR HydroData, USACE CWMS, Colorado DWR publish many of the "
          "same gauges) are merged using `site_links`, so each physical gauge counts once.",
          "4. **Daily mean per copy** is the source's own daily mean where it has one, otherwise the mean of "
          "its sub-daily readings over the America/Denver day (at least half the expected readings). "
          "Daily and sub-daily rows are never averaged together: doing so halves a USGS mean when its "
          "15-minute record covers only part of the year.",
          f"5. **One value per gauge and day** comes from the first copy in this order: "
          f"{' > '.join(PRIORITY)}.",
          f"6. **Weeks** run Monday to Sunday. A gauge-week is the mean of its daily means when at least "
          f"{MIN_DAYS} of 7 days exist. A segment-week is the mean of its gauges' values that week, with the "
          "gauge count, minimum and maximum kept in the CSV.", "",
          f"Data through {span[1]}; the week in progress is excluded. Catalog data as of the last update.", "",
          "## Gauges", "",
          f"A gauge counts in its segment's mean when it has at least {MIN_WEEKS} reported weeks; shorter "
          "records are kept in the by-gauge CSV (`in_segment_mean` = False).", "",
          "| Segment | Gauge | Copies | Source used most | Weeks reported | Days of data | In segment mean |",
          "|---|---|---|---|---|---|---|"]
    silent = []
    for s in SEGMENTS:
        for gname, gd in gauges[gauges["segment"] == s].groupby("gauge", sort=False):
            w = gw[gw["gauge"] == gname]
            if w.empty:
                silent.append(gname)
                continue
            used = w["source_used"].value_counts().index[0]
            L.append(f"| {s} | {gname} | {', '.join(sorted(set(gd['source'])))} | {used} | {len(w)} | "
                     f"{int(w['n_days'].sum())} | {'yes' if gname in elig else 'no'} |")
    if silent:
        L += ["", f"{len(silent)} more gauges on the river in these segments have no daily data in the period "
              "(discontinued, or not yet reporting) and are left out of the table."]
    L += ["", "## How well the copies agree", "",
          f"Each copy not used was compared with the value used, day by day. A day counts as different when "
          f"it is off by more than {DISAGREE_PCT:.0f}% and 5 cfs. The median percent difference uses only days "
          f"when the flow used was at least {PCT_MIN_CFS:.0f} cfs.", ""]
    if agree.empty:
        L.append("No gauge had more than one copy with overlapping days.")
    else:
        tot = agree["overlap_days"].sum()
        off = int(agree["days_off"].sum())
        L += [f"Across {len(agree)} gauge-copy pairs and {int(tot):,} overlapping days, {off:,} days "
              f"({off / tot * 100:.1f}%) differed. The pairs with the most differing days:", "",
              "| Gauge | Copy | Overlap days | Median % difference | Days different |", "|---|---|---|---|---|"]
        for r in agree.sort_values("days_off", ascending=False).head(10).itertuples():
            L.append(f"| {r.gauge} | {r.source} | {r.overlap_days} | {r.median_abs_pct_diff} | {int(r.days_off)} |")
    L += ["", "## Caveats", "",
          "- A segment value is an average over gauges at different points on the river. Diversions, "
          "returns and tributary inflow between gauges move it, so it is not the flow at any one place. "
          "A segment with one gauge is that gauge.",
          "- The number of gauges reporting can change from week to week (see `n_gauges` in the CSV), "
          "which can move a segment's value without any change in the river.",
          "- Provisional data: recent USGS, USBR and Corps values can be revised. Re-run after `nmwater update`.",
          "- Elephant Butte Reservoir's segment includes gauges at San Marcial and in the reservoir's "
          "Narrows, upstream of the dam. Flow below the dam is reported under Caballo.",
          "- Daily values are not clipped or otherwise cleaned here beyond the catalog's `observations_clean` "
          "view; disagreements between copies are reported above rather than corrected.", ""]
    path.write_text("\n".join(L))


def write_chart(path: Path, seg: pd.DataFrame, gw: pd.DataFrame, elig: set[str], segment_of: dict[str, str],
                weeks: list[date]) -> None:
    """Fill scripts/templates/rio_grande_weekly_flow.html with the segment weeks (one file, no network)."""
    import json

    keys = [w.isoformat() for w in weeks]
    series = []
    for s in SEGMENTS:
        d = seg[seg["segment"] == s].assign(k=lambda x: x["week_start"].astype(str)).set_index("k")
        n_g = len({g for g in elig if segment_of.get(g) == s})
        series.append({"name": s, "gauges": n_g,
                       "v": [round(float(d.loc[k, "mean_cfs"])) if k in d.index else None for k in keys],
                       "n": [int(d.loc[k, "n_gauges"]) if k in d.index else None for k in keys]})
    last = weeks[-1] + timedelta(days=6)
    html = (ROOT / "scripts/templates/rio_grande_weekly_flow.html").read_text()
    html = (html.replace("__DATA__", json.dumps({"weeks": keys, "series": series}, separators=(",", ":")))
            .replace("__FIRST__", f"{weeks[0]:%b} {weeks[0].day}, {weeks[0].year}")
            .replace("__LAST__", f"{last:%b} {last.day}, {last.year}")
            .replace("__DATA_THROUGH__", f"{last:%b} {last.day}, {last.year}"))
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="margin:0">'
            f"{html}</body></html>")
    path.write_text(page)


def write_explorer(path: Path, seg: pd.DataFrame, weeks: list[date], gauges_by_segment: dict[str, int]) -> None:
    """Fill scripts/templates/rio_grande_flow_explorer.html with every segment week on record."""
    import json

    keys = [w.isoformat() for w in weeks]
    pos = {k: i for i, k in enumerate(keys)}
    series = []
    for s in SEGMENTS:
        d = seg[seg["segment"] == s]
        v = [None] * len(keys)
        n = [None] * len(keys)
        for k, m, c in zip(d["week_start"].astype(str), d["mean_cfs"], d["n_gauges"]):
            v[pos[k]] = round(float(m), 1)
            n[pos[k]] = int(c)
        series.append({"name": s, "gauges": gauges_by_segment.get(s, 0), "v": v, "n": n})
    last = weeks[-1] + timedelta(days=6)
    html = (ROOT / "scripts/templates/rio_grande_flow_explorer.html").read_text()
    html = (html.replace("__DATA__", json.dumps({"weeks": keys, "series": series}, separators=(",", ":")))
            .replace("__LAST__", f"{last:%b} {last.day}, {last.year}"))
    path.write_text(html)


def all_time(con, g: pd.DataFrame, latest: date, segment_of: dict[str, str]):
    """Segment weeks for the whole record, same rules as the 52-week report."""
    first = con.sql("SELECT min(datetime_utc)::DATE FROM observations_clean WHERE variable = 'discharge' "
                    "AND interval = 'daily' AND statistic = 'mean' AND site_uid IN "
                    f"({','.join(repr(u) for u in g['site_uid'])})").fetchone()[0]
    n_weeks = ((latest - first).days // 7) + 1
    weeks = [w for w in week_starts(latest, n_weeks) if w >= first - timedelta(days=6)]
    raw = read_daily(con, list(g["site_uid"]), weeks[0], weeks[-1] + timedelta(days=6))
    daily = raw.merge(g[["site_uid", "source", "gauge"]], on="site_uid")[["gauge", "date", "source", "site_uid", "cfs"]]
    daily["date"] = pd.to_datetime(daily["date"])
    gw = gauge_weekly(pick_copy(daily), weeks)
    elig = eligible_gauges(gw)
    seg = segment_weekly(gw[gw["gauge"].isin(elig)], segment_of)
    counts = {s: len({x for x in elig if segment_of.get(x) == s}) for s in SEGMENTS}
    return weeks, seg, counts


# ---------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data/duckdb/nmwater.duckdb"))
    ap.add_argument("--weeks", type=int, default=52)
    ap.add_argument("--no-notes", action="store_true")
    a = ap.parse_args()

    con = duckdb.connect(a.db, read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    g = read_gauges(con)
    links = con.sql("SELECT site_uid_a, site_uid_b FROM site_links").df()
    group_of = group_copies(list(g["site_uid"]), links)
    g["group"] = g["site_uid"].map(group_of)

    # the gauge name and segment come from the highest-priority copy in each group
    rank = {s: i for i, s in enumerate(PRIORITY)}
    best = g.assign(_r=g["source"].map(rank).fillna(99)).sort_values("_r").drop_duplicates("group")
    label = best.set_index("group")
    g["gauge"] = g["group"].map(label["name"].str.strip().str.rstrip(".").str.title().str.replace(", Nm", ", NM"))
    g["segment"] = g["group"].map(label["segment"])
    segment_of = dict(zip(g["gauge"], g["segment"]))

    latest = con.sql("SELECT max(datetime_utc)::DATE FROM observations_clean WHERE variable = 'discharge' "
                     "AND interval = 'daily' AND statistic = 'mean' AND site_uid IN "
                     f"({','.join(repr(u) for u in g['site_uid'])})").fetchone()[0]
    weeks = week_starts(latest, a.weeks)
    start, end = weeks[0], weeks[-1] + timedelta(days=6)
    raw = read_daily(con, list(g["site_uid"]), start, end)
    daily = raw.merge(g[["site_uid", "source", "gauge"]], on="site_uid")[["gauge", "date", "source", "site_uid", "cfs"]]
    daily["date"] = pd.to_datetime(daily["date"])

    chosen = pick_copy(daily)
    gw = gauge_weekly(chosen, weeks)
    elig = eligible_gauges(gw)
    seg = segment_weekly(gw[gw["gauge"].isin(elig)], segment_of)
    agree = copy_agreement(daily, chosen)

    out = ROOT / "reports"
    seg.round({"mean_cfs": 1, "min_cfs": 1, "max_cfs": 1}).to_csv(out / "rio_grande_weekly_flow_by_segment.csv", index=False)
    gw.assign(segment=gw["gauge"].map(segment_of), in_segment_mean=gw["gauge"].isin(elig)).round({"mean_cfs": 1})[
        ["segment", "gauge", "source_used", "week_start", "mean_cfs", "n_days", "in_segment_mean"]
    ].sort_values(["segment", "gauge", "week_start"], key=lambda c: c.map({s: i for i, s in enumerate(SEGMENTS)}) if c.name == "segment" else c
                  ).to_csv(out / "rio_grande_weekly_flow_by_gauge.csv", index=False)
    agree.to_csv(out / "rio_grande_flow_copy_agreement.csv", index=False)
    write_chart(out / "rio_grande_weekly_flow_chart.html", seg, gw, elig, segment_of, weeks)
    weeks_all, seg_all, counts_all = all_time(con, g, latest, segment_of)
    seg_all.round({"mean_cfs": 1, "min_cfs": 1, "max_cfs": 1}).to_csv(
        out / "rio_grande_flow_all_weeks_by_segment.csv", index=False)
    write_explorer(out / "rio_grande_flow_explorer.html", seg_all, weeks_all, counts_all)
    print(f"all time: {len(weeks_all)} weeks from {weeks_all[0]}, {len(seg_all)} segment-weeks")
    if not a.no_notes:
        write_notes(ROOT / "docs/reports/rio-grande-weekly-flow.md", weeks, g[["segment", "gauge", "source"]], gw, seg,
                    agree, (str(start), str(chosen["date"].max().date())), elig)
    print(f"{len(weeks)} weeks {start} to {end}; {g['gauge'].nunique()} gauges; "
          f"{len(seg)} segment-weeks, {len(gw)} gauge-weeks")
    for s in SEGMENTS:
        d = seg[seg["segment"] == s]
        n_g = len(set(g[g["segment"] == s]["gauge"]) & elig)
        print(f"  {s:26s} gauges {n_g:2d}  weeks {len(d):2d}  mean {d['mean_cfs'].mean():7.1f} cfs")


if __name__ == "__main__":
    main()
