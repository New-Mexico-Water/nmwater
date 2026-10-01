"""River streamflow data: weekly mean cfs per river segment for every river with gauges.

A segment is the HUC8 watershed a river gauge sits in (the catalog's `river_segments` view). The same gauge is often published by several agencies; copies are merged and counted once. This module builds the data (nmwater/site/river.py writes the bundle files from it) and the CSV and notes downloads.

Method
  1. Gauges: stream sites snapped to the river's own reaches (river_method = 'snap'), whose name contains the river's distinctive word (drains and ditches that sit on the river are not river gauges), with daily discharge. NHD leaves some main-stem reaches unnamed, so a site on an unnamed reach also counts when the first named reach downstream is this river, it lies in a watershed the river runs through, and its name starts with the river's name followed by a place (names_the_river). Optional per-river state filter (config).
  2. Copies of one gauge are merged with site_links (same sensor or colocated within 250 m).
  3. Daily mean per copy: the source's own daily mean, else the mean of its sub-daily readings over the America/Denver day (at least half the expected readings). Daily and sub-daily rows are never averaged together.
  4. One value per gauge and day from the first copy in PRIORITY.
  5. Weeks run Monday to Sunday; a gauge-week needs MIN_DAYS of 7. A gauge counts in its segment when it has MIN_WEEKS reported weeks. A segment-week is the mean of its gauges.
  6. Segments are ordered upstream to downstream by the median drainage area of their gauges.
"""

from __future__ import annotations
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
import pandas as pd


log = logging.getLogger("nmwater.derived.river_flow")


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


STREAM_TYPE = {"river", "r", "rvr", "rv", "creek", "cr", "ck", "c", "arroyo", "arr", "wash", "canyon"}


PLACE_WORD = {"at", "near", "nr", "n", "above", "abv", "ab", "below", "bl", "blw", "bel", "in", "from", "to"}


def names_the_river(name: str, river: str) -> bool:
    """Stricter test for gauges on unnamed reaches: the gauge's name must start with the river's name
    (creek and river are interchangeable, abbreviations allowed) followed by a place ("at", "near",
    "abv", a distance) or nothing. "Rio Ruidoso at Hollywood" names the Rio Ruidoso; "Little Tesuque Cr",
    "Pecos River Trib" and "San Antonio Arroyo at Rio Grande confluence" do not."""
    g = re.findall(r"[a-z0-9]+", re.sub(r"\(.*?\)", " ", _plain(name)).replace("@", " at "))
    r = re.findall(r"[a-z0-9]+", _plain(river.split(" (")[0]))
    core = r[:-1] if len(r) > 1 and r[-1] in STREAM_TYPE else r
    if not core or g[:len(core)] != core:
        return False
    rest = g[len(core):]
    if rest and rest[0] in STREAM_TYPE:
        rest = rest[1:]
    return not rest or rest[0] in PLACE_WORD or rest[0].isdigit()


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


def nice_name(name: str) -> str:
    """Agency names are often all capitals: 'RIO GRANDE AT OTOWI BRIDGE, NM' -> 'Rio Grande At Otowi Bridge, NM'."""
    n = str(name).strip().rstrip(".")
    if n.upper() != n:
        return n
    return re.sub(r", (Nm|Co|Tx|Az|Ok|Ut)\b", lambda m: m.group(0).upper(), n.title())


