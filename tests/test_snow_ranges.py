"""Mountain ranges drawn where snow is usual: the rule (threshold, radius, nearest seed, size), the climatology and the config."""

import numpy as np
import pytest
import xarray as xr
from shapely.geometry import Point

from nmwater.derived import snow_ranges as sr

LAT = np.linspace(37.0, 35.0, 201)          # descending, 0.01 degree cells
LON = np.linspace(-107.0, -105.0, 201)


def grid(blobs, base=0.0):
    a = np.full((len(LAT), len(LON)), base)
    for lat, lon, half, v in blobs:
        r = np.abs(LAT - lat) <= half
        c = np.abs(LON - lon) <= half
        a[np.ix_(r, c)] = v
    return a


def rng(i, seed, **kw):
    return {"id": i, "name": i.upper(), "note": "", "seeds": [seed], "threshold_in": 0.8, "radius_km": 80, "min_cells": 30, **kw}


def feats(mean, ranges, in_state=None):
    inside = np.ones_like(mean, dtype=bool) if in_state is None else in_state
    return {f["id"]: f for f in sr.zone_polygons(mean, LAT, LON, inside, ranges)}


def test_each_snowy_patch_becomes_a_polygon_of_its_nearest_range():
    mean = grid([(36.5, -106.5, 0.1, 3.0), (35.5, -105.5, 0.1, 3.0)])
    f = feats(mean, [rng("a", (36.5, -106.5)), rng("b", (35.5, -105.5))])
    assert set(f) == {"a", "b"}
    assert f["a"]["geometry"].contains(Point(-106.5, 36.5)) and not f["a"]["geometry"].contains(Point(-105.5, 35.5))
    assert f["b"]["geometry"].contains(Point(-105.5, 35.5))


def test_a_patch_between_two_ranges_is_split_by_the_nearer_seed():
    mean = grid([(36.0, -106.0, 0.4, 3.0)])            # a long patch with the seeds at its two ends
    f = feats(mean, [rng("west", (36.0, -106.4), radius_km=200), rng("east", (36.0, -105.6), radius_km=200)])
    assert f["west"]["geometry"].contains(Point(-106.3, 36.0)) and not f["west"]["geometry"].contains(Point(-105.7, 36.0))
    assert f["east"]["geometry"].contains(Point(-105.7, 36.0))


def test_a_speck_under_min_cells_is_dropped():
    mean = grid([(36.5, -106.5, 0.1, 3.0), (36.0, -106.0, 0.01, 3.0)])      # 21 x 21 cells and 3 x 3 cells
    f = feats(mean, [rng("a", (36.5, -106.5), radius_km=200)])
    assert f["a"]["cells"] > 400 and not f["a"]["geometry"].contains(Point(-106.0, 36.0))


def test_the_threshold_is_per_range_and_a_low_one_keeps_a_smoothed_range():
    mean = grid([(36.5, -106.5, 0.1, 0.4)])
    assert feats(mean, [rng("plain", (36.5, -106.5))]) == {}                                 # 0.4 in is under the default 0.8
    assert "low" in feats(mean, [rng("low", (36.5, -106.5), threshold_in=0.3)])


def test_snow_beyond_the_radius_or_outside_the_state_is_not_a_range():
    mean = grid([(36.5, -106.5, 0.1, 3.0)])
    assert feats(mean, [rng("a", (35.2, -105.2), radius_km=20)]) == {}
    outside = np.ones_like(mean, dtype=bool)
    outside[:, LON > -106.55] = False                                              # the state ends west of the patch's middle
    f = feats(mean, [rng("a", (36.5, -106.5), radius_km=200)], outside)
    assert f["a"]["geometry"].bounds[2] < -106.5


def test_climatology_averages_the_files_skipping_ones_without_swe_or_with_another_shape(tmp_path):
    def write(name, swe_m, lat=LAT, var="swe"):
        ds = xr.Dataset({var: (("lat", "lon"), swe_m)}, coords={"lat": lat, "lon": LON[:swe_m.shape[1]]})
        ds.to_netcdf(tmp_path / name)

    ones = np.ones((len(LAT), len(LON))) / sr.M_TO_IN                  # 1 inch
    write("snodas_20040210.nc", ones)
    write("snodas_20040215.nc", 3 * ones, lat=LAT[::-1])               # stored south to north: flipped on reading
    write("snodas_20040220.nc", ones, var="snow_depth")                # no SWE
    write("snodas_20040310.nc", ones[:50, :50], lat=LAT[:50])          # another shape
    mean, lat, lon, n = sr.climatology(tmp_path, (2004, 2004), [[2, 10], [2, 15], [2, 20], [3, 10], [3, 15]])
    assert n == 2 and lat[0] > lat[-1]                                  # two usable files, latitude north first
    assert mean[0, 0] == pytest.approx(2.0) and mean[-1, -1] == pytest.approx(2.0)
    with pytest.raises(FileNotFoundError):
        sr.climatology(tmp_path, (1990, 1990), [[2, 10]])


def test_the_config_loads_with_defaults_and_every_range_has_seeds():
    cfg = sr.load_config()
    ids = [r["id"] for r in cfg["ranges"]]
    assert len(ids) == len(set(ids)) >= 8 and "sandia" in ids                     # the Sandia and Manzano are a range
    by = {r["id"]: r for r in cfg["ranges"]}
    assert by["sdc_north"]["threshold_in"] == cfg["zone"]["threshold_in"] and by["sandia"]["threshold_in"] < cfg["zone"]["threshold_in"]
    assert all(r["seeds"] and all(-110 < s[1] < -102 and 30 < s[0] < 38 for s in r["seeds"]) for r in cfg["ranges"])
    assert set(cfg["zone"]["model_smooths"]) <= set(ids)


def test_a_config_with_a_repeated_id_or_no_seeds_is_refused(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("zone: {years: [2004, 2005], dates: [[2, 10]], threshold_in: 0.8, radius_km: 80, min_cells: 30}\nranges:\n  - {id: a, name: A, seeds: [[36, -106]]}\n  - {id: a, name: B, seeds: [[35, -105]]}\n")
    with pytest.raises(ValueError, match="duplicate"):
        sr.load_config(p)
    p.write_text("zone: {years: [2004, 2005], dates: [[2, 10]], threshold_in: 0.8, radius_km: 80, min_cells: 30}\nranges:\n  - {id: a, name: A, seeds: []}\n")
    with pytest.raises(ValueError, match="no seeds"):
        sr.load_config(p)
