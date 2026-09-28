"""River streamflow reports: weekly mean cfs per river segment, for every river with gauges.

A segment is the HUC8 watershed a river gauge sits in (the catalog's `river_segments` view). The
same gauge is often published by several agencies; copies are merged and counted once. For each
river this writes, under <out>/rivers/<slug>/:

    index.html                      interactive explorer over the whole record (segment toggles,
                                    time-range presets and dates, weekly/monthly/yearly means)
    all_weeks_by_segment.csv        every segment-week on record
    last_52_weeks_by_segment.csv    the most recent 52 complete weeks
    last_52_weeks_by_gauge.csv      the gauge-weeks behind them
    copy_agreement.csv              lower-priority copies of each gauge compared with the one used
    notes.md                        method, gauges, agreement, caveats

and <out>/rivers/index.html plus <out>/rivers/manifest.json listing every river. The whole
<out>/rivers tree is built in a temporary directory and swapped in at the end, so a failed or
interrupted run never leaves a half-written site.

Method
  1. Gauges: stream sites snapped to the river's own reaches (river_method = 'snap'), whose
     name contains the river's distinctive word (drains and ditches that sit on the river are
     not river gauges), with daily discharge. Optional per-river state filter (config).
  2. Copies of one gauge are merged with site_links (same sensor or colocated within 250 m).
  3. Daily mean per copy: the source's own daily mean, else the mean of its sub-daily readings
     over the America/Denver day (at least half the expected readings). Daily and sub-daily rows
     are never averaged together.
  4. One value per gauge and day from the first copy in PRIORITY.
  5. Weeks run Monday to Sunday; a gauge-week needs MIN_DAYS of 7. A gauge counts in its
     segment when it has MIN_WEEKS reported weeks. A segment-week is the mean of its gauges.
  6. Segments are ordered upstream to downstream by the median drainage area of their gauges.
"""

from __future__ import annotations

import html
import json
import logging
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

log = logging.getLogger("nmwater.reports.river_flow")

TEMPLATES = Path(__file__).parent / "templates"
PRIORITY = ["usgs", "usbr_hydrodata", "usace_cwms", "codwr", "ose_meas", "nwps", "usbr_albuq"]
MIN_DAYS = 4                 # days of 7 needed for a gauge-week
MIN_WEEKS = 26               # weeks a gauge needs to count in segment means (short records jitter n_gauges)
MIN_COVERAGE = 0.5           # share of expected sub-daily readings needed for a day
DISAGREE_PCT = 10.0          # a copy "differs" on a day when it is off by more than this and 5 cfs
PCT_MIN_CFS = 10.0           # percent differences are computed only on days the flow used is at least this
SUBDAILY_PER_DAY = {"5min": 288, "15min": 96, "hourly": 24}
STATE_FIPS = {"35": "NM", "08": "CO", "48": "TX", "04": "AZ", "40": "OK", "49": "UT"}
MAX_SEGMENTS = 8             # the chart palette has eight validated colors
GENERIC_WORDS = {"river", "creek", "rio", "arroyo", "fork", "north", "south", "east", "west", "middle", "branch",
                 "wash", "de", "del", "los", "las", "la", "el", "the", "canyon", "draw", "little", "rito", "canada"}


# ---------------------------------------------------------------------------- pure pieces
def week_starts(last_data_day: date, n_weeks: int = 52) -> list[date]:
    """Mondays of the last n complete Monday-to-Sunday weeks ending on or before last_data_day."""
    last_sunday = last_data_day - timedelta(days=(last_data_day.weekday() + 1) % 7)
    last_monday = last_sunday - timedelta(days=6)
    return [last_monday - timedelta(weeks=i) for i in range(n_weeks - 1, -1, -1)]


