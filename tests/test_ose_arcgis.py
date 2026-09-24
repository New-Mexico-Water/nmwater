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
