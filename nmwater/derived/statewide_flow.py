"""Water leaving New Mexico on its five largest exiting rivers, and entering on the Rio Grande at Lobatos: data only; nmwater/site/statewide.py publishes it.

Not published as "the state's outflow": these are five gauges, not every stream that crosses the border. Each exit is the last gauge on that river that this archive holds with a
long record, one gauge per river, so no water is counted twice (the Rio Grande's Elephant Butte releases flow into Caballo, so only Caballo is used).

Method
- Daily mean flow (cfs) per gauge from the archive's cleaned observations; where several sources publish the same gauge one copy per day is used (river_flow.PRIORITY).
- A window is the last k days ending END; its volume is mean flow x k x 1.983471 acre-feet per cfs-day (so a few missing days do not shrink it), stated only when at least
  MIN_COVERAGE of the days have data. END is the latest day on which every exit that is not stale has data.
- A gauge whose newest reading is more than STALE_DAYS older than the newest gauge is left out of the sum and named. The sum is stated only when every remaining exit is rated.
- Normal: the same calendar window in each year of 1991-2020 (30 years; at least MIN_BASE_YEARS are needed, else the whole record when it has MIN_FALLBACK_YEARS). For the sum, a year
  counts only when every exit has a value that year. Percent of median is the volume over the median volume; the percentile and class use the same bands as flow and rain.
- Water years run October to September; a year counts when each gauge has at least WY_COVERAGE of its days.
- Not computed: the compact accounting (Rio Grande, Pecos, Colorado River, Canadian), which rests on annual reports we do not hold.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import river_flow as rf
from . import river_normal as rn
from .reservoirs import CFS_DAY_AF

log = logging.getLogger("nmwater.derived.statewide_flow")

BASELINE = rn.BASELINE
MIN_BASE_YEARS = rn.MIN_BASE_YEARS
MIN_FALLBACK_YEARS = rn.MIN_FALLBACK_YEARS
WINDOWS = (7, 30, 90, 180, 365)
MIN_COVERAGE = 0.75
WY_COVERAGE = 0.9
STALE_DAYS = 14
MIN_WYTD_DAYS = 30
HISTORY_FROM_WY = 1978                 # the first water year the shortest exit record (San Juan, 1977) covers

# One gauge per river; copies are the same gauge published by another source (the daily copy of a day is chosen by river_flow.PRIORITY).
GAUGES = [
    {"key": "rio_grande", "role": "exit", "river": "Rio Grande", "name": "Rio Grande below Caballo Dam, NM", "uids": ["usbr_hydrodata:1113", "usgs:08362500"],
     "caveats": ["managed", "diverted_below", "not_at_line"]},
    {"key": "pecos", "role": "exit", "river": "Pecos River", "name": "Pecos River at Red Bluff, NM", "uids": ["usgs:08407500", "usbr_hydrodata:1568"], "caveats": ["managed"]},
    {"key": "canadian", "role": "exit", "river": "Canadian River", "name": "Canadian River at Logan, NM", "uids": ["usgs:07227000"], "caveats": ["managed", "not_at_line"]},
    {"key": "san_juan", "role": "exit", "river": "San Juan River", "name": "San Juan River at Four Corners", "uids": ["usgs:09371010", "usbr_hydrodata:801"], "caveats": ["managed", "exports"]},
    {"key": "gila", "role": "exit", "river": "Gila River", "name": "Gila River below Blue Creek, near Virden, NM", "uids": ["usgs:09432000"], "caveats": ["not_at_line"]},
    {"key": "lobatos", "role": "entrance", "river": "Rio Grande", "name": "Rio Grande near Lobatos, CO", "uids": ["usgs:08251500", "codwr:RIOLOBCO"], "caveats": ["managed", "usgs_ended"]},
]
CAVEATS = {
    "managed": "Flow here is regulated by upstream dams and releases, not only by the weather.",
    "diverted_below": "Much of the water released here is diverted for irrigation farther downstream, so the flow reaching the Texas line is not measured by a gauge in the archive and is smaller than this.",
    "not_at_line": "The gauge is upstream (or downstream) of the state line, not on it; tributaries and diversions between the gauge and the line are not counted.",
    "exports": "The San Juan-Chama Project diverts water out of the San Juan's headwaters to the Rio Grande basin upstream of this gauge, and Navajo Dam regulates what remains, so the flow is not the river's natural flow.",
    "usgs_ended": "The USGS gauge here stopped in December 2024; the Colorado Division of Water Resources' copy of the same station supplies the days since.",
}


def read_series(con, end: date | None = None) -> dict[str, pd.Series]:
    """Daily mean cfs for every gauge in GAUGES, one value per day, indexed by date (Timestamp)."""
    end = end or date.today()
    out = {}
    for g in GAUGES:
        d = rf.read_daily(con, g["uids"], date(1900, 1, 1), end)
        if d.empty:
            continue
        d["source"] = d["site_uid"].str.split(":").str[0]
        d["gauge"] = g["key"]
        ch = rf.pick_copy(d)
        s = ch.assign(date=pd.to_datetime(ch["date"])).groupby("date")["cfs"].mean().sort_index()
        out[g["key"]] = s[s >= 0]
    return out


def select(series: dict[str, pd.Series]) -> tuple[list[str], list[dict], pd.Timestamp | None]:
    """(exits used, exits left out as stale, END): END is the latest day on which every used exit has data."""
    exits = [g["key"] for g in GAUGES if g["role"] == "exit" and g["key"] in series]
    if not exits:
        return [], [], None
    newest = max(series[k].index[-1] for k in exits)
    used, out = [], []
    for k in exits:
        last = series[k].index[-1]
        if (newest - last).days > STALE_DAYS:
            out.append({"key": k, "last_date": last.date().isoformat(), "reason": f"newest reading is more than {STALE_DAYS} days older than the other gauges'"})
        else:
            used.append(k)
    return used, out, min(series[k].index[-1] for k in used)


def _window(s: pd.Series, end: pd.Timestamp, days: int, min_cov: float = MIN_COVERAGE) -> tuple[float, float] | None:
    """(mean cfs, coverage) of the `days` ending `end`, or None below min_cov. A window that starts before the record does."""
    start = end - pd.Timedelta(days=days - 1)
    if s.index[0] > start:
        return None
    w = s[start:end]
    cov = len(w) / days
    return None if cov < min_cov else (float(w.mean()), cov)


def _same_end(y: int, end: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(year=y, month=end.month, day=min(end.day, pd.Timestamp(year=y, month=end.month, day=1).days_in_month))


def _year_means(s: pd.Series, end: pd.Timestamp, days: int) -> dict[int, float]:
    out = {}
    for y in range(s.index[0].year, end.year):
        e = _same_end(y, end)
        r = _window(s, e, days)
        if r:
            out[y] = r[0]
    return out


def _base(ym: dict[int, float]) -> np.ndarray:
    """Baseline-year means (BASELINE when MIN_BASE_YEARS exist, else the whole record when MIN_FALLBACK_YEARS do; else empty)."""
    b = np.array([v for y, v in ym.items() if BASELINE[0] <= y <= BASELINE[1]])
    if len(b) >= MIN_BASE_YEARS:
        return b
    a = np.array(list(ym.values()))
    return a if len(a) >= MIN_FALLBACK_YEARS else np.array([])


def _rate(volume_af: float, base_af: np.ndarray) -> dict:
    if len(base_af) == 0:
        return {"normal_af": None, "percent_of_median": None, "percentile": None, "class": None, "years": 0}
    med = float(np.median(base_af))
    pct = rn.percentile_rank(base_af, volume_af)
    return {"normal_af": med, "percent_of_median": None if med <= 0 else float(100 * volume_af / med), "percentile": float(pct), "class": rn.classify(pct), "years": int(len(base_af))}


def window_stats(series: dict[str, pd.Series], keys: list[str], end: pd.Timestamp, days: int) -> dict | None:
    """One window across the exits `keys`: per-gauge volumes and ratings, and the rated sum (when every exit has the window)."""
    by = []
    cur = {}
    for k in keys:
        s = series[k]
        r = _window(s, end, days)
        if r is None:
            continue
        cur[k] = r
        ym = _year_means(s, end, days)
        vol = r[0] * days * CFS_DAY_AF
        by.append({"key": k, "mean_cfs": r[0], "volume_af": vol, "coverage": r[1], **_rate(vol, _base(ym) * days * CFS_DAY_AF)})
    if not by:
        return None
    out = {"days": days, "start": (end - pd.Timedelta(days=days - 1)).date().isoformat(), "end": end.date().isoformat(), "by_gauge": by, "complete": len(by) == len(keys)}
    if out["complete"]:
        yms = {k: _year_means(series[k], end, days) for k in keys}
        years = sorted(set.intersection(*[set(v) for v in yms.values()]))
        tot = np.array([sum(yms[k][y] for k in keys) * days * CFS_DAY_AF for y in years])
        yrs = np.array(years)
        b = tot[(yrs >= BASELINE[0]) & (yrs <= BASELINE[1])]
        b = b if len(b) >= MIN_BASE_YEARS else (tot if len(tot) >= MIN_FALLBACK_YEARS else np.array([]))
        vol = sum(x["volume_af"] for x in by)
        out["total"] = {"volume_af": vol, **_rate(vol, b)}
    else:
        out["total"] = None
    return out


def water_year_start(end: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(end.year if end.month >= 10 else end.year - 1, 10, 1)


def water_years(series: dict[str, pd.Series], keys: list[str], last_complete: int) -> dict:
    """Volume per complete water year (named for the year it ends) for each gauge in `keys` and their sum (None where a gauge lacks WY_COVERAGE of the year)."""
    years = list(range(HISTORY_FROM_WY, last_complete + 1))
    by = {}
    for k in keys:
        s = series[k]
        row = []
        for y in years:
            a, b = pd.Timestamp(y - 1, 10, 1), pd.Timestamp(y, 9, 30)
            r = _window(s, b, (b - a).days + 1, WY_COVERAGE)
            row.append(None if r is None else r[0] * ((b - a).days + 1) * CFS_DAY_AF)
        by[k] = row
    total = [None if any(by[k][i] is None for k in keys) else float(sum(by[k][i] for k in keys)) for i in range(len(years))]
    return {"years": years, "by_gauge": by, "total": total}


def summarise(series: dict[str, pd.Series]) -> dict | None:
    used, stale, end = select(series)
    if not used or end is None:
        return None
    windows = [w for d in WINDOWS if (w := window_stats(series, used, end, d))]
    wytd_days = (end - water_year_start(end)).days + 1
    wytd = window_stats(series, used, end, wytd_days) if wytd_days >= MIN_WYTD_DAYS else None
    ent = None
    if "lobatos" in series:
        el = series["lobatos"].index[-1]
        ent = [w for d in WINDOWS if (w := window_stats(series, ["lobatos"], el, d))]
    wy_last = end.year if (end.month, end.day) >= (9, 30) else end.year - 1
    gauges = []
    for g in GAUGES:
        if g["key"] not in series:
            continue
        s = series[g["key"]]
        gauges.append({"key": g["key"], "role": g["role"], "river": g["river"], "name": g["name"], "uids": g["uids"], "first_date": s.index[0].date().isoformat(), "last_date": s.index[-1].date().isoformat(),
                       "years_of_record": int(round((s.index[-1] - s.index[0]).days / 365.25)), "caveats": g["caveats"], "stale": g["key"] in {x["key"] for x in stale}})
    return {"as_of": end.date().isoformat(), "gauges": gauges, "stale": stale, "windows": windows, "water_year_to_date": wytd, "entrance": ent,
            "entrance_as_of": None if "lobatos" not in series else series["lobatos"].index[-1].date().isoformat(),
            "history": {**water_years(series, used, wy_last), "entrance": water_years(series, ["lobatos"], wy_last)["by_gauge"]["lobatos"] if "lobatos" in series else None},
            "baseline": f"{BASELINE[0]}-{BASELINE[1]}", "members": used}
