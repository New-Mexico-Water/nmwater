"""Automatic checks for missing data and disparities behind a river's flow report.

Every check works on a river's daily values and returns plain findings, so the same list feeds the
page, notes.md and manifest.json. Hand-written explanations live in config/river_notes.yaml; these
checks say *what* is odd, the notes say *why* when we know.

Inputs (from nmwater.derived.river_flow.build_river)
    daily   every agency copy: gauge, date, source, site_uid, cfs
    chosen  one value per gauge and day (the copy used)
    seg     segment-weeks: segment, week_start, mean_cfs, n_gauges

Checks
    seasonal      calendar months a gauge (almost) never reports, e.g. no winter record
    gap           runs of 60+ missing days inside a gauge's record, not explained by its season
    ended         the gauge's record stops well before the archive's newest data
    copy_bias     an agency's copy runs consistently high or low against the copy used
                  (a ratio near 35.3, 0.0283, 1000 or 0.001 points to a unit error)
    copy_disagree an agency's copy differs on many days although its median agrees
    fallback      many of a gauge's days come from a lower-priority agency (the preferred one is missing)
    negative      negative daily flows
    spike         isolated daily values far above the gauge's normal range
    flatline      30+ days in a row with exactly the same non-zero value (a stuck sensor or a fill)
    zero_run      long runs of zero flow (often real: a dry river or a closed dam)
    single_gauge  weeks where a segment rests on one gauge
    no_yearly     no year has enough weeks for a yearly mean (seasonal records)
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from itertools import pairwise

import numpy as np
import pandas as pd

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
GAP_DAYS = 60
BELOW_DAM = re.compile(r"\b(below|bl|blw|bel)\b.*\b(dam|reservoir|res)\b", re.I)
ENDED_DAYS = 90
UNIT_RATIOS = {35.3147: "cubic metres per second read as cfs (or the reverse)", 0.0283168: "cfs read as m3/s",
               1000.0: "cfs read as thousands of cfs", 0.001: "thousands of cfs read as cfs"}


@dataclass
class Finding:
    kind: str
    severity: str        # "warn" (affects the numbers) or "info" (context)
    subject: str         # gauge or segment name
    text: str

    def as_dict(self) -> dict:
        return asdict(self)


def _fmt(d) -> str:
    d = pd.Timestamp(d)
    return f"{d:%b} {d.day}, {d.year}"


def _month_span(months: list[int]) -> str:
    """[12, 1, 2] -> 'Dec–Feb'; handles wrap-around and single months."""  # noqa: RUF002
    ms = sorted(set(months))
    if not ms:
        return ""
    runs, cur = [], [ms[0]]
    for m in ms[1:]:
        if m == cur[-1] + 1:
            cur.append(m)
        else:
            runs.append(cur)
            cur = [m]
    runs.append(cur)
    if len(runs) > 1 and runs[0][0] == 1 and runs[-1][-1] == 12:
        runs[0] = runs.pop() + runs[0]
    return ", ".join(MONTHS[r[0] - 1] if len(r) == 1 else f"{MONTHS[r[0] - 1]}–{MONTHS[r[-1] - 1]}" for r in runs)  # noqa: RUF001


# ---------------------------------------------------------------------------- per-gauge checks
def seasonal_months(days: pd.Series) -> list[int]:
    """Months the gauge reports in fewer than 20% of its active years while most months are well covered."""
    d = pd.to_datetime(pd.Series(days)).drop_duplicates()
    per = d.groupby([d.dt.year, d.dt.month]).size()
    years = [y for y, n in d.groupby(d.dt.year).size().items() if n >= 30]
    if len(years) < 3:
        return []
    frac = {m: sum(per.get((y, m), 0) >= 10 for y in years) / len(years) for m in range(1, 13)}
    covered = [m for m, f in frac.items() if f >= 0.6]
    if len(covered) < 4:
        return []
    return [m for m, f in frac.items() if f < 0.2]


def gaps(days: pd.Series, skip_months: list[int], min_days: int = GAP_DAYS) -> list[tuple[pd.Timestamp, pd.Timestamp, int]]:
    """Runs of missing days (start, end, length) of at least min_days, ignoring the gauge's off-season."""
    d = pd.to_datetime(pd.Series(days)).drop_duplicates().sort_values().reset_index(drop=True)
    out = []
    for a, b in pairwise(d):
        n = (b - a).days - 1
        if n < min_days:
            continue
        missing = pd.date_range(a + pd.Timedelta(days=1), b - pd.Timedelta(days=1))
        in_season = (~missing.month.isin(skip_months)).sum()
        if skip_months and in_season < min_days:
            continue
        out.append((missing[0], missing[-1], n))
    return out


