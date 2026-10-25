"""Recent flooding: which National Weather Service gauges reached a flood category in the last few days.

The categories are the NWS's own, one set per gauge (nmwater fetch nwps -> reference/source=nwps/flood_thresholds, the catalog table flood_thresholds):
action (the NWS starts to watch the river), minor, moderate and major flooding. They are defined on the river's stage, so the stage decides; a gauge that
publishes only flows is judged on its flow. We do not set a threshold of our own.

A single reading is not enough: a spike from ice, debris or a bad transmission would raise a false alarm. A category counts as reached only when two
readings in a row, about half an hour apart, are at or above it.
"""

from __future__ import annotations

import pandas as pd

CATEGORIES = ("action", "minor", "moderate", "major")
FLOODING = ("minor", "moderate", "major")           # "action" is below flooding: it is not announced as flooding
CFS = {"cfs": 1.0, "kcfs": 1000.0}
RUN = 2                                              # readings in a row that must be at or above a level
WINDOW_DAYS = 3                                      # how far back a flood is shown: a gauge that peaked longer ago drops out, and with it the band


def levels(th: dict) -> list[tuple[str, float, str]]:
    """The gauge's categories as (name, level, 'stage'|'flow'), rising. Stage levels are used when the gauge has them, flow levels (in cfs) otherwise."""
    out = []
    for c in CATEGORIES:
        stage, flow = th.get(f"{c}_stage"), th.get(f"{c}_flow")
        if stage is not None and not pd.isna(stage) and th.get("stage_units") in (None, "ft"):
            out.append((c, float(stage), "stage"))
        elif flow is not None and not pd.isna(flow) and th.get("flow_units") in CFS:
            out.append((c, float(flow) * CFS[th["flow_units"]], "flow"))
    return out


def rank_of(stage: float | None, flow: float | None, lv: list[tuple[str, float, str]]) -> int:
    """0 below every category, else 1 + the index in CATEGORIES of the highest category the reading meets."""
    r = 0
    for c, level, kind in lv:
        v = stage if kind == "stage" else flow
        if v is not None and not pd.isna(v) and v >= level:
            r = max(r, CATEGORIES.index(c) + 1)
    return r


def assess(readings: pd.DataFrame, th: dict) -> dict | None:
    """One gauge over a window. `readings`: columns time, stage (ft) and flow (cfs), either may be missing; `th`: its flood_thresholds row.
    Returns None without readings or categories; otherwise: reached (highest category held for RUN readings in a row, or None), peak_time and
    peak_stage / peak_flow at the highest reading, last_time, and current (the category of the last RUN readings, or None)."""
    lv = levels(th)
    if readings.empty or not lv:
        return None
    d = readings.sort_values("time").reset_index(drop=True)
    stage = d["stage"] if "stage" in d else pd.Series([None] * len(d))
    flow = d["flow"] if "flow" in d else pd.Series([None] * len(d))
    ranks = pd.Series([rank_of(s, f, lv) for s, f in zip(stage, flow)])
    held = ranks.rolling(RUN, min_periods=RUN).min().fillna(0).astype(int)
    top = int(held.max())
    # the peak is the highest reading of the run that held the top category (or of the whole window when nothing was reached)
    pool = d[ranks >= top] if top else d
    key = "stage" if "stage" in d and d["stage"].notna().any() else "flow"
    p = pool.loc[pool[key].idxmax()] if pool[key].notna().any() else d.iloc[-1]
    cur = int(held.iloc[-1])
    name = lambda r: CATEGORIES[r - 1] if r else None  # noqa: E731
    return {"reached": name(top), "current": name(cur), "peak_time": p["time"], "peak_stage": _num(p.get("stage")), "peak_flow": _num(p.get("flow")),
            "last_time": d["time"].iloc[-1]}


def _num(v) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def read(con, now: pd.Timestamp, days: int = WINDOW_DAYS) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(flood_thresholds, readings of those gauges over the last `days` days up to `now`). Empty frames when the catalog has no thresholds yet."""
    try:
        th = con.sql("SELECT * FROM flood_thresholds").df()
    except Exception:           # a catalog built before the thresholds were fetched has no such table
        th = pd.DataFrame()
    if th.empty:
        return th, pd.DataFrame(columns=["site_uid", "time", "stage", "flow"])
    since = (now - pd.Timedelta(days=days)).to_pydatetime()
    obs = con.execute("""
        SELECT site_uid, datetime_utc AS time,
               max(CASE WHEN variable = 'stage' THEN value END) AS stage,
               max(CASE WHEN variable = 'discharge' THEN value END) AS flow
        FROM observations_clean
        WHERE source = 'nwps' AND site_uid IN (SELECT site_uid FROM flood_thresholds) AND variable IN ('stage', 'discharge') AND datetime_utc >= ?
        GROUP BY 1, 2""", [since]).df()
    obs["time"] = pd.to_datetime(obs["time"], utc=True)
    return th, obs


FRESH = pd.Timedelta(hours=6)       # a gauge whose last reading is older than this is not said to be in flood now: its last word may be a day old


def summarise(th: pd.DataFrame, obs: pd.DataFrame, names: dict[str, dict], now: pd.Timestamp) -> dict | None:
    """The gauges that reached minor flooding or more in the window, worst first, and how many gauges were checked. None when no gauge has readings.
    `names`: site_uid -> {"name", "river", "usgs", "state"}. `current` is the category still held, and only when the gauge reported within FRESH of `now`."""
    rows, checked, last = [], 0, None
    for r in th.to_dict("records"):
        d = obs[obs["site_uid"] == r["site_uid"]]
        a = assess(d, r)
        if a is None:
            continue
        checked += 1
        last = a["last_time"] if last is None else max(last, a["last_time"])
        if a["reached"] not in FLOODING:
            continue
        n = names.get(r["site_uid"], {})
        last_row = d.sort_values("time").iloc[-1]
        rows.append({"key": r["site_uid"], "lid": r["lid"], "name": n.get("name"), "state": n.get("state"), "river": n.get("river"), "usgs_site": n.get("usgs"),
                     "category": a["reached"], "current": a["current"] if a["current"] in FLOODING and now - a["last_time"] <= FRESH else None,
                     "peak_time": a["peak_time"].strftime("%Y-%m-%dT%H:%M:%SZ"), "peak_stage_ft": a["peak_stage"], "peak_flow_cfs": a["peak_flow"],
                     "last_time": a["last_time"].strftime("%Y-%m-%dT%H:%M:%SZ"), "last_stage_ft": _num(last_row["stage"]),
                     "levels_ft": {c: r.get(f"{c}_stage") if not pd.isna(r.get(f"{c}_stage")) else None for c in CATEGORIES}})
    if not checked:
        return None
    rows.sort(key=lambda g: (-CATEGORIES.index(g["category"]), g["peak_time"]), reverse=False)
    return {"checked": checked, "latest_reading": last.strftime("%Y-%m-%dT%H:%M:%SZ"), "gauges": rows}