def group_copies(uids: list[str], links: pd.DataFrame) -> dict[str, str]:
    """Union-find over site_links: each copy of a gauge maps to one group id (its smallest uid)."""
    parent = {u: u for u in uids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in links[["site_uid_a", "site_uid_b"]].itertuples(index=False):
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    return {u: find(u) for u in uids}


def pick_copy(daily: pd.DataFrame) -> pd.DataFrame:
    """One row per (gauge, day): the copy earliest in PRIORITY (ties broken by site id, so a source
    that publishes two versions of a gauge always yields the same one). daily has gauge, date,
    source, cfs and optionally site_uid."""
    rank = {s: i for i, s in enumerate(PRIORITY)}
    d = daily.assign(_r=daily["source"].map(rank).fillna(len(PRIORITY)))
    keys = ["gauge", "date", "_r"] + (["site_uid"] if "site_uid" in d.columns else [])
    d = d.sort_values(keys, kind="stable").drop_duplicates(["gauge", "date"])
    return d.drop(columns="_r")


def gauge_weekly(chosen: pd.DataFrame, weeks: list[date], min_days: int = MIN_DAYS) -> pd.DataFrame:
    """Weekly mean per gauge. chosen has gauge, date, source, cfs. Rows below min_days are dropped."""
    d = chosen.copy()
    d["date"] = pd.to_datetime(d["date"])
    first = pd.Timestamp(weeks[0])
    d = d[(d["date"] >= first) & (d["date"] < pd.Timestamp(weeks[-1]) + pd.Timedelta(days=7))]
    d["week_start"] = (d["date"] - pd.to_timedelta(d["date"].dt.weekday, unit="D")).dt.date
    g = d.groupby(["gauge", "week_start"])
    out = g.agg(mean_cfs=("cfs", "mean"), n_days=("cfs", "size"),
                source_used=("source", lambda s: s.value_counts().index[0])).reset_index()
    return out[out["n_days"] >= min_days].reset_index(drop=True)


def eligible_gauges(gw: pd.DataFrame, min_weeks: int = MIN_WEEKS) -> set[str]:
    """Gauges with enough reported weeks to count in a segment mean."""
    n = gw.groupby("gauge")["week_start"].nunique()
    return set(n[n >= min_weeks].index)


def segment_weekly(gw: pd.DataFrame, segment_of: dict[str, str], segments: list[str]) -> pd.DataFrame:
    """Mean of the gauges' weekly means per segment, with gauge count and range, segments in order."""
    d = gw.assign(segment=gw["gauge"].map(segment_of))
    g = d.groupby(["segment", "week_start"])["mean_cfs"]
    out = g.agg(mean_cfs="mean", n_gauges="size", min_cfs="min", max_cfs="max").reset_index()
    order = {s: i for i, s in enumerate(segments)}
    out = out[out["segment"].isin(order)]
    return out.sort_values(["segment", "week_start"], key=lambda c: c.map(order) if c.name == "segment" else c
                           ).reset_index(drop=True)


def copy_agreement(daily: pd.DataFrame, chosen: pd.DataFrame) -> pd.DataFrame:
    """Compare every copy that was not used with the value that was, day by day."""
    used = chosen.rename(columns={"source": "used_source", "cfs": "used_cfs"})
    used = used[["gauge", "date", "used_source", "used_cfs"]]
    m = daily.merge(used, on=["gauge", "date"])
    m = m[m["source"] != m["used_source"]].copy()
    if m.empty:
        return pd.DataFrame(columns=["gauge", "source", "overlap_days", "median_abs_pct_diff", "days_off"])
    # percent differences are only meaningful away from zero flow (1 cfs vs 0 cfs is not "100% off")
    m["pct"] = (m["cfs"] - m["used_cfs"]).abs() / m["used_cfs"].abs() * 100
    m.loc[m["used_cfs"].abs() < PCT_MIN_CFS, "pct"] = float("nan")
    m["off"] = (m["pct"] > DISAGREE_PCT) & ((m["cfs"] - m["used_cfs"]).abs() > 5)
    g = m.groupby(["gauge", "source"])
    return g.agg(overlap_days=("cfs", "size"), median_abs_pct_diff=("pct", "median"),
                 days_off=("off", "sum")).reset_index().round({"median_abs_pct_diff": 1})


def river_tokens(river: str) -> list[str]:
    """Distinctive words of a river name, accent-free and lower case ('Rio Peñasco' -> ['penasco'])."""
    words = re.findall(r"[a-z]+", _plain(river))
    return [w for w in words if w not in GENERIC_WORDS]


# names of ditches, ponds and reservoirs themselves; "below Continental Reservoir" is a river gauge
NON_RIVER = re.compile(r"^(acequia|ditch|canal|lateral)\b|#|\b(dump|pond)\b|\bconveyance channel\b"
                       r"|^(?!.*\b(bl|below|blw|abv|above|nr|near|at|in)\b).*\breservoir$", re.I)


def is_river_gauge(name: str, river: str) -> bool:
    """A site on the river counts as a river gauge when its name carries the river's distinctive word
    and does not name a ditch, pond or reservoir (those measure water taken out, not the river)."""
    if NON_RIVER.search(_plain(name).strip()):
        return False
    toks = river_tokens(river)
    return not toks or any(t in _plain(name) for t in toks)


def _ose_ditch(meta: str | None) -> bool:
    """OSE real-time stations that OSE's meter layer ties to a ditch (rtm_ditch_name) measure a diversion."""
    try:
        v = json.loads(meta or "{}").get("rtm_ditch_name")
    except ValueError:
        return False
    return v not in (None, "", "nan") and str(v).strip() != ""


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _plain(name)).strip("-")


