"""OSE ArcGIS site builders must survive missing identifiers."""

import numpy as np
import pandas as pd

from nmwater.sources.ose_arcgis import OSEArcGIS


def _src():
    return OSEArcGIS.__new__(OSEArcGIS)          # the builders use no instance state


def test_rtm_sites_skip_meters_without_a_station_id():
    df = pd.DataFrame({"Station_ID": [101.0, np.nan, None, 0, 205.0], "Gauge_name": list("abcde"),
                       "lat_ddd": 35.0, "long_ddd": -106.0, "SW_or_GW": "SW", "Meter_status": "complete"})
    out = _src()._rtm_sites(df)
    assert out.native_id.tolist() == ["rtm:101", "rtm:205"]


def test_spring_sites_skip_missing_ids():
    df = pd.DataFrame({"SiteID": pd.Series([7, np.nan], dtype=object), "SiteName": ["a", "b"], "LatitudeDD": 35.0, "LongitudeD": -106.0})
    assert _src()._spring_sites(df).native_id.tolist() == ["spring:7"]


def test_pod_sites_handle_missing_elevation_and_depth():
    df = pd.DataFrame({"pod_basin": ["RG"], "pod_nbr": ["59185"], "pod_suffix": ["POD1"], "elevation": [np.nan],
                       "depth_well": [np.nan], "grnd_wtr_s": ["S"], "geometry_wkt": ["POINT (-106.0 35.0)"]})
    out = _src()._pod_sites(df)
    assert out.native_id.tolist() == ["pod:RG-59185-POD1"]
    assert out.site_type.tolist() == ["diversion"] and out.lat.notna().all()


def test_ose_meas_coordinates_fall_back_and_skip_missing_station_ids(tmp_path):
    from nmwater.sources.ose_meas import _dms, _rtm_index

    assert abs(_dms('35° 49\' 16.069" N') - 35.821130) < 1e-5
    assert _dms('105° 53\' 30.437" W') < 0
    rtm = pd.DataFrame({
        "Station_ID": [140.0, np.nan, np.nan, np.nan, 7.0],
        "Gauge_name": ["a", "Heredia", "Gonzales", "Gonzales", "b"],
        "Ditch_Name": [None] * 5,
        "River_src": [None] * 5,
        "lat_ddd": [35.8, 32.1, 36.0, 36.1, np.nan],
        "long_ddd": [-105.9, -107.7, -106.2, -106.3, np.nan],
        "Latitude": [None] * 4 + ['35° 36\' 51.918" N'],
        "Longitude": [None] * 4 + ['105° 14\' 39.529" W'],
        "Northing": [np.nan] * 5, "Easting": [np.nan] * 5,
    })
    p = tmp_path / "rtm.parquet"
    rtm.to_parquet(p)
    by_id, by_name = _rtm_index(p)
    assert set(by_id) == {"140", "7"} and abs(by_id["7"][0] - 35.6144) < 1e-3
    assert "heredia" in by_name and "gonzales" not in by_name     # ambiguous names are not used


# Issue #45: an update must re-pull the layers, or the points of diversion never change after the first run.
import os
import time
from datetime import date
from types import SimpleNamespace

from nmwater.catalog.crosswalk import Crosswalk
from nmwater.sources.ose_arcgis import LAYER_REFRESH_DAYS, layers_stale


def test_layers_are_stale_when_missing_or_older_than_a_week(tmp_path):
    p = tmp_path / "points_of_diversion.parquet"
    assert layers_stale(p)
    p.write_text("x")
    assert not layers_stale(p)
    old = time.time() - (LAYER_REFRESH_DAYS + 1) * 86400
    os.utime(p, (old, old))
    assert layers_stale(p)


def _fetch_source(tmp_path, calls):
    s = OSEArcGIS.__new__(OSEArcGIS)
    s.name = "ose_arcgis"
    s.xw = Crosswalk()
    d = tmp_path / "reference" / "source=ose_arcgis"
    d.mkdir(parents=True)
    pd.DataFrame({"pod_basin": ["RG"], "pod_nbr": ["1"], "pod_suffix": [None], "depth_wate": [50.0], "log_file_d": [None], "finish_dat": [None]}).to_parquet(d / "points_of_diversion.parquet")
    s.store = SimpleNamespace(root=tmp_path, write_sites=lambda df, name: calls.append(("write_sites", len(df))))
    s.write_obs = lambda df, tag=None: 0
    s.discover = lambda refresh_big=None: (calls.append(("discover", refresh_big)) or pd.DataFrame({"native_id": ["pod:RG-1"]}))
    return s, d / "points_of_diversion.parquet"


def test_an_update_re_pulls_stale_layers_and_rewrites_the_sites_table(tmp_path):
    calls = []
    s, p = _fetch_source(tmp_path, calls)
    old = time.time() - 30 * 86400
    os.utime(p, (old, old))
    s.fetch(since=date(2026, 9, 1))
    assert calls == [("discover", True), ("write_sites", 1)]


def test_a_fresh_copy_a_manual_fetch_or_a_limited_run_does_not_re_pull(tmp_path):
    calls = []
    s, p = _fetch_source(tmp_path, calls)
    s.fetch(since=date(2026, 9, 1))                            # fresh
    s.fetch()                                                  # a plain `fetch`, not an update
    old = time.time() - 30 * 86400
    os.utime(p, (old, old))
    s.fetch(since=date(2026, 9, 1), limit=5)                   # a test run
    assert calls == []


def test_a_failed_re_pull_falls_back_to_the_stored_layers(tmp_path):
    calls = []
    s, p = _fetch_source(tmp_path, calls)
    old = time.time() - 30 * 86400
    os.utime(p, (old, old))

    def boom(refresh_big=None):
        raise RuntimeError("service down")
    s.discover = boom
    summ = s.fetch(since=date(2026, 9, 1))
    assert any("PODs with depth-to-water" in n for n in summ.notes)
