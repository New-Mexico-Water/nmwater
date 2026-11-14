"""Daily snow averaged over watersheds and mountain ranges: the means, the orientation of the grids, incremental updates, skipped days, and a changed unit set."""

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
import xarray as xr
from shapely.geometry import box

from nmwater.derived import watershed_snow as ws

STEP = 1 / 120
NY, NX = 12, 16
LAT0, LON0 = 36.0, -106.0                      # north-west corner of the first cell centre


def lat_lon(north_first=True, shift=0.0):
    lat = LAT0 - np.arange(NY) * STEP + shift
    lon = LON0 + np.arange(NX) * STEP + shift
    return (lat if north_first else lat[::-1]), lon


def write(d, day, swe_in, depth_in=None, north_first=True, shift=0.0, drop=None, lat=None):
    la, lo = lat_lon(north_first, shift)
    la = la if lat is None else lat
    swe = np.array(swe_in, float)[:, :]
    dep = swe * 2 if depth_in is None else np.array(depth_in, float)
    if not north_first:
        swe, dep = swe[::-1], dep[::-1]
    data = {"swe": (("lat", "lon"), swe / ws.M_TO_IN), "snow_depth": (("lat", "lon"), dep / ws.M_TO_IN)}
    if drop:
        data.pop(drop)
    xr.Dataset(data, coords={"lat": la, "lon": lo}).to_netcdf(d / f"snodas_{day}.nc")


def units(extra=False):
    west = box(LON0 - STEP / 2, LAT0 - NY * STEP + STEP / 2, LON0 + 8 * STEP - STEP / 2, LAT0 + STEP / 2)           # the left 8 columns
    east = box(LON0 + 8 * STEP - STEP / 2, LAT0 - NY * STEP + STEP / 2, LON0 + NX * STEP - STEP / 2, LAT0 + STEP / 2)
    rows = [{"unit": "W", "kind": "watershed", "name": "West", "nm_fraction": 1.0, "geometry": west},
            {"unit": "E", "kind": "range", "name": "East", "nm_fraction": 1.0, "geometry": east}]
    if extra:
        rows.append({"unit": "X", "kind": "watershed", "name": "Extra", "nm_fraction": 1.0, "geometry": box(LON0, LAT0 - 4 * STEP, LON0 + 3 * STEP, LAT0)})
    return gpd.GeoDataFrame(rows, crs=4326)


def field(west, east):
    a = np.zeros((NY, NX))
    a[:, :8], a[:, 8:] = west, east
    return a


def run(tmp_path, polys=None, rebuild=False):
    base = tmp_path / "derived"
    return ws.compute(tmp_path / "snodas", polys if polys is not None else units(), base / "watershed_snow", base / "units.parquet", base / "manifest.json", rebuild)


def table(tmp_path):
    return pd.read_parquet(tmp_path / "derived" / "watershed_snow")


@pytest.fixture()
def snodas(tmp_path):
    (tmp_path / "snodas").mkdir()
    return tmp_path / "snodas"


def test_the_mean_of_each_unit_is_the_mean_of_its_cells_in_inches(tmp_path, snodas):
    write(snodas, "20260115", field(2.0, 10.0))
    r = run(tmp_path)
    t = table(tmp_path).set_index("unit")
    assert r == {"units": 2, "days_added": 1, "rows": 2}
    assert t.loc["W", "swe_in"] == pytest.approx(2.0) and t.loc["E", "swe_in"] == pytest.approx(10.0)
    assert t.loc["W", "depth_in"] == pytest.approx(4.0) and t.loc["W", "kind"] == "watershed" and t.loc["E", "kind"] == "range"


def test_a_grid_stored_south_to_north_gives_the_same_means(tmp_path, snodas):
    write(snodas, "20260115", field(2.0, 10.0))
    write(snodas, "20260116", field(2.0, 10.0), north_first=False)
    run(tmp_path)
    t = table(tmp_path).pivot(index="date", columns="unit", values="swe_in")
    assert t["W"].round(6).nunique() == 1 and t["E"].round(6).nunique() == 1


def test_the_grid_a_tenth_of_a_cell_away_shares_the_masks(tmp_path, snodas):
    write(snodas, "20131001", field(2.0, 10.0))
    write(snodas, "20140101", field(2.0, 10.0), shift=0.0004)
    run(tmp_path)
    t = table(tmp_path)
    assert len(t) == 4 and t.groupby("unit")["swe_in"].nunique().max() == 1


def test_an_update_adds_only_the_new_days_and_never_repeats_a_row(tmp_path, snodas):
    write(snodas, "20260115", field(2.0, 10.0))
    write(snodas, "20260116", field(3.0, 10.0))
    assert run(tmp_path)["days_added"] == 2
    write(snodas, "20260117", field(4.0, 10.0))
    assert run(tmp_path)["days_added"] == 1
    assert run(tmp_path)["days_added"] == 0
    t = table(tmp_path)
    assert len(t) == 6 and not t.duplicated(["unit", "date"]).any()
    assert list(t[t["unit"] == "W"].sort_values("date")["swe_in"].round(3)) == [2.0, 3.0, 4.0]


def test_a_day_without_a_variable_or_on_an_irregular_grid_is_skipped_then_picked_up(tmp_path, snodas):
    write(snodas, "20260115", field(2.0, 10.0))
    write(snodas, "20260116", field(2.0, 10.0), drop="swe")
    la, _ = lat_lon()
    write(snodas, "20260117", field(2.0, 10.0), lat=la * np.linspace(1, 1.05, NY))                 # steps far from 1/120
    assert run(tmp_path)["days_added"] == 1
    write(snodas, "20260116", field(5.0, 10.0))                                                      # repaired
    assert run(tmp_path)["days_added"] == 1
    assert sorted(set(table(tmp_path)["date"].dt.day)) == [15, 16]


def test_a_changed_unit_set_recomputes_every_day(tmp_path, snodas):
    write(snodas, "20260115", field(2.0, 10.0))
    write(snodas, "20260116", field(3.0, 10.0))
    run(tmp_path)
    r = run(tmp_path, units(extra=True))
    assert r["days_added"] == 2 and r["units"] == 3
    assert set(table(tmp_path)["unit"]) == {"W", "E", "X"} and len(table(tmp_path)) == 6
    assert run(tmp_path, units(extra=True))["days_added"] == 0                                        # the same set again: nothing to do


def test_grid_fraction_is_the_share_of_a_unit_the_grid_covers(tmp_path, snodas):
    write(snodas, "20260115", field(2.0, 10.0))
    wide = units()
    wide.loc[wide["unit"] == "E", "geometry"] = box(LON0 + 8 * STEP - STEP / 2, LAT0 - NY * STEP + STEP / 2, LON0 + 2 * NX * STEP - STEP / 2, LAT0 + STEP / 2)      # runs past the east edge: 8 of its 24 columns inside the grid
    run(tmp_path, wide)
    u = pd.read_parquet(tmp_path / "derived" / "units.parquet").set_index("unit")
    assert u.loc["W", "grid_fraction"] == pytest.approx(1.0, abs=0.02) and u.loc["E", "grid_fraction"] == pytest.approx(1 / 3, abs=0.03)
