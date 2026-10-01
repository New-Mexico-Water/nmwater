"""Overview tab: acequia matching, citation numbering, the unreviewed-research gate, and share metadata."""

from datetime import date

from nmwater.derived import river_context as rc
from nmwater.derived import river_facts as ro
from nmwater.site import meta as rs


def ctx(counties, mapped):
    return rc.Context(acequias={"Seg": {"count": len(mapped), "names": mapped}}, water_use={"counties": counties})


DB = {"acequias": [
    {"name": "Acequia de Alcalde", "county": "Rio Arriba", "stream": "Rio Grande"},
    {"name": "Acequia de Los Ortizes", "county": "Santa Fe", "stream": "Nambe River"},
    {"name": "Acequia Madre", "county": "Taos", "stream": None},
    {"name": "Acequia Madre", "county": "Santa Fe", "stream": None},
]}


def test_acequia_on_named_stream():
    got = ro.governed_acequias("Rio Grande", ctx(["Rio Arriba", "Taos"], []), DB)
    assert [a["name"] for a in got] == ["Acequia de Alcalde"]


def test_acequia_on_another_stream_is_left_out_even_when_the_name_is_mapped_here():
    got = ro.governed_acequias("Rio Tesuque", ctx(["Santa Fe"], ["Acequia De Los Ortizes"]), DB)
    assert got == []


def test_repeated_names_match_only_in_the_rivers_counties():
    got = ro.governed_acequias("Rio Pueblo", ctx(["Taos"], ["Acequia Madre"]), DB)
    assert [(a["name"], a["county"]) for a in got] == [("Acequia Madre", "Taos")]


def test_base_name_does_not_match_a_longer_river():
    db = {"acequias": [{"name": "X", "county": "Taos", "stream": "Rio Hondo"}]}
    assert ro.governed_acequias("Rio Grande", ctx(["Taos"], []), db) == []
    assert len(ro.governed_acequias("Rio Hondo (Rio Grande-Elephant Butte)", ctx(["Taos"], []), db)) == 1



def test_unreviewed_research_is_not_used(tmp_path):
    d = tmp_path / "config" / "river_context"
    d.mkdir(parents=True)
    (d / "x.yaml").write_text(f"river: X\n{ro.UNCHECKED}\nsummary: [{{text: hi, sources: [1]}}]\n")
    (d / "y.yaml").write_text("river: Y\n# Checked 2026-09-28\nsummary: [{text: hi, sources: [1]}]\n")
    assert ro.load_research(tmp_path, "x") == {}
    assert ro.load_research(tmp_path, "y")["river"] == "Y"
    assert ro.load_research(tmp_path, "missing") == {}


def test_description_keeps_the_status_within_160_characters():
    rows = [{"cls": "much below normal"}] * 4 + [{"cls": "normal"}, {"cls": None}]
    d = rs.description("Cimarron River (Upper Canadian)", rows, ["normal", "quality", "watershed"], date(2026, 9, 20))
    assert len(d) <= 160
    assert "4 of 5 segments were below normal" in d and d.endswith("Sep 20, 2026.")
    one = rs.description("Rio Tesuque", [{"cls": "much above normal"}], [], date(2026, 9, 20))
    assert "Last week's flow: much above normal." in one




def test_acequia_list_merges_the_map_and_the_research():
    db = {"acequias": [{"name": "Acequia de Alcalde", "community": "Alcalde", "county": "Rio Arriba", "stream": "Rio Grande",
                        "sources": [3]},
                       {"name": "Acequia de Chamita", "community": "Chamita (officer's listed address)", "county": "Rio Arriba",
                        "stream": None, "sources": [3]}]}
    c = ctx(["Rio Arriba"], ["Acequia De Chamita", "Acequia De Atalaya"])
    got = ro.acequia_list("Rio Grande", c, db)
    assert [x["name"] for x in got] == ["Acequia de Alcalde", "Acequia De Atalaya", "Acequia de Chamita"]
    assert got[0]["where"] == "Alcalde, Rio Arriba County"
    assert got[2]["where"] == "Rio Arriba County"          # a guessed community is not shown
