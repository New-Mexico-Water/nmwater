"""Annual reservoir reports for every New Mexico reservoir in catalog/reservoirs.yaml.

For each reservoir: how much water it held each year, how full that was against the pools the
operator actually defines, how much flood storage it used, and how much water came in and went
out. Every capacity is the one in force that year, recovered from the record where the operator
has since replaced its table.

    uv run python scripts/reservoir_fill.py            (or: just reports)
    uv run python scripts/reservoir_fill.py --only cochiti abiquiu
    uv run python scripts/reservoir_fill.py --no-notes

Writes
    reports/reservoir_annual_fill.csv        every reservoir-year
    reports/reservoir_capacity_eras.csv      every capacity-table era, with capacity per pool
    reports/reservoir_pools.csv              pool definitions and where they came from
    reports/reservoirs/<key>_annual_fill.csv one per reservoir
    docs/reports/reservoirs/<key>.md         one notes file per reservoir (generated; edit the
                                             registry text, not the file)

Reads only the DuckDB catalog, the NID CSV archived under data/grids/nid, and the registry. No
network calls. Needs `nmwater fetch usace_cwms --kind ratings --kind levels` and a catalog build.

Method (docs/reports/reservoir-fill.md has the long version)
  1. Capacity table. The operator's current elevation-to-storage table from CWMS
     (reservoir_ratings), validated by reproducing reported storage from reported elevation
     after the table's effective date.
  2. Pools. Top of conservation, top of flood control and so on from CWMS location levels,
     or a full-pool elevation given in the registry with its evidence. Pools are elevations;
     their capacity is looked up in the table, so it changes when the table does.
  3. Eras. Where reported storage departs from the current table at the same elevation, an
     older table was in force. Years are compared only over the elevation band they share,
     because the departure varies with elevation. Each era's capacity at each pool elevation is
     the current table plus that era's departure there, measured locally when the lake was at
     that level during the era and extrapolated when it was not.
  4. Monotone. Capacity at a fixed elevation can only fall, as sediment accumulates. A weighted
     pool-adjacent-violators fit enforces that per pool, with measured capacities pinned.
"""

from __future__ import annotations

import argparse
import math
from itertools import pairwise
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "catalog" / "reservoirs.yaml"
NID_CSV = ROOT / "data" / "grids" / "nid" / "nation.csv"
CFS_DAY_AF = 1.983471  # one cfs for one day, in acre-feet

MIN_ERA_DAYS = 400         # shorter eras merge into the previous one
LOCAL_BAND_FT = 2.0        # departure measured locally if the lake sat within this of the pool elevation
FLOW_MIN_DAYS = 330        # annual volumes only for years this complete

SOURCE_CITES = {
    "usbr_hydrodata": ("Bureau of Reclamation, Upper Colorado Region HydroData",
                       "https://www.usbr.gov/uc/water/hydrodata/"),
    "usace_cwms": ("U.S. Army Corps of Engineers, CWMS Data API, Albuquerque District",
                   "https://cwms-data.usace.army.mil/cwms-data/"),
    "usgs": ("U.S. Geological Survey, National Water Information System",
             "https://api.waterdata.usgs.gov/ogcapi/v1/"),
}


# ---------------------------------------------------------------------------- data access
def connect(db: str):
    con = duckdb.connect(db, read_only=True)
    con.execute("SET TimeZone='UTC'")
    return con


def daily(con, sites: list[str], variables: list[str]) -> pd.DataFrame:
    """Daily means by local calendar date. Daily-or-coarser values are already stored at their
    local date; sub-daily values are true UTC and are shifted to Mountain Standard Time.

    Sub-daily storage and elevation are despiked first: a reading is dropped when it sits far
    from BOTH neighbours on the same side, which a real reservoir cannot do in fifteen minutes.
    Galisteo's CWMS series has a single hourly value of 72,874 acre-feet between zeros; averaged
    into a day it would pass for a flood. Genuine rising and falling limbs have one neighbour on
    each side and are kept."""
    if not sites:
        return pd.DataFrame(columns=["site_uid", "variable", "date", "value", "n"])
    s = ",".join(f"'{x}'" for x in sites)
    v = ",".join(f"'{x}'" for x in variables)
    return con.execute(f"""
        with x as (
            select site_uid, variable, interval, datetime_utc, value,
                   lag(value) over w as pv, lead(value) over w as nv
            from observations where site_uid in ({s}) and variable in ({v})
            window w as (partition by site_uid, variable order by datetime_utc)),
        t as (
            select *, case when variable = 'reservoir_elevation' then 5.0
                           else greatest(50.0, 0.2 * greatest(abs(pv), abs(nv))) end as tol
            from x),
        clean as (
            select * from t
            where interval in ('daily','weekly','monthly','annual')
               or variable not in ('reservoir_storage','reservoir_elevation')
               or pv is null or nv is null
               or not ((value - pv > tol and value - nv > tol) or (pv - value > tol and nv - value > tol)))
        select site_uid, variable,
               case when interval in ('daily','weekly','monthly','annual')
                    then cast(datetime_utc as date)
                    else cast(datetime_utc - interval 7 hour as date) end as date,
               avg(value) as value, count(*) as n
        from clean group by all""").df()


def splice(df: pd.DataFrame, sites: list[str], variable: str) -> pd.DataFrame:
    """One value per date, from the highest-priority site that has one."""
    d = df[df.variable == variable]
    if d.empty:
        return pd.DataFrame(columns=["date", "value", "site_uid"])
    rank = {s: i for i, s in enumerate(sites)}
    d = d.assign(r=d.site_uid.map(rank)).sort_values(["date", "r"])
    return d.drop_duplicates("date")[["date", "value", "site_uid"]].reset_index(drop=True)


def load_nid() -> dict[str, dict]:
    if not NID_CSV.exists():
        return {}
    df = pd.read_csv(NID_CSV, skiprows=1, dtype=str, low_memory=False)
    df = df[df.State == "New Mexico"].copy()
    df["_h"] = pd.to_numeric(df["NID Height (Ft)"], errors="coerce")
    df["_isdam"] = df["Dam Name"].str.contains(r"\bDam\b", case=False, na=False)
    df["_filled"] = df.notna().sum(axis=1)        # prefer the most complete record for a shared id
    df = df.sort_values(["_isdam", "_filled", "_h"], ascending=False)
    return {k: g.iloc[0].to_dict() for k, g in df.groupby("NID ID")}


