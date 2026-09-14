"""Annual reservoir fill for every New Mexico reservoir the archive can do honestly.

Storage as a percent of the capacity table actually in force that year, for each reservoir with
a Reclamation area-capacity (ACAP) table that validates against its operational record.

Writes to reports/:
  reservoir_annual_fill.csv        all reservoirs, one row per reservoir-year
  reservoir_capacity_eras.csv      one row per reservoir and capacity-table vintage
  <reservoir>_annual_fill.csv      one file per reservoir

Run:  uv run python scripts/reservoir_fill.py          (or: just report-reservoirs)

Elephant Butte is NOT built here. It has its own script, scripts/elephant_butte_fill.py, whose
14 capacity-table adoption dates were detected, reviewed by hand, and validated against the
National Inventory of Dams. That curated result is authoritative; this script only folds its
output into the combined CSV. Run the Elephant Butte script first, or `just reports`.

WHY A CAPACITY TABLE IS NOT A CONSTANT
Every one of these reservoirs is silting up, and Reclamation periodically resurveys and reissues
the elevation-to-storage table. Only the most recent table per reservoir is published in
machine-readable form (`reservoir_acap`); earlier ones exist as PDF survey reports. So the
earlier capacities are recovered from the operational record: reported storage minus the modern
table's storage at the same elevation is the sediment deposited since, and extrapolating that
offset to the spillway crest gives the era's full-pool capacity.

WHAT IS EXCLUDED, AND WHY
  Heron       Reclamation's published table stops at 7102 ft / 74,615 AF, far below the
              reservoir's operating range (median observed elevation 7156 ft). No full-pool
              capacity can be derived; percent full is not computable. Needs the complete table.
  everything  100 sites carry a reservoir_storage series and 81 can be joined to a National
  else        Inventory of Dams capacity, but that is design storage which is never corrected
              for sediment. Those sites can carry storage in acre-feet honestly; they cannot
              carry a trustworthy percentage.
"""

from __future__ import annotations

import argparse
from itertools import pairwise
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

# crest_ft      full-pool elevation, detected as the level above which the lake essentially never
#               goes (an uncontrolled spillway shows a sharp cliff in the exceedance counts);
#               verify with --crest.
# elev_shift_ft added to the observed elevation before looking up the ACAP table, where the
#               operational series and the table are published on different vertical datums.
# flood_control the crest is a flood pool well above the conservation pool the reservoir is
#               normally operated to, so percentages read low by design. See interpretation.md.
RESERVOIRS = {
    "Brantley":       {"acap": "Brantley Reservoir", "site": "usbr_hydrodata:937",
                       "crest_ft": 3264.6, "elev_shift_ft": 0.0, "flood_control": True},
    "Lake Sumner":    {"acap": "Lake Sumner", "site": "usbr_hydrodata:943",
                       "crest_ft": 4277.0, "elev_shift_ft": 0.0},
    "El Vado":        {"acap": "El Vado Reservoir", "site": "usbr_hydrodata:2685",
                       "crest_ft": 6900.0, "elev_shift_ft": -1.45},
    "Avalon":         {"acap": "Avalon Reservoir", "site": "usbr_hydrodata:2684",
                       "crest_ft": 3179.0, "elev_shift_ft": 0.0},
    "Nambe Falls":    {"acap": "Nambe Falls Reservoir", "site": "usgs:08294200",
                       "crest_ft": 6827.0, "elev_shift_ft": 0.0},
}
# A year opens a new capacity era when its median offset from the modern table moves by more than
# this much. Absolute floor stops tiny reservoirs splitting on rounding noise.
ERA_TOL_PCT, ERA_TOL_AF = 0.5, 150.0
# An era shorter than this merges into the one before it. Without it a partial final year, or a
# single noisy year, invents a capacity vintage out of one season of data.
MIN_ERA_DAYS = 400


def load(con, cfg) -> tuple[pd.DataFrame, pd.DataFrame]:
    acap = con.execute("select elevation_ft, capacity_af from reservoir_acap "
                       "where reservoir = ? order by 1", [cfg["acap"]]).df()
    daily = con.execute(f"""
        with e as (select datetime_utc d, value elev from observations
                   where site_uid = '{cfg["site"]}' and variable = 'reservoir_elevation'),
             s as (select datetime_utc d, value stor from observations
                   where site_uid = '{cfg["site"]}' and variable = 'reservoir_storage')
        select cast(e.d as date) dt, e.elev, s.stor
        from e join s on e.d = s.d where s.stor >= 0 order by e.d""").df()
    if daily.empty or acap.empty:
        return daily, acap
    daily["dd"] = pd.to_datetime(daily.dt.astype(str))
    daily["y"] = daily.dd.dt.year
    daily["off"] = daily.stor - np.interp(daily.elev + cfg["elev_shift_ft"],
                                          acap.elevation_ft, acap.capacity_af)
    return daily, acap


