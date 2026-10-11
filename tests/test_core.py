"""Offline unit tests for the core: store, crosswalk, ledger, RDB parsing, scope."""

from __future__ import annotations

import pandas as pd
import pyarrow.parquet as pq

from nmwater.catalog.crosswalk import Crosswalk
from nmwater.catalog.variables import VariableRegistry
from nmwater.core.config import Scope
from nmwater.core.ledger import FetchRecord, Ledger, now_iso
from nmwater.core.store import Store
from nmwater.sources.usgs import parse_rdb


def _obs(site, var, dates, values, run="r1", stat="mean", interval="daily"):
    return pd.DataFrame(
        {
            "site_uid": site,
            "variable": var,
            "datetime_utc": pd.to_datetime(dates, utc=True),
            "utc_offset_min": None,
            "value": values,
            "unit": "cfs",
            "interval": interval,
            "statistic": stat,
            "qualifier": "A",
            "source_param": "00060",
            "source_unit": "ft3/s",
            "ingest_run_id": run,
        }
    )


def test_store_partitions_and_compact(tmp_path):
    store = Store(tmp_path / "pq")
    n = store.write_observations(_obs("usgs:1", "discharge", ["2020-01-01", "2020-01-02", "2021-01-01"], [1, 2, 3]), "usgs", run_id="r1")
    assert n == 3
    assert (tmp_path / "pq/timeseries/source=usgs/variable=discharge/year=2020").exists()
    assert (tmp_path / "pq/timeseries/source=usgs/variable=discharge/year=2021").exists()
    # overlapping re-fetch with a revised value; newer run wins after compaction
    store.write_observations(_obs("usgs:1", "discharge", ["2020-01-02", "2020-01-03"], [20, 30], run="r2"), "usgs", run_id="r2")
    removed = store.compact("usgs")
    assert sum(removed.values()) == 1
    files = list((tmp_path / "pq/timeseries/source=usgs/variable=discharge/year=2020").glob("*.parquet"))
    assert len(files) == 1
    df = pq.read_table(files[0]).to_pandas().sort_values("datetime_utc")
    assert df["value"].tolist() == [1.0, 20.0, 30.0]


def test_store_drops_null_values_and_keys(tmp_path):
    store = Store(tmp_path / "pq")
    df = _obs("usgs:1", "discharge", ["2020-01-01", "2020-01-02"], [None, 2])
    assert store.write_observations(df, "usgs", run_id="r1") == 1


def test_sites_roundtrip(tmp_path):
    store = Store(tmp_path / "pq")
    sites = pd.DataFrame({"native_id": ["a", "b"], "name": ["A", "B"], "lat": [35.0, 36.0], "lon": [-106.0, -105.0]})
    assert store.write_sites(sites, "x") == 2
    back = store.read_sites("x")
    assert set(back["site_uid"]) == {"x:a", "x:b"}
    assert "huc8" in back.columns


def test_crosswalk_conversion_and_unmapped():
    xw = Crosswalk(registry=VariableRegistry())
    df = pd.DataFrame(
        {
            "site_uid": ["usgs:1"] * 3,
            "datetime_utc": pd.to_datetime(["2020-01-01"] * 3, utc=True),
            "value": [50.0, 32.0, 1.0],
            "source_param": ["00060", "00011", "99999"],
            "statistic": [None, "max", None],
        }
    )
    out = xw.apply(df, "usgs")
    assert len(out) == 2  # 99999 unmapped and dropped
    row = out[out["source_param"] == "00011"].iloc[0]
    assert row["variable"] == "water_temp"
    assert abs(row["value"] - 0.0) < 1e-6  # 32 F -> 0 C
    assert row["statistic"] == "max"  # existing statistic preserved
    assert row["unit"] == "degC"
    assert out[out["source_param"] == "00060"].iloc[0]["unit"] == "cfs"
    # intentionally unmapped rows (empty variable) behave as unmapped
    assert xw.lookup("usgs", "00025") is None


def test_ledger_roundtrip_and_stats(tmp_path):
    led = Ledger(tmp_path / "ledger.sqlite")
    run = led.start_run("usgs", "fetch", {"since": None})
    rec = FetchRecord(
        request_key="k1", run_id=run, source="usgs", kind="daily", url="http://x", params={"a": 1},
        site_uid="usgs:1", variable="00060", window_start="2020-01-01", window_end="2020-12-31",
        status="ok", http_status=200, sha256="abc", bytes=10, raw_path=None, content_type="text/plain",
        fetched_at=now_iso(),
    )
    led.record(rec)
    led.set_rows("k1", 42)
    got = led.get("k1")
    assert got is not None and got.n_rows == 42 and got.params == {"a": 1}
    assert led.last_window_end("usgs", "usgs:1", "00060") == "2020-12-31"
    led.finish_run(run, "ok")
    st = led.stats("usgs")
    assert st[0]["n"] == 1 and st[0]["rows"] == 42
    assert led.runs("usgs")[0]["n_rows"] == 42
    led.close()