def runs(values: pd.Series, dates: pd.Series, kind: str) -> list[tuple[pd.Timestamp, pd.Timestamp, int, float]]:
    """Consecutive-day runs of the same value (kind="same", non-zero) or of zero (kind="zero").
    Returns (start, end, days, value) for runs of two or more days."""
    s = pd.DataFrame({"d": pd.to_datetime(dates), "v": np.asarray(values, dtype=float)}).sort_values("d")
    d, v = s["d"].to_numpy(), s["v"].to_numpy()
    if len(v) < 2:
        return []
    consecutive = np.diff(d).astype("timedelta64[D]").astype(int) == 1
    same = v[1:] == v[:-1]
    cond = consecutive & same & ((v[1:] != 0) if kind == "same" else (v[1:] == 0))
    # a run is a maximal block of True in cond; block [i, j) covers days i..j
    edges = np.diff(np.concatenate([[0], cond.astype(int), [0]]))
    starts, ends = np.where(edges == 1)[0], np.where(edges == -1)[0]
    return [(pd.Timestamp(d[a]), pd.Timestamp(d[b]), int(b - a + 1), float(v[a])) for a, b in zip(starts, ends)]


def spikes(values: pd.Series, dates: pd.Series) -> list[tuple[pd.Timestamp, float, float]]:
    """Days far above both neighbours' level and the gauge's normal range: (date, value, typical high)."""
    s = pd.DataFrame({"d": pd.to_datetime(dates), "v": values}).sort_values("d").reset_index(drop=True)
    if len(s) < 60:
        return []
    p99 = float(s.v.quantile(0.99))
    local = s.v.rolling(7, center=True, min_periods=3).median()
    hit = (s.v > 5 * max(p99, 1)) & (s.v > 10 * local.clip(lower=1)) & (s.v > 100)
    return [(s.d[i], float(s.v[i]), p99) for i in s.index[hit]]


def copy_checks(daily: pd.DataFrame, chosen: pd.DataFrame) -> pd.DataFrame:
    """Whole-record comparison of each unused copy with the value used: overlap days, median ratio, share off."""
    used = chosen[["gauge", "date", "source", "cfs"]].rename(columns={"source": "used_source", "cfs": "used_cfs"})
    m = daily.merge(used, on=["gauge", "date"])
    m = m[(m["source"] != m["used_source"]) & (m["used_cfs"] >= 10)]
    if m.empty:
        return pd.DataFrame(columns=["gauge", "source", "used_source", "days", "ratio", "share_off", "first", "last"])
    m = m.assign(ratio=m["cfs"] / m["used_cfs"],
                 off=((m["cfs"] - m["used_cfs"]).abs() > 5) & ((m["cfs"] - m["used_cfs"]).abs() / m["used_cfs"] > 0.10))
    g = m.groupby(["gauge", "source"])
    return g.agg(used_source=("used_source", lambda s: s.value_counts().index[0]), days=("ratio", "size"),
                 ratio=("ratio", "median"), share_off=("off", "mean"), first=("date", "min"),
                 last=("date", "max")).reset_index()


def unit_hint(ratio: float) -> str | None:
    for r, why in UNIT_RATIOS.items():
        if abs(np.log(ratio / r)) < 0.08:
            return why
    return None


