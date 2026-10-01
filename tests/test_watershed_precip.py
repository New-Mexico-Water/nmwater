"""Watershed precipitation: cell weights, the zonal mean with missing cells, and the time convention."""

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from nmwater.derived import watershed_precip as wp


def Polys(geoms):
    return gpd.GeoDataFrame({"huc8": [f"{i:08d}" for i in range(len(geoms))]}, geometry=geoms, crs=4326)


LAT = np.array([32.0, 32.1, 32.2, 32.3])           # ascending, 0.1 degree cells
LON = np.array([-106.0, -105.9, -105.8, -105.7])


def test_cell_weights_are_fractions_of_each_cell():
    # a polygon covering all of cell (row 1, col 1) and the western half of cell (row 1, col 2)
    poly = box(-105.95, 32.05, -105.80, 32.15)
    W, cells = wp.cell_weights(Polys([poly]), LAT, LON)
    w = W[0].reshape(4, 4)
    assert w[1, 1] == pytest.approx(1.0)
    assert w[1, 2] == pytest.approx(0.5)
    assert w.sum() == pytest.approx(1.5)                       # nothing leaks into other cells
    assert cells[0] == pytest.approx(1.5)                      # a 0.15 x 0.1 degree polygon is 1.5 cell areas


def test_cell_weights_follow_latitude_orientation():
    poly = box(-106.05, 32.25, -105.65, 32.35)                 # the top (north) row only
    W, _ = wp.cell_weights(Polys([poly]), LAT, LON)
    w = W[0].reshape(4, 4)
    assert w[3].sum() == pytest.approx(4.0) and w[:3].sum() == 0


def test_zonal_mean_weights_cells_and_skips_missing_ones():
    a = np.arange(16, dtype=float).reshape(1, 4, 4)
    W = np.zeros((1, 16))
    W[0, 5], W[0, 6] = 1.0, 0.5                                # cells with values 5 (full) and 6 (half)
    assert wp.zonal_mean(a, W)[0, 0] == pytest.approx((5 * 1.0 + 6 * 0.5) / 1.5)
    a[0, 1, 2] = np.nan                                        # cell 6 has no data: only cell 5 counts
    assert wp.zonal_mean(a, W)[0, 0] == pytest.approx(5.0)
    a[0, 1, 1] = np.nan
    assert np.isnan(wp.zonal_mean(a, W)[0, 0])                 # nothing valid: no value, not zero


def test_a_prism_day_ends_at_1200_utc_on_its_date():
    t = pd.DatetimeIndex(["2024-07-15"])
    df = wp._rows(["13020101"], t, np.array([[25.4]]), "daily")
    r = df.iloc[0]
    assert r["precip_in"] == pytest.approx(1.0, abs=1e-3)               # mm to inches
    assert pd.Timestamp(r["period_end_utc"]) == pd.Timestamp("2024-07-15 12:00")
    assert pd.Timestamp(r["period_start_utc"]) == pd.Timestamp("2024-07-14 12:00")
    assert r["source"] == "prism_daily" and r["interval"] == "daily"


def test_monthly_rows_cover_the_calendar_month_and_drop_missing():
    t = pd.DatetimeIndex(["1950-01-01", "1950-02-01"])
    df = wp._rows(["a", "b"], t, np.array([[10.0, np.nan], [20.0, 30.0]]), "monthly")
    assert len(df) == 3                                                  # the NaN is dropped
    jan = df[df["date"] == pd.Timestamp("1950-01-01").date()].iloc[0]
    assert pd.Timestamp(jan["period_end_utc"]) == pd.Timestamp("1950-02-01")
    assert set(df["source"]) == {"prism_monthly"}
