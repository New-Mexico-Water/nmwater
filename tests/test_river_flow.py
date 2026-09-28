"""River report helpers that decide which gauges and segments a river page shows."""

import json

import pandas as pd

from nmwater.reports import river_flow as rf


def test_river_tokens_drop_generic_words_and_accents():
    assert rf.river_tokens("Rio Peñasco") == ["penasco"]
    assert rf.river_tokens("West Fork Gila River") == ["gila"]
    assert rf.river_tokens("Rio Grande") == ["grande"]


def test_river_gauges_need_the_river_word_in_their_name():
    assert rf.is_river_gauge("RIO GRANDE AT EMBUDO, NM", "Rio Grande")
    assert rf.is_river_gauge("Rio Grande bl Elebutte Butte", "Rio Grande")
    assert not rf.is_river_gauge("Heuchling #2", "Rio Grande")          # a ditch gauge sitting on the river
    assert rf.is_river_gauge("RIO PENASCO AT DAYTON, NM", "Rio Peñasco")


def test_slugify():
    assert rf.slugify("Rio Peñasco") == "rio-penasco"
    assert rf.slugify("Kim-me-ni-oli Wash") == "kim-me-ni-oli-wash"


def test_segments_run_upstream_to_downstream_by_drainage_area():
    g = pd.DataFrame({"segment": ["Lower", "Upper", "Middle", "Upper"], "totdasqkm": [900.0, 50.0, 400.0, 80.0]})
    assert rf.order_segments(g) == ["Upper", "Middle", "Lower"]


def test_run_swaps_in_a_complete_tree_and_keeps_other_rivers_on_a_partial_run(tmp_path, monkeypatch):
    out = tmp_path / "dist"
    old = out / "rivers" / "pecos-river"
    old.mkdir(parents=True)
    (old / "index.html").write_text("old pecos")
    (out / "rivers" / "manifest.json").write_text(json.dumps({"rivers": [
        {"river": "Pecos River", "slug": "pecos-river", "path": "pecos-river/index.html", "segments": ["A"],
         "gauges": 3, "first_week": "1903-01-05", "last_week": "2026-09-14", "last_52_mean_cfs": 88.0, "reporting": True}]}))

    class FakeCon:
        def execute(self, *a, **k): return self
        def sql(self, *a, **k): return self
        def df(self): return pd.DataFrame(columns=["site_uid_a", "site_uid_b"])
        def fetchone(self): return (None,)
        def fetchall(self): return [("Gila River", "1", 3, "Upper Gila", "Upper Gila")]
        def close(self): pass

    import duckdb
    monkeypatch.setattr(duckdb, "connect", lambda *a, **k: FakeCon())
    monkeypatch.setattr(rf, "build_river", lambda *a, **k: None)          # the requested river has no data
    entries, failed = rf.run(tmp_path / "x.duckdb", out, rivers=["Gila River"])
    assert failed == [] and [e["slug"] for e in entries] == ["pecos-river"]
    assert (out / "rivers" / "pecos-river" / "index.html").read_text() == "old pecos"
    assert (out / "rivers" / "index.html").exists() and not list(out.glob(".rivers*"))


def test_rivers_sharing_a_name_get_their_basin_in_the_label():
    a = rf.River("Rio Hondo", "910242", "Rio Hondo")
    a.basin, a.huc8 = "Upper Pecos", "Rio Hondo"
    b = rf.River("Rio Hondo", "910243", "Rio Hondo")
    b.basin, b.huc8 = "Rio Grande-Elephant Butte", "Upper Rio Grande"
    c = rf.River("Rocky Arroyo", "1", "Rocky Arroyo")
    c.basin, c.huc8 = "Upper Pecos", "Rio Hondo"
    d = rf.River("Rocky Arroyo", "2", "Rocky Arroyo")
    d.basin, d.huc8 = "Upper Pecos", "Upper Pecos-Black"
    e = rf.River("Pecos River", "3", "Pecos River")
    e.basin, e.huc8 = "Upper Pecos", "x"
    rf.label_rivers([a, b, c, d, e])
    assert (a.label, b.label) == ("Rio Hondo (Upper Pecos)", "Rio Hondo (Rio Grande-Elephant Butte)")
    assert (c.label, d.label) == ("Rocky Arroyo (Rio Hondo)", "Rocky Arroyo (Upper Pecos-Black)")   # same basin: use watershed
    assert e.label == "Pecos River"


def test_ditches_ponds_channels_and_reservoirs_are_not_river_gauges():
    assert not rf.is_river_gauge("ACEQUIA MADRE AT COSTILLA, NM", "Costilla Creek")
    assert not rf.is_river_gauge("Chama Valley #3", "Rio Chama")
    assert not rf.is_river_gauge("RIO GRANDE CONVEYANCE CHANNEL NEAR BERNARDO, NM", "Rio Grande")
    assert not rf.is_river_gauge("BLANCO DIVERSION RESERVOIR", "Rio Blanco")
    assert rf.is_river_gauge("NORTH CLEAR CREEK BELOW CONTINENTAL RESERVOIR", "North Clear Creek")
    assert rf.is_river_gauge("RIO GRANDE FLOODWAY AT SAN ACACIA, NM", "Rio Grande")
