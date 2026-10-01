"""Move stored observations to the variable catalog/crosswalk_sites.csv now gives them.

The crosswalk's site overrides relabel new fetches (for example a reservoir's gauge height sent under
the pool-elevation code becomes `stage`). This moves the rows already in the Parquet store from the old
variable's partition to the new one, so no re-fetch or full reprocess is needed. A rule may name one
site or "*" (every site of the source) and may require a provider qualifier (a SHEF code, say).

Safe to re-run: a second run finds nothing to move, and an interrupted run is finished by running it
again (rows are written to the new partition before the old file is removed; if a run stops between the
two steps, `nmwater compact <source>` removes the duplicate rows). Rebuild the catalog afterwards
(`nmwater catalog build`).

    uv run python scripts/relabel_series.py [--dry-run]
"""

from __future__ import annotations

import sys
import uuid

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from nmwater.catalog.crosswalk import Crosswalk
from nmwater.core.config import Settings


def _mask(t: pa.Table, site: str, param: str, qual: str):
    m = pc.equal(t["source_param"], param)
    if site != "*":
        m = pc.and_(m, pc.equal(t["site_uid"], site))
    if qual:
        m = pc.and_(m, pc.equal(t["qualifier"], qual))
    return m


def main(dry: bool = False) -> None:
    ts = Settings.load().parquet_dir / "timeseries"
    xw = Crosswalk()
    # (source, old variable) -> [(site, param, qualifier, new variable)]
    plan: dict[tuple[str, str], list[tuple[str, str, str, str]]] = {}
    for (site, param, qual), new in xw.site_overrides.items():
        src = site.split(":", 1)[0] if site != "*" else None
        sources = [src] if src else sorted(p.name.split("=", 1)[1] for p in ts.glob("source=*"))
        for s in sources:
            e = xw.lookup(s, param)
            if e is None or e.variable == new:
                continue
            plan.setdefault((s, e.variable), []).append((site, param, qual, new))
    for (src, old), rules in sorted(plan.items()):
        base = ts / f"source={src}" / f"variable={old}"
        for f in sorted(base.glob("year=*/*.parquet")):
            t = pq.read_table(f)
            if not {"source_param", "site_uid", "qualifier"} <= set(t.column_names):
                continue
            sel = None
            for site, param, qual, _ in rules:
                m = _mask(t, site, param, qual)
                sel = m if sel is None else pc.or_(sel, m)
            n = pc.sum(sel).as_py() or 0
            if not n:
                continue
            print(f"{f.relative_to(ts)}: {n:,} rows")
            if dry:
                continue
            moved, kept = t.filter(sel), t.filter(pc.invert(sel))
            for site, param, qual, new in rules:
                part = moved.filter(_mask(moved, site, param, qual))
                if not len(part):
                    continue
                for col, val in (("variable", new), ("unit", xw.registry.unit_of(new))):
                    i = part.schema.get_field_index(col)
                    part = part.set_column(i, col, pa.array([val] * len(part), type=part.schema.field(col).type))
                out = ts / f"source={src}" / f"variable={new}" / f.parent.name
                out.mkdir(parents=True, exist_ok=True)
                tmp = out / f"relabel-{uuid.uuid4().hex[:8]}.parquet.tmp"
                pq.write_table(part, tmp, compression="zstd")
                tmp.rename(tmp.with_suffix(""))
            # rewrite the old file without the moved rows (write first, then swap)
            if len(kept):
                tmp = f.with_name(f"compact-{uuid.uuid4().hex[:8]}.parquet.tmp")
                pq.write_table(kept, tmp, compression="zstd")
                tmp.rename(tmp.with_suffix(""))
            f.unlink()


if __name__ == "__main__":
    main(dry="--dry-run" in sys.argv)