# ---------------------------------------------------------------------------- all checks for one river
def find_issues(daily: pd.DataFrame, chosen: pd.DataFrame, seg: pd.DataFrame, as_of: date,
                elig: set[str], segment_of: dict[str, str], priority: list[str],
                peak_of: dict | None = None, conflicts: list[dict] | None = None) -> list[Finding]:
    """peak_of: {(gauge, water_year): USGS annual peak cfs}, used to confirm spikes as real floods.
    conflicts: approved USGS daily values above their own annual peak (kept, reported here)."""
    out: list[Finding] = []
    peak_of = peak_of or {}
    rank = {s: i for i, s in enumerate(priority)}
    ended: list[tuple[str, pd.Timestamp]] = []
    for gname, c in chosen.groupby("gauge", sort=False):
        c = c.sort_values("date")
        if gname not in elig:
            continue
        last = c["date"].max()
        season = seasonal_months(c["date"])
        if season:
            out.append(Finding("seasonal", "warn", gname,
                               f"No record in {_month_span(season)} most years: the gauge reports seasonally, so "
                               "yearly and some monthly means are missing or cover only part of the year."))
        gl = gaps(c["date"], season)
        if gl:
            longest = sorted(gl, key=lambda x: -x[2])[:3]
            txt = "; ".join(f"{_fmt(a)} to {_fmt(b)} ({n:,} days)" for a, b, n in longest)
            total = sum(n for *_, n in gl)
            out.append(Finding("gap", "warn" if max(n for *_, n in gl) >= 365 else "info", gname,
                               f"{len(gl)} gap{'s' if len(gl) > 1 else ''} of {GAP_DAYS}+ days inside the record "
                               f"({total:,} days in all). Longest: {txt}."))
        if (pd.Timestamp(as_of) - last).days > ENDED_DAYS:
            ended.append((gname, last))
        neg = c[c["cfs"] < 0]
        if len(neg):
            out.append(Finding("negative", "warn", gname,
                               f"{len(neg):,} days with negative flow (lowest {neg['cfs'].min():,.1f} cfs, "
                               f"{_fmt(neg['date'].min())} to {_fmt(neg['date'].max())}). Negative values usually "
                               "mean reverse flow, backwater or a computed (not measured) record."))
        sp = spikes(c["cfs"], c["date"])
        if sp:
            # a daily mean at or below that water year's USGS instantaneous peak is a real flood
            wy = lambda d: d.year + (d.month >= 10)  # noqa: E731
            # values above the peak are removed or reported as peak_conflict elsewhere
            unchecked = [x for x in sp if (gname, wy(x[0])) not in peak_of]
            if unchecked:
                top = sorted(unchecked, key=lambda x: -x[1])[:3]
                out.append(Finding("spike", "info", gname,
                                   f"{len(unchecked)} single-day flow{'s' if len(unchecked) > 1 else ''} far above the "
                                   f"gauge's usual range (99th percentile {top[0][2]:,.0f} cfs) with no annual peak on "
                                   "record to confirm them: " + "; ".join(f"{v:,.0f} cfs on {_fmt(d)}" for d, v, _ in top)
                                   + ". Flash floods are common here, but these are unverified."))
        # below 1 cfs identical values are the gauge's reporting resolution, not a flat line
        flat = [r for r in runs(c["cfs"], c["date"], "same") if r[2] >= 30 and r[3] >= 1]
        if flat:
            top = sorted(flat, key=lambda x: -x[2])[:3]
            below_dam = bool(BELOW_DAM.search(gname))
            recent = not below_dam and any((pd.Timestamp(as_of) - b).days < 3 * 365 for _, b, _, _ in flat)
            why = ("Below a dam this is usually a steady set release, which is real flow." if below_dam else
                   "Usually an estimated period (ice, a broken gauge) rather than measured daily flow"
                   + ("; the recent run may be a stuck sensor." if recent else "."))
            out.append(Finding("flatline", "warn" if recent else "info", gname,
                               f"{len(flat)} run{'s' if len(flat) > 1 else ''} of 30+ days at exactly the same flow: "
                               + "; ".join(f"{v:,.1f} cfs for {n} days from {_fmt(a)}" for a, _, n, v in top)
                               + ". " + why))
        zeros = [r for r in runs(c["cfs"], c["date"], "zero") if r[2] >= 60]
        if zeros:
            top = sorted(zeros, key=lambda x: -x[2])[:2]
            out.append(Finding("zero_run", "info", gname,
                               f"{len(zeros)} run{'s' if len(zeros) > 1 else ''} of 60+ days at zero flow; longest "
                               + "; ".join(f"{n:,} days from {_fmt(a)}" for a, _, n, _ in top)
                               + ". Usually real (a dry channel or no release), not missing data."))
        mix = c["source"].value_counts(normalize=True)
        best = min(mix.index, key=lambda s: rank.get(s, 99))
        others = mix.drop(best)
        if len(others) and others.sum() > 0.10:
            srcs = ", ".join(f"{s} {v:.0%}" for s, v in others.items())
            out.append(Finding("fallback", "info", gname,
                               f"{mix[best]:.0%} of days come from {best}; {srcs} fill days {best} does not cover, "
                               "so the source changes within the record."))

    by_gauge: dict[str, list[dict]] = {}
    for c_ in conflicts or []:
        by_gauge.setdefault(c_["gauge"], []).append(c_)
    for gname, cs in by_gauge.items():
        cs.sort(key=lambda x: -x["cfs"] / max(x["peak"], 1))
        years = sorted({int(x["date"][:4]) + (int(x["date"][5:7]) >= 10) for x in cs})
        out.append(Finding("peak_conflict", "warn", gname,
                           f"{len(cs)} approved USGS daily value{'s' if len(cs) > 1 else ''} exceed that water year's USGS "
                           f"annual peak, in water year{'s' if len(years) > 1 else ''} "
                           + ", ".join(map(str, years[:8])) + (" and more" if len(years) > 8 else "")
                           + f" (e.g. {cs[0]['cfs']:,.0f} cfs on {_fmt(cs[0]['date'])} against a peak of {cs[0]['peak']:,.0f}). "
                           "A day's mean cannot exceed the year's peak, so one of the two USGS records is wrong for "
                           "those years; the daily values are kept."))

    if ended:
        ended.sort(key=lambda x: x[1])
        out.append(Finding("ended", "info", "Discontinued gauges",
                           f"{len(ended)} gauge{'s have' if len(ended) > 1 else ' has'} stopped reporting: "
                           + "; ".join(f"{g} (last {d.year})" for g, d in ended) + ". Their segments rest on "
                           "fewer gauges after those years."))

    used_days = chosen.groupby(["gauge", "source"]).size()
    cc = copy_checks(daily, chosen)
    for x in cc.itertuples():
        if x.days < 30 or x.gauge not in elig:
            continue
        n_used = int(used_days.get((x.gauge, x.source), 0))
        if n_used == 0 and x.source == "nwps":
            continue      # the NWS feed is 30 days of provisional readings and is not used where other copies exist
        sev = "warn" if n_used else "info"
        use = (f"The report takes {n_used:,} days from {x.source} where {x.used_source} is missing, so the level "
               "can shift there." if n_used else f"The report does not use {x.source} for this gauge.")
        hint = unit_hint(x.ratio)
        if hint:
            out.append(Finding("copy_bias", sev, x.gauge,
                               f"The {x.source} copy is {x.ratio:,.4g} times the {x.used_source} values on {x.days:,} "
                               f"shared days: likely a unit error ({hint}). {use}"))
        elif not 0.9 <= x.ratio <= 1.1:
            out.append(Finding("copy_bias", sev, x.gauge,
                               f"The {x.source} copy runs {abs(x.ratio - 1):.0%} {'higher' if x.ratio > 1 else 'lower'} "
                               f"than {x.used_source} (median, {x.days:,} shared days, {_fmt(x.first)} to {_fmt(x.last)}). "
                               + use))
        elif x.share_off > 0.2:
            out.append(Finding("copy_disagree", "info", x.gauge,
                               f"The {x.source} copy agrees on the median but differs by more than 10% on "
                               f"{x.share_off:.0%} of {x.days:,} shared days (often provisional vs approved data, or "
                               f"a different daily averaging window). {use}"))

    for s, d in seg.groupby("segment", sort=False):
        one = (d["n_gauges"] == 1).mean()
        if len(d) and one >= 0.5 and d["n_gauges"].max() > 1:
            out.append(Finding("single_gauge", "info", s,
                               f"{one:.0%} of this segment's weeks rest on a single gauge; the segment value is that gauge "
                               "in those weeks, and jumps when other gauges join or drop out."))
        weeks_per_year = pd.to_datetime(d["week_start"]).dt.year.value_counts()
        if len(weeks_per_year) >= 3 and weeks_per_year.max() < 45:
            out.append(Finding("no_yearly", "warn", s,
                               f"No year has the 45 weeks a yearly mean needs (most has {weeks_per_year.max()}), so "
                               "the Year view is empty for this segment. Use Month or Week."))
    order = {"warn": 0, "info": 1}
    return sorted(out, key=lambda f: (order[f.severity], f.subject, f.kind))