RDB = """# comment line
# another
agency_cd\tsite_no\tdatetime\t1234_00060_00003\t1234_00060_00003_cd
5s\t15s\t20d\t14n\t10s
USGS\t08279500\t1889-01-01\t500\tA
USGS\t08279500\t1889-01-02\t510\tA:e
"""


def test_parse_rdb():
    df = parse_rdb(RDB)
    assert list(df.columns) == ["agency_cd", "site_no", "datetime", "1234_00060_00003", "1234_00060_00003_cd"]
    assert len(df) == 2
    assert df.iloc[1]["1234_00060_00003_cd"] == "A:e"


MULTI_RDB = RDB + """# next site block, different width
agency_cd\tsite_no\tdatetime\t99_72019_00002\t99_72019_00002_cd\t99_72019_00001\t99_72019_00001_cd
5s\t15s\t20d\t14n\t10s\t14n\t10s
USGS\t314816106325901\t2009-08-26\t4.80\tA\t4.90\tA
USGS\t314816106325901\t2009-08-27\t\t\t4.95\tA
"""


def test_parse_rdb_multiblock():
    from nmwater.sources.usgs import iter_rdb_blocks

    blocks = list(iter_rdb_blocks(MULTI_RDB))
    assert [len(b) for b in blocks] == [2, 2]
    assert blocks[1].columns[3] == "99_72019_00002"
    assert parse_rdb(MULTI_RDB).shape[0] == 4


def test_scope_bbox():
    scope = Scope.load()
    assert scope.in_bbox(35.0, -106.0)
    assert not scope.in_bbox(40.0, -106.0)
    assert scope.in_bbox(32.0, -102.8, buffered=True)  # west Texas, inside buffer
    assert not scope.in_bbox(32.0, -102.8, buffered=False)
    assert scope.in_bbox(None, -106.0) is False


def test_store_rejects_impossible_dates(tmp_path):
    store = Store(tmp_path / "pq")
    df = _obs("x:1", "discharge", ["0990-02-23", "2020-01-01", "2316-05-01"], [1, 2, 3])
    assert store.write_observations(df, "x", run_id="r1") == 1


def test_store_rejects_readings_dated_in_the_future(tmp_path):
    """A typo such as 2026-12-08 for a 2016 reading must not be stored. A day ahead (a station's clock) is allowed."""
    store = Store(tmp_path / "pq")
    now = pd.Timestamp.now(tz="UTC")
    when = [(now - pd.Timedelta(days=1)).isoformat(), (now + pd.Timedelta(days=1)).isoformat(), (now + pd.Timedelta(days=30)).isoformat()]
    assert store.write_observations(_obs("x:1", "discharge", when, [1, 2, 3]), "x", run_id="r1") == 2


def test_soil_sensors_at_several_depths_all_survive_writing_and_compaction(tmp_path):
    """Issue #63: the key had no depth, so five depths reporting at the same instant collapsed to one."""
    store = Store(tmp_path / "pq")
    xw = Crosswalk(registry=VariableRegistry())
    when = pd.to_datetime(["2020-06-01T00:00Z"] * 5 + ["2020-06-01T01:00Z"] * 5)
    params = ["SOIL_MOISTURE_5", "SOIL_MOISTURE_10", "SOIL_MOISTURE_20", "SOIL_MOISTURE_50", "SOIL_MOISTURE_100"] * 2
    raw = pd.DataFrame({"site_uid": "uscrn:1", "datetime_utc": when, "value": [0.10, 0.12, 0.15, 0.20, 0.25, 0.11, 0.13, 0.16, 0.21, 0.26], "source_param": params,
                        "interval": "hourly", "ingest_run_id": "r1"})
    obs = xw.apply(raw, "uscrn")
    assert sorted(obs["depth_cm"].unique()) == [5.0, 10.0, 20.0, 50.0, 100.0]
    assert store.write_observations(obs, "uscrn", run_id="r1") == 10                  # all ten rows, not two
    store.write_observations(obs.assign(ingest_run_id="r2", value=lambda d: d["value"] + 1), "uscrn", run_id="r2")      # a re-fetch with revised values
    store.compact("uscrn")
    files = list((tmp_path / "pq/timeseries/source=uscrn/variable=soil_moisture/year=2020").glob("*.parquet"))
    assert len(files) == 1
    df = pq.read_table(files[0]).to_pandas().sort_values(["datetime_utc", "depth_cm"])
    assert len(df) == 10 and df["depth_cm"].tolist() == [5.0, 10.0, 20.0, 50.0, 100.0] * 2
    assert df["value"].round(2).tolist()[:5] == [11.0, 13.0, 16.0, 21.0, 26.0]            # percent (x100) plus the revision of 1: the newer run replaced each depth separately


