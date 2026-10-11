"""Site coordinates: zero-zero is a missing location, a lost minus sign is put back, nothing else is touched."""

import json

import pandas as pd

from nmwater.core.geo import clean_site_coordinates
from nmwater.core.store import Store


def frame(rows):
    return pd.DataFrame([{"site_uid": f"t:{i}", "native_id": str(i), "lat": a, "lon": b, "raw_metadata": json.dumps({"k": 1})} for i, (a, b) in enumerate(rows)])


def test_zero_zero_becomes_missing_and_is_recorded():
    out, n = clean_site_coordinates(frame([(0.0, 0.0), (35.1, -106.6)]))
    assert n == {"zero_zero": 1, "sign_flipped": 0}
    assert pd.isna(out.loc[0, "lat"]) and pd.isna(out.loc[0, "lon"])
    assert "coordinate_fix" in json.loads(out.loc[0, "raw_metadata"]) and json.loads(out.loc[0, "raw_metadata"])["k"] == 1
    assert (out.loc[1, "lat"], out.loc[1, "lon"]) == (35.1, -106.6)
    assert "coordinate_fix" not in json.loads(out.loc[1, "raw_metadata"])


def test_a_positive_longitude_that_lands_in_the_us_is_flipped():
    out, n = clean_site_coordinates(frame([(35.9, 108.49), (35.9, 12.0), (0.0, 108.0)]))
    assert n["sign_flipped"] == 1
    assert out.loc[0, "lon"] == -108.49
    assert out.loc[1, "lon"] == 12.0           # flipped, it would be in the Atlantic: left as given
    assert out.loc[2, "lon"] == 108.0          # latitude 0 is not a US point either


def test_one_zero_coordinate_and_missing_values_are_left_alone():
    out, n = clean_site_coordinates(frame([(0.0, -106.0), (35.0, 0.0), (None, None)]))
    assert n == {"zero_zero": 0, "sign_flipped": 0}
    assert out.loc[0, "lon"] == -106.0 and out.loc[1, "lat"] == 35.0


def test_write_sites_applies_the_repair(tmp_path):
    store = Store(tmp_path)
    store.write_sites(pd.DataFrame({"native_id": ["a", "b"], "lat": [0.0, 35.0], "lon": [0.0, 108.0]}), "demo")
    got = store.read_sites("demo").set_index("native_id")
    assert pd.isna(got.loc["a", "lat"]) and got.loc["b", "lon"] == -108.0
