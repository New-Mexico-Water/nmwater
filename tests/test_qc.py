"""Tests for missing-value codes and the observations_qc flag."""

import duckdb
import pandas as pd

from nmwater.catalog.build import QC_FLAG_SQL, create_qc_views
from nmwater.core.store import Store, missing_code_mask


def _obs(rows):
    return pd.DataFrame(rows, columns=["variable", "value"])


def test_missing_codes_dropped_only_where_they_are_impossible():
    df = _obs([
        ("precip", -99.9), ("precip", -99.90000000000001), ("precip", 0.4),
        ("discharge", -9999.0), ("discharge", -99.9),                   # -99.9 cfs can be real at a canal
        ("reservoir_delta_storage", -99.9), ("reservoir_delta_storage", -9999.0),   # signed: exempt
        ("wind_speed", -99.9), ("swe", -0.2),
    ])
    hit = missing_code_mask(df).tolist()
    assert hit == [True, True, False, True, False, False, False, True, False]


def test_write_observations_drops_codes_and_keeps_small_negatives(tmp_path):
    store = Store(tmp_path)
    df = pd.DataFrame({
        "site_uid": "s:1", "variable": ["swe", "swe", "precip"], "value": [-0.2, 3.0, -99.9],
        "datetime_utc": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-01"], utc=True),
        "unit": "in", "interval": "daily", "statistic": "mean",
    })
    assert store.write_observations(df, "test", run_id="r1") == 2      # the -99.9 precip is gone
    left = duckdb.connect().execute(
        f"select variable, value from read_parquet('{tmp_path}/timeseries/**/*.parquet') order by value").fetchall()
    assert left == [("swe", -0.2), ("swe", 3.0)]


def test_purge_removes_codes_from_stored_files(tmp_path):
    store = Store(tmp_path)
    d = store.obs_partition_dir("test", "precip", 2026)
    d.mkdir(parents=True)
    df = pd.DataFrame({
        "site_uid": "s:1", "variable": "precip", "value": [1.0, -99.9],
        "datetime_utc": pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True),
        "unit": "in", "interval": "daily", "statistic": "mean",
    })
    import pyarrow.parquet as pq

    from nmwater.core.store import OBS_SCHEMA, coerce_to_schema
    pq.write_table(coerce_to_schema(df, OBS_SCHEMA), d / "part-x.parquet")
    assert store.purge_missing_codes(dry_run=True) == {("test", "precip"): 1}
    assert store.purge_missing_codes() == {("test", "precip"): 1}
    assert store.purge_missing_codes(dry_run=True) == {}


def test_qc_flag_classifies_by_registry_bounds():
    con = duckdb.connect()
    con.execute("CREATE TABLE variables (variable VARCHAR, valid_min DOUBLE, noise_floor DOUBLE, valid_max DOUBLE)")
    con.execute("INSERT INTO variables VALUES ('swe', 0, -1, NULL), ('precip', 0, NULL, NULL), ('discharge', NULL, NULL, NULL), ('do', 0, NULL, 25)")
    con.execute("CREATE TABLE observations (variable VARCHAR, value DOUBLE)")
    con.execute("""INSERT INTO observations VALUES ('swe', 5), ('swe', 0), ('swe', -0.5), ('swe', -1), ('swe', -1.01),
        ('precip', -0.1), ('discharge', -50), ('do', 2374), ('do', 9), ('unknown', -5)""")
    create_qc_views(con)
    got = dict(con.execute("select rowid, qc_flag from (select row_number() over () rowid, qc_flag from observations_qc)").fetchall())
    assert [got[i] for i in range(1, 11)] == [
        "ok", "ok", "near_zero", "near_zero", "implausible", "implausible", "ok", "implausible", "ok", "ok"]
    assert con.execute("select count(*) from observations_clean").fetchone()[0] == 7
    assert "CASE" in QC_FLAG_SQL
