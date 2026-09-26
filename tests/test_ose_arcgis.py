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
