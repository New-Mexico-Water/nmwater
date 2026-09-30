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


def test_bad_values_are_removed_from_every_copy_and_recorded():
    d = pd.DataFrame({
        "gauge": ["G"] * 5, "source": ["usgs", "usbr_hydrodata", "usgs", "usace_cwms", "usgs"],
        "site_uid": ["usgs:1", "usbr:1", "usgs:1", "cwms:1", "usgs:1"],
        "date": pd.to_datetime(["2014-05-24", "2014-05-24", "2014-05-25", "2007-01-23", "2007-01-26"]),
        "cfs": [3340.0, 3340.0, 18.0, 50000.0, 38.0], "qualifier": ["A:e", None, "A", None, "A"]})
    peaks = {("G", 2014): 2580.0}
    ex = [{"site_uid": "cwms:1", "from": "2007-01-22", "to": "2007-01-24", "reason": "glitch"}]
    kept, removed, conflicts = rf.remove_bad_values(d, peaks, ex, {"usgs:1": "G", "usbr:1": "G", "cwms:1": "G"})
    assert kept["cfs"].tolist() == [18.0, 38.0]
    assert sorted((x["date"], x["source"]) for x in removed) == [
        ("2007-01-23", "usace_cwms"), ("2014-05-24", "usbr_hydrodata"), ("2014-05-24", "usgs")]
    assert all(x["reason"].startswith(("automatic", "listed")) for x in removed) and conflicts == []


def test_approved_usgs_values_above_the_peak_are_kept_as_conflicts():
    d = pd.DataFrame({"gauge": ["G"], "source": ["usgs"], "site_uid": ["usgs:1"],
                      "date": pd.to_datetime(["2014-06-03"]), "cfs": [115.0], "qualifier": ["A"]})
    kept, removed, conflicts = rf.remove_bad_values(d, {("G", 2014): 22.0}, [], {"usgs:1": "G"})
    assert len(kept) == 1 and removed == [] and conflicts[0]["peak"] == 22.0


def test_names_the_river_for_gauges_on_unnamed_reaches():
    from nmwater.reports.river_flow import names_the_river as n

    assert n("RIO RUIDOSO AT HOLLYWOOD, NM", "Rio Ruidoso")
    assert n("GALLINAS CREEK AT MONTEZUMA, NM", "Gallinas River")          # creek and river interchangeable
    assert n("PECOS RIVER (KAISER CHANNEL) NEAR LAKEWOOD, NM", "Pecos River")
    assert n("Pecos River 6 miles NE of Lakewood", "Pecos River")
    assert n("SAN JUAN RVR @ BOLACK RANCH BRDG", "San Juan River")
    assert n("MIMBRES R BL WAMEL CA NR DEMING, NM", "Mimbres River")
    assert n("ZUNI RIVER ABV BLACK ROCK RESERVOIR, NM", "Zuñi River")
    assert n("RIO HONDO AT DAMSI AT VALDEZ, NM", "Rio Hondo (Rio Grande-Elephant Butte)")
    assert not n("LITTLE TESUQUE CR AT BISHOPS LODGE NR SANTA FE, NM", "Rio Tesuque")
    assert not n("LITTLE NAVAJO RIVER AT CHROMO, CO.", "Navajo River")
    assert not n("PECOS RIVER TRIB NR PUERTO DE LUNA, NM", "Pecos River")
    assert not n("SAN ANTONIO ARROYO AT RIO GRANDE CONFLUENCE IN ABQ", "Rio Grande")
    assert not n("Rio Grande Nature Center High-Flow Channel at Albq", "Rio Grande")
