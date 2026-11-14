"""Usual and ratings for the snow pages: water-year days, the pooled window, the band, and the little/rated rules."""

import numpy as np
import pandas as pd
import pytest

from nmwater.derived import snow_normal as sn


def test_day_index_counts_from_1_october_and_drops_feb_29():
    assert sn.day_index(pd.Timestamp("2025-10-01")) == (2026, 0)
    assert sn.day_index(pd.Timestamp("2026-09-30")) == (2026, 364)
    assert sn.day_index(pd.Timestamp("2024-02-29")) is None
    assert sn.day_index(pd.Timestamp("2024-02-28")) == (2024, 150) and sn.day_index(pd.Timestamp("2024-03-01")) == (2024, 150 + 1)     # leap year: 1 March follows 28 February
    assert sn.day_index(pd.Timestamp("2024-09-30")) == (2024, 364) and sn.day_index(pd.Timestamp("2023-10-01")) == (2024, 0)


def matrix(per_year):
    """Each water year 2004-2025 holds one constant value on day 100, and 0 elsewhere (one series)."""
    out = {}
    for w, v in per_year.items():
        m = np.zeros((1, sn.DAYS))
        m[0, 100] = v
        out[w] = m
    return out


def test_the_pool_is_the_same_day_and_three_either_side_across_the_baseline_years():
    mats = matrix({w: float(w - 2000) for w in range(2004, 2026)})
    p = sn.pool(mats, 0, 100)
    assert len(p) == 22 * 7 and sorted(set(p)) == [0.0] + [float(k) for k in range(4, 26)]
    assert len(sn.pool(mats, 0, 0)) == 22 * 4                                    # the window stops at the start of the water year
    assert len(sn.pool(mats, 0, 100, years=(2010, 2012))) == 3 * 7


def test_season_matrix_places_days_and_a_missing_day_stays_nan():
    dates = pd.date_range("2025-10-01", "2025-10-05")
    v = np.array([1.0, 2.0, np.nan, 4.0, 5.0])
    m = sn.season_matrix(dates, v, range(2026, 2027))[2026]
    assert m.shape == (1, 365) and list(m[0, :5]) == [1.0, 2.0, pytest.approx(np.nan, nan_ok=True), 4.0, 5.0] and np.isnan(m[0, 2]) and np.isnan(m[0, 10])


def test_band_has_percentiles_in_order_and_none_without_enough_years():
    rng = np.random.default_rng(1)
    mats = {w: rng.uniform(0, 10, (1, sn.DAYS)) for w in range(2004, 2026)}
    b = sn.band(mats, 0)
    assert all(len(b[k]) == 365 for k in sn.BAND_KEYS)
    assert all(b["p10"][i] <= b["p25"][i] <= b["p50"][i] <= b["p75"][i] <= b["p90"][i] for i in range(365))
    assert sn.band({w: mats[w] for w in range(2004, 2008)}, 0)["p50"][100] is not None          # 4 years x 7 days = 28 values: enough
    assert sn.band({w: mats[w] for w in range(2004, 2006)}, 0)["p50"][100] is None              # 2 years x 7 days = 14 values: under the 20 a usual needs


def test_rate_classes_follow_the_percentile_and_little_is_unrated():
    pool = np.arange(1.0, 101.0)                                                    # 1..100
    assert sn.rate(5.0, pool, "swe")["cls"] == "much below normal"
    assert sn.rate(20.0, pool, "swe")["cls"] == "below normal"
    assert sn.rate(50.0, pool, "swe")["cls"] == "normal"
    assert sn.rate(85.0, pool, "swe")["cls"] == "above normal"
    r = sn.rate(99.0, pool, "swe")
    assert r["cls"] == "much above normal" and r["usual"] == 50.5 and r["n"] == 100 and r["little"] is False
    little = sn.rate(0.01, np.full(30, 0.02), "swe")                                # both under 0.05 in
    assert little["little"] is True and little["cls"] is None and little["usual"] == 0.02
    assert sn.rate(0.01, np.full(30, 0.02), "depth")["little"] is True and sn.rate(0.6, np.full(30, 0.02), "depth")["little"] is False


def test_rate_with_too_small_a_pool_or_no_value_states_no_usual():
    assert sn.rate(3.0, np.arange(10.0), "swe") == {"now": 3.0, "usual": None, "pct": None, "cls": None, "little": False, "n": 10}
    assert sn.rate(3.0, np.arange(16.0), "new7", min_pool=sn.MIN_POOL_NEW)["usual"] is not None
    assert sn.rate(None, np.arange(30.0), "swe")["now"] is None and sn.rate(float("nan"), np.arange(30.0), "swe")["cls"] is None


def test_thresholds_are_defined_for_every_measure():
    for t in (sn.LITTLE_IN, sn.MIN_USUAL_IN, sn.PERCENT_FROM_IN):
        assert set(t) == set(sn.MEASURES)