# ---------------------------------------------------------------------------- capacity pieces
def rating_table(con, loc: str) -> tuple[pd.DataFrame, dict]:
    t = con.execute("select * from reservoir_ratings where location = ? order by elevation_ft", [loc]).df()
    if t.empty:
        return t, {}
    t = t.drop_duplicates("elevation_ft")
    t["storage_af"] = np.maximum.accumulate(t.storage_af.values)   # enforce monotone
    meta = {"rating_id": t.rating_id.iloc[0], "agency": t.rating_agency.iloc[0],
            "effective_date": t.effective_date.iloc[0], "datum": t.elev_datum.iloc[0],
            "description": t.description.iloc[0], "n_points": len(t),
            "elev_min": float(t.elevation_ft.min()), "elev_max": float(t.elevation_ft.max()),
            "stor_max": float(t.storage_af.max())}
    return t[["elevation_ft", "storage_af"]].reset_index(drop=True), meta


def lookup(t: pd.DataFrame, h):
    return np.interp(h, t.elevation_ft.values, t.storage_af.values)


POOL_NAMES = {"conservation": ["Top of Conservation"], "flood": ["Top of Flood"],
              "spillway": ["Spillway Crest"], "dam": ["Top of Dam"],
              "entitlement": ["Top of Entitlement"], "inactive": ["Top of Inactive"]}


def pools_for(con, key: str, cfg: dict, t: pd.DataFrame) -> list[dict]:
    """Pool elevations with provenance. Registry full_pool first, then CWMS levels."""
    out = []
    lv = con.execute("select * from reservoir_levels where location = ?", [cfg.get("rating") or ""]).df()
    shifts = cfg.get("pool_level_shift_ft", 0.0)
    for pool, names in POOL_NAMES.items():
        shift = float(shifts.get(pool, 0.0) if isinstance(shifts, dict) else shifts)
        e = lv[(lv.level_name.isin(names)) & (lv.parameter == "Elev")].sort_values("level_date")
        if e.empty:
            continue
        r = e.iloc[-1]
        s = lv[(lv.level_name.isin(names)) & (lv.parameter == "Stor")].sort_values("level_date")
        out.append({"pool": pool, "elev_ft": float(r.value) + shift,
                    "source": f"CWMS level {r.level_id} dated {r.level_date}"
                              + (f", shifted {shift:+.2f} ft to the table datum" if shift else ""),
                    "comment": r.comment if isinstance(r.comment, str) else None,
                    "level_storage_af": float(s.value.iloc[-1]) if len(s) else None,
                    "level_storage_date": s.level_date.iloc[-1] if len(s) else None})
    if cfg.get("full_pool"):
        fp = cfg["full_pool"]
        out = [p for p in out if p["pool"] != "full"]
        out.insert(0, {"pool": "full", "elev_ft": float(fp["elev_ft"]), "source": fp["source"],
                       "comment": None, "level_storage_af": None, "level_storage_date": None})
    for p in out:
        p["capacity_current_af"] = float(lookup(t, p["elev_ft"])) if not t.empty else None
        p["reservoir"] = key
    return out


def pool_history(con, cfg: dict, pool: dict | None) -> pd.DataFrame:
    """Dated published capacities for a pool from CWMS levels, as a step function by date.

    When the operator records a pool's storage under successive tables or entitlements (Cochiti's
    1,200-acre recreation pool under eight survey tables, Brantley's annual entitlement,
    Abiquiu's easement change), those published values replace the derived ones from their
    date forward. Level dates of 1900-01-01 mean "as built" and apply from the start.
    Values more than a factor of two from the median are dropped as entry errors."""
    if not pool or not cfg.get("rating"):
        return pd.DataFrame(columns=["from_date", "af", "note"])
    if cfg.get("pool_history") is False:
        return pd.DataFrame(columns=["from_date", "af", "note"])
    names = cfg.get("pool_history_levels") or POOL_NAMES.get(pool["pool"], [])
    if not names:
        return pd.DataFrame(columns=["from_date", "af", "note"])
    lv = con.execute("select level_date, value, comment from reservoir_levels where location = ? "
                     "and parameter = 'Stor' and level_name in (" + ",".join("?" * len(names)) + ")",
                     [cfg["rating"], *names]).df()
    if lv.empty:
        return pd.DataFrame(columns=["from_date", "af", "note"])
    med = lv.value.median()
    lv = lv[(lv.value > 0.5 * med) & (lv.value < 2 * med)]
    lv = lv.sort_values("level_date").drop_duplicates("level_date", keep="last")
    return pd.DataFrame({"from_date": pd.to_datetime(lv.level_date), "af": lv.value.values,
                         "note": lv.comment.fillna("").values})


def primary_and_flood(role: str, pools: list[dict]) -> tuple[dict | None, dict | None]:
    by = {p["pool"]: p for p in pools}
    flood = by.get("flood")
    if role == "dry_flood":
        return flood, flood
    primary = by.get("full") or by.get("conservation") or by.get("spillway")
    if role != "flood_control":
        flood = flood if (flood and primary and flood["elev_ft"] > primary["elev_ft"] + 0.5) else None
    return primary, flood


def era_tolerance(pr: pd.DataFrame, pool_af: float) -> float:
    """Smallest departure step treated as a table change: three times the record's own
    day-to-day noise in departure, but never below 0.1% of the pool or 5 acre-feet. A fixed share
    of the pool misses real but small table changes at large reservoirs (Heron's 2011 change was
    1,300 acre-feet on 400,000) and splits noisy small ones."""
    mad = pr.groupby("y").off.agg(lambda x: float(np.median(np.abs(x - np.median(x)))))
    noise = float(np.median(mad)) if len(mad) else 0.0
    return max(5.0, 0.001 * pool_af, 3.0 * noise)


def segment_eras(pr: pd.DataFrame, tol: float) -> pd.Series:
    """Era id per year. Each year is compared with the previous one over the elevations both
    occupied, because departure varies with elevation. When the two years share no elevation
    band (the lake fell or rose past everything the previous year touched, as at Navajo in 2022),
    their median departures are compared directly rather than assuming nothing changed."""
    years = sorted(pr.y.unique())
    by = dict(iter(pr.groupby("y")))
    era, cur = {years[0]: 0}, 0
    for prev, y in pairwise(years):
        a, b = by[prev], by[y]
        lo, hi = max(a.elev.min(), b.elev.min()), min(a.elev.max(), b.elev.max())
        if hi - lo >= 1.0:
            sa = a[(a.elev >= lo) & (a.elev <= hi)].off.median()
            sb = b[(b.elev >= lo) & (b.elev <= hi)].off.median()
        else:
            sa, sb = a.off.median(), b.off.median()
        if pd.notna(sa) and pd.notna(sb) and abs(sb - sa) > tol:
            cur += 1
        era[y] = cur
    out = pd.Series(era)
    counts = pr.y.map(out).value_counts()
    remap, keep = {}, 0
    for eid in sorted(counts.index):
        if counts[eid] >= MIN_ERA_DAYS or eid == 0:
            keep = eid
        remap[eid] = keep
    return out.map(remap)


