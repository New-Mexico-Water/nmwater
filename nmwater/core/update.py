"""Measurement helpers for `nmwater update`: what each source costs to keep current.

Every update appends one row per source to reports/update_log.csv, so the log becomes a record of
how fast the archive grows (net new rows, bytes on disk) and what keeping it current consumes
(requests, bytes downloaded, wall-clock time). See docs/usage.md, "Keeping the archive current".
"""

from __future__ import annotations

import csv
import subprocess
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import pyarrow.parquet as pq

LOG_COLUMNS_DOC = {
    "update_id": "UTC timestamp identifying one `nmwater update` invocation",
    "source": "source name, or _catalog_build / _total",
    "policy": "delta (fetched), skip (not fetched this time) or summary rows",
    "since": "start date requested from the source",
    "status": "ok, partial (some requests failed), error, skipped",
    "fetch_seconds": "wall-clock time of the fetch",
    "compact_seconds": "wall-clock time of compaction afterwards",
    "n_requests": "requests made, including cache hits",
    "n_cached": "requests answered from the raw archive",
    "n_errors": "requests that failed",
    "rows_fetched": "rows written by the fetch, before deduplication",
    "bytes_downloaded": "bytes received from the network this run (ledger)",
    "duplicates_removed": "rows removed by compaction (overlap re-pulled for revisions)",
    "rows_before": "rows in this source's Parquet before the update",
    "rows_after": "rows after fetch and compaction",
    "net_new_rows": "rows_after - rows_before",
    "raw_bytes_before": "data/raw/<source> size before",
    "raw_bytes_after": "data/raw/<source> size after",
    "parquet_bytes_before": "Parquet files for this source, before",
    "parquet_bytes_after": "Parquet files for this source, after",
    "grid_bytes_before": "data/grids/<source> size before",
    "grid_bytes_after": "data/grids/<source> size after",
    "disk_bytes_delta": "total change on disk across raw, Parquet and grids",
    "notes": "free text",
}


@dataclass
class UpdateRow:
    update_id: str
    source: str
    policy: str
    since: str = ""
    status: str = ""
    fetch_seconds: float = 0.0
    compact_seconds: float = 0.0
    n_requests: int = 0
    n_cached: int = 0
    n_errors: int = 0
    rows_fetched: int = 0
    bytes_downloaded: int = 0
    duplicates_removed: int = 0
    rows_before: int = 0
    rows_after: int = 0
    net_new_rows: int = 0
    raw_bytes_before: int = 0
    raw_bytes_after: int = 0
    parquet_bytes_before: int = 0
    parquet_bytes_after: int = 0
    grid_bytes_before: int = 0
    grid_bytes_after: int = 0
    disk_bytes_delta: int = 0
    notes: str = field(default="")


def dir_bytes(p: Path) -> int:
    """Apparent size of a directory tree in bytes (du -sb; fast on large trees)."""
    if not p.exists():
        return 0
    out = subprocess.run(["du", "-sb", str(p)], capture_output=True, text=True, check=False)
    try:
        return int(out.stdout.split()[0])
    except (IndexError, ValueError):
        return 0


def source_parquet(parquet_dir: Path, source: str) -> list[Path]:
    """Every Parquet file belonging to a source: hive dirs named source=<name> anywhere."""
    files: list[Path] = []
    for d in parquet_dir.rglob(f"source={source}"):
        if d.is_dir():
            files += list(d.rglob("*.parquet"))
    return files


def parquet_stats(parquet_dir: Path, source: str) -> tuple[int, int]:
    """(rows, bytes) across a source's Parquet, from file metadata only."""
    rows = size = 0
    for f in source_parquet(parquet_dir, source):
        try:
            rows += pq.ParquetFile(f).metadata.num_rows
            size += f.stat().st_size
        except Exception:
            continue
    return rows, size


def append_log(path: Path, rows: list[UpdateRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = [f.name for f in fields(UpdateRow)]
    new = not path.exists()
    with path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        if new:
            w.writeheader()
        for r in rows:
            w.writerow(asdict(r))
