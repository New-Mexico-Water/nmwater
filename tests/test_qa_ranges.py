"""The QA report's plausible-range table and the bounds in catalog/variables.yaml (#26)."""

from types import SimpleNamespace

import duckdb
import pandas as pd

from nmwater.catalog.qa import range_lines, range_stats
from nmwater.catalog.variables import VariableRegistry


def _con():
    con = duckdb.connect()
    con.execute("CREATE TABLE variables (variable VARCHAR, unit VARCHAR, valid_min DOUBLE, noise_floor DOUBLE, valid_max DOUBLE)")
    con.execute("INSERT INTO variables VALUES ('swe','in',0,-1,200), ('flow','cfs',NULL,NULL,1000), ('pct','percent',0,NULL,100), ('idx','index',NULL,NULL,NULL), ('newvar','m',NULL,NULL,NULL)")
    con.execute("CREATE TABLE observations (variable VARCHAR, value DOUBLE)")
    rows = [("swe", v) for v in (5, 0.5, -0.4, -0.9, -5, 250)]          # ok, ok, noise band x2, impossible below, impossible above
    rows += [("flow", v) for v in (-3, 10, 5000)]                         # negative but unbounded below; one above the cap
    rows += [("pct", v) for v in (50, 100, 100.2, -1)]                    # one above, one below
    rows += [("idx", v) for v in (-4, 3)] + [("ghost", 7.0)] + [("swe", None)]   # unbounded signed index; a variable missing from the registry; a null
    con.executemany("INSERT INTO observations VALUES (?, ?)", rows)
    return con


def test_range_stats_counts_each_kind_of_value_by_the_same_rules_as_the_qc_flag():
    df = range_stats(_con()).set_index("variable")
    assert df.loc["swe", ["n", "below", "near_zero", "above"]].tolist() == [6, 1, 2, 1]
    assert df.loc["swe", ["lo", "hi"]].tolist() == [-5, 250]
    assert df.loc["flow", ["below", "above", "negative"]].tolist() == [0, 1, 1]
    assert df.loc["pct", ["below", "above"]].tolist() == [1, 1]
    assert "newvar" not in df.index                                          # no data, no row
    assert df.loc["ghost", "valid_min"] != df.loc["ghost", "valid_min"]      # not in the registry: no bounds (NaN)


def test_the_report_lists_unbounded_variables_and_says_why_negatives_are_allowed():
    reg = SimpleNamespace(vars={"flow": SimpleNamespace(signed=False, extra={"unbounded_below_reason": "reverse flow unconfirmed"}),
                                "idx": SimpleNamespace(signed=True, extra={})})
    text = "\n".join(range_lines(range_stats(_con()), reg))
    assert "| swe | in | 0 to 200 | -5 to 250 | 1 | 1 | 2 |" in text
    assert "no bounds at all, so no check: 2 of 5" in text and "idx" in text and "ghost" in text
    assert "flow (1: reverse flow unconfirmed)" in text and "idx (1: negative values expected (signed))" in text
    assert "| ghost | - | no bounds |" in text
    assert "| flow | cfs | at most 1,000 |" in text and "| swe | in | 0 to 200 |" in text


def test_a_clean_catalog_reports_none_unbounded():
    df = pd.DataFrame({"variable": ["a"], "unit": ["m"], "valid_min": [0.0], "noise_floor": [None], "valid_max": [9.0], "n": [3], "lo": [1.0], "hi": [2.0],
                       "below": [0], "near_zero": [0], "above": [0], "negative": [0]})
    text = "\n".join(range_lines(df))
    assert "no bounds at all, so no check: 0 of 1" in text and "no lower bound although" not in text


def test_the_registry_bounds_set_for_26_are_valid_and_physically_exact():
    reg = VariableRegistry()
    assert reg.validate() == []
    for name in ("soil_moisture", "snow_density", "drought_d0_pct", "drought_d4_pct"):
        assert (reg[name].valid_min, reg[name].valid_max) == (0, 100)
    assert (reg["dsci"].valid_min, reg["dsci"].valid_max) == (0, 500)
    assert reg["et_reference"].valid_max == 60 and reg["soil_temp"].valid_min == -50
    for name in ("tds", "chloride", "sulfate", "nitrate", "fluoride", "arsenic", "uranium", "diversion", "reservoir_release", "reservoir_area"):
        assert reg[name].valid_min == 0
    # a variable left open below on purpose says why, and signed ones need no reason
    assert reg["discharge"].valid_min is None and "#61" in reg["discharge"].extra["unbounded_below_reason"]
    assert reg["reservoir_inflow"].signed


# ---- duplicate keys: the store's key (depth included), one source and variable at a time
from nmwater.catalog.qa import duplicate_keys


def _obs_con():
    con = duckdb.connect()
    con.execute("CREATE TABLE site_variables (source VARCHAR, variable VARCHAR)")
    con.execute("INSERT INTO site_variables VALUES ('nrcs','soil_temp'), ('usgs','discharge')")
    con.execute("""CREATE TABLE observations (source VARCHAR, site_uid VARCHAR, variable VARCHAR, datetime_utc TIMESTAMP, interval VARCHAR, statistic VARCHAR, depth_cm DOUBLE, value DOUBLE)""")
    t = "2026-11-01 00:00:00"
    con.executemany("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?)", [
        ("nrcs", "nrcs:1", "soil_temp", t, "hourly", "instant", 5.0, 10.0), ("nrcs", "nrcs:1", "soil_temp", t, "hourly", "instant", 20.0, 9.0),     # two depths, one time: not duplicates
        ("usgs", "usgs:1", "discharge", t, "daily", "mean", None, 5.0), ("usgs", "usgs:1", "discharge", t, "daily", "mean", None, 5.0),            # a real duplicate
        ("usgs", "usgs:2", "discharge", t, "daily", "mean", None, 7.0)])
    return con


def test_sensors_at_different_depths_are_not_duplicates_but_a_repeated_key_is():
    total, unchecked = duplicate_keys(_obs_con())
    assert total == 1 and unchecked == []


def test_the_qa_duplicate_key_is_the_stores_key():
    from nmwater.core.models import OBS_KEY
    assert "depth_cm" in OBS_KEY
