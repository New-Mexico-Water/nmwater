"""Drop the blank suffix from the stored ose_arcgis point-of-diversion ids.

Until November 2026 the OSE points-of-diversion layer stored a single space where a point has no suffix, so a point's id was `ose_arcgis:pod:RG-27764- `. The layer now stores nothing,
and the source builds `ose_arcgis:pod:RG-27764`. This rewrites the ids of the drilling-time water levels already in the Parquet store to match, so each point keeps one id and the
86,680 points whose observations had no site row get one. Files are rewritten in place (a temporary file, then a rename). Safe to re-run: a second run finds nothing to change.
Rebuild the catalog afterwards (`nmwater catalog build`); `nmwater compact ose_arcgis` removes any row that now repeats.

    uv run python scripts/relabel_ose_pod_ids.py [--dry-run]
"""

from __future__ import annotations

import os
import sys

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from nmwater.core.config import Settings

BLANK_SUFFIX = r"-\s+$"


def fix(t: pa.Table) -> tuple[pa.Table, int]:
    """The table with blank point suffixes removed from its site ids, and how many ids changed."""
    ids = t["site_uid"]
    new = pc.replace_substring_regex(ids, BLANK_SUFFIX, "")
    changed = int(pc.sum(pc.cast(pc.not_equal(ids, new), pa.int64())).as_py() or 0)
    return (t.set_column(t.schema.get_field_index("site_uid"), "site_uid", new), changed) if changed else (t, 0)


def main(dry: bool = False) -> None:
    root = Settings.load().parquet_dir / "timeseries" / "source=ose_arcgis"
    files = sorted(root.rglob("*.parquet"))
    n_files = n_rows = 0
    for f in files:
        t = pq.read_table(f)
        out, n = fix(t)
        if not n:
            continue
        n_files += 1
        n_rows += n
        if dry:
            continue
        tmp = f.with_suffix(".tmp")
        pq.write_table(out, tmp, compression="zstd")
        os.replace(tmp, f)
    verb = "would change" if dry else "changed"
    print(f"{verb} {n_rows:,} ids in {n_files} of {len(files)} files under {root}")


if __name__ == "__main__":
    main(dry="--dry-run" in sys.argv[1:])