def departure_at(g: pd.DataFrame, h: float) -> tuple[float, float]:
    """This era's departure from the current table at elevation h, and how far that reached."""
    near = g[(g.elev - h).abs() <= LOCAL_BAND_FT]
    if len(near) >= 30:
        return float(near.off.median()), 0.0
    hi, lo = float(np.percentile(g.elev, 99.5)), float(np.percentile(g.elev, 0.5))
    if h > hi:
        band, gap = g[g.elev >= hi - 20], h - hi
    elif h < lo:
        band, gap = g[g.elev <= lo + 20], lo - h
    else:   # inside the range but sparsely visited
        band, gap = g[(g.elev - h).abs() <= 10], 0.0
    if len(band) >= 30 and band.elev.std() > 1:
        slope, icept = np.polyfit(band.elev, band.off, 1)
        return float(slope * h + icept), gap
    return float(band.off.median() if len(band) else g.off.median()), gap


def dry_threshold(flood_af: float) -> float:
    """Storage above this at a dry dam counts as holding a flood: 5 acre-feet, or 0.05% of the
    flood pool if larger (about 45 af at Galisteo), so residual ponding and sensor noise do not."""
    return max(5.0, 0.0005 * (flood_af or 0.0))


def pava(v: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Weighted pool-adjacent-violators, non-increasing."""
    v, w = v.astype(float).copy(), w.astype(float).copy()
    idx = [[i] for i in range(len(v))]
    i = 0
    while i < len(v) - 1:
        if v[i] < v[i + 1] - 1e-9:
            v[i] = (v[i] * w[i] + v[i + 1] * w[i + 1]) / (w[i] + w[i + 1])
            w[i] += w[i + 1]
            idx[i] += idx[i + 1]
            v, w = np.delete(v, i + 1), np.delete(w, i + 1)
            idx.pop(i + 1)
            i = max(i - 1, 0)
        else:
            i += 1
    fit = np.empty(sum(len(x) for x in idx))
    for val, group in zip(v, idx):
        fit[group] = val
    return fit


# ---------------------------------------------------------------------------- one reservoir
def build(con, key: str, cfg: dict) -> dict:
    sites = list(cfg.get("sites") or [])
    gauges = list(cfg.get("release_gauges") or [])
    flows = list(dict.fromkeys((cfg.get("inflow_sites") or []) + (cfg.get("release_sites") or [])))
    raw = daily(con, list(dict.fromkeys(sites + flows)),
                ["reservoir_storage", "reservoir_elevation", "reservoir_inflow", "reservoir_release"])
    if gauges:
        gq = daily(con, gauges, ["discharge"])
        gq = gq.assign(variable="reservoir_release")     # measured just below the dam
        raw = pd.concat([raw, gq], ignore_index=True)
    stor = splice(raw[raw.site_uid.isin(sites)], sites, "reservoir_storage").rename(columns={"value": "stor"})
    stor = stor[stor.stor >= 0]
    stor["date"] = pd.to_datetime(stor.date)
    if cfg.get("start_year"):
        stor = stor[stor.date.dt.year >= int(cfg["start_year"])]
    stor["y"] = stor.date.dt.year

    t, rmeta = rating_table(con, cfg["rating"]) if cfg.get("rating") else (pd.DataFrame(), {})
    override = cfg.get("capacity_basis_override")
    pools = pools_for(con, key, cfg, t) if not t.empty and not override else []
    primary, flood = primary_and_flood(cfg["role"], pools)

    # ---- validation: does the current table reproduce reported storage after it took effect?
    shift = float(cfg.get("elev_shift_ft", 0.0))
    ps = cfg.get("paired_site")
    pr = pd.DataFrame()
    valid = {}
    if ps and not t.empty and not override:
        e = raw[(raw.site_uid == ps) & (raw.variable == "reservoir_elevation")][["date", "value"]]
        s = raw[(raw.site_uid == ps) & (raw.variable == "reservoir_storage")][["date", "value"]]
        pr = e.merge(s, on="date", suffixes=("_e", "_s")).rename(columns={"value_e": "elev", "value_s": "stor"})
        pr["date"] = pd.to_datetime(pr.date)
        pr = pr[(pr.stor >= 0) & pr.elev.notna()].sort_values("date")
        pr["y"] = pr.date.dt.year
        pr["off"] = pr.stor - lookup(t, pr.elev + shift)
        eff = pd.Timestamp(rmeta["effective_date"]) if rmeta.get("effective_date") else pr.date.min()
        after = pr[pr.date >= eff]
        if len(after) >= 20:
            ab = (after.off).abs()
            valid = {"n": len(after), "since": str(eff.date()), "median_abs_af": float(ab.median()),
                     "p95_abs_af": float(np.percentile(ab, 95)),
                     "median_rel_pct": float(np.median(ab / np.maximum(after.stor, 1)) * 100)}

    # ---- eras and capacity per pool
    ent = next((p for p in pools if p["pool"] == "entitlement"), None)
    if ent and primary and abs(ent["elev_ft"] - primary["elev_ft"]) < 1.0:
        ent = None       # same pool under another name (Sumner, Brantley)
    pool_list = [p for p in pools if (p["pool"] in ("full", "conservation", "flood", "spillway")
                                      and (p["capacity_current_af"] or 0) > 0)
                 or (ent is not None and p is ent)]
    eras = pd.DataFrame()
    if len(pr) and primary:
        prim_cap = primary["capacity_current_af"] or 1.0
        tol = era_tolerance(pr, max(prim_cap, flood["capacity_current_af"] if flood else 0))
        if cfg.get("era_boundaries"):
            b = sorted(pd.Timestamp(x) for x in cfg["era_boundaries"])
            # boundaries fall on 30 Nov / 31 Dec; assign by date so the swap day counts
            pr["era_id"] = [sum(1 for x in b if x <= d) - 1 for d in pr.date]
        else:
            pr["era_id"] = pr.y.map(segment_eras(pr, tol))
        pub_tol = max(3.0, 0.002 * max(prim_cap, 1.0))
        rows = []
        for eid, g in pr.groupby("era_id"):
            med = float(g.off.median())
            published = abs(med) < pub_tol and abs(float(g[g.date >= g.date.max() - pd.Timedelta(days=365)].off.median())) < pub_tol
            row = {"reservoir": key, "era_id": int(eid), "first_date": g.date.min().date(), "last_date": g.date.max().date(),
                   "n_days": len(g), "min_elev_ft": round(float(np.percentile(g.elev, 0.5)), 1),
                   "max_elev_ft": round(float(np.percentile(g.elev, 99.5)), 1),
                   "median_departure_af": round(med), "on_current_table": published}
            for p in pool_list:
                if published:
                    dep, gap = 0.0, 0.0
                else:
                    dep, gap = departure_at(g, p["elev_ft"])
                row[f"{p['pool']}_raw_af"] = p["capacity_current_af"] + dep
                row[f"{p['pool']}_gap_ft"] = round(gap, 1)
            rows.append(row)
        eras = pd.DataFrame(rows)
        for p in pool_list:
            n = p["pool"]
            w = 1.0 / (1.0 + eras[f"{n}_gap_ft"].values)
            w[eras.on_current_table.values] = 1e6
            eras[f"{n}_af"] = np.round(pava(eras[f"{n}_raw_af"].values, w))
            eras[f"{n}_shift_pct"] = (100 * (eras[f"{n}_af"] - eras[f"{n}_raw_af"])
                                      / eras[f"{n}_raw_af"].replace(0, np.nan)).round(1)

            def conf(r, n=n):
                if r.on_current_table:
                    return "current_table"
                sh = abs(r[f"{n}_shift_pct"]) if pd.notna(r[f"{n}_shift_pct"]) else 0
                if r[f"{n}_gap_ft"] <= 0.0 and sh < 0.5:
                    return "high"
                if r[f"{n}_gap_ft"] <= 10 and sh < 1:
                    return "high"
                if r[f"{n}_gap_ft"] <= 30 and sh < 2:
                    return "medium"
                return "low"
            eras[f"{n}_confidence"] = eras.apply(conf, axis=1)
            eras[f"{n}_raw_af"] = eras[f"{n}_raw_af"].round()
        # label
        eras["label"] = [("current table" if r.on_current_table else f"{r.first_date.year}-{r.last_date.year} table")
                         for r in eras.itertuples()]

    # ---- attach capacities to each storage day
    st = stor.copy()
    if len(eras) and primary:
        edates = eras[["era_id", "first_date"]].copy()
        edates["first_date"] = pd.to_datetime(edates.first_date)
        edates = edates.sort_values("first_date")
        idx = np.searchsorted(edates.first_date.values, st.date.values, side="right") - 1
        before = idx < 0
        st["era_id"] = edates.era_id.values[np.clip(idx, 0, None)]
        er = eras.set_index("era_id")
        pn = primary["pool"]
        st["primary_af"] = st.era_id.map(er[f"{pn}_af"])
        st["confidence"] = st.era_id.map(er[f"{pn}_confidence"])
        st.loc[before, "confidence"] = "before_elevation_record"
        # storage days with no paired elevation that day, inside the paired span, keep the era's value
        if flood:
            st["flood_af"] = st.era_id.map(er[f"{flood['pool']}_af"])
        if "conservation" in {p["pool"] for p in pool_list} and cfg["role"] == "flood_control":
            st["cons_af"] = st.era_id.map(er["conservation_af"])
        st["table_label"] = st.era_id.map(er["label"])
        st.loc[before, "table_label"] = "earliest derived table (no elevation record yet)"
    elif primary:
        st["primary_af"] = primary["capacity_current_af"]
        st["confidence"] = "current_table_unverified"
        st["table_label"] = "current table"
        if flood:
            st["flood_af"] = flood["capacity_current_af"]
    elif override:
        st["primary_af"] = float(override["af"])
        st["confidence"] = "owner_reported"
        st["table_label"] = "dam registry normal storage"
    else:
        st["primary_af"] = np.nan
        st["confidence"] = "none"
        st["table_label"] = "none"

    if ent is not None and len(eras):
        st["ent_af"] = st.era_id.map(eras.set_index("era_id")["entitlement_af"])
        eh = pool_history(con, {**cfg, "pool_history_levels": ["Top of Entitlement"]}, ent)
        if len(eh):
            ix = np.searchsorted(eh.from_date.values, st.date.values, side="right") - 1
            st.loc[ix >= 0, "ent_af"] = eh.af.values[ix[ix >= 0]]

    hist = pool_history(con, cfg, primary) if primary and not override else pd.DataFrame()
    if len(hist):
        idx = np.searchsorted(hist.from_date.values, st.date.values, side="right") - 1
        on = idx >= 0
        st.loc[on, "primary_af"] = hist.af.values[idx[on]]
        st.loc[on, "confidence"] = "published_pool"
        st.loc[on, "table_label"] = "published pool capacity"
        if "cons_af" in st:
            st.loc[on, "cons_af"] = st.loc[on, "primary_af"]

    role = cfg["role"]
    if role == "dry_flood":
        thr = dry_threshold(st.flood_af.max() if "flood_af" in st else 0.0)
        st["flood_day"] = st.stor > thr
    elif role == "flood_control" and "cons_af" in st:
        # above the conservation pool by more than ordinary pool fluctuation: 2% of the flood
        # space or 5% of the pool, whichever is larger
        fl_af = st["flood_af"] if "flood_af" in st else st.cons_af * 2
        margin = np.maximum(0.02 * (fl_af - st.cons_af), 0.05 * st.cons_af)
        st["flood_day"] = st.stor > st.cons_af + margin
    else:
        st["flood_day"] = False

    # ---- flows
    fl = {}
    for var, key2 in (("reservoir_inflow", "inflow"), ("reservoir_release", "release")):
        ss = (gauges if key2 == "release" else []) + (cfg.get(f"{key2}_sites") or [])
        f = splice(raw[raw.site_uid.isin(ss)], ss, var)
        # computed inflow is a mass balance and is legitimately negative on some days; keep them,
        # the annual sum is the net volume
        if len(f):
            f["date"] = pd.to_datetime(f.date)
            # a gauge below the dam may predate it (San Juan near Archuleta runs from 1954, Navajo
            # Dam closed in 1962): flow before storage began is river flow, not release
            f = f[f.date >= st.date.min()]
            f["y"] = f.date.dt.year
        fl[key2] = f

    # ---- annual table
    g = st.groupby("y")
    a = pd.DataFrame({
        "n_days": g.size(),
        "capacity_table": g.table_label.agg(lambda x: x.mode().iloc[0] if len(x) else None),
        "capacity_confidence": g.confidence.agg(lambda x: x.mode().iloc[0] if len(x) else None),
        "full_pool_af": g.primary_af.median() if "primary_af" in st else np.nan,
        "peak_af": g.stor.max(), "mean_af": g.stor.mean(), "low_af": g.stor.min(),
        "peak_date": g.apply(lambda x: x.loc[x.stor.idxmax(), "date"].date(), include_groups=False),
    })
    for k in ("peak", "mean", "low"):
        a[f"{k}_pct"] = 100 * a[f"{k}_af"] / a.full_pool_af
    if flood is not None and "flood_af" in st:
        a["flood_pool_af"] = g.flood_af.median()
        a["peak_flood_pct"] = 100 * a.peak_af / a.flood_pool_af
    if role in ("flood_control", "dry_flood"):
        a["days_flood_storage"] = g.flood_day.sum().astype(int)
    if "ent_af" in st:
        a["entitlement_af"] = g.ent_af.median()
        a["peak_entitlement_pct"] = 100 * a.peak_af / a.entitlement_af
    for k2, f in fl.items():
        if len(f):
            fg = f.groupby("y")
            vol = fg.value.sum() * CFS_DAY_AF
            nd = fg.size()
            a[f"{k2}_af"] = vol.where(nd >= FLOW_MIN_DAYS)
            a[f"peak_{k2}_cfs"] = fg.value.max()
            a[f"peak_{k2}_date"] = fg.apply(lambda x: x.loc[x.value.idxmax(), "date"].date(), include_groups=False)
            a[f"n_days_{k2}"] = nd
    a = a.reset_index().rename(columns={"y": "year"})
    a.insert(0, "reservoir", key)
    a.insert(1, "role", role)
    for c in a.columns:
        if c.endswith("_af") or c.endswith("_cfs"):
            a[c] = a[c].round(0)
        if c.endswith("_pct"):
            a[c] = a[c].round(1)

    return {"key": key, "cfg": cfg, "annual": a, "eras": eras, "pools": pools, "primary": primary,
            "flood": flood, "rating": rmeta, "valid": valid, "raw": raw, "stor": st, "flows": fl,
            "paired": pr, "hist": hist}


# ---------------------------------------------------------------------------- notes
def _fmt(x, nd=0):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:,.{nd}f}"


def render_notes(con, r: dict, nid: dict) -> str:
    k, cfg = r["key"], r["cfg"]
    n = nid.get(cfg.get("nid_id"), {})
    L = [f"# {cfg['name']}", "",
         "_Generated by `scripts/reservoir_fill.py` from `catalog/reservoirs.yaml`. Edit the registry, "
         "not this file._", "", cfg["summary"].strip(), "",
         f"Report: `reports/reservoirs/{k}_annual_fill.csv`. Regenerate with `just reports`. "
         "Shared method and column definitions: [../reservoir-fill.md](../reservoir-fill.md).", ""]
    if k == "elephant_butte":
        L += ["The curated, hand-reviewed treatment of this reservoir, with its full capacity history, is "
              "[../elephant-butte-fill.md](../elephant-butte-fill.md).", ""]

    def nv(c):
        v = n.get(c)
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else v

    L += ["## Dam at a glance", "", "| | |", "|---|---|",
          f"| Dam | {cfg['dam']} |", f"| River | {cfg['river']} |", f"| Basin | {cfg['basin']} |",
          f"| Owner | {_owner(nv('Owner Names'))} ({nv('Primary Owner Type') or 'n/a'}) |"]
    if nv("Federal Agency Involvement Operation"):
        L.append(f"| Operated with | {nv('Federal Agency Involvement Operation')} |")
    L += [f"| Dam type | {str(nv('Dam Types') or 'n/a').replace(';', ', ')} |",
          f"| Purposes, per the dam registry | {str(nv('Purposes') or 'n/a').replace(';', ', ')} |",
          f"| How it is operated | {ROLE_TEXT[cfg['role']]} |",
          f"| Completed | {nv('Year Completed') or 'n/a'} |",
          f"| Height | {_fmt(float(nv('NID Height (Ft)')) if nv('NID Height (Ft)') else None)} ft |",
          f"| Drainage area | {_fmt(float(nv('Drainage Area (Sq Miles)')) if nv('Drainage Area (Sq Miles)') else None)} sq mi |",
          f"| Hazard class | {nv('Hazard Potential Classification') or 'n/a'} |",
          f"| Dam registry capacity | {_fmt(float(nv('NID Storage (Acre-Ft)')) if nv('NID Storage (Acre-Ft)') else None)} af maximum, "
          f"{_fmt(float(nv('Normal Storage (Acre-Ft)')) if nv('Normal Storage (Acre-Ft)') else None)} af normal. "
          "Owner-reported and not corrected for sediment; not used as a denominator unless stated. |",
          f"| National Inventory of Dams id | {cfg.get('nid_id')} |", ""]

    L += ["## How to use this data", ""] + [f"- {u}" for u in cfg.get("uses", [])] + [""]
    L += ["## Read before quoting a number", ""] + [f"- {c}" for c in ROLE_CAUTION.get(cfg["role"], [])] \
        + [f"- {c}" for c in cfg.get("cautions", [])] + [""]

    # provenance
    L += ["## Data provenance", "", "| Series | Site | Observations (daily) | From | To |", "|---|---|---|---|---|"]
    raw = r["raw"]
    used = set(r["stor"].site_uid.unique()) if "site_uid" in r["stor"] else set()
    for (site, var), gg in raw.groupby(["site_uid", "variable"]):
        lab = "release (gauge below dam)" if site in (cfg.get("release_gauges") or []) else var.replace("reservoir_", "")
        L.append(f"| {lab} | `{site}` | {len(gg):,} | {pd.Timestamp(gg.date.min()).date()} | "
                 f"{pd.Timestamp(gg.date.max()).date()} |")
    L += [""]
    days = r["stor"].groupby("site_uid").size().sort_values(ascending=False)
    if len(days) > 1:
        L += ["Storage is spliced day by day in registry priority order. Days taken from each site: "
              + ", ".join(f"`{s}` {c:,}" for s, c in days.items()) + ".", ""]
        ov = _overlap(raw, list(cfg["sites"]))
        if ov:
            L += ["Agreement where sites overlap, median absolute difference in storage: "
                  + "; ".join(ov) + ".", ""]
    if cfg.get("release_gauges"):
        L += ["Release is taken first from the USGS gauge just below the dam ("
              + ", ".join(f"`{g}`" for g in cfg["release_gauges"]) + "), which measures what actually "
              "leaves the reservoir plus minor local inflow and has a longer record than the operator's "
              "reported release; the reported release fills days the gauge lacks.", ""]
    if cfg.get("start_year"):
        L += [f"Years before {cfg['start_year']} are excluded: {cfg.get('start_year_reason', 'the dam was not yet in operation')}.", ""]
    srcs = sorted({s.split(":")[0] for s in raw.site_uid.unique()})
    L += ["Sources:", ""] + [f"- {SOURCE_CITES[s][0]}, `{SOURCE_CITES[s][1]}`, accessed 2026." for s in srcs if s in SOURCE_CITES] + [""]
    _ = used

    # capacity
    L += ["## Capacity and pools", ""]
    rm, v = r["rating"], r["valid"]
    ov = cfg.get("capacity_basis_override")
    if ov:
        L += [f"**No validated operator table.** Percentages are against {_fmt(ov['af'])} acre-feet, the "
              f"{ov['source']}. Storage figures themselves are the operator's and are unaffected.", ""]
        if rm:
            L += [f"An operator table exists in CWMS (`{rm['rating_id']}`, effective {rm['effective_date']}), but without "
                  "an elevation series it cannot be validated or used.", ""]
    elif rm:
        L += [f"**Capacity table:** `{rm['rating_id']}` from the Corps' CWMS, the operator's current elevation-to-storage "
              f"table, maintained by {rm['agency'] or 'the operator'}, effective {rm['effective_date']}, elevations in "
              f"{rm['datum'] or 'the project datum (unstated)'}. {rm['n_points']:,} points from "
              f"{_fmt(rm['elev_min'], 2)} to {_fmt(rm['elev_max'], 2)} ft, {_fmt(rm['stor_max'])} acre-feet at the top.", ""]
        if rm.get("description"):
            L += [f"Table note: _{rm['description']}_", ""]
        if v:
            L += [f"**Validation.** Since {v['since']}, looking up each day's reported elevation in this table reproduces "
                  f"reported storage to a median of {_fmt(v['median_abs_af'], 1)} acre-feet "
                  f"({v['median_rel_pct']:.2f}%), 95th percentile {_fmt(v['p95_abs_af'], 1)} acre-feet, over "
                  f"{v['n']:,} days.", ""]
        if float(cfg.get("elev_shift_ft", 0)):
            L += [f"Observed elevations are shifted {cfg['elev_shift_ft']:+.2f} ft before lookup.", ""]
        acap = con.execute("select any_value(item_id) i, any_value(survey_label) s, max(capacity_af) c "
                           "from reservoir_acap where reservoir ilike ?", [f"%{cfg['name'].split()[0]}%"]).fetchone()
        if acap and acap[0]:
            L += [f"Reclamation's sedimentation-survey table (RISE catalog item {acap[0]}, survey {acap[1]}) is also in "
                  "the archive as `reservoir_acap` and serves as corroboration; the CWMS table is used because it is "
                  "what the operator computes storage with today.", ""]
    if r["pools"]:
        L += ["**Pools.** Pools are defined by elevation; capacity is looked up in the current table, so it changes "
              "when the table does. The published storage for each level, where the Corps records one, is shown "
              "for comparison and often comes from an older table.", "",
              "| Pool | Elevation (ft) | Capacity, current table (af) | Published level storage (af) | Source |",
              "|---|---|---|---|---|"]
        for p in r["pools"]:
            L.append(f"| {POOL_LABEL.get(p['pool'], p['pool'])} | {_fmt(p['elev_ft'], 2)} | {_fmt(p['capacity_current_af'])} | "
                     f"{_fmt(p['level_storage_af'])} | {p['source']}"
                     + (f"; _{p['comment'].strip()}_" if p.get("comment") else "") + " |")
        L += [""]
    h = r.get("hist")
    if h is not None and len(h):
        L += ["**Published pool capacity history.** The operator records this pool's capacity under successive "
              "tables or entitlements. From each date below, that published figure is used as the denominator "
              "instead of the derived capacity, and those years carry the confidence `published_pool`. A level "
              "dated 1900-01-01 is the as-built figure and applies from the start of the record.", "",
              "| From | Capacity (af) | Note |", "|---|---|---|"]
        for x in h.itertuples():
            L.append(f"| {x.from_date.date()} | {_fmt(x.af)} | {x.note or ''} |")
        L += [""]
    if r["primary"]:
        L += [f"**Full pool used for percentages:** {POOL_LABEL.get(r['primary']['pool'], r['primary']['pool']).lower()}, "
              f"{_fmt(r['primary']['elev_ft'], 2)} ft."
              + (f" **Flood pool:** top of flood control, {_fmt(r['flood']['elev_ft'], 2)} ft." if r["flood"] else ""), ""]

    # method specifics
    e = r["eras"]
    if len(e):
        how = "forced from the reviewed adoption dates in the registry" if cfg.get("era_boundaries") else \
            "detected by comparing each year with the previous one over the elevations both occupied"
        L += ["## Capacity history", "",
              f"Reported storage departs from the current table in earlier years because older tables were in force. "
              f"{len(e)} table {'era was' if len(e) == 1 else 'eras were'} found, {how}. Departure is the median of reported storage minus the current "
              "table's storage at the same elevation. Capacities are at today's pool elevations"
              + (", so where a published pool capacity history exists above it takes precedence for percentages."
                 if h is not None and len(h) else "."), ""]
        pcols = [c[:-3] for c in e.columns if c.endswith("_af") and not c.endswith("_raw_af") and c != "median_departure_af"]
        hdr = "| Era | Dates | Elevation range (ft) | Departure (af) | " + " | ".join(
            f"{POOL_LABEL.get(pc, pc)} capacity (af), confidence" for pc in pcols) + " |"
        L += [hdr, "|" + "---|" * (4 + len(pcols))]
        for row in e.itertuples():
            cells = [f"{_fmt(getattr(row, pc + '_af'))}, {getattr(row, pc + '_confidence')}" for pc in pcols]
            L.append(f"| {row.label} | {row.first_date} to {row.last_date} | {row.min_elev_ft}-{row.max_elev_ft} | "
                     f"{_fmt(row.median_departure_af)} | " + " | ".join(cells) + " |")
        L += ["", "Confidence: `current_table` means reported storage reproduces from the current table; `high` means "
              "the lake reached the pool elevation during that era, or came within 10 ft; `medium` within 30 ft; "
              "`low` farther, or the monotone fit had to move the estimate more than 2%.", ""]
        first_cap, last_cap = e[f"{pcols[0]}_af"].iloc[0], e[f"{pcols[0]}_af"].iloc[-1]
        if first_cap and first_cap > last_cap:
            L += [f"Capacity of the {POOL_LABEL.get(pcols[0], pcols[0]).lower()} fell from {_fmt(first_cap)} to "
                  f"{_fmt(last_cap)} acre-feet over the record, {100 * (1 - last_cap / first_cap):.0f}%.", ""]
    elif not ov:
        L += ["## Capacity history", "", "No elevation-paired record long enough to identify earlier tables; every year "
              "is measured against the current table.", ""]

    L += _highlights(r)
    return "\n".join(L).rstrip() + "\n"


ACRONYMS = {"USACE", "DOI", "BLM", "BIA", "NM", "USBR", "US", "ISC"}
SMALL = {"of", "and", "the", "for", "de"}


def _owner(v) -> str:
    if not v or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    words = []
    for i, w in enumerate(str(v).split()):
        if w.upper() in ACRONYMS:
            words.append(w.upper())
        elif w.lower() in SMALL and i:
            words.append(w.lower())
        else:
            words.append("-".join(p.capitalize() for p in w.split("-")))
    return " ".join(words)


def _overlap(raw: pd.DataFrame, sites: list[str]) -> list[str]:
    s = raw[raw.variable == "reservoir_storage"]
    out = []
    for a_, b_ in pairwise(sites):
        m = s[s.site_uid == a_][["date", "value"]].merge(s[s.site_uid == b_][["date", "value"]], on="date")
        if len(m) >= 30:
            d = (m.value_x - m.value_y).abs()
            out.append(f"`{a_}` vs `{b_}` {d.median():,.0f} af over {len(m):,} shared days")
    return out


def _highlights(r: dict) -> list[str]:
    a = r["annual"]
    full = a[a.n_days >= 330]
    if full.empty:
        return []
    L = ["## What the record shows", "",
         f"Record: {a.year.min()} to {a.year.max()}, {len(a)} years, {int(a.n_days.sum()):,} days of storage.", ""]
    role = r["cfg"]["role"]
    if role == "dry_flood":
        thr = dry_threshold(float(a.flood_pool_af.max()) if "flood_pool_af" in a else 0.0)
        top = full[full.peak_af > thr].nlargest(5, "peak_af")
        L += [f"Largest flood-storage years, by peak storage (a flood is storage above {thr:,.0f} af): " + "; ".join(
            f"{int(x.year)} {_fmt(x.peak_af)} af on {x.peak_date}"
            + (f" ({x.peak_flood_pct:.1f}% of the flood pool)" if "peak_flood_pct" in a and pd.notna(x.peak_flood_pct) else "")
            for x in top.itertuples()) + ".", ""]
        if "days_flood_storage" in a:
            L += [f"Years with any flood storage: {int((a.days_flood_storage > 0).sum())} of {len(a)}.", ""]
    elif role == "flood_control":
        if "peak_flood_pct" in a:
            fy = full[full.days_flood_storage > 0].nlargest(5, "peak_flood_pct")
            if len(fy):
                L += ["Largest use of the flood pool: " + "; ".join(
                    f"{int(x.year)} peaking at {x.peak_flood_pct:.1f}% of flood capacity on {x.peak_date}, "
                    f"{int(x.days_flood_storage)} days above the conservation pool" for x in fy.itertuples()) + ".", ""]
            L += [f"Years holding flood water: {int((full.days_flood_storage > 0).sum())} of {len(full)} complete years.", ""]
        lo = full.nsmallest(3, "low_pct")
        L += ["Lowest conservation storage, by annual low: " + "; ".join(
            f"{int(x.year)} {x.low_pct:.1f}%" for x in lo.itertuples()) + ".", ""]
        if len(full) >= 20:
            f10, l10 = full.head(10).mean_pct.mean(), full.tail(10).mean_pct.mean()
            L += [f"Average annual mean storage as a share of the conservation pool: {f10:.1f}% over the first ten "
                  f"complete years, {l10:.1f}% over the last ten. Values above 100% include flood storage.", ""]
    else:
        if full.peak_pct.notna().any():
            hi = full.nlargest(3, "peak_pct")
            lo = full.nsmallest(3, "peak_pct")
            L += ["Fullest years, by annual peak: " + "; ".join(f"{int(x.year)} {x.peak_pct:.1f}%" for x in hi.itertuples()) + ".",
                  "", "Emptiest years, by annual peak: " + "; ".join(f"{int(x.year)} {x.peak_pct:.1f}%" for x in lo.itertuples()) + ".", ""]
            if len(full) >= 20:
                f10, l10 = full.head(10).mean_pct.mean(), full.tail(10).mean_pct.mean()
                L += [f"Average annual mean fill: {f10:.1f}% over the first ten complete years, {l10:.1f}% over the last ten.", ""]
    if "inflow_af" in a and a.inflow_af.notna().sum() >= 5:
        iv = a.dropna(subset=["inflow_af"])
        L += [f"Annual inflow, complete years: median {_fmt(iv.inflow_af.median())} af, largest {_fmt(iv.inflow_af.max())} "
              f"af in {int(iv.loc[iv.inflow_af.idxmax(), 'year'])}, smallest {_fmt(iv.inflow_af.min())} af in "
              f"{int(iv.loc[iv.inflow_af.idxmin(), 'year'])}.", ""]
    if "peak_release_cfs" in a and a.peak_release_cfs.notna().any():
        rv = a.dropna(subset=["peak_release_cfs"]).nlargest(3, "peak_release_cfs")
        lab = "measured below the dam" if r["cfg"].get("release_gauges") else "reported"
        L += [f"Highest daily releases, {lab}: " + "; ".join(f"{_fmt(x.peak_release_cfs)} cfs on {x.peak_release_date}"
                                                    for x in rv.itertuples()) + ".", ""]
    last = a.iloc[-1]
    L += [f"{int(last.year)} so far ({int(last.n_days)} days): peak {_fmt(last.peak_af)} af"
          + (f", {last.peak_pct:.1f}% of full pool" if pd.notna(last.get('peak_pct')) else "")
          + f"; latest low {_fmt(last.low_af)} af.", ""]
    return L


ROLE_TEXT = {
    "storage": "Storage reservoir: holds water for later release.",
    "flood_control": "Flood control with a conservation or recreation pool beneath a much larger flood pool.",
    "dry_flood": "Dry flood control: no permanent pool; stores water only while holding back a flood.",
    "diversion": "Diversion forebay for irrigation canals, routinely drawn down.",
    "municipal": "Municipal water supply.",
}
ROLE_CAUTION = {
    "flood_control": ["This is a flood-control dam. Percent full is measured against the conservation pool, which "
                      "is what the dam normally holds. Storage above it is flood water being held back, measured "
                      "by `days_flood_storage` and `peak_flood_pct`. Dividing storage by the dam's total or design "
                      "capacity answers a different question and will read very low."],
    "dry_flood": ["This is a dry dam. It is empty by design, and percent full is measured against the flood pool, "
                  "so small numbers are normal. Peak storage, `days_flood_storage` and `peak_flood_pct` carry the "
                  "signal: each is a flood being held."],
    "diversion": ["This is a diversion forebay. Low storage usually reflects canal operations, not dry conditions."],
}
POOL_LABEL = {"full": "Full pool", "conservation": "Top of conservation", "flood": "Top of flood control",
              "spillway": "Spillway crest", "dam": "Top of dam", "entitlement": "Top of entitlement",
              "inactive": "Top of inactive"}


BASIS_TEXT = {"table": "operator table, validated", "owner": "dam registry normal storage (not validated)"}


def _nv(n: dict, k: str) -> str:
    v = n.get(k)
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v)


def _index_row(r: dict, nid: dict) -> dict:
    cfg, a = r["cfg"], r["annual"]
    n = nid.get(cfg.get("nid_id"), {})
    last = a.iloc[-1]
    full = a[a.n_days >= 330]
    basis = "owner" if cfg.get("capacity_basis_override") else "table"
    return {"key": r["key"], "name": cfg["name"], "basin": cfg["basin"], "role": cfg["role"],
            "type": _nv(n, "Primary Dam Type"), "purpose": _nv(n, "Primary Purpose"),
            "owner": _owner(n.get("Owner Names")), "completed": _nv(n, "Year Completed"),
            "first": int(a.year.min()), "last": int(a.year.max()), "basis": BASIS_TEXT[basis],
            "full_af": last.get("full_pool_af"), "last_year": int(last.year), "last_peak_pct": last.get("peak_pct"),
            "median_peak_pct": float(full.peak_pct.median()) if len(full) and "peak_pct" in full else float("nan")}


ROLE_SHORT = {"storage": "storage", "flood_control": "flood control + conservation pool",
              "dry_flood": "dry flood control", "diversion": "diversion forebay", "municipal": "municipal supply"}


def render_index(rows: list[dict]) -> str:
    L = ["# Reservoir reports", "",
         "_Generated by `scripts/reservoir_fill.py`. Method, columns and caveats: "
         "[../reservoir-fill.md](../reservoir-fill.md)._", "",
         "Each reservoir has its own notes file covering the dam, how its data can be used, data provenance, "
         "capacity source and pool definitions, capacity history, and what the record shows. Read the one for the "
         "reservoir you are quoting: several carry warnings that change what a percentage means.", "",
         "| Reservoir | Basin | Dam type | Primary purpose (registry) | How it is operated | Owner | Completed | "
         "Record | Capacity basis | Full pool (af) | Median annual peak | Latest peak |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for x in rows:
        L.append(f"| [{x['name']}]({x['key']}.md) | {x['basin']} | {x['type']} | {x['purpose']} | "
                 f"{ROLE_SHORT[x['role']]} | {x['owner']} | {x['completed']} | {x['first']}-{x['last']} | "
                 f"{x['basis']} | {_fmt(x['full_af'])} | {_fmt(x['median_peak_pct'], 1)}% | "
                 f"{_fmt(x['last_peak_pct'], 1)}% ({x['last_year']}) |")
    L += ["", "Percentages are against the full pool: the conservation pool for flood-control dams (so "
          "values above 100% include flood storage), the flood pool for dry dams (so small values are normal), "
          "and the owner-reported normal storage where no operator table can be validated. The latest year is "
          "partial.", ""]
    return "\n".join(L)


# ---------------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(ROOT / "data" / "duckdb" / "nmwater.duckdb"))
    ap.add_argument("--out", default=str(ROOT / "reports"))
    ap.add_argument("--notes", default=str(ROOT / "docs" / "reports" / "reservoirs"))
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--no-notes", action="store_true")
    args = ap.parse_args()
    reg = yaml.safe_load(REGISTRY.read_text())["reservoirs"]
    con = connect(args.db)
    nid = load_nid()
    out, notes = Path(args.out), Path(args.notes)
    (out / "reservoirs").mkdir(parents=True, exist_ok=True)
    notes.mkdir(parents=True, exist_ok=True)
    ann, eras, pools, index = [], [], [], []
    for key, cfg in reg.items():
        if args.only and key not in args.only:
            continue
        r = build(con, key, cfg)
        a = r["annual"]
        a.to_csv(out / "reservoirs" / f"{key}_annual_fill.csv", index=False)
        ann.append(a)
        if len(r["eras"]):
            eras.append(r["eras"])
        pools += r["pools"]
        if not args.no_notes:
            (notes / f"{key}.md").write_text(render_notes(con, r, nid))
        index.append(_index_row(r, nid))
        v = r["valid"]
        vtxt = f"{v['median_abs_af']:.1f} af median" if v else "-"
        peak = a.peak_pct.iloc[-1] if "peak_pct" in a else float("nan")
        print(f"{key:<15} {a.year.min()}-{a.year.max()} {len(a):>3} yrs  eras {len(r['eras']):>2}  "
              f"validation {vtxt:>16}  latest peak {peak:>6.1f}%")
    if args.only:
        return
    pd.concat(ann, ignore_index=True).to_csv(out / "reservoir_annual_fill.csv", index=False)
    pd.concat(eras, ignore_index=True).to_csv(out / "reservoir_capacity_eras.csv", index=False)
    pd.DataFrame(pools).to_csv(out / "reservoir_pools.csv", index=False)
    if not args.no_notes:
        (notes / "index.md").write_text(render_index(index))
    print(f"wrote {len(ann)} reservoirs to {out}")


if __name__ == "__main__":
    main()
