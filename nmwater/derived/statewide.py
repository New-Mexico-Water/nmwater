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


def storage_on(frame: pd.DataFrame, asof: pd.Timestamp) -> float:
    """One reservoir's storage as of `asof`: its last reading on or before that day (a reading that is newer than the group's date must not count, or the shares would not add up to the group)."""
    f = frame.assign(date=pd.to_datetime(frame["date"])).dropna(subset=["stor"])
    f = f[f["date"] <= asof].sort_values("date")
    return float(f["stor"].iloc[-1]) if len(f) else 0.0


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


# Drought -----------------------------------------------------------------------------------------------------------------------------------------------
# The U.S. Drought Monitor's state series for New Mexico (site usdm:state:35): the share of the state in each cumulative category (D0 abnormally dry or worse ...
# D4 exceptional drought) and the Drought Severity and Coverage Index (DSCI, 0-500: the sum of the five cumulative percents). Weekly, valid Tuesdays, since 2000.
# Because the record is only 26 years the percentile is of the same time of year in those years, not of a 30-year normal, and the file says so.

DROUGHT_WEEKS = (1, 4, 13, 26, 52)
DROUGHT_BASELINE = (2000, 2025)
DROUGHT_PLUS_MINUS_DAYS = 21                          # the same time of year: this week of the year and three either side
DROUGHT_VARS = {"d0": "drought_d0_pct", "d1": "drought_d1_pct", "d2": "drought_d2_pct", "d3": "drought_d3_pct", "d4": "drought_d4_pct", "dsci": "dsci"}
DROUGHT_SITE = "usdm:state:35"


def drought_weekly(con) -> pd.DataFrame:
    """Weekly statewide drought: index = valid date (UTC), columns d0..d4 (percent of the state, cumulative) and dsci. Empty when the catalog has no state series."""
    names = ",".join(f"'{v}'" for v in DROUGHT_VARS.values())
    d = con.sql(f"""SELECT (datetime_utc AT TIME ZONE 'UTC')::DATE AS date, variable, value FROM observations
                    WHERE site_uid = '{DROUGHT_SITE}' AND variable IN ({names})""").df()
    if d.empty:
        return pd.DataFrame(columns=list(DROUGHT_VARS))
    w = d.pivot_table(index="date", columns="variable", values="value", aggfunc="last")
    w = w.rename(columns={v: k for k, v in DROUGHT_VARS.items()})
    w.index = pd.to_datetime(w.index)
    return w.reindex(columns=list(DROUGHT_VARS)).dropna(how="any").sort_index()


def drought_summary(w: pd.DataFrame) -> dict | None:
    """The latest week, what changed over DROUGHT_WEEKS, and where this week's DSCI stands among the same time of year in DROUGHT_BASELINE."""
    if w.empty:
        return None
    asof = w.index[-1]
    now = w.iloc[-1]
    changes = []
    for k in DROUGHT_WEEKS:
        then = asof - pd.Timedelta(days=7 * k)
        if then in w.index:
            b = w.loc[then]
            changes.append({"weeks": k, "from": then.date().isoformat(), "dsci_then": float(b["dsci"]), "change_dsci": float(now["dsci"] - b["dsci"]),
                            **{f"{c}_then": float(b[c]) for c in ("d0", "d1", "d2", "d3", "d4")},
                            **{f"change_{c}": float(now[c] - b[c]) for c in ("d0", "d1", "d2", "d3", "d4")}})
    yrs = w[(w.index.year >= DROUGHT_BASELINE[0]) & (w.index.year <= DROUGHT_BASELINE[1]) & (w.index.year != asof.year)]
    doy = yrs.index.dayofyear.to_numpy()
    dist = np.abs(doy - asof.dayofyear)
    dist = np.minimum(dist, 365 - dist)
    sample = yrs[dist <= DROUGHT_PLUS_MINUS_DAYS]["dsci"].to_numpy()
    cats = [c for c in ("d4", "d3", "d2", "d1", "d0") if now[c] > 0]
    return {"as_of": asof.date().isoformat(), "dsci": float(now["dsci"]), **{c: float(now[c]) for c in ("d0", "d1", "d2", "d3", "d4")},
            "worst_category": cats[0].upper() if cats else None, "changes": changes,
            "percentile": None if not len(sample) else float(rn.percentile_rank(sample, float(now["dsci"]))),
            "sample_weeks": int(len(sample)), "sample_years": int(yrs[dist <= DROUGHT_PLUS_MINUS_DAYS].index.year.nunique()),
            "baseline": f"{DROUGHT_BASELINE[0]}-{DROUGHT_BASELINE[1]}",
            "record_start": w.index[0].date().isoformat(), "weeks": int(len(w))}
