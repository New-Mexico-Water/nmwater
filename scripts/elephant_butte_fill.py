"""Elephant Butte annual fill: storage as a percent of the capacity table actually in force.

Writes two CSVs to reports/:
  elephant_butte_capacity_eras.csv  one row per capacity-table vintage
  elephant_butte_annual_fill.csv    one row per year, 1915-present

Run:  uv run python scripts/elephant_butte_fill.py

Why this is not a one-line query. A reservoir's "percent full" needs a capacity, and Elephant
Butte's capacity has been rewritten fourteen times since 1915 as sediment filled the pool. Only
the most recent table (2017/2019 survey) is published in machine-readable form; the rest exist
as PDF survey reports. This script recovers the older ones from the operational record itself.

Method, in four steps:

1. Detect when the table changed. On the day a new table is adopted, reported storage jumps
   while the lake level barely moves. `detect_adoptions` finds days where the storage change
   cannot be explained by the elevation change times the local dV/dh. Almost every adoption
   lands on 31 December. The result was reviewed by hand and frozen in ADOPTIONS below, because
   the detector also fires on transient data-entry errors, which appear as a +N one day and a
   -N the next and must not be treated as table changes.

2. Measure each era's offset from the 2017 table. For every day, off(h) = reported_storage -
   acap2017(h). Physically this is the sediment deposited between that era's survey and 2017,
   below elevation h, so it rises with h and flattens out above the sediment wedge.

3. Extrapolate each era's offset to the spillway crest (4407 ft) to get its full-pool capacity.
   Reliability depends entirely on how close that era's lake got to the crest, which is what
   the `confidence` column reports. This is the weak step and it is labelled as such.

4. Enforce monotonicity. Capacity can only fall: sediment accumulates and Elephant Butte has
   never been dredged. Raw extrapolations violate this for the low-confidence eras, so a
   weighted pool-adjacent-violators fit (weights 1/(1+gap to crest)) pulls the sequence
   non-increasing, letting the eras that actually reached the spillway dominate.

Two independent checks on the result, neither used in fitting:
  1988 survey table -> 2,064,866 AF vs 2,065,010 in the National Inventory of Dams (0.007%)
  original 1915 table -> 2,643,340 AF vs a 2,593,255 design figure (1.9%)

Caveats worth carrying into any report built on this: see docs/reports/elephant-butte-fill.md.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

SITE = "usbr_hydrodata:1119"
RESERVOIR = "Elephant Butte Reservoir"
CREST_FT = 4407.0  # spillway crest, Reclamation Project Vertical Datum (45.0 ft below NAVD88)

# Capacity-table adoption dates, from detect_adoptions() and reviewed by hand.
# Label is the survey the table is believed to come from; "rev" marks a year-end revision that
# does not line up with a known survey year and may be an interim sediment adjustment.
ADOPTIONS = [
    ("1915-01-01", "orig 1915"), ("1925-11-30", "1925 svy"), ("1934-12-31", "1935 svy"),
    ("1939-12-31", "1939 rev"), ("1946-12-31", "1946 rev"), ("1950-12-31", "1947 svy"),
    ("1955-12-31", "1955 rev"), ("1960-12-31", "1960 rev"), ("1969-12-31", "1969 svy"),
    ("1973-12-31", "1973 rev"), ("1980-12-31", "1980 svy"), ("1988-12-31", "1988 svy"),
    ("2000-12-31", "1999 svy"), ("2008-12-31", "2007 svy"), ("2019-12-31", "2017 svy"),
]


def load(con) -> tuple[pd.DataFrame, callable, float]:
    acap = con.execute(
        "select elevation_ft, capacity_af from reservoir_acap where reservoir = ? order by 1",
        [RESERVOIR]).df()
    if acap.empty:
        raise SystemExit("reservoir_acap is empty -- run `nmwater fetch usbr_rise --kind acap` "
                         "then `nmwater catalog build`")
    def capf(h):
        return np.interp(h, acap.elevation_ft, acap.capacity_af)
    daily = con.execute(f"""
        with e as (select datetime_utc d, value elev from observations
                   where site_uid = '{SITE}' and variable = 'reservoir_elevation'),
             s as (select datetime_utc d, value stor from observations
                   where site_uid = '{SITE}' and variable = 'reservoir_storage')
        select e.d::date dt, e.elev, s.stor from e join s on e.d = s.d
        where s.stor > 0 order by e.d""").df()
    daily["dd"] = pd.to_datetime(daily.dt)
    daily["y"] = daily.dd.dt.year
    daily["off"] = daily.stor - capf(daily.elev)
    return daily, capf, float(capf(CREST_FT))


def detect_adoptions(daily: pd.DataFrame) -> pd.DataFrame:
    """Days where storage moves more than the lake level can account for. Diagnostic only:
    the reviewed output is frozen in ADOPTIONS, because transient data errors also fire here."""
    d = daily.copy()
    d["de"] = d.elev.diff()
    d["ds"] = d.stor.diff()
    d["gap"] = d.dd.diff().dt.days
    w = d[(d.gap == 1) & (d.de.abs() > 0.02)].copy()
    w["dvdh"] = w.ds / w.de
    slope = w.groupby([w.dd.dt.year, (w.elev / 10).round()])["dvdh"].median()
    out = []
    for r in d[d.gap == 1].itertuples():
        sl = slope.get((r.dd.year, round(r.elev / 10)))
        if sl is None or not np.isfinite(sl):
            continue
        resid = r.ds - sl * r.de
        if abs(resid) / r.stor > 0.008 and abs(resid) > 3000:
            out.append({"date": r.dt, "elev": r.elev, "unexplained_af": round(resid)})
    return pd.DataFrame(out)


def _pava_non_increasing(v: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Weighted pool-adjacent-violators, constrained non-increasing (sediment only accumulates)."""
    v, w = v.astype(float).copy(), w.astype(float).copy()
    idx = [[i] for i in range(len(v))]
    i = 0
    while i < len(v) - 1:
        if v[i] < v[i + 1] - 1e-9:
            v[i] = (v[i] * w[i] + v[i + 1] * w[i + 1]) / (w[i] + w[i + 1])
            w[i] += w[i + 1]
            idx[i] = idx[i] + idx[i + 1]
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