def test_a_source_without_depths_is_deduplicated_exactly_as_before(tmp_path):
    store = Store(tmp_path / "pq")
    df = _obs("usgs:1", "discharge", ["2020-01-01", "2020-01-01"], [1, 2])            # the same key twice: the last wins
    assert "depth_cm" not in df.columns
    assert store.write_observations(df, "usgs", run_id="r1") == 1
    store.write_observations(_obs("usgs:1", "discharge", ["2020-01-01"], [9], run="r2"), "usgs", run_id="r2")
    assert sum(store.compact("usgs").values()) == 1


def test_crosswalk_files_with_and_without_a_depth_column_load():
    xw = Crosswalk(registry=VariableRegistry())
    assert xw.lookup("uscrn", "SOIL_TEMP_50").depth_cm == 50.0
    assert xw.lookup("nrcs", "SMS:-8").depth_cm == 20.32                                  # 8 inches
    assert xw.lookup("usgs", "00060").depth_cm is None


def test_nrcs_asks_for_depth_elements_with_a_wildcard_depth_and_leaves_the_rest_alone():
    """Issue #63 (NRCS part): the bare code SMS returns nothing from the AWDB API, so no soil data was ever stored."""
    from nmwater.sources.nrcs import request_elements
    assert request_elements(["WTEQ", "SMS", "STO", "SNWD"]) == "WTEQ,SMS:*,STO:*,SNWD"
    assert request_elements(["WTEQ"]) == "WTEQ"


def test_appended_tables_are_merged_and_exact_duplicates_dropped_but_distinct_rows_and_grids_are_kept(tmp_path):
    """Issue #44: whole-table pulls (USGS peaks) append the same rows every run, so the table only grew."""
    store = Store(tmp_path / "pq")
    peaks = pd.DataFrame({"site": ["a", "a", "b"], "year": [2001, 2002, 2001], "peak_cfs": [10.0, 20.0, 30.0]})
    store.append_table(peaks, "reference", "usgs", "peaks", "r1")
    store.append_table(peaks, "reference", "usgs", "peaks", "r2")                                                     # the same pull again
    store.append_table(pd.concat([peaks, pd.DataFrame({"site": ["c"], "year": [2003], "peak_cfs": [40.0]})]), "reference", "usgs", "peaks", "r3")   # plus a new row
    store.append_table(pd.DataFrame({"site": ["a"], "year": [2001], "peak_cfs": [10.5]}), "reference", "usgs", "peaks", "r4")                        # a revised value is a different row
    for run in ("r1", "r2"):
        store.append_table(pd.DataFrame({"file": [run]}), "reference", "prism", "grids", run)
    store.append_table(pd.DataFrame({"x": [1]}), "waterquality", "wqp", "results_a", "r1")                           # one part: untouched
    removed = store.compact_tables()
    td = tmp_path / "pq/reference/source=usgs/peaks"
    files = list(td.glob("*.parquet"))
    assert len(files) == 1 and removed == {"reference/source=usgs/peaks": 6}                                          # 3 + 3 + 4 + 1 rows in, 5 distinct out
    got = pq.read_table(files[0]).to_pandas().sort_values(["site", "year", "peak_cfs"]).reset_index(drop=True)
    assert len(got) == 5 and got["peak_cfs"].tolist() == [10.0, 10.5, 20.0, 30.0, 40.0]
    assert len(list((tmp_path / "pq/reference/source=prism/grids").glob("*.parquet"))) == 2                           # the grid registry is left alone
    assert len(list((tmp_path / "pq/waterquality/source=wqp/results_a").glob("*.parquet"))) == 1
    assert store.compact_tables() == {}                                                                               # nothing left to do


def test_a_table_whose_parts_cannot_be_read_together_is_left_as_it_is(tmp_path):
    store = Store(tmp_path / "pq")
    store.append_table(pd.DataFrame({"v": [1, 2]}), "reference", "usgs", "odd", "r1")
    store.append_table(pd.DataFrame({"v": [[1, 2], [3]]}), "reference", "usgs", "odd", "r2")                          # a list where the first had integers
    assert store.compact_tables() == {}
    assert len(list((tmp_path / "pq/reference/source=usgs/odd").glob("*.parquet"))) == 2
