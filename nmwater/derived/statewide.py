"""Statewide figures for New Mexico: totals and comparisons that cover the whole state. Data only; nmwater/site/statewide.py publishes them.

Every figure is defined by a fixed membership and a stated method, so it can be reproduced and audited:

reservoirs   acre-feet held, and percent full, of named groups of the registry's reservoirs (catalog/reservoirs.yaml), summed day by day. A reservoir is in
             `supply` (measure percent_full: storage reservoirs and forebays with an operator-table capacity and a real full pool), `flood_control` (measure
             conservation_pool: percent of the conservation pool), `dry` (flood_pool_use: empty by design) or `owner_reported` (storage_only: the only capacity is the
             owner's estimate of normal storage, so acre-feet only). Percent full is storage / capacity in force that day, summed over the group (capacity-weighted, never
             a mean of percents), and only on days when every member reports (a member's last reading is carried forward at most FFILL_DAYS). The normal for the date is of
             acre-feet held (see storage_normal).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import river_normal as rn
from .reservoirs import BASELINE, MATERIAL_POINTS, MIN_NORMAL_YEARS, TRUSTED, doy366, normal_for_date  # noqa: F401

GROUP_OF_MEASURE = {"percent_full": "supply", "conservation_pool": "flood_control", "flood_pool_use": "dry", "storage_only": "owner_reported"}
GROUPS = ("supply", "flood_control", "dry", "owner_reported")
PERCENT_GROUPS = ("supply", "flood_control")          # the others have no capacity worth dividing by
FFILL_DAYS = 7                                       # a feed that is a few days behind another still counts
STALE_DAYS = 14                                      # a member whose newest reading is older than this is named, not hidden
WINDOWS = (7, 30, 90, 180, 365)
HISTORY_FROM = {"supply": 1986}                       # the first year every supply member reports (Eagle Nest)


def member_frame(st: pd.DataFrame) -> pd.DataFrame:
    """One reservoir's daily frame (date, stor, primary_af, confidence) as a daily-indexed frame with trusted (capacity figure usable for a normal)."""
    d = st.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.drop_duplicates("date").set_index("date").sort_index()
    out = pd.DataFrame({"stor": d["stor"], "cap": d["primary_af"], "trusted": d["confidence"].isin(TRUSTED)})
    return out.reindex(pd.date_range(out.index.min(), out.index.max(), freq="D"))


def group_series(frames: dict[str, pd.DataFrame], keys: list[str], end: pd.Timestamp | None = None) -> pd.DataFrame:
    """Daily statewide series for a group: storage, capacity, members reporting, complete (every member reports), trusted (every reporting member's capacity usable).

    Each member's last reading is carried forward at most FFILL_DAYS, so feeds that lag each other by a few days still line up."""
    mem = {k: member_frame(frames[k]) for k in keys if k in frames}
    if not mem:
        return pd.DataFrame(columns=["storage_af", "capacity_af", "members", "complete", "trusted"])
    last = max(f.index.max() for f in mem.values())
    idx = pd.date_range(min(f.index.min() for f in mem.values()), end or last, freq="D")
    stor, cap, trusted = {}, {}, {}
    for k, f in mem.items():
        g = f.reindex(idx)
        g[["stor", "cap"]] = g[["stor", "cap"]].ffill(limit=FFILL_DAYS)
        stor[k], cap[k] = g["stor"], g["cap"]
        trusted[k] = g["trusted"].astype("boolean").ffill(limit=FFILL_DAYS)
    S, C, T = pd.DataFrame(stor), pd.DataFrame(cap), pd.DataFrame(trusted)
    n = S.notna().sum(axis=1)
    out = pd.DataFrame({"storage_af": S.sum(axis=1, min_count=1), "capacity_af": C.where(S.notna()).sum(axis=1, min_count=1), "members": n}, index=idx)
    out["complete"] = out["members"] == len(mem)
    out["trusted"] = (T.where(S.notna()).fillna(False).astype(bool)).all(axis=1) & (out["members"] > 0)
    out.loc[out["members"] == 0, ["storage_af", "capacity_af"]] = np.nan
    return out


def latest_complete(s: pd.DataFrame) -> pd.Timestamp | None:
    ok = s[s["complete"] & s["storage_af"].notna()]
    return None if ok.empty else ok.index[-1]


def stale_members(frames: dict[str, pd.DataFrame], keys: list[str], as_of: pd.Timestamp) -> list[dict]:
    """Members whose newest reading is more than STALE_DAYS before as_of (or whose data ends well before the others')."""
    out = []
    for k in keys:
        if k not in frames:
            continue
        last = pd.to_datetime(frames[k]["date"]).max()
        if (as_of - last).days > STALE_DAYS:
            out.append({"key": k, "last_date": last.date().isoformat()})
    return out


def window_change(s: pd.DataFrame, as_of: pd.Timestamp, days: int, percent: bool) -> dict | None:
    """Change in storage (and percent points) over the `days` ending as_of, when both ends are complete days."""
    then = as_of - pd.Timedelta(days=days)
    if then not in s.index or not bool(s.loc[then, "complete"]):
        return None
    a, b = s.loc[as_of], s.loc[then]
    out = {"days": days, "from": then.date().isoformat(), "storage_af": float(a["storage_af"]), "storage_then_af": float(b["storage_af"]), "change_af": float(a["storage_af"] - b["storage_af"])}
    if percent and a["capacity_af"] and b["capacity_af"]:
        pa, pb = 100 * a["storage_af"] / a["capacity_af"], 100 * b["storage_af"] / b["capacity_af"]
        out.update(percent=float(pa), percent_then=float(pb), change_points=float(pa - pb))
    return out


def storage_normal(s: pd.DataFrame, floor_af: float | None = None) -> tuple[dict | None, float | None, str | None, bool, float | None]:
    """(normal band by day of year, percentile of the latest storage, class, floored, the median for the date) for a group's *acre-feet held*, or Nones when
    there are too few years. The normal is of water held, not of percent of a pool: individual capacity tables changed over the decades but the water was
    measured, so the statewide normal does not depend on how well each reservoir's capacity is known. Only days on which every member reports count, in 1991-2020,
    at least MIN_NORMAL_YEARS years, +/-3 days. A reading within `floor_af` of the median is called normal whatever its percentile."""
    ok = s[s["complete"] & s["storage_af"].notna()]
    st = pd.DataFrame({"date": ok.index, "pct": ok["storage_af"].to_numpy(), "confidence": "high"})
    normal = normal_for_date(st)
    if normal is None or ok.empty:
        return None, None, None, False, None
    d = int(doy366(pd.Series([ok.index[-1]]))[0])
    sample = normal["_samples"][d - 1]
    if not len(sample):
        return normal, None, None, False, None
    now = float(ok["storage_af"].iloc[-1])
    median = float(normal["p50"][d - 1])
    pctile = rn.percentile_rank(np.asarray(sample), now)
    klass = rn.classify(pctile)
    floored = False
    if floor_af is not None and klass != "normal" and abs(now - median) < floor_af:
        klass, floored = "normal", True
    return normal, float(pctile), klass, floored, median
