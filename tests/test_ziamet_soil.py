"""ZiaMet daily soil temperature (tmps): the columns are named statistic-first ("Max 10cm Soil Temperature"), in degrees F, and each statistic and depth must survive."""

from nmwater.catalog.crosswalk import Crosswalk
from nmwater.sources.ziamet import ZiaMet

TEXT = """Date,Max 10cm Soil Temperature,Min 10cm Soil Temperature,Mean 10cm Soil Temperature,
,F,F,F,
2026-08-01,105.8,84.7,94.4,
2026-08-02,104,85.8,94.5,
"""


def _source():
    s = ZiaMet.__new__(ZiaMet)
    s.name = "ziamet"
    s.xw = Crosswalk()
    return s


def test_three_statistics_at_ten_centimetres_are_kept_as_separate_series_in_degrees_c():
    out = _source()._parse_daily(TEXT, "scvegu", "tmps")
    assert set(out["variable"]) == {"soil_temp"} and set(out["depth_cm"]) == {10.0}
    by = {k: g.sort_values("datetime_utc")["value"].round(2).tolist() for k, g in out.groupby("statistic")}
    assert by["max"] == [41.0, 40.0] and by["min"] == [29.28, 29.89] and by["mean"] == [34.67, 34.72]      # (F - 32) * 5/9
    assert len(out) == 6                                                                                    # nothing collapsed


def test_the_same_statistic_and_depth_in_celsius_is_not_converted_twice():
    out = _source()._parse_daily(TEXT.replace(",F,F,F,", ",C,C,C,"), "scvegu", "tmps")
    assert out[out["statistic"] == "max"]["value"].round(1).tolist() == [105.8, 104.0]
