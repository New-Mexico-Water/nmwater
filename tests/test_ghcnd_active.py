"""Issue #46: a station recorded as ending in 2026 must not drop out of updates in 2027."""

import json

import pandas as pd

from nmwater.sources.noaa_ghcnd import active_stations


def _sites(**last):
    return pd.DataFrame({"native_id": list(last), "raw_metadata": [json.dumps({"inventory": {"PRCP": [1950, y]}}) for y in last.values()]})


def test_a_station_whose_stored_year_is_2026_is_kept_in_2027_when_the_live_inventory_says_it_still_reports():
    sites = _sites(USC1=2026, USC2=2026, USC3=2019)
    kept = active_stations(sites, 2027, {"USC1": 2027, "USC2": 2026, "USC3": 2019})
    assert list(kept["native_id"]) == ["USC1"]                       # USC2 really ended in 2026, USC3 long ago


def test_without_the_live_inventory_the_stored_year_gets_a_years_tolerance_so_nothing_is_lost():
    sites = _sites(USC1=2026, USC3=2019)
    assert list(active_stations(sites, 2027, None)["native_id"]) == ["USC1"]
    assert list(active_stations(sites, 2026, None)["native_id"]) == ["USC1"]


def test_a_station_missing_from_the_live_inventory_uses_its_stored_year_and_unreadable_metadata_keeps_the_station():
    sites = pd.DataFrame({"native_id": ["A", "B", "C"], "raw_metadata": [json.dumps({"inventory": {"PRCP": [1950, 2027]}}), "not json", json.dumps({"inventory": {"PRCP": [1950, 2010]}})]})
    assert list(active_stations(sites, 2027, {"Z": 2027})["native_id"]) == ["A", "B"]
