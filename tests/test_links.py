"""Proximity linking must not pair permit locations with monitoring sites."""

import pandas as pd

from nmwater.catalog.links import build_links


def _site(uid, source, native, lat, lon, site_type="well"):
    return {"site_uid": uid, "source": source, "native_id": native, "lat": lat, "lon": lon,
            "site_type": site_type, "raw_metadata": None}


def test_points_of_diversion_are_not_proximity_linked():
    sites = pd.DataFrame([
        _site("usgs:1", "usgs", "1", 35.0, -106.0),
        _site("nmwdi_st2:9", "nmwdi_st2", "9", 35.0001, -106.0001),
        _site("ose_arcgis:pod:RG-1", "ose_arcgis", "pod:RG-1", 35.0, -106.0),
    ])
    links = build_links(sites)
    ids = set(links.site_uid_a) | set(links.site_uid_b)
    assert "ose_arcgis:pod:RG-1" not in ids
    assert {"usgs:1", "nmwdi_st2:9"} <= ids            # the two monitoring wells still pair up


def test_nm_county_list_has_all_33_counties():
    from nmwater.core.constants import NM_COUNTY_FIPS

    assert len(NM_COUNTY_FIPS) == 33 and len(set(NM_COUNTY_FIPS)) == 33
    assert {"35006", "35028"} <= set(NM_COUNTY_FIPS)        # the two even-numbered counties
    assert all(c.startswith("35") and len(c) == 5 for c in NM_COUNTY_FIPS)
