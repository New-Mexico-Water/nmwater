"""GHCNh normalization: one value per hour, running-total precipitation, quality-code policy."""

import io
from types import SimpleNamespace

import pandas as pd

from nmwater.catalog.crosswalk import Crosswalk
from nmwater.sources.noaa_ghcnh import NOAAGHCNh, _drop_mask

URL = "https://example.test/by-year/2025/parquet/GHCNh_USW00023050_2025.parquet"


def _normalize(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    df.to_parquet(buf)
    src = NOAAGHCNh.__new__(NOAAGHCNh)
    src.name = "noaa_ghcnh"
    src.xw = Crosswalk()
    art = SimpleNamespace(url=URL, read_bytes=lambda: buf.getvalue())
    return src.normalize(art)


def _row(date, **kw):
    return {"DATE": date, **kw}


def test_temperature_uses_the_report_nearest_the_hour_and_converts_nothing():
    out = _normalize([
        _row("2025-01-01T00:52:00", temperature=5.0, temperature_Quality_Code="1"),
        _row("2025-01-01T01:15:00", temperature=9.0, temperature_Quality_Code="1"),
        _row("2025-01-01T01:52:00", temperature=6.0, temperature_Quality_Code="5"),
    ])
    t = out[out.variable == "air_temp"].sort_values("datetime_utc")
    assert t.datetime_utc.dt.strftime("%H:%M").tolist() == ["01:00", "02:00"]
    assert t.value.tolist() == [5.0, 6.0]      # 00:52 wins hour 01 over 01:15, and degC is canonical
    assert (t.unit == "degC").all() and (t.statistic == "instantaneous").all()


def test_precip_takes_the_last_report_of_the_period_in_inches():
    # routine reports at :52 close a period; specials in between carry running totals
    rows = [
        _row("2025-01-09T14:52:00", precipitation=0.0, precipitation_Report_Type="FM15"),
        _row("2025-01-09T15:09:00", precipitation=2.0, precipitation_Report_Type="FM16"),
        _row("2025-01-09T15:28:00", precipitation=5.0, precipitation_Report_Type="FM16"),
        _row("2025-01-09T15:52:00", precipitation=7.0, precipitation_Report_Type="FM15"),
        _row("2025-01-09T16:04:00", precipitation=1.0, precipitation_Report_Type="FM16"),   # new period
        _row("2025-01-09T16:52:00", precipitation=1.0, precipitation_Report_Type="FM15"),
    ]
    p = _normalize(rows)
    p = p[p.variable == "precip"].sort_values("datetime_utc")
    assert p.datetime_utc.dt.strftime("%H:%M").tolist() == ["15:00", "16:00", "17:00"]
    assert p.value.round(4).tolist() == [0.0, round(7.0 / 25.4, 4), round(1.0 / 25.4, 4)]
    assert (p.unit == "in").all() and (p.statistic == "total").all()


def test_trace_precip_is_zero_with_a_qualifier():
    out = _normalize([_row("2025-01-09T14:52:00", precipitation=0.0, precipitation_Quality_Code="5",
                           precipitation_Measurement_Code="T", precipitation_Report_Type="FM15")])
    r = out[out.variable == "precip"].iloc[0]
    assert r.value == 0.0 and r.qualifier == "5,T"


def test_erroneous_and_failed_qc_values_are_dropped_but_suspect_are_kept():
    out = _normalize([
        _row("2025-01-01T00:00:00", temperature=99.0, temperature_Quality_Code="7"),   # erroneous
        _row("2025-01-01T01:00:00", temperature=98.0, temperature_Quality_Code="s"),   # failed spike check
        _row("2025-01-01T02:00:00", temperature=7.0, temperature_Quality_Code="2"),    # suspect: kept
        _row("2025-01-01T03:00:00", temperature=8.0),                                  # no flag: kept
    ])
    t = out[out.variable == "air_temp"].sort_values("datetime_utc")
    assert t.value.tolist() == [7.0, 8.0]
    assert t.qualifier.tolist()[0] == "2"


def test_variable_wind_direction_and_future_dates_are_dropped():
    future = (pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    out = _normalize([
        _row("2025-01-01T00:00:00", wind_direction="999", wind_speed=4.0),
        _row("2025-01-01T01:00:00", wind_direction="270", wind_speed=4.0),
        _row(future, temperature=1.0),
    ])
    assert out[out.variable == "wind_direction"].value.tolist() == [270.0]
    assert "air_temp" not in set(out.variable)
    assert out[out.variable == "wind_speed"].value.round(3).tolist() == [8.948, 8.948]   # m/s -> mph


def test_drop_mask_rules():
    q = pd.Series(["1", "3", "s", None, "f"])
    assert _drop_mask("temperature", q).tolist() == [False, True, True, False, True]
    assert _drop_mask("relative_humidity", q).tolist() == [False, True, False, False, True]
    assert _drop_mask("precipitation", pd.Series(["A", "Q", "1"])).tolist() == [True, False, False]
