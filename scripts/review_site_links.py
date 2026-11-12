"""Review the automatic `colocated` surface-water and well site links and promote the pairs the evidence settles (issues #24, #65-#69).

    uv run python scripts/review_site_links.py              # show what would be added
    uv run python scripts/review_site_links.py --write      # append the new rows to catalog/sites_manual.csv

Covers stream, canal, diversion, reservoir and lake pairs between different sources (USGS against water-quality points included) and well pairs (same depth at the same spot, or a shared USGS station number). Springs and weather sites are not reviewed here. For each pair it compares the daily series the two
sites share (stage, reservoir elevation and storage, discharge), reads the names, and applies the rules in nmwater/catalog/link_review.py. A pair the evidence settles becomes a row
in catalog/sites_manual.csv (`same_sensor` or `same_location_different_sensor`, with what settled it); every other pair stays a `colocated` candidate. Pairs already in the file are
left as they are. Reads the DuckDB catalog (read-only); rebuild the catalog afterwards to see the links.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from nmwater.catalog.link_review import MEASURED, Agreement, decide, decide_well

ROOT = Path(__file__).resolve().parents[1]
KINDS = ("stream", "reservoir", "lake", "canal", "diversion")
COMPARE = (*MEASURED, "discharge")


def candidates(con) -> pd.DataFrame:
    return con.sql(f"""
        SELECT l.site_uid_a AS a, a.name AS na, a.source AS sa, l.site_uid_b AS b, b.name AS nb, b.source AS sb, l.distance_m AS m
        FROM site_links l JOIN sites a ON a.site_uid = l.site_uid_a JOIN sites b ON b.site_uid = l.site_uid_b
        WHERE l.link_type = 'colocated' AND a.site_type IN {KINDS} AND b.site_type IN {KINDS} AND a.source <> b.source
        ORDER BY l.distance_m""").df()


def well_candidates(con) -> pd.DataFrame:
    return con.sql("""
        SELECT l.site_uid_a AS a, a.name AS na, a.source AS sa, a.well_depth_m AS da, l.site_uid_b AS b, b.name AS nb, b.source AS sb, b.well_depth_m AS db, l.distance_m AS m
        FROM site_links l JOIN sites a ON a.site_uid = l.site_uid_a JOIN sites b ON b.site_uid = l.site_uid_b
        WHERE l.link_type = 'colocated' AND a.site_type = 'well' AND b.site_type = 'well' AND a.source <> b.source
        ORDER BY l.distance_m""").df()


def shared_series(con, pairs: pd.DataFrame) -> pd.DataFrame:
    """(a, b, variable) for the pairs whose two sites both hold a compared variable over 30 or more days at once."""
    sv = con.sql(f"SELECT site_uid, variable, first_datetime AS f, last_datetime AS l FROM site_variables WHERE variable IN {COMPARE}").df()
    x = pairs[["a", "b"]].merge(sv.rename(columns={"site_uid": "a", "f": "fa", "l": "la"}), on="a").merge(
        sv.rename(columns={"site_uid": "b", "f": "fb", "l": "lb"}), on=["b", "variable"])
    x = x[(x[["la", "lb"]].min(axis=1) - x[["fa", "fb"]].max(axis=1)).dt.days >= 30]
    return x[["a", "b", "variable"]].drop_duplicates()


def compare(con, shared: pd.DataFrame) -> dict[tuple[str, str], list[Agreement]]:
    sites = sorted(set(shared.a) | set(shared.b))
    if not sites:
        return {}
    con.register("review_sites", pd.DataFrame({"site_uid": sites}))
    d = con.sql(f"""SELECT site_uid, variable, CAST(datetime_utc AS DATE) AS d, avg(value) AS v FROM observations_clean
                    WHERE site_uid IN (SELECT site_uid FROM review_sites) AND variable IN {COMPARE} GROUP BY 1, 2, 3""").df()
    daily = {k: v.set_index("d").v for k, v in d.groupby(["site_uid", "variable"])}
    out: dict[tuple[str, str], list[Agreement]] = {}
    for r in shared.itertuples():
        x, y = daily.get((r.a, r.variable)), daily.get((r.b, r.variable))
        if x is None or y is None:
            continue
        j = pd.concat([x, y], axis=1, join="inner").dropna()
        j.columns = ["x", "y"]
        if len(j) < 20:
            continue
        rel = (j.x - j.y).abs() / np.maximum(np.maximum(j.x.abs(), j.y.abs()), 1e-9)
        cor = float(np.corrcoef(j.x, j.y)[0, 1]) if j.x.std() > 0 and j.y.std() > 0 else float("nan")
        out.setdefault((r.a, r.b), []).append(Agreement(r.variable, len(j), float(rel.median()), cor))
    return out


def existing_pairs(path: Path) -> set[frozenset]:
    if not path.exists():
        return set()
    return {frozenset((r["site_uid_a"], r["site_uid_b"])) for r in csv.DictReader(path.open())}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(ROOT / "data" / "duckdb" / "nmwater.duckdb"))
    ap.add_argument("--manual", default=str(ROOT / "catalog" / "sites_manual.csv"))
    ap.add_argument("--write", action="store_true", help="append the new rows to the manual file")
    ap.add_argument("--preview", help="write the new rows to this CSV instead, to read them first")
    args = ap.parse_args()
    con = duckdb.connect(args.db, read_only=True)
    con.execute("SET TimeZone='UTC'")
    con.execute("SET threads=2")
    pairs = candidates(con)
    print(f"{len(pairs):,} colocated surface-water pairs between different sources")
    agree = compare(con, shared_series(con, pairs))
    have = existing_pairs(Path(args.manual))
    rows, skipped = [], 0
    for r in pairs.itertuples():
        if frozenset((r.a, r.b)) in have:
            skipped += 1
            continue
        d = decide({"site_uid": r.a, "name": r.na, "source": r.sa}, {"site_uid": r.b, "name": r.nb, "source": r.sb}, r.m, agree.get((r.a, r.b), []))
        if d:
            rows.append({"site_uid_a": r.a, "site_uid_b": r.b, "link_type": d[0], "evidence": d[1]})
    wells = well_candidates(con)
    print(f"{len(wells):,} colocated well pairs between different sources")
    n_wells = 0
    for r in wells.itertuples():
        if frozenset((r.a, r.b)) in have:
            skipped += 1
            continue
        d = decide_well({"site_uid": r.a, "name": r.na, "source": r.sa}, {"site_uid": r.b, "name": r.nb, "source": r.sb}, r.m,
                        None if pd.isna(r.da) else float(r.da), None if pd.isna(r.db) else float(r.db))
        if d:
            rows.append({"site_uid_a": r.a, "site_uid_b": r.b, "link_type": d[0], "evidence": d[1]})
            n_wells += 1
    pairs = pd.concat([pairs, wells[["a", "b"]]], ignore_index=True)
    out = pd.DataFrame(rows)
    print(f"{skipped} already in the manual file; {len(out):,} settled by the evidence, {len(pairs) - skipped - len(out):,} left as candidates")
    if len(out):
        print(out.groupby("link_type").size().to_string())
    if args.write and len(out):
        p = Path(args.manual)
        with p.open("a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["site_uid_a", "site_uid_b", "link_type", "evidence"], lineterminator="\n")     # the file is LF: csv's default CRLF made DuckDB's CSV reader fail the nightly catalog build
            for row in rows:
                w.writerow(row)
        print(f"appended {len(rows):,} rows to {p}")
    elif len(out) and args.preview:
        out.to_csv(args.preview, index=False)
        print(f"rows written to {args.preview} (not added to the manual file)")


if __name__ == "__main__":
    main()
