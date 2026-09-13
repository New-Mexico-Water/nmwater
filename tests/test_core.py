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