def order_segments(g: pd.DataFrame) -> list[str]:
    """Upstream to downstream: segments sorted by the median drainage area of their gauges."""
    med = g.groupby("segment")["totdasqkm"].median().sort_values(kind="stable")
    return list(med.index)


def _plain(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()


# ---------------------------------------------------------------------------- catalog reads
@dataclass
class River:
    name: str             # GNIS name, e.g. "Rio Hondo"
    gnis_id: str          # NHD GNIS id; two rivers can share a name (Rio Hondo near Taos and near Roswell)
    label: str            # name, plus the basin when another river shares the name
    n_sites: int = 0


def list_rivers(con, min_sites: int = 2) -> list[River]:
    """Rivers (by GNIS id) with at least min_sites stream sites that carry daily discharge."""
    rows = con.sql(f"""
        SELECT f.gnis_name, f.gnis_id, count(DISTINCT r.site_uid) AS n,
               mode(s.basin) AS basin, mode(r.huc8_name) AS huc8
        FROM river_segments r JOIN site_reaches sr USING (site_uid) JOIN flowlines f ON f.comid = sr.comid
        JOIN sites s USING (site_uid) JOIN site_variables sv USING (site_uid)
        WHERE r.river_method = 'snap' AND r.site_type = 'stream' AND f.gnis_id IS NOT NULL AND trim(f.gnis_id) <> ''
          AND sv.variable = 'discharge' AND sv.interval = 'daily'
        GROUP BY 1, 2 HAVING count(DISTINCT r.site_uid) >= {int(min_sites)}
        ORDER BY n DESC, 1""").fetchall()
    out: list[River] = []
    for name, gid, n, basin, huc8 in rows:
        name = _name_fix(name)
        out.append(River(name, str(gid), name, int(n)))
        out[-1].basin, out[-1].huc8 = basin, huc8          # type: ignore[attr-defined]
    return label_rivers(out)


def label_rivers(rivers: list[River]) -> list[River]:
    """Give rivers that share a name a distinguishing label: '(basin)', else '(watershed)', else '(GNIS id)'."""
    by_name: dict[str, list[River]] = {}
    for r in rivers:
        by_name.setdefault(r.name, []).append(r)
    for same in by_name.values():
        if len(same) == 1:
            continue
        for key in ("basin", "huc8", "gnis_id"):
            vals = [getattr(r, key, None) for r in same]
            if all(vals) and len(set(vals)) == len(vals):
                for r, v in zip(same, vals):
                    r.label = f"{r.name} ({v})"
                break
    return rivers


def _name_fix(name: str) -> str:
    """NHD sometimes stores 'ñ' as '¿' (Ca¿ones Creek)."""
    return re.sub(r"(?<=\w)\u00bf(?=\w)", "\u00f1", str(name).strip())


def read_gauges(con, river: River, states: list[str] | None = None) -> pd.DataFrame:
    """Stream sites on this river's reaches with discharge data, filtered to river gauges."""
    # The state comes from the county the site falls in (a spatial join), not the agency's state
    # field: some agencies leave it blank or mislabel a Colorado gauge as NM.
    st = ""
    if states:
        st = "AND coalesce(fips.st, s.state, 'NM') IN (" + ",".join(f"'{x}'" for x in states) + ")"
    fips_case = " ".join(f"WHEN '{k}' THEN '{v}'" for k, v in STATE_FIPS.items())
    g = con.execute(f"""
        WITH fips AS (SELECT site_uid, CASE left(region_id, 2) {fips_case} END AS st
                      FROM site_regions WHERE region_type = 'county')
        SELECT r.site_uid, r.huc8_name AS segment, s.source, s.name, coalesce(fips.st, s.state) AS state, r.totdasqkm,
               s.raw_metadata
        FROM river_segments r JOIN sites s USING (site_uid) LEFT JOIN fips USING (site_uid)
        JOIN site_reaches sr USING (site_uid) JOIN flowlines f ON f.comid = sr.comid
        WHERE f.gnis_id = ? AND r.river_method = 'snap' AND r.site_type = 'stream'
          AND r.huc8_name IS NOT NULL {st}
          AND s.site_uid IN (SELECT site_uid FROM site_variables WHERE variable = 'discharge')""",
                    [river.gnis_id]).df()
    keep = [is_river_gauge(n, river.name) and not (src == "ose_meas" and _ose_ditch(m))
            for n, src, m in zip(g["name"], g["source"], g["raw_metadata"])]
    return g[keep].drop(columns="raw_metadata").reset_index(drop=True)


def read_daily(con, uids: list[str], start: date, end: date) -> pd.DataFrame:
    """Daily mean cfs per copy: the source's daily mean, else sub-daily readings averaged per Denver day."""
    ids = ",".join("'" + u.replace("'", "''") + "'" for u in uids)
    expected = " ".join(f"WHEN '{k}' THEN {v}" for k, v in SUBDAILY_PER_DAY.items())
    return con.sql(f"""
        WITH daily AS (
          SELECT site_uid, datetime_utc::DATE AS date, avg(value) AS cfs
          FROM observations_clean
          WHERE variable = 'discharge' AND interval = 'daily' AND statistic = 'mean'
            AND site_uid IN ({ids}) AND datetime_utc >= '{start}' AND datetime_utc < '{end + timedelta(days=1)}'
          GROUP BY 1, 2),
        sub AS (
          SELECT site_uid, (datetime_utc AT TIME ZONE 'America/Denver')::DATE AS date, interval,
                 avg(value) AS cfs, count(*) AS n
          FROM observations_clean
          WHERE variable = 'discharge' AND interval IN ('5min', '15min', 'hourly')
            AND site_uid IN ({ids}) AND datetime_utc >= '{start - timedelta(days=1)}'
            AND datetime_utc < '{end + timedelta(days=2)}'
          GROUP BY 1, 2, 3),
        sub_ok AS (
          SELECT site_uid, date, cfs FROM sub
          WHERE date BETWEEN '{start}' AND '{end}'
            AND n >= {MIN_COVERAGE} * CASE interval {expected} END)
        SELECT site_uid, date, cfs FROM daily
        UNION ALL
        SELECT site_uid, date, cfs FROM sub_ok
        WHERE (site_uid, date) NOT IN (SELECT site_uid, date FROM daily)""").df()


# ---------------------------------------------------------------------------- building one river
@dataclass
class RiverReport:
    river: str
    slug: str
    segments: list[str]
    gauges: pd.DataFrame                  # site_uid, segment, source, name, state, totdasqkm, gauge
    weeks_all: list[date]
    seg_all: pd.DataFrame
    gw_all: pd.DataFrame
    elig: set[str]
    weeks52: list[date]
    seg52: pd.DataFrame
    gw52: pd.DataFrame
    agree52: pd.DataFrame
    notes: list[str] = field(default_factory=list)
    dropped_segments: list[str] = field(default_factory=list)
    gnis_id: str = ""
    issues: list = field(default_factory=list)       # river_issues.Finding, found automatically
    data_notes: list = field(default_factory=list)   # hand-written explanations (config/river_notes.yaml)


def build_river(con, river: River, links: pd.DataFrame, states: list[str] | None = None,
                notes: list[str] | None = None, as_of: date | None = None) -> RiverReport | None:
    """as_of: the archive's newest data day; "last 52 weeks" counts back from it for every river, so a
    discontinued gauge does not look current. Defaults to this river's own newest day."""
    g = read_gauges(con, river, states)
    if g.empty:
        return None
    g["group"] = g["site_uid"].map(group_copies(list(g["site_uid"]), links))
    rank = {s: i for i, s in enumerate(PRIORITY)}
    best = g.assign(_r=g["source"].map(rank).fillna(99)).sort_values(["_r", "site_uid"]).drop_duplicates("group")
    label = best.set_index("group")
    names = label["name"].str.strip().str.rstrip(".").str.title().str.replace(r", (Nm|Co|Tx|Az|Ok)\b",
                                                                              lambda m: m.group(0).upper(), regex=True)
    g["gauge"] = g["group"].map(names)
    g["segment"] = g["group"].map(label["segment"])
    segment_of = dict(zip(g["gauge"], g["segment"]))

    uids = list(g["site_uid"])
    ids = ",".join("'" + u.replace("'", "''") + "'" for u in uids)
    first, latest = con.sql(f"""SELECT min(datetime_utc)::DATE, max(datetime_utc)::DATE FROM observations_clean
        WHERE variable = 'discharge' AND interval = 'daily' AND statistic = 'mean' AND site_uid IN ({ids})""").fetchone()
    if first is None:
        return None
    n_weeks = ((latest - first).days // 7) + 1
    weeks_all = [w for w in week_starts(latest, n_weeks) if w >= first - timedelta(days=6)]
    if not weeks_all:
        return None
    raw = read_daily(con, uids, weeks_all[0], weeks_all[-1] + timedelta(days=6))
    daily = raw.merge(g[["site_uid", "source", "gauge"]], on="site_uid")[["gauge", "date", "source", "site_uid", "cfs"]]
    daily["date"] = pd.to_datetime(daily["date"])
    chosen = pick_copy(daily)
    gw_all = gauge_weekly(chosen, weeks_all)
    elig = eligible_gauges(gw_all)
    if not elig:
        return None

    ge = g[g["gauge"].isin(elig)]
    segments = order_segments(ge)
    dropped = []
    if len(segments) > MAX_SEGMENTS:
        weight = gw_all[gw_all["gauge"].isin(elig)].assign(seg=lambda d: d["gauge"].map(segment_of)).groupby("seg").size()
        keep = set(weight.sort_values(ascending=False).index[:MAX_SEGMENTS])
        dropped = [s for s in segments if s not in keep]
        segments = [s for s in segments if s in keep]
    seg_all = segment_weekly(gw_all[gw_all["gauge"].isin(elig)], segment_of, segments)

    weeks52 = week_starts(as_of or latest, 52)
    w0 = pd.Timestamp(weeks52[0])
    gw52 = gw_all[pd.to_datetime(gw_all["week_start"]) >= w0].reset_index(drop=True)
    seg52 = seg_all[pd.to_datetime(seg_all["week_start"]) >= w0].reset_index(drop=True)
    recent = daily[daily["date"] >= w0]
    agree52 = copy_agreement(recent, pick_copy(recent))
    from .river_issues import find_issues

    peaks = con.sql(f"""SELECT site_uid, datetime_utc, value FROM observations_clean
        WHERE variable = 'discharge' AND interval = 'water_year' AND statistic = 'max' AND site_uid IN ({ids})""").df()
    peaks = peaks.merge(g[["site_uid", "gauge"]], on="site_uid")
    peaks["wy"] = pd.to_datetime(peaks["datetime_utc"]).dt.year + (pd.to_datetime(peaks["datetime_utc"]).dt.month >= 10)
    peak_of = peaks.groupby(["gauge", "wy"])["value"].max().to_dict()
    issues = find_issues(daily, chosen, seg_all, as_of or latest, elig, segment_of, PRIORITY, peak_of)
    return RiverReport(river=river.label, slug=slugify(river.label), segments=segments, gauges=g, weeks_all=weeks_all,
                       seg_all=seg_all, gw_all=gw_all, elig=elig, weeks52=weeks52, seg52=seg52, gw52=gw52,
                       agree52=agree52, notes=list(notes or []), dropped_segments=dropped,
                       gnis_id=river.gnis_id, issues=issues)


# ---------------------------------------------------------------------------- writing
def _fmt_day(d: date) -> str:
    return f"{d:%b} {d.day}, {d.year}"


def _gauge_rows(r: RiverReport) -> list[dict]:
    rows = []
    for s in r.segments:
        for gname, gd in r.gauges[r.gauges["segment"] == s].groupby("gauge", sort=False):
            w = r.gw_all[r.gw_all["gauge"] == gname]
            if w.empty:
                continue
            rows.append({"segment": s, "gauge": gname, "copies": ", ".join(sorted(set(gd["source"]))),
                         "first": str(min(w["week_start"])), "last": str(max(w["week_start"])),
                         "weeks": len(w), "in_mean": gname in r.elig})
    return rows


def issues_section(r: RiverReport) -> str:
    """HTML for the 'Data gaps and disparities' card: reviewed notes, then automatic findings."""
    warn = [f for f in r.issues if f.severity == "warn"]
    info = [f for f in r.issues if f.severity != "warn"]
    parts = [f'<p class="hint">{len(r.data_notes)} reviewed note{"s" if len(r.data_notes) != 1 else ""}, '
             f'{len(warn)} finding{"s" if len(warn) != 1 else ""} that can affect the numbers, '
             f'{len(info)} for context. Findings are recomputed on every build.</p>']
    if r.data_notes:
        parts.append('<h3>Reviewed notes</h3><ul class="issues">')
        for n in r.data_notes:
            basis = n.get("basis", "")
            chip = f'<span class="chip {html.escape(basis)}">{html.escape(basis)}</span>' if basis else ""
            parts.append(f'<li>{chip}<b>{html.escape(str(n.get("subject", "")))}</b> '
                         f'{html.escape(str(n.get("text", "")))} <span class="when">Reviewed {html.escape(str(n.get("reviewed", "")))}</span></li>')
        parts.append("</ul>")
    for title, items, cls in (("Can affect the numbers", warn, "warn"), ("Context", info, "info")):
        if not items:
            continue
        parts.append(f'<h3>{title}</h3><ul class="issues">')
        for f in items:
            parts.append(f'<li><span class="chip {cls}">{html.escape(f.kind.replace("_", " "))}</span>'
                         f'<b>{html.escape(f.subject)}</b> {html.escape(f.text)}</li>')
        parts.append("</ul>")
    if not r.issues and not r.data_notes:
        parts.append("<p>No gaps or disparities found.</p>")
    return "".join(parts)


def write_explorer(path: Path, r: RiverReport, generated: str) -> None:
    keys = [w.isoformat() for w in r.weeks_all]
    pos = {k: i for i, k in enumerate(keys)}
    series = []
    for s in r.segments:
        d = r.seg_all[r.seg_all["segment"] == s]
        v, n = [None] * len(keys), [None] * len(keys)
        for k, m, c in zip(d["week_start"].astype(str), d["mean_cfs"], d["n_gauges"]):
            v[pos[k]] = round(float(m), 1)
            n[pos[k]] = int(c)
        series.append({"name": s, "v": v, "n": n})
    first = next((r.weeks_all[i] for i in range(len(keys)) if any(sr["v"][i] is not None for sr in series)), r.weeks_all[0])
    last = r.weeks_all[-1] + timedelta(days=6)
    rows = _gauge_rows(r)
    gt = "".join(
        f"<tr><td>{html.escape(x['segment'])}</td><td>{html.escape(x['gauge'])}</td><td>{html.escape(x['copies'])}</td>"
        f"<td class=\"n\">{x['first'][:4]}</td><td class=\"n\">{x['last'][:4]}</td><td class=\"n\">{x['weeks']:,}</td>"
        f"<td>{'yes' if x['in_mean'] else 'no, fewer than ' + str(MIN_WEEKS) + ' weeks'}</td></tr>" for x in rows)
    extra = "".join(f"<li>{html.escape(t)}</li>" for t in r.notes)
    issues_html = issues_section(r)
    if r.dropped_segments:
        extra += ("<li>" + html.escape("Segments left out because the chart shows at most eight: "
                                       + ", ".join(r.dropped_segments) + ".") + "</li>")
    page = (TEMPLATES / "river_flow.html").read_text()
    for k, v in {"__RIVER__": html.escape(r.river), "__FIRST_YEAR__": str((first + timedelta(days=6)).year), "__LAST__": _fmt_day(last),
                 "__GAUGE_ROWS__": gt, "__EXTRA_NOTES__": extra, "__ISSUES__": issues_html, "__GENERATED__": html.escape(generated),
                 "__MIN_WEEKS__": str(MIN_WEEKS), "__N_SEGMENTS__": str(len(r.segments)),
                 "__DATA__": json.dumps({"weeks": keys, "series": series}, separators=(",", ":"))}.items():
        page = page.replace(k, v)
    path.write_text(page)


def write_notes(path: Path, r: RiverReport, generated: str) -> None:
    last = r.weeks_all[-1] + timedelta(days=6)
    L = [f"# {r.river} weekly streamflow by segment", "",
         f"Generated {generated} by `nmwater report-rivers`. Weekly mean discharge (cfs) per segment for the whole "
         f"record, through {_fmt_day(last)}. Open `index.html` for the interactive view.", "",
         "## Segments, upstream to downstream", ""]
    for s in r.segments:
        d = r.seg52[r.seg52["segment"] == s]
        a = r.seg_all[r.seg_all["segment"] == s]
        L.append(f"- **{s}**: record from {min(a['week_start']) if len(a) else 'n/a'}; last 52 weeks mean "
                 f"{d['mean_cfs'].mean():,.0f} cfs over {len(d)} weeks" if len(d) else
                 f"- **{s}**: record from {min(a['week_start']) if len(a) else 'n/a'}; no data in the last 52 weeks")
    L += ["", "## Gauges", "", f"A gauge counts in its segment's mean when it has at least {MIN_WEEKS} reported weeks.", "",
          "| Segment | Gauge | Copies | First | Last | Weeks | In segment mean |", "|---|---|---|---|---|---|---|"]
    for x in _gauge_rows(r):
        L.append(f"| {x['segment']} | {x['gauge']} | {x['copies']} | {x['first']} | {x['last']} | {x['weeks']} | "
                 f"{'yes' if x['in_mean'] else 'no'} |")
    L += ["", "## Data gaps and disparities", "",
          "Reviewed notes (config/river_notes.yaml) first, then findings from the automatic checks "
          "(nmwater/reports/river_issues.py), recomputed on every build.", ""]
    if r.data_notes:
        L += ["### Reviewed notes", ""]
        L += [f"- **{n.get('subject', '')}** ({n.get('basis', '')}, reviewed {n.get('reviewed', '')}): {n.get('text', '')}"
              for n in r.data_notes]
        L.append("")
    for title, sev in (("Can affect the numbers", "warn"), ("Context", "info")):
        items = [f for f in r.issues if f.severity == sev]
        if items:
            L += [f"### {title}", ""] + [f"- **{f.subject}** ({f.kind}): {f.text}" for f in items] + [""]
    if not r.issues and not r.data_notes:
        L += ["No gaps or disparities found.", ""]
    L += ["## How well the copies agree (last 52 weeks)", "",
          f"A day counts as different when a copy is off by more than {DISAGREE_PCT:.0f}% and 5 cfs from the value "
          f"used; percentages use only days of at least {PCT_MIN_CFS:.0f} cfs.", ""]
    if r.agree52.empty:
        L.append("No gauge had more than one copy with overlapping days.")
    else:
        tot, off = int(r.agree52["overlap_days"].sum()), int(r.agree52["days_off"].sum())
        L += [f"{len(r.agree52)} gauge-copy pairs, {tot:,} overlapping days, {off:,} different "
              f"({off / tot * 100:.1f}%).", "",
              "| Gauge | Copy | Overlap days | Median % difference | Days different |", "|---|---|---|---|---|"]
        for x in r.agree52.sort_values("days_off", ascending=False).head(10).itertuples():
            L.append(f"| {x.gauge} | {x.source} | {x.overlap_days} | {x.median_abs_pct_diff} | {int(x.days_off)} |")
    L += ["", "## Method", "", "See the module docstring of `nmwater/reports/river_flow.py`. Copy priority: "
          + " > ".join(PRIORITY) + ".", "", "## Caveats", "",
          "- A segment value averages gauges at different points on the river; diversions and inflow between them move it.",
          "- The gauges behind a segment change over time; see `n_gauges` in the CSVs.",
          "- Recent values are provisional and can be revised."]
    L += [f"- {t}" for t in r.notes]
    path.write_text("\n".join(L) + "\n")


def write_river(d: Path, r: RiverReport, generated: str) -> dict:
    d.mkdir(parents=True, exist_ok=True)
    rnd = {"mean_cfs": 1, "min_cfs": 1, "max_cfs": 1}
    r.seg_all.round(rnd).to_csv(d / "all_weeks_by_segment.csv", index=False)
    r.seg52.round(rnd).to_csv(d / "last_52_weeks_by_segment.csv", index=False)
    seg_of = dict(zip(r.gauges["gauge"], r.gauges["segment"]))
    r.gw52.assign(segment=r.gw52["gauge"].map(seg_of), in_segment_mean=r.gw52["gauge"].isin(r.elig)).round(
        {"mean_cfs": 1})[["segment", "gauge", "source_used", "week_start", "mean_cfs", "n_days", "in_segment_mean"]
                         ].to_csv(d / "last_52_weeks_by_gauge.csv", index=False)
    r.agree52.to_csv(d / "copy_agreement.csv", index=False)
    (d / "data_issues.json").write_text(json.dumps(
        {"river": r.river, "reviewed_notes": r.data_notes, "findings": [f.as_dict() for f in r.issues]}, indent=1, default=str))
    write_explorer(d / "index.html", r, generated)
    write_notes(d / "notes.md", r, generated)
    with_data = r.seg_all.groupby("segment")["week_start"].agg(["min", "max"])
    return {"river": r.river, "slug": r.slug, "path": f"{r.slug}/index.html",
            "segments": r.segments, "gauges": len(r.elig),
            "first_week": str(min(with_data["min"])), "last_week": str(max(with_data["max"])),
            "first_year": (min(with_data["min"]) + timedelta(days=6)).year if isinstance(min(with_data["min"]), date)
            else (pd.Timestamp(min(with_data["min"])) + pd.Timedelta(days=6)).year,
            "gnis_id": r.gnis_id,
            "issues_warn": sum(f.severity == "warn" for f in r.issues),
            "issues_info": sum(f.severity != "warn" for f in r.issues),
            "reviewed_notes": len(r.data_notes),
            "last_52_mean_cfs": None if r.seg52.empty else round(float(r.seg52["mean_cfs"].mean()), 1),
            "reporting": bool(len(r.seg52) and max(pd.to_datetime(r.seg52["week_start"])) >= pd.Timestamp(r.weeks52[-4]))}


def write_index(d: Path, entries: list[dict], generated: str) -> None:
    rows = "".join(
        f"<tr><td><a href=\"{html.escape(e['path'])}\">{html.escape(e['river'])}</a></td>"
        f"<td class=\"n\">{len(e['segments'])}</td><td class=\"n\">{e['gauges']}</td>"
        f"<td class=\"n\">{e.get('first_year') or e['first_week'][:4]}</td><td class=\"n\">{e['last_week']}</td>"
        f"<td class=\"n\">{'' if e['last_52_mean_cfs'] is None else f'{e['last_52_mean_cfs']:,.0f}'}</td>"
        f"<td>{'yes' if e['reporting'] else 'no'}</td>"
        f"<td class=\"n\"><a href=\"{html.escape(e['slug'])}/index.html#issues\">{e.get('issues_warn', 0)}</a></td></tr>"
        for e in entries)
    page = (TEMPLATES / "river_index.html").read_text()
    page = page.replace("__ROWS__", rows).replace("__GENERATED__", html.escape(generated)).replace(
        "__N_RIVERS__", str(len(entries)))
    (d / "index.html").write_text(page)
    (d / "manifest.json").write_text(json.dumps({"generated": generated, "rivers": entries}, indent=2))


def run(db: Path, out: Path, rivers: list[str] | None = None, config: dict | None = None,
        river_notes: dict | None = None) -> tuple[list[dict], list[str]]:
    """Build every river's report into out/rivers, atomically. Returns (written entries, failed rivers).
    river_notes: reviewed notes by river label (config/river_notes.yaml)."""
    river_notes = river_notes or {}
    import duckdb

    cfg = config or {}
    overrides = cfg.get("rivers") or {}
    con = duckdb.connect(str(db), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    found = list_rivers(con, 1 if rivers else int(cfg.get("min_sites", 2)))
    if rivers:
        want = set(rivers)
        found = [r for r in found if r.name in want or r.label in want]
    excl = set(cfg.get("exclude") or [])
    names = [r for r in found if r.name not in excl and r.label not in excl]
    links = con.sql("SELECT site_uid_a, site_uid_b FROM site_links").df()
    as_of = con.sql("SELECT max(datetime_utc)::DATE FROM observations_clean WHERE variable = 'discharge' "
                    "AND interval = 'daily' AND statistic = 'mean' AND datetime_utc <= now()").fetchone()[0]
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    out.mkdir(parents=True, exist_ok=True)
    tmp = out / f".rivers.tmp-{datetime.now(UTC):%Y%m%d%H%M%S}"
    tmp.mkdir()
    entries, failed = [], []
    try:
        for name in names:
            o = overrides.get(name.label) or overrides.get(name.name) or {}
            try:
                r = build_river(con, name, links, states=o.get("states"), notes=o.get("notes"), as_of=as_of)
                if r is not None:
                    r.data_notes = list(river_notes.get(r.river) or [])
                if r is None:
                    log.info("%s: no gauge with %d+ weeks of daily flow; skipped", name.label, MIN_WEEKS)
                    continue
                entries.append(write_river(tmp / r.slug, r, generated))
                log.info("%s: %d segments, %d gauges, %s to %s", name.label, len(r.segments), len(r.elig),
                         entries[-1]["first_week"], entries[-1]["last_week"])
            except Exception as e:                      # one bad river must not stop the rest
                log.exception("%s failed: %s", name.label, e)
                failed.append(name.label)
        if rivers and (out / "rivers").exists():        # partial run: keep the other rivers' pages
            for p in (out / "rivers").iterdir():
                if p.is_dir() and not (tmp / p.name).exists():
                    shutil.copytree(p, tmp / p.name)
            old = json.loads((out / "rivers" / "manifest.json").read_text()).get("rivers", []) \
                if (out / "rivers" / "manifest.json").exists() else []
            done = {e["slug"] for e in entries}
            entries += [e for e in old if e["slug"] not in done]
        entries.sort(key=lambda e: (-e["gauges"], e["river"]))
        write_index(tmp, entries, generated)
        final, old = out / "rivers", out / ".rivers.old"
        if old.exists():
            shutil.rmtree(old)
        if final.exists():
            final.rename(old)
        tmp.rename(final)
        if old.exists():
            shutil.rmtree(old)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
        con.close()
    return entries, failed
