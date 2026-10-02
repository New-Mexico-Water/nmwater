"""Reservoir fill: how full each reservoir was, against the pools its operator defines, with the capacity in force on each day.

Moved from scripts/reservoir_fill.py (which keeps the Markdown notes and the CSV reports and imports from here) so the site data bundle can publish
the same numbers. Method, in short (docs/reports/reservoir-fill.md has the long version):
  1. Capacity table: the operator's current elevation-to-storage table from CWMS (reservoir_ratings), validated by reproducing reported storage from
     reported elevation after the table's effective date.
  2. Pools: top of conservation, top of flood control and so on from CWMS location levels, or a full-pool elevation given in the registry with its
     evidence. Pools are elevations; their capacity is looked up in the table, so it changes when the table does.
  3. Eras: where reported storage departs from the current table at the same elevation, an older table was in force. Years are compared only over the
     elevation band they share. Each era's capacity at each pool elevation is the current table plus that era's departure there.
  4. Monotone: capacity at a fixed elevation can only fall, as sediment accumulates (weighted pool-adjacent-violators, measured values pinned).
"""

from __future__ import annotations
from itertools import pairwise
import numpy as np
import pandas as pd
import yaml

from ..core.config import PROJECT_ROOT

REGISTRY = PROJECT_ROOT / "catalog" / "reservoirs.yaml"
NID_CSV = PROJECT_ROOT / "data" / "grids" / "nid" / "nation.csv"


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


def load_registry() -> dict[str, dict]:
    return yaml.safe_load(REGISTRY.read_text())["reservoirs"]


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