def crest_scan(daily: pd.DataFrame, span: float = 14.0) -> pd.DataFrame:
    """Exceedance counts near the top of the record. An uncontrolled spillway crest shows up as
    a sharp cliff: the lake sits below it and only briefly surcharges above."""
    mx = daily.elev.max()
    rows = []
    prev = None
    for h in np.arange(np.floor(mx) - span, np.floor(mx) + 2, 1.0):
        k = int((daily.elev >= h).sum())
        rows.append({"elev_ft": h, "days_at_or_above": k,
                     "drop_factor": round(prev / max(k, 1), 1) if prev is not None else None})
        prev = k
    return pd.DataFrame(rows)


def _pava_non_increasing(v: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Weighted pool-adjacent-violators, non-increasing: capacity can only fall, because
    sediment accumulates and none of these reservoirs has been dredged."""
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
        for j in group:
            fit[j] = val
    return fit


def segment_eras(daily: pd.DataFrame, modern_cap: float) -> pd.Series:
    """Assign each year an era id.

    The offset from the modern table is a function of elevation (it is the sediment volume below
    that level), so two years under the SAME table show different median offsets if the lake sat
    at different heights. Comparing raw annual medians therefore invents era boundaries out of
    wet and dry years. Instead each year is compared with the previous one only over the band of
    elevation they both occupied; a new era opens when the offset differs there by more than the
    tolerance. Years with no overlap carry the era forward rather than guessing."""
    tol = max(ERA_TOL_AF, ERA_TOL_PCT / 100.0 * modern_cap)
    years = sorted(daily.y.unique())
    by = dict(daily.groupby("y").__iter__())
    era = {years[0]: 0}
    cur = 0
    for prev, y in pairwise(years):
        a, b = by[prev], by[y]
        lo = max(a.elev.min(), b.elev.min())
        hi = min(a.elev.max(), b.elev.max())
        if hi - lo < 1.0:                      # no usable overlap: assume the table held
            era[y] = cur
            continue
        sa = a[(a.elev >= lo) & (a.elev <= hi)].off.median()
        sb = b[(b.elev >= lo) & (b.elev <= hi)].off.median()
        if pd.notna(sa) and pd.notna(sb) and abs(sb - sa) > tol:
            cur += 1
        era[y] = cur
    out = pd.Series(era, name="era_id")
    # merge away eras too short to support a capacity estimate
    counts = daily.y.map(out).value_counts()
    remap, prev_keep = {}, 0
    for eid in sorted(counts.index):
        if counts[eid] >= MIN_ERA_DAYS or eid == 0:
            remap[eid] = eid
            prev_keep = eid
        else:
            remap[eid] = prev_keep
    return out.map(remap).rename("era_id")


def robust_max_elev(g: pd.DataFrame) -> float:
    """Top of an era's elevation range, ignoring single spurious readings. Lake Sumner has one
    1991 value 12 ft above anything else in 54 years; taking the raw max lets it define the
    era's reach and wrecks the extrapolation."""
    return float(np.percentile(g.elev, 99.5)) if len(g) >= 200 else float(g.elev.max())


def era_capacity(g: pd.DataFrame, crest: float, modern_cap: float) -> float:
    """Extrapolate this era's offset from the modern table up to the spillway crest."""
    mx = robust_max_elev(g)
    top = g[g.elev >= mx - 20]
    if len(top) >= 30 and top.elev.std() > 1:
        slope, icept = np.polyfit(top.elev, top.off, 1)
        return modern_cap + slope * crest + icept
    return modern_cap + g[g.elev >= mx - 5].off.median()


def build(name: str, cfg: dict, daily: pd.DataFrame, acap: pd.DataFrame):
    crest = cfg["crest_ft"]
    modern_cap = float(np.interp(crest, acap.elevation_ft, acap.capacity_af))
    daily = daily.join(segment_eras(daily, modern_cap), on="y")
    pub_tol = max(ERA_TOL_AF, 0.002 * modern_cap)
    rows = []
    for eid, g in daily.groupby("era_id"):
        mx = robust_max_elev(g)
        off = float(g.off.median())
        # When reported storage reproduces from the published table, that table IS in force and
        # its capacity at the crest is the answer. Extrapolating instead would be strictly worse,
        # and for a reservoir that never approaches its crest it is wildly worse.
        published = abs(off) < pub_tol
        cap = modern_cap if published else era_capacity(g, crest, modern_cap)
        rows.append({"reservoir": name, "era_id": int(eid),
                     "first_year": int(g.y.min()), "last_year": int(g.y.max()),
                     "n_days": len(g), "max_elev_ft": round(mx, 1),
                     "gap_to_crest_ft": round(crest - mx, 1),
                     "median_offset_af": round(off), "on_published_table": published,
                     "capacity_af_raw": round(cap)})
    e = pd.DataFrame(rows)
    w = 1.0 / (1.0 + np.maximum(e.gap_to_crest_ft.values, 0))
    w[e.on_published_table.values] = 1e6      # a published capacity is not up for adjustment
    e["capacity_af"] = np.round(_pava_non_increasing(e.capacity_af_raw.values, w)).astype(int)
    e["monotonic_shift_pct"] = (100 * (e.capacity_af - e.capacity_af_raw)
                                / e.capacity_af_raw.replace(0, np.nan)).round(1)
    e["crest_ft"] = crest
    e["modern_table_capacity_af"] = round(modern_cap)
    e["full_pool_basis"] = ("flood pool (flood-control dam; the conservation pool it is normally "
                            "operated to is far lower)" if cfg.get("flood_control")
                            else "spillway crest")

    def conf(r):
        if r.on_published_table:
            return "published"
        if r.gap_to_crest_ft <= 10 and abs(r.monotonic_shift_pct or 0) < 0.5:
            return "high"
        if r.gap_to_crest_ft <= 30 and abs(r.monotonic_shift_pct or 0) < 2:
            return "medium"
        return "low"

    e["capacity_confidence"] = e.apply(conf, axis=1)

    cap = e.set_index("era_id")
    d = daily.assign(capacity_af=daily.era_id.map(cap.capacity_af),
                     capacity_confidence=daily.era_id.map(cap.capacity_confidence))
    g = d.groupby("y")
    a = pd.DataFrame({
        "reservoir": name, "era_id": g.era_id.first(), "capacity_af": g.capacity_af.first(),
        "peak_af": g.stor.max().round(0), "mean_af": g.stor.mean().round(0),
        "low_af": g.stor.min().round(0), "n_days": g.size(),
        "capacity_confidence": g.capacity_confidence.first(),
        "full_pool_basis": "flood pool" if cfg.get("flood_control") else "spillway crest",
    })
    for k in ("peak", "mean", "low"):
        a[f"{k}_pct"] = (100 * a[f"{k}_af"] / a.capacity_af).round(1)
    for k in ("capacity_af", "peak_af", "mean_af", "low_af"):
        a[k] = a[k].astype(int)
    a = a.rename_axis("year").reset_index()
    return e, a[["reservoir", "year", "capacity_af", "peak_af", "peak_pct", "mean_af",
                 "mean_pct", "low_af", "low_pct", "n_days", "capacity_confidence",
                 "full_pool_basis"]]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default="data/duckdb/nmwater.duckdb")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--crest", action="store_true", help="print the crest-detection scan and exit")
    a = ap.parse_args()
    con = duckdb.connect(a.db, read_only=True)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    eras_all, ann_all = [], []
    for name, cfg in RESERVOIRS.items():
        daily, acap = load(con, cfg)
        if daily.empty or acap.empty:
            print(f"{name}: no paired record or no ACAP table, skipped")
            continue
        if a.crest:
            print(f"\n=== {name} (configured crest {cfg['crest_ft']} ft) ===")
            print(crest_scan(daily).to_string(index=False))
            continue
        e, an = build(name, cfg, daily, acap)
        eras_all.append(e)
        ann_all.append(an)
        an.to_csv(out / f"{name.lower().replace(' ', '_')}_annual_fill.csv", index=False)
        pub = (an.capacity_confidence == "published").sum()
        print(f"{name:<16} {an.year.min()}-{an.year.max()}  {len(an):>3} years, {len(e)} eras, "
              f"{pub} years on the published table")
    if a.crest:
        return
    eb = out / "elephant_butte_annual_fill.csv"
    if eb.exists():
        e = pd.read_csv(eb).rename(columns={"capacity_confidence": "capacity_confidence"})
        e.insert(0, "reservoir", "Elephant Butte")
        e["full_pool_basis"] = "spillway crest"
        ann_all.insert(0, e[[c for c in ann_all[0].columns if c in e.columns]])
        print(f"folded in curated Elephant Butte rows from {eb}")
    else:
        print(f"note: {eb} not found -- run scripts/elephant_butte_fill.py for Elephant Butte")
    pd.concat(eras_all, ignore_index=True).to_csv(out / "reservoir_capacity_eras.csv", index=False)
    pd.concat(ann_all, ignore_index=True).to_csv(out / "reservoir_annual_fill.csv", index=False)
    print(f"\nwrote {out/'reservoir_annual_fill.csv'} and {out/'reservoir_capacity_eras.csv'}")


if __name__ == "__main__":
    main()
