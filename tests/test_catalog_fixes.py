"""Reviewed catalog corrections: NHDPlus river-name fixes and crosswalk site/qualifier overrides."""

import pandas as pd

from nmwater.catalog import reach_fixes as rf
from nmwater.catalog.crosswalk import Crosswalk

FLOW = pd.DataFrame({
    "comid": [1, 2, 3, 4, 5, 6],
    "gnis_id": ["928787", "928787", "928790", "", "", ""],
    "gnis_name": ["Rio Fernando de Taos", "Rio Fernando de Taos", "Rio Pueblo de Taos", None, None, "Named Creek"],
    "totdasqkm": [159.0, 486.0, 175.0, 482.0, 900.0, 300.0],
    "levelpathi": [10, 10, 11, 20, 20, 20],
    "huc8": ["13020101", "13020101", "13020101", "11080007", "11080006", "11080007"],
})
REACHES = pd.DataFrame({"site_uid": ["usgs:07226500"], "comid": [4]})


def test_rename_applies_only_above_the_drainage_threshold():
    rows = [{"rule": "rename", "match": "gnis_id=928787", "min_totdasqkm": "300", "huc8": "",
             "new_name": "Rio Pueblo de Taos", "new_gnis_id": "928790"}]
    fx = rf.resolve_frames(FLOW, REACHES, rows)
    assert list(fx["comid"]) == [2]                      # 486 km2 is renamed, the Rio Fernando's own 159 km2 is not
    assert fx.iloc[0]["gnis_name"] == "Rio Pueblo de Taos" and fx.iloc[0]["gnis_id"] == "928790"


def test_unnamed_reaches_on_a_sites_level_path_are_named_within_the_listed_watersheds():
    rows = [{"rule": "levelpath_unnamed", "match": "site=usgs:07226500", "min_totdasqkm": "", "huc8": "11080007",
             "new_name": "Ute Creek", "new_gnis_id": "fix:ute-creek-canadian"}]
    fx = rf.resolve_frames(FLOW, REACHES, rows)
    assert list(fx["comid"]) == [4]                      # comid 5 is in another HUC8, comid 6 already has a name


def test_unknown_rule_is_an_error():
    import pytest

    with pytest.raises(ValueError):
        rf.resolve_frames(FLOW, REACHES, [{"rule": "nope", "match": "x=1", "min_totdasqkm": "", "huc8": "",
                                           "new_name": "n", "new_gnis_id": "i"}])


def test_crosswalk_site_and_qualifier_overrides():
    x = Crosswalk()
    df = pd.DataFrame({"site_uid": ["iem_dcp:ABIN5", "iem_dcp:ABIN5", "iem_dcp:BONN5"], "source_param": ["HP"] * 3,
                       "qualifier": ["HPIRGZ", "HPIRZZZ", "HPIRGZ"], "value": [6200.0, 192.0, 6.8]})
    out = x.apply(df, "iem_dcp").set_index(["site_uid", "qualifier"])
    assert out.loc[("iem_dcp:ABIN5", "HPIRGZ"), "variable"] == "reservoir_elevation"   # a real pool elevation stays
    assert out.loc[("iem_dcp:ABIN5", "HPIRZZZ"), "variable"] == "stage"                # the gauge-height series moves
    assert out.loc[("iem_dcp:BONN5", "HPIRGZ"), "variable"] == "stage"                 # a site that only sends gauge height
    assert out.loc[("iem_dcp:ABIN5", "HPIRZZZ"), "unit"] == "ft"
