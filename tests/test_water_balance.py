"""The water-balance engine: unit conversion, signs, completeness, storage change, copies and the registry (synthetic data; no catalog needed)."""

import re
from datetime import date

import duckdb
import numpy as np
import pandas as pd
import pytest

from nmwater.derived import water_balance as wb
from nmwater.derived.reservoirs import CFS_DAY_AF

REACH = wb.Reach(
    key="t", title="Test", upstream="a", downstream="b", reading="",
    terms=(
        wb.Term("in", "in", "inflow", "flow", ("usgs:1",)),
        wb.Term("out", "out", "outflow", "flow", ("usgs:2",)),
        wb.Term("stor", "stor", "storage", "storage", ("usgs:3",), "reservoir_storage"),
        wb.Term("evap", "evap", "evaporation", "volume", ("usgs:3",), "reservoir_evaporation"),
        wb.Term("ref", "ref", "reference", "volume", ("usgs:3",), "reservoir_inflow_volume"),
    ),
)


def daily(first: str, last: str, value) -> pd.Series:
    idx = pd.date_range(first, last, freq="D")
    return pd.Series(value if np.ndim(value) else np.full(len(idx), float(value)), index=idx)


def series(inflow=100.0, outflow=60.0, storage=None, evap=0.0, start="2020-12-25", end="2021-02-05"):
    s = {"in": daily(start, end, inflow), "out": daily(start, end, outflow), "evap": daily(start, end, evap), "ref": daily(start, end, 0.0)}
    s["stor"] = storage if storage is not None else daily(start, end, 1000.0)
    return s


def january(s, by="month"):
    df = wb.balance(s, REACH, date(2021, 1, 1), date(2021, 1, 31), by)
    return df.iloc[0]


def test_cfs_for_a_day_is_the_acre_foot_factor():
    r = january(series(inflow=100.0, outflow=0.0))
    assert r.inflow_af == pytest.approx(100 * 31 * CFS_DAY_AF)
    assert CFS_DAY_AF == pytest.approx(86400 / 43560, rel=1e-5)


def test_a_closed_reach_balances_to_zero():
    # 100 cfs in, 60 cfs out, the other 40 cfs fills the reservoir: storage gains 40 cfs-days a day
    idx = pd.date_range("2020-12-25", "2021-02-05")
    stor = pd.Series(5000.0 + np.arange(len(idx)) * 40 * CFS_DAY_AF, index=idx)
    r = january(series(storage=stor))
    assert r.complete and abs(r.unaccounted_af) < 1e-6
    assert r.storage_change_af == pytest.approx(31 * 40 * CFS_DAY_AF)
    assert r.implied_inflow_af == pytest.approx(r.inflow_af)


def test_signs_positive_is_water_that_went_missing_and_negative_is_water_that_appeared():
    assert january(series(inflow=100, outflow=60)).unaccounted_af > 0
    assert january(series(inflow=60, outflow=100)).unaccounted_af < 0


def test_evaporation_is_subtracted_and_the_reference_column_is_not():
    base = january(series(inflow=100, outflow=100, evap=0))
    with_evap = january(series(inflow=100, outflow=100, evap=2.0))
    assert with_evap.unaccounted_af == pytest.approx(base.unaccounted_af - 2.0 * 31)
    s = series()
    s["ref"] = daily("2020-12-25", "2021-02-05", 999.0)
    assert january(s).unaccounted_af == pytest.approx(january(series()).unaccounted_af)


def test_a_few_missing_days_are_scaled_not_lost():
    s = series()
    s["in"] = s["in"].drop(pd.Timestamp("2021-01-10"))                      # 1 of 31 days
    r = january(s)
    assert r.complete and r.inflow_af == pytest.approx(100 * 31 * CFS_DAY_AF)


def test_too_many_missing_days_make_the_period_incomplete_with_no_balance():
    s = series()
    s["out"] = s["out"].drop(pd.date_range("2021-01-10", "2021-01-20"))    # 11 of 31 days
    r = january(s)
    assert not r.complete and np.isnan(r.unaccounted_af) and r.min_coverage < wb.MIN_COVERAGE


def test_storage_change_needs_readings_near_both_edges():
    idx = pd.date_range("2020-12-25", "2021-02-05")
    stor = pd.Series(1000.0, index=idx).drop(pd.date_range("2020-12-28", "2021-01-10"))       # nothing within three days of 31 Dec
    assert not january(series(storage=stor)).complete
    near = pd.Series(1000.0, index=idx).drop(pd.date_range("2020-12-30", "2020-12-31"))      # 29 Dec is within tolerance
    assert january(series(storage=near)).complete


def test_storage_change_uses_the_last_reading_of_the_period_and_the_one_before_it():
    idx = pd.date_range("2020-12-25", "2021-02-05")
    stor = pd.Series(np.where(idx < "2021-01-01", 100.0, np.where(idx <= "2021-01-31", 150.0, 999.0)), index=idx)
    assert january(series(storage=stor)).storage_change_af == 50.0