def build_eras(daily: pd.DataFrame, full17: float) -> pd.DataFrame:
    bounds = [pd.Timestamp(x) for x, _ in ADOPTIONS] + [pd.Timestamp("2100-01-01")]
    labels = [lab for _, lab in ADOPTIONS]
    daily["era"] = pd.cut(daily.dd, bins=bounds, labels=labels, right=False)
    rows = []
    for lab, g in daily.groupby("era", observed=True):
        mx = g.elev.max()
        gap = CREST_FT - mx
        if lab == labels[-1]:
            cap = full17                      # published table, no extrapolation
        else:
            top = g[g.elev >= mx - 20]
            if len(top) >= 30 and top.elev.std() > 1:
                slope, icept = np.polyfit(top.elev, top.off, 1)
                cap = full17 + slope * CREST_FT + icept
            else:
                cap = full17 + g[g.elev >= mx - 5].off.median()
        rows.append({"era": lab, "first_day": g.dt.min(), "last_day": g.dt.max(),
                     "n_days": len(g), "max_elev_ft": round(mx, 1), "gap_to_crest_ft": round(gap, 1),
                     "capacity_af_raw": round(float(cap))})
    e = pd.DataFrame(rows)
    e["capacity_af"] = np.round(_pava_non_increasing(
        e.capacity_af_raw.values, 1.0 / (1.0 + np.maximum(e.gap_to_crest_ft.values, 0)))).astype(int)
    e["monotonic_shift_pct"] = (100 * (e.capacity_af - e.capacity_af_raw) / e.capacity_af_raw).round(1)

    def confidence(r):
        if r.era == labels[-1]:
            return "published"
        if r.gap_to_crest_ft <= 10 and abs(r.monotonic_shift_pct) < 0.5:
            return "high"
        if r.gap_to_crest_ft <= 30 and abs(r.monotonic_shift_pct) < 2:
            return "medium"
        return "low"

    e["confidence"] = e.apply(confidence, axis=1)
    return e


def build_annual(daily: pd.DataFrame, eras: pd.DataFrame) -> pd.DataFrame:
    cap = eras.set_index("era").capacity_af
    conf = eras.set_index("era").confidence
    d = daily.assign(capacity_af=daily.era.map(cap), confidence=daily.era.map(conf))
    g = d.groupby("y")
    o = pd.DataFrame({
        "capacity_table": g.era.first(), "capacity_af": g.capacity_af.first(),
        "peak_af": g.stor.max().round(0), "mean_af": g.stor.mean().round(0),
        "low_af": g.stor.min().round(0), "n_days": g.size(),
        "capacity_confidence": g.confidence.first(),
    })
    for k in ("peak", "mean", "low"):
        o[f"{k}_pct"] = (100 * o[f"{k}_af"] / o.capacity_af).round(1)
    for k in ("capacity_af", "peak_af", "mean_af", "low_af"):
        o[k] = o[k].astype(int)
    return o[["capacity_table", "capacity_af", "peak_af", "peak_pct", "mean_af", "mean_pct",
              "low_af", "low_pct", "n_days", "capacity_confidence"]].rename_axis("year")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default="data/duckdb/nmwater.duckdb")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--show-adoptions", action="store_true",
                    help="print the raw table-change detector output and exit")
    a = ap.parse_args()
    con = duckdb.connect(a.db, read_only=True)
    daily, _capf, full17 = load(con)
    if a.show_adoptions:
        print(detect_adoptions(daily).to_string(index=False))
        return
    eras = build_eras(daily, full17)
    annual = build_annual(daily, eras)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    eras.to_csv(out / "elephant_butte_capacity_eras.csv", index=False)
    annual.to_csv(out / "elephant_butte_annual_fill.csv")
    print(eras.to_string(index=False))
    print(f"\n{len(annual)} years, {annual.index.min()}-{annual.index.max()}; "
          f"2017 published full pool at {CREST_FT} ft = {full17:,.0f} AF")
    print(f"wrote {out/'elephant_butte_capacity_eras.csv'} and {out/'elephant_butte_annual_fill.csv'}")


if __name__ == "__main__":
    main()