def _plain(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()


# ---------------------------------------------------------------------------- catalog reads
@dataclass
class River:
    name: str             # GNIS name, e.g. "Rio Hondo"
    gnis_id: str          # NHD GNIS id; two rivers can share a name (Rio Hondo near Taos and near Roswell)
    label: str            # name, plus the basin when another river shares the name
    n_sites: int = 0


def list_rivers(con, min_sites: int = 2, min_years: float | None = None) -> list[River]:
    """Rivers (by GNIS id) with at least min_sites stream sites that carry daily discharge, or, with
    min_years, fewer sites but one whose daily record holds at least that many years of days."""
    rows = con.sql(f"""
        SELECT f.gnis_name, f.gnis_id, count(DISTINCT r.site_uid) AS n,
               mode(s.basin) AS basin, mode(r.huc8_name) AS huc8
        FROM river_segments r JOIN site_reaches sr USING (site_uid) JOIN flowlines f ON f.comid = sr.comid
        JOIN sites s USING (site_uid) JOIN site_variables sv USING (site_uid)
        WHERE r.river_method = 'snap' AND r.site_type = 'stream' AND f.gnis_id IS NOT NULL AND trim(f.gnis_id) <> ''
          AND sv.variable = 'discharge' AND sv.interval = 'daily'
        GROUP BY 1, 2 HAVING count(DISTINCT r.site_uid) >= {int(min_sites)}
            {"" if not min_years else f"OR max(sv.n_obs) >= {float(min_years) * 365.25}"}
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
               s.raw_metadata, r.river_method
        FROM river_segments r JOIN sites s USING (site_uid) LEFT JOIN fips USING (site_uid)
        JOIN site_reaches sr USING (site_uid) JOIN flowlines f ON f.comid = sr.comid
        WHERE r.site_type = 'stream' AND r.huc8_name IS NOT NULL {st}
          AND s.site_uid IN (SELECT site_uid FROM site_variables WHERE variable = 'discharge')
          AND ((f.gnis_id = ? AND r.river_method = 'snap')
               -- a gauge on an unnamed reach (NHD leaves some main-stem reaches unnamed) whose first named
               -- reach downstream is this river, in a watershed the river runs through; its name is checked below
               OR (r.river_method = 'downstream' AND sr.river_name = ?
                   AND sr.huc8 IN (SELECT DISTINCT huc8 FROM flowlines WHERE gnis_id = ?)))""",
                    [river.gnis_id, river.name, river.gnis_id]).df()
    keep = pd.Series([(is_river_gauge(n, river.name) if meth == "snap" else names_the_river(n, river.name))
                      and not (src == "ose_meas" and _ose_ditch(m))
                      for n, src, m, meth in zip(g["name"], g["source"], g["raw_metadata"], g["river_method"])],
                     index=g.index, dtype=bool)
    return g[keep].drop(columns=["raw_metadata", "river_method"]).reset_index(drop=True)


def read_daily(con, uids: list[str], start: date, end: date) -> pd.DataFrame:
    """Daily mean cfs per copy: the source's daily mean, else sub-daily readings averaged per Denver day."""
    ids = ",".join("'" + u.replace("'", "''") + "'" for u in uids)
    expected = " ".join(f"WHEN '{k}' THEN {v}" for k, v in SUBDAILY_PER_DAY.items())
    return con.sql(f"""
        WITH daily AS (
          SELECT site_uid, datetime_utc::DATE AS date, avg(value) AS cfs, any_value(qualifier) AS qualifier
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
          SELECT site_uid, date, cfs, NULL::VARCHAR AS qualifier FROM sub
          WHERE date BETWEEN '{start}' AND '{end}'
            AND n >= {MIN_COVERAGE} * CASE interval {expected} END)
        SELECT site_uid, date, cfs, qualifier FROM daily
        UNION ALL
        SELECT site_uid, date, cfs, qualifier FROM sub_ok
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
    removed: list = field(default_factory=list)      # daily values removed as bad (dicts), automatic or listed
    chosen: pd.DataFrame | None = None               # one daily value per gauge and day: gauge, date, source, cfs
    as_of: date | None = None                        # the archive's newest data day
    data_notes: list = field(default_factory=list)   # hand-written explanations (config/river_notes.yaml)


def remove_bad_values(daily: pd.DataFrame, peak_of: dict, exclusions: list[dict] | None,
                      gauge_of_site: dict[str, str]) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    """Drop known-bad daily values. Returns (kept rows, removed records, conflicts kept for review).

    - listed: every copy of the gauge on the listed dates (config/river_exclusions.yaml)
    - automatic: a daily mean above 1.05 x that water year's USGS annual peak, when the value is a
      non-USGS copy or a USGS value flagged estimated ("e"). An approved USGS daily value above the
      peak is kept and returned as a conflict: the peak file is sometimes incomplete, so either record
      could be the wrong one.
    """
    d = daily.copy()
    if "qualifier" not in d.columns:
        d["qualifier"] = None
    drop = pd.Series(False, index=d.index)
    why = pd.Series("", index=d.index, dtype=object)
    for ex in exclusions or []:
        gauge = gauge_of_site.get(str(ex.get("site_uid")))
        if gauge is None:
            continue
        m = (d["gauge"] == gauge) & (d["date"] >= pd.Timestamp(str(ex["from"]))) & (d["date"] <= pd.Timestamp(str(ex["to"])))
        drop |= m
        why[m] = "listed: " + " ".join(str(ex.get("reason", "")).split())
    conflicts: list[dict] = []
    if peak_of:
        wy = d["date"].dt.year + (d["date"].dt.month >= 10)
        peak = pd.Series([peak_of.get((g, y)) for g, y in zip(d["gauge"], wy)], index=d.index, dtype=float)
        over = peak.notna() & (d["cfs"] > 1.05 * peak) & ~drop
        estimated = d["qualifier"].fillna("").astype(str).str.contains(r"(?:^|[:|,])e(?:$|[:|,])", regex=True)
        approved_usgs = (d["source"] == "usgs") & ~estimated
        m = over & ~approved_usgs
        drop |= m
        why[m] = [f"automatic: daily mean above that water year's USGS peak of {p:,.0f} cfs"
                  + (" (USGS flags the daily value as estimated)" if src == "usgs" else "")
                  for p, src in zip(peak[m], d.loc[m, "source"])]
        k = over & approved_usgs
        conflicts = [{"gauge": r.gauge, "date": r.date.date().isoformat(), "cfs": round(float(r.cfs), 1),
                      "peak": round(float(p), 1)} for r, p in zip(d[k].itertuples(), peak[k])]
    removed = [{"gauge": r.gauge, "date": r.date.date().isoformat(), "source": r.source, "cfs": round(float(r.cfs), 1),
                "reason": w} for r, w in zip(d[drop].itertuples(), why[drop])]
    removed.sort(key=lambda x: (x["gauge"], x["date"], x["source"]))          # the database's scan order is not stable
    return d[~drop].reset_index(drop=True), removed, conflicts


def build_river(con, river: River, links: pd.DataFrame, states: list[str] | None = None,
                notes: list[str] | None = None, as_of: date | None = None,
                exclusions: list[dict] | None = None) -> RiverReport | None:
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
    daily = raw.merge(g[["site_uid", "source", "gauge"]], on="site_uid")[["gauge", "date", "source", "site_uid", "cfs",
                                                                           "qualifier"]]
    daily["date"] = pd.to_datetime(daily["date"])
    # USGS annual peak flows, as an upper bound on any daily mean that water year. Peaks USGS marks as
    # estimated or "greater than" are not bounds, and are left out.
    peaks = con.sql(f"""SELECT site_uid, datetime_utc, value FROM observations_clean
        WHERE variable = 'discharge' AND interval = 'water_year' AND statistic = 'max' AND site_uid IN ({ids})
          AND coalesce(qualifier, '') NOT LIKE '%ESTIMATED%' AND coalesce(qualifier, '') NOT LIKE '%GREATERTHAN%'""").df()
    peaks = peaks.merge(g[["site_uid", "gauge"]], on="site_uid")
    peaks["wy"] = pd.to_datetime(peaks["datetime_utc"]).dt.year + (pd.to_datetime(peaks["datetime_utc"]).dt.month >= 10)
    peak_of = peaks.groupby(["gauge", "wy"])["value"].max().to_dict()
    daily, removed, conflicts = remove_bad_values(daily, peak_of, exclusions, dict(zip(g["site_uid"], g["gauge"])))
    daily = daily.drop(columns="qualifier")
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

    issues = find_issues(daily, chosen, seg_all, as_of or latest, elig, segment_of, PRIORITY, peak_of, conflicts)
    return RiverReport(river=river.label, slug=slugify(river.label), segments=segments, gauges=g, weeks_all=weeks_all,
                       seg_all=seg_all, gw_all=gw_all, elig=elig, weeks52=weeks52, seg52=seg52, gw52=gw52,
                       agree52=agree52, notes=list(notes or []), dropped_segments=dropped,
                       gnis_id=river.gnis_id, issues=issues, removed=removed,
                       chosen=chosen[["gauge", "date", "source", "cfs"]].reset_index(drop=True), as_of=as_of or latest)


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


def explorer_data(r: RiverReport) -> dict:
    """Weekly mean cfs per segment for the whole record: {keys, series: [{name, v, n}], first, last}."""
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
    return {"keys": keys, "series": series, "first": first, "last": r.weeks_all[-1] + timedelta(days=6)}


def write_notes(path: Path, r: RiverReport, generated: str) -> None:
    last = r.weeks_all[-1] + timedelta(days=6)
    L = [f"# {r.river} weekly streamflow by segment", "",
         f"Generated {generated} by `nmwater export-site-data`. Weekly mean discharge (cfs) per segment for the whole "
         f"record, through {_fmt_day(last)}.", "",
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
          "(nmwater/derived/river_issues.py), recomputed on every build.", ""]
    if r.removed:
        L += [f"### Removed values ({len(r.removed)})", ""]
        L += [f"- **{x['gauge']}** {x['date']}, {x['cfs']:,.1f} cfs ({x['source']}): {x['reason']}" for x in r.removed]
        L.append("")
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
    L += ["", "## Method", "", "See the module docstring of `nmwater/derived/river_flow.py`. Copy priority: "
          + " > ".join(PRIORITY) + ".", "", "## Caveats", "",
          "- A segment value averages gauges at different points on the river; diversions and inflow between them move it.",
          "- The gauges behind a segment change over time; see `n_gauges` in the CSVs.",
          "- Recent values are provisional and can be revised."]
    L += [f"- {t}" for t in r.notes]
    path.write_text("\n".join(L) + "\n")


def write_csvs(d: Path, r: RiverReport) -> None:
    """The river's CSV files: weekly means by segment (all time, last 52 weeks), by gauge, and copy agreement."""
    rnd = {"mean_cfs": 1, "min_cfs": 1, "max_cfs": 1}
    r.seg_all.round(rnd).to_csv(d / "all_weeks_by_segment.csv", index=False)
    r.seg52.round(rnd).to_csv(d / "last_52_weeks_by_segment.csv", index=False)
    seg_of = dict(zip(r.gauges["gauge"], r.gauges["segment"]))
    r.gw52.assign(segment=r.gw52["gauge"].map(seg_of), in_segment_mean=r.gw52["gauge"].isin(r.elig)).round(
        {"mean_cfs": 1})[["segment", "gauge", "source_used", "week_start", "mean_cfs", "n_days", "in_segment_mean"]
                         ].to_csv(d / "last_52_weeks_by_gauge.csv", index=False)
    r.agree52.to_csv(d / "copy_agreement.csv", index=False)
