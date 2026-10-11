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


def test_wqp_usgs_ids_link_to_the_usgs_site_and_never_to_its_neighbours():
    from nmwater.catalog.links import gauge_id_links
    s = frame(site("usgs", "370758107380001", 37.0, -107.0, stype="well"), site("usgs", "370757107380000", 37.0001, -107.0001, stype="well"),
              site("wqp", "USGS-370758107380001", 37.0, -107.0, stype="well"), site("wqp", "NMED-1", 37.00005, -107.00005, stype="well"))
    assert [(r["site_uid_a"], r["site_uid_b"]) for r in gauge_id_links(s)] == [("wqp:USGS-370758107380001", "usgs:370758107380001")]
    links = build_links(s)
    pairs = {(r.site_uid_a, r.site_uid_b, r.link_type) for r in links.itertuples()}
    assert ("wqp:USGS-370758107380001", "usgs:370757107380000", "colocated") not in pairs and ("usgs:370757107380000", "wqp:USGS-370758107380001", "colocated") not in pairs
    assert ("wqp:NMED-1", "usgs:370758107380001", "colocated") in pairs or ("usgs:370758107380001", "wqp:NMED-1", "colocated") in pairs   # an unrelated id near a USGS site stays a candidate


def test_cocorahs_ids_map_to_ghcn_daily_ids():
    from nmwater.catalog.links import gauge_id_links
    s = frame(site("cocorahs", "NM-BR-35"), site("noaa_ghcnd", "US1NMBR0035"), site("cocorahs", "NM-DA-22"), site("noaa_ghcnd", "US1NMDA0099"))
    got = gauge_id_links(s)
    assert [(r["site_uid_a"], r["site_uid_b"]) for r in got] == [("cocorahs:NM-BR-35", "noaa_ghcnd:US1NMBR0035")]


def test_a_proximity_pair_is_settled_by_the_usgs_site_each_end_is_linked_to():
    from nmwater.catalog.links import settle_by_usgs_anchor
    cols = ["site_uid_a", "site_uid_b", "link_type", "evidence", "distance_m", "confidence"]
    links = pd.DataFrame([
        ["codwr:X", "usgs:1", "same_sensor", "e", None, 0.95], ["nwps:X", "usgs:1", "same_sensor", "e", None, 0.95], ["codwr:X", "nwps:X", "colocated", "near", 12.0, 0.8],
        ["codwr:Y", "usgs:2", "same_sensor", "e", None, 0.95], ["nwps:Y", "usgs:3", "same_sensor", "e", None, 0.95], ["codwr:Y", "nwps:Y", "colocated", "near", 30.0, 0.7],
        ["wqp:Z", "usgs:4", "same_location_different_sensor", "e", None, 0.95], ["nwps:Z", "usgs:4", "same_sensor", "e", None, 0.95], ["wqp:Z", "nwps:Z", "colocated", "near", 5.0, 0.8],
        ["codwr:W", "nwps:W", "colocated", "near", 40.0, 0.7]], columns=cols)
    out = settle_by_usgs_anchor(links)
    pairs = {(r.site_uid_a, r.site_uid_b): r.link_type for r in out.itertuples()}
    assert pairs[("codwr:X", "nwps:X")] == "same_sensor"                        # same USGS site at both ends
    assert ("codwr:Y", "nwps:Y") not in pairs                                   # two different USGS sites: not the same place
    assert pairs[("wqp:Z", "nwps:Z")] == "same_location_different_sensor"       # one end is only a sampling point at the site
    assert pairs[("codwr:W", "nwps:W")] == "colocated"                          # no anchors: still a candidate


def test_a_manual_link_settles_the_proximity_candidate_for_the_same_pair(tmp_path):
    s = frame(site("codwr", "A", 35.0, -106.0, stype="stream"), site("nwps", "B", 35.0001, -106.0001, stype="stream"))
    manual = tmp_path / "m.csv"
    manual.write_text("site_uid_a,site_uid_b,link_type,evidence\nnwps:B,codwr:A,same_sensor,daily values agree\n")
    got = build_links(s, manual=manual)
    assert list(got["link_type"]) == ["same_sensor"]
