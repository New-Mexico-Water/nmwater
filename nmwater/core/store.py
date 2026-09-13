"""Parquet store: observations partitioned by source/variable/year, plus sites and misc tables."""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .models import OBS_KEY, OBS_SCHEMA, SITES_SCHEMA

log = logging.getLogger("nmwater.store")

_SAFE = re.compile(r"[^A-Za-z0-9_.\-]+")
# Oldest plausible hydrologic observation. New Mexico's longest records begin in 1888 (OSE
# streamflow compilation) and 1889 (USGS at Embudo); anything earlier is a data-entry error.
MIN_YEAR = 1850


def _safe(s: str) -> str:
    return _SAFE.sub("_", str(s))


def coerce_to_schema(df: pd.DataFrame, schema: pa.Schema) -> pa.Table:
    """Add missing columns as null, drop extras, cast to schema."""
    cols = {}
    for f in schema:
        if f.name in df.columns:
            s = df[f.name]
        else:
            s = pd.Series([None] * len(df), index=df.index, dtype="object")
        if pa.types.is_timestamp(f.type):
            s = pd.to_datetime(s, utc=True, errors="coerce")
        elif pa.types.is_floating(f.type):
            s = pd.to_numeric(s, errors="coerce")
        elif pa.types.is_integer(f.type):
            s = pd.to_numeric(s, errors="coerce").astype("Int64")
        elif pa.types.is_boolean(f.type):
            s = s.astype("boolean")
        elif pa.types.is_string(f.type):
            s = s.astype("object").where(s.notna(), None)
            s = s.map(lambda v: None if v is None else str(v))
        cols[f.name] = s
    out = pd.DataFrame(cols, index=df.index)
    return pa.Table.from_pandas(out, schema=schema, preserve_index=False)


class Store:
    def __init__(self, parquet_dir: Path):
        self.root = parquet_dir
        self.root.mkdir(parents=True, exist_ok=True)

    # Observations -------------------------------------------------------
    def obs_partition_dir(self, source: str, variable: str, year: int) -> Path:
        return self.root / "timeseries" / f"source={_safe(source)}" / f"variable={_safe(variable)}" / f"year={year}"

    def write_observations(self, df: pd.DataFrame, source: str, run_id: str | None = None,
                           tag: str | None = None) -> int:
        """Write a tidy observations frame. Returns row count written."""
        if df is None or len(df) == 0:
            return 0
        df = df.copy()
        if run_id and ("ingest_run_id" not in df.columns or df["ingest_run_id"].isna().all()):
            df["ingest_run_id"] = run_id
        df["datetime_utc"] = pd.to_datetime(df["datetime_utc"], utc=True, errors="coerce")
        df = df[df["datetime_utc"].notna() & df["site_uid"].notna() & df["variable"].notna()]
        # Guard against impossible timestamps (data-entry typos such as year 990 or 2316).
        if len(df):
            yr = df["datetime_utc"].dt.year
            hi = pd.Timestamp.utcnow().year + 1
            bad = (yr < MIN_YEAR) | (yr > hi)
            if bad.any():
                log.warning("%s: dropped %d observations with implausible dates (%s..%s)",
                            source, int(bad.sum()), yr[bad].min(), yr[bad].max())
                df = df[~bad]
        df = df[df["value"].notna()] if "value" in df.columns else df
        if len(df) == 0:
            return 0
        df = df.drop_duplicates(subset=OBS_KEY, keep="last")
        df["_year"] = df["datetime_utc"].dt.year
        n = 0
        for (variable, year), part in df.groupby(["variable", "_year"], sort=False):
            part = part.drop(columns=["_year"])
            table = coerce_to_schema(part, OBS_SCHEMA)
            d = self.obs_partition_dir(source, variable, int(year))
            d.mkdir(parents=True, exist_ok=True)
            fname = f"part-{run_id or 'adhoc'}-{_safe(tag) + '-' if tag else ''}{uuid.uuid4().hex[:8]}.parquet"
            pq.write_table(table, d / fname, compression="zstd")
            n += table.num_rows
        return n

    def compact(self, source: str | None = None, variable: str | None = None) -> dict[str, int]:
        """Merge part files per partition, dedup on OBS_KEY keeping the newest ingest_run_id."""
        base = self.root / "timeseries"
        out: dict[str, int] = {}
        if not base.exists():
            return out
        src_dirs = [base / f"source={_safe(source)}"] if source else sorted(base.glob("source=*"))
        for sd in src_dirs:
            var_dirs = [sd / f"variable={_safe(variable)}"] if variable else sorted(sd.glob("variable=*"))
            for vd in var_dirs:
                for yd in sorted(vd.glob("year=*")):
                    files = sorted(yd.glob("*.parquet"))
                    if len(files) <= 1:
                        continue
                    tables = [pq.read_table(f, schema=OBS_SCHEMA) for f in files]
                    df = pa.concat_tables(tables).to_pandas()
                    before = len(df)
                    df = df.sort_values("ingest_run_id", kind="stable").drop_duplicates(subset=OBS_KEY, keep="last")
                    table = coerce_to_schema(df, OBS_SCHEMA)
                    tmp = yd / f"compact-{uuid.uuid4().hex[:8]}.parquet.tmp"
                    pq.write_table(table, tmp, compression="zstd")
                    for f in files:
                        f.unlink()
                    tmp.rename(yd / f"compact-{uuid.uuid4().hex[:8]}.parquet")
                    out[str(yd.relative_to(base))] = before - len(df)
        return out

    # Sites ---------------------------------------------------------------
    def sites_path(self, source: str) -> Path:
        d = self.root / "sites" / f"source={_safe(source)}"
        d.mkdir(parents=True, exist_ok=True)
        return d / "sites.parquet"

    def write_sites(self, df: pd.DataFrame, source: str) -> int:
        if df is None or len(df) == 0:
            return 0
        df = df.copy()
        df["source"] = source
        if "site_uid" not in df.columns:
            df["site_uid"] = source + ":" + df["native_id"].astype(str)
        df = df.drop_duplicates(subset=["site_uid"], keep="last")
        table = coerce_to_schema(df, SITES_SCHEMA)
        pq.write_table(table, self.sites_path(source), compression="zstd")
        return table.num_rows

    def read_sites(self, source: str | None = None) -> pd.DataFrame:
        base = self.root / "sites"
        files = [self.sites_path(source)] if source else sorted(base.glob("source=*/sites.parquet"))
        files = [f for f in files if f.exists()]
        if not files:
            return pd.DataFrame(columns=[f.name for f in SITES_SCHEMA])
        return pd.concat([pq.read_table(f).to_pandas() for f in files], ignore_index=True)

    # Misc tables (water use, quality, reference) -------------------------
    def write_table(self, df: pd.DataFrame, group: str, source: str, name: str) -> Path:
        d = self.root / _safe(group) / f"source={_safe(source)}"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{_safe(name)}.parquet"
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, p, compression="zstd")
        return p

    def append_table(self, df: pd.DataFrame, group: str, source: str, name: str, run_id: str) -> Path:
        d = self.root / _safe(group) / f"source={_safe(source)}" / _safe(name)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"part-{run_id}-{uuid.uuid4().hex[:8]}.parquet"
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, p, compression="zstd")
        return p
