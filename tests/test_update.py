"""Tests for the update log helpers."""

import csv

import pandas as pd

from nmwater.core.update import UpdateRow, append_log, parquet_stats


def test_append_log_writes_header_once(tmp_path):
    p = tmp_path / "log.csv"
    append_log(p, [UpdateRow(update_id="u1", source="a", policy="delta", rows_fetched=5)])
    append_log(p, [UpdateRow(update_id="u1", source="b", policy="skip")])
    rows = list(csv.DictReader(p.open()))
    assert [r["source"] for r in rows] == ["a", "b"]
    assert rows[0]["rows_fetched"] == "5"


def test_parquet_stats_counts_only_the_named_source(tmp_path):
    for src, n in (("usgs", 3), ("nrcs", 7)):
        d = tmp_path / "timeseries" / f"source={src}" / "variable=x" / "year=2026"
        d.mkdir(parents=True)
        pd.DataFrame({"v": range(n)}).to_parquet(d / "part.parquet")
    rows, size = parquet_stats(tmp_path, "usgs")
    assert rows == 3 and size > 0