def test_periods_a_water_year_starts_on_the_first_of_october():
    p = wb.periods(date(2005, 10, 1), date(2007, 3, 1), "water_year")
    assert [x[0] for x in p] == ["WY2006", "WY2007"]
    assert p[0][1] == pd.Timestamp("2005-10-01") and p[0][2] == pd.Timestamp("2006-09-30")
    assert wb.water_year(pd.Timestamp("2006-09-30")) == 2006 and wb.water_year(pd.Timestamp("2006-10-01")) == 2007
    assert [x[0] for x in wb.periods(date(2021, 1, 15), date(2021, 3, 2), "month")] == ["2021-01", "2021-02", "2021-03"]
    with pytest.raises(ValueError):
        wb.periods(date(2021, 1, 1), date(2021, 2, 1), "week")


def test_a_period_after_the_data_end_is_incomplete():
    df = wb.balance(series(end="2021-02-28"), REACH, date(2021, 1, 1), date(2021, 3, 31), "month")
    assert list(df.complete) == [True, True, False]


def test_one_copy_prefers_usgs_and_fills_the_days_it_lacks():
    d = pd.DataFrame({
        "site_uid": ["usbr_hydrodata:9", "usgs:1", "usbr_hydrodata:9"],
        "date": [date(2021, 1, 1), date(2021, 1, 1), date(2021, 1, 2)], "value": [11.0, 10.0, 12.0]})
    s = wb.one_copy(d)
    assert list(s) == [10.0, 12.0]


# ---------------------------------------------------------------------------- reading from the catalog
@pytest.fixture()
def con():
    c = duckdb.connect()
    c.execute("SET TimeZone='UTC'")
    c.execute("""CREATE TABLE observations_clean (site_uid VARCHAR, variable VARCHAR, datetime_utc TIMESTAMPTZ, value DOUBLE, interval VARCHAR, statistic VARCHAR, qualifier VARCHAR)""")
    rows = []
    for i in range(10):                                                    # usgs:1 daily means; usgs:2 hourly readings; usgs:3 storage (daily)
        day = pd.Timestamp("2021-01-01", tz="UTC") + pd.Timedelta(days=i)
        rows.append(("usgs:1", "discharge", day, 100.0, "daily", "mean", "A"))
        rows += [("usgs:2", "discharge", day + pd.Timedelta(hours=h), 50.0, "hourly", None, None) for h in range(24)]
        rows.append(("usgs:3", "reservoir_storage", day, 1000.0 + i, "daily", "mean", None))
    c.executemany("INSERT INTO observations_clean VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return c


def test_read_term_gives_one_value_per_day_for_each_kind(con):
    flow = wb.read_term(con, REACH.terms[0], date(2021, 1, 1), date(2021, 1, 10))
    assert len(flow) == 10 and flow.iloc[0] == 100.0
    hourly = wb.read_term(con, REACH.terms[1], date(2021, 1, 1), date(2021, 1, 10))
    assert len(hourly) == 10 and hourly.iloc[0] == 50.0
    stor = wb.read_term(con, REACH.terms[2], date(2021, 1, 1), date(2021, 1, 10))
    assert list(stor.iloc[:3]) == [1000.0, 1001.0, 1002.0]
    assert wb.read_term(con, wb.Term("none", "n", "inflow", "flow", ("usgs:404",)), date(2021, 1, 1), date(2021, 1, 10)).empty


def test_run_reads_balances_and_stops_at_the_newest_day(con):
    reach = wb.Reach("t", "T", "a", "b", reading="", terms=(
        wb.Term("in", "in", "inflow", "flow", ("usgs:1",)), wb.Term("out", "out", "outflow", "flow", ("usgs:2",)),
        wb.Term("stor", "s", "storage", "storage", ("usgs:3",), "reservoir_storage")))
    df = wb.run(con, reach, "month", first=date(2021, 1, 1), last=date(2021, 1, 10))
    assert len(df) == 1 and not df.iloc[0].complete       # ten days is not a month: reported as incomplete, not extrapolated


# ---------------------------------------------------------------------------- the registry
@pytest.mark.parametrize("reach", wb.REACHES.values(), ids=lambda r: r.key)
def test_every_reach_is_well_formed(reach):
    keys = [t.key for t in reach.terms]
    assert len(keys) == len(set(keys))
    roles = {t.role for t in reach.terms}
    assert {"inflow", "outflow"} <= roles and roles <= {"inflow", "outflow", "storage", "evaporation", "reference"}
    for t in reach.terms:
        assert t.kind in {"flow", "volume", "storage"} and t.uids and all(re.fullmatch(r"[a-z_]+:.+", u) for u in t.uids)
        assert re.fullmatch(r"[a-z_]+", t.variable), f"{t.key}: variable {t.variable!r} looks like text (a note passed in the wrong position?)"
        assert (t.kind == "storage") == (t.role == "storage")
        if t.kind == "flow":
            assert t.variable == "discharge"
    assert reach.reading and reach.omitted
