"""Issue #62: one weather instrument published by GHCNh, GHCN-Daily, ISD-Lite and USCRN is linked by id, not only by distance."""

import json

import pandas as pd

from nmwater.catalog.links import build_links, weather_links


def site(source, native_id, lat=35.0, lon=-106.0, meta=None, stype="met"):
    return {"site_uid": f"{source}:{native_id}", "source": source, "native_id": native_id, "lat": lat, "lon": lon, "site_type": stype,
            "raw_metadata": json.dumps(meta or {})}


def frame(*rows):
    return pd.DataFrame(rows)


def test_ghcnh_and_ghcnd_share_a_ghcn_id_and_isd_shares_the_wban():
    s = frame(site("noaa_ghcnh", "USW00023050"), site("noaa_ghcnd", "USW00023050"), site("noaa_isd", "723650-23050", meta={"icao": "KABQ"}))
    pairs = {(r["site_uid_a"], r["site_uid_b"]): r for r in weather_links(s)}
    assert len(pairs) == 3
    assert all(r["link_type"] == "same_sensor" for r in pairs.values())
    assert "same GHCN station id USW00023050" in pairs[("noaa_ghcnd:USW00023050", "noaa_ghcnh:USW00023050")]["evidence"]
    assert any("same WBAN 23050" in r["evidence"] for r in pairs.values())


def test_uscrn_matches_by_wban_and_a_missing_wban_matches_nothing():
    s = frame(site("uscrn", "53024", meta={"WBAN": "53024"}), site("noaa_ghcnh", "USW00053024"), site("noaa_isd", "999999-99999"), site("noaa_ghcnh", "USW00099999"))
    uids = {(r["site_uid_a"], r["site_uid_b"]) for r in weather_links(s)}
    assert uids == {("noaa_ghcnh:USW00053024", "uscrn:53024")}


def test_a_cooperative_station_links_across_ghcn_feeds_and_icao_links_isd_to_ghcnh():
    s = frame(site("noaa_ghcnh", "USC00290600"), site("noaa_ghcnd", "USC00290600"),
              site("noaa_ghcnh", "ZZZ", meta={"icao": "KXYZ"}), site("noaa_isd", "111111-22222", meta={"icao": "kxyz"}))
    uids = {(r["site_uid_a"], r["site_uid_b"]) for r in weather_links(s)}
    assert uids == {("noaa_ghcnd:USC00290600", "noaa_ghcnh:USC00290600"), ("noaa_ghcnh:ZZZ", "noaa_isd:111111-22222")}


def test_an_exact_link_replaces_the_proximity_guess_for_the_same_pair():
    s = frame(site("noaa_ghcnh", "USW00023050", 35.04, -106.62), site("noaa_ghcnd", "USW00023050", 35.04, -106.62), site("noaa_ghcnd", "USC00290001", 35.0401, -106.6201))
    links = build_links(s)
    pair = links[(links.site_uid_a == "noaa_ghcnd:USW00023050") & (links.site_uid_b == "noaa_ghcnh:USW00023050")]
    assert list(pair["link_type"]) == ["same_sensor"]
