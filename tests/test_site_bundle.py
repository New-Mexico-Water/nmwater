"""The site data bundle: helpers, the schemas, and the checks a schema cannot make."""

import json
import math

import numpy as np
import pandas as pd
import pytest

from nmwater.site import river as sr
from nmwater.site import schema as sch
from nmwater.site.sources import Sources

pytest.importorskip("jsonschema")


def test_clean_makes_values_json_safe():
    out = sr.clean({"a": float("nan"), "b": np.float64(1.5), "c": np.int64(3), "d": pd.Timestamp("2026-09-29 13:00"),
                    "e": [float("inf"), 6.6049999999999995], "f": np.bool_(True)})
    assert out == {"a": None, "b": 1.5, "c": 3, "d": "2026-09-29", "e": [None, 6.605], "f": True}
    json.dumps(out, allow_nan=False)


def test_iso_from_epoch_milliseconds():
    assert sr.iso(pd.Timestamp("2026-09-24").value // 1_000_000) == "2026-09-24" and sr.iso(None) is None


def test_sources_are_numbered_once_per_url_across_files():
    s = Sources()
    a = {"1": {"url": "https://a", "title": "A", "publisher": "P"}, "2": {"url": "https://b", "title": "B"}}
    b = {"9": {"url": "https://b", "title": "B again"}, "3": {"url": "https://c"}}
    assert s.ids([2, 1], a) == [1, 2]
    assert s.ids([9, 3], b) == [1, 3]                    # https://b keeps number 1 (first seen), https://c is 3
    assert [x["url"] for x in s.items] == ["https://b", "https://a", "https://c"]
    assert s.ids([9], b) == [1] and s.ids([99], b) == []
    st = s.statements([{"text": "  two   spaces ", "sources": [1]}], a)
    assert st == [{"text": "two spaces", "sources": [2]}]


# ---------------------------------------------------------------------------- a minimal valid bundle
def make_bundle(root):
    d = root / "rivers" / "rio-x"
    d.mkdir(parents=True)
    weeks = ["2026-09-07", "2026-09-14"]
    summary = {
        "schema_version": 2, "river": {"name": "Rio X", "slug": "rio-x", "gnis_id": "1"}, "generated": "2026-10-01T00:00:00+00:00",
        "data_through": "2026-09-20", "tabs": ["overview", "flow", "normal", "drying", "watershed", "notes"],
        "files": {"summary": "summary.json", "geo": ["geo/state-view.geojson", "geo/river-view.geojson", "geo/bounds.json"], "flow": "flow.json", "drying": "drying.json", "notes": "notes.json",
                  "normal": "normal.json", "watershed": "watershed.json", "csv": [], "notes_md": "notes.md"},
        "description": {"background": [{"text": "It flows.", "sources": [1]}], "background_references": []},
        "status": {"segments_rated": 1, "segments_below_normal": 0, "segments": 1},
        "segments": [{"index": 1, "name": "Seg", "color_index": 1, "huc8": "13020101", "flow_cfs": 5.0, "class": "normal", "percentile": 50.0,
                      "last_52_weeks_cfs": [1.0, None], "dry_days_this_year": 0, "dry_days_normal": 0.0, "water_temp_c": None,
                      "water_temp_site": None, "conductance_uS_cm": None, "conductance_decade": None,
                      "precip_3_months_percent_of_normal": 100.0, "dsci": None, "huc4": "1302", "towns": [], "acequias": []}],
        "facts": {"length_km": 10.0, "drainage_km2": None, "flows_into": None, "tributaries": [], "reservoirs": [], "towns": [], "gauges": 1,
                  "first_year": 1990, "as_of_year": 2026},
        "culture": [], "habitat": [], "users": [],
        "acequias": {"items": [{"name": "Acequia A", "where": None, "segment": "Seg", "sources": [1]}], "map_distance_km": 1.5, "note": "n"},
        "reservoirs": [], "water_use": None, "irrigation_districts": [], "water_systems": [],
        "sources": [{"id": 1, "title": "T", "publisher": "P", "url": "https://example.org", "accessed": "2026-09-28"}]}
    flow = {"schema_version": 2, "river": "Rio X", "weeks": weeks, "first_year": 1990, "last_week_end": "2026-09-20", "min_weeks": 26,
            "series": [{"name": "Seg", "color_index": 1, "v": [1.0, 2.0], "n": [1, 1]}], "gauges": [], "notes": [], "dropped_segments": []}
    normal = {"schema_version": 2, "baseline": "1991-2020", "classes": ["normal"], "weeks": weeks,
              "segments": [{"name": "Seg", "color_index": 1, "cls": ["normal", None], "pct": [50.0, None]}], "gauges": []}
    drying = {"schema_version": 2, "dry_cfs": 0.1, "normally_dry_share": 0.9, "years": [2025, 2026], "as_of": "2026-09-29", "baseline": "1991-2020",
              "segments": [{"name": "Seg", "color_index": 1, "dry_days": [0, 1], "days_with_data": [365, 200], "median": 0.0}], "left_out": []}
    ws = {"schema_version": 2, "as_of": "2026-09-29", "segments": [{
        "name": "Seg", "color_index": 1, "huc8": "13020101", "huc4": "1302", "coverage": {"grid_fraction": 1.0, "has_precip": True, "note": None},
        "precipitation": {"months": ["2026-08-01", "2026-09-01"], "total_in": [1.0, 2.0], "normal_in": [1.5, 1.1], "days_counted": [31, 26],
                          "complete": [True, False]},
        "daily_precipitation": {"dates": ["2026-09-25"], "inches": [0.2], "note": "n"}, "recent": None,
        "temperature": {"months": ["2026-08-01"], "anomaly_c": [1.0]},
        "snow": {"weeks_of_water_year": [0, 1], "median_2004_2025_in": [0.0, 1.0], "winters": [{"water_year": 2026, "inches": [0.0, 2.0]}]},
        "drought": {"dates": ["2026-09-22"], "dsci": [100]}}]}
    notes = {"schema_version": 2, "counts": {"removed_values": 0, "reviewed_notes": 0, "warnings": 0, "context": 0}, "removed_values": [],
             "reviewed_notes": [], "findings": [], "gauges": [], "min_weeks": 26, "method_notes": []}
    for name, obj in (("summary", summary), ("flow", flow), ("normal", normal), ("drying", drying), ("watershed", ws), ("notes", notes)):
        (d / f"{name}.json").write_text(json.dumps(obj))
    (d / "notes.md").write_text("notes")
    (d / "geo").mkdir()
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"kind": "river"}, "geometry": {"type": "LineString", "coordinates": [[-106.0, 35.0], [-105.9, 35.1]]}}]}
    (d / "geo" / "state-view.geojson").write_text(json.dumps(fc))
    (d / "geo" / "river-view.geojson").write_text(json.dumps(fc))
    (d / "geo" / "bounds.json").write_text(json.dumps({"schema_version": 2, "extent": {"west": -106.1, "south": 34.9, "east": -105.8, "north": 35.2}}))
    manifest = {"schema_version": 2, "producer": {"name": "nmwater", "git_sha": None}, "generated": "2026-10-01T00:00:00+00:00",
                "data_through": "2026-09-29", "site": {"name": "S"},
                "rivers": [{"slug": "rio-x", "name": "Rio X", "path": "rivers/rio-x/", "segments": 1, "gauges": 1, "first_year": 1990,
                            "last_week": "2026-09-14", "tabs": summary["tabs"], "reporting": True, "last_52_mean_cfs": 3.0, "segments_rated": 1,
                            "segments_below_normal": 0, "issues_warn": 0, "removed_values": 0, "social_image": None,
                            "bytes": {}, "generated": "2026-10-01T00:00:00+00:00"}],
                "headlines": {"rivers": {"total": 1, "rated": 1, "with_segment_below_normal": 0}}}
    (root / "manifest.json").write_text(json.dumps(manifest))
    return d


def edit(d, name, fn):
    p = d / name
    obj = json.loads(p.read_text())
    fn(obj)
    p.write_text(json.dumps(obj))


def test_a_conforming_bundle_has_no_problems(tmp_path):
    make_bundle(tmp_path)
    assert sch.validate_bundle(tmp_path) == []


@pytest.mark.parametrize("name,fn,expect", [
    ("summary.json", lambda o: o.pop("sources"), "sources"),
    ("flow.json", lambda o: o["series"][0].update(v=[1.0]), "series 'Seg' has 1 values"),
    ("normal.json", lambda o: o["segments"][0].update(pct=[1.0]), "arrays do not match"),
    ("drying.json", lambda o: o["segments"][0].update(dry_days=[0]), "arrays do not match"),
    ("watershed.json", lambda o: o["segments"][0]["precipitation"].update(days_counted=[31]), "arrays differ in length"),
    ("summary.json", lambda o: o["description"]["background"][0].update(sources=[7]), "cites source 7"),
    ("summary.json", lambda o: o.update(tabs=o["tabs"] + ["quality"]), "tab 'quality' has no file"),
    ("summary.json", lambda o: o["files"].pop("geo"), "files.geo is missing"),
    ("flow.json", lambda o: o.update(schema_version=1), "schema_version"),
])
def test_problems_are_found(tmp_path, name, fn, expect):
    d = make_bundle(tmp_path)
    edit(d, name, fn)
    problems = sch.validate_bundle(tmp_path)
    assert problems and any(expect in p for p in problems), problems


def test_missing_file_and_bad_geometry_are_found(tmp_path):
    d = make_bundle(tmp_path)
    (d / "flow.json").unlink()
    assert any("summary.files.flow names flow.json" in p for p in sch.validate_bundle(tmp_path))
    d = make_bundle(tmp_path / "b")
    edit(d / "geo", "river-view.geojson", lambda o: o["features"][0]["properties"].pop("kind"))
    assert any("has no kind" in p for p in sch.validate_bundle(tmp_path / "b"))
    edit(d / "geo", "river-view.geojson", lambda o: o.update(type="Feature"))
    assert any("river-view.geojson" in p for p in sch.validate_bundle(tmp_path / "b"))


def test_null_values_in_series_are_allowed(tmp_path):
    d = make_bundle(tmp_path)
    edit(d, "flow.json", lambda o: o["series"][0].update(v=[None, 2.0], n=[None, 1]))
    assert sch.validate_bundle(tmp_path) == []


def test_every_schema_is_valid_json_schema():
    from jsonschema import Draft202012Validator

    for name in ("manifest", "summary", "flow", "normal", "drying", "quality", "watershed", "notes"):
        Draft202012Validator.check_schema(sch.load_schema(name))
    assert math.isfinite(1.0)


def test_licence_block_is_optional_but_checked_when_present(tmp_path):
    d = make_bundle(tmp_path)
    block = {"spdx": "CC-BY-SA-4.0", "url": "https://creativecommons.org/licenses/by-sa/4.0/", "attribution": "New Mexico Water",
             "attribution_url": None, "scope": "Our contribution.", "not_covered": ["The logo and icon"]}
    p = tmp_path / "manifest.json"
    m = json.loads(p.read_text())
    m["license"] = block
    p.write_text(json.dumps(m))
    assert sch.validate_bundle(tmp_path) == []
    m["license"] = {k: v for k, v in block.items() if k != "url"}
    p.write_text(json.dumps(m))
    assert any("license" in x for x in sch.validate_bundle(tmp_path))


# ---------------------------------------------------------------------------- precipitation
from nmwater.site import precip as sp                     # noqa: E402


def test_window_stats_rates_the_last_days_against_the_same_dates_in_other_years():
    idx = pd.date_range("1981-01-01", "2026-09-26", freq="D")
    s = pd.Series(0.1, index=idx)
    s[(s.index >= "2026-09-20")] = 0.5                              # the last 7 days are far wetter than any year's same week
    st = sp.window_stats(s, 7)
    assert st["total_in"] == pytest.approx(3.5) and st["normal_in"] == pytest.approx(0.7)
    assert st["class"] == "much above normal" and st["percentile"] == 100.0
    assert st["window_end"] == pd.Timestamp("2026-09-26") and st["window_start"] == pd.Timestamp("2026-09-20")
    assert sp.window_stats(s.iloc[:3], 7) is None
    short = s[s.index >= "2015-01-01"]                               # fewer than 20 baseline years: no "normal"
    assert sp.window_stats(short, 7)["normal_in"] is None and sp.window_stats(short, 7)["class"] is None


def make_precip(root, man):
    base = root / "precipitation"
    (base / "13020101").mkdir(parents=True)
    stat = {"window_start": "2026-09-20", "window_end": "2026-09-26", "total_in": 1.0, "normal_in": 0.5, "percent_of_normal": 200.0, "percentile": 95.0,
            "class": "much above normal", "wettest_date": "2026-09-24", "wettest_in": 0.6}
    stats = {"7": stat, "30": stat, "90": None}
    head = {"huc8": "13020101", "name": "Upper Rio Grande", "states": ["CO", "NM"], "area_km2": 100.0, "nm_fraction": 0.8, "grid_fraction": 1.0, "partial": False,
            "coverage_note": None, "stats": stats}
    one = {**head, "schema_version": 2, "as_of": "2026-09-26", "baseline": "1991-2020", "months": ["2026-08-01", "2026-09-01"], "total_in": [1.0, 2.0], "normal_in": [1.5, 1.1],
           "days_counted": [31, 26], "complete": [True, False], "daily": {"dates": ["2026-09-25"], "inches": [0.2], "note": "n"}, "rivers": [{"slug": "rio-x", "name": "Rio X"}]}
    (base / "13020101" / "precip.json").write_text(json.dumps(one))
    index = {"schema_version": 2, "generated": "2026-10-01T00:00:00+00:00", "as_of": "2026-09-26", "windows": [7, 30, 90], "baseline": "1991-2020",
             "classes": ["normal"], "note": "n", "watersheds": [{**head, "rivers": [{"slug": "rio-x", "name": "Rio X"}]}],
             "files": {"index": "index.json", "csv": []}}
    (base / "index.json").write_text(json.dumps(index))
    man["precipitation"] = {"path": "precipitation/", "watersheds": 1, "as_of": "2026-09-26", "windows": [7, 30, 90], "files": index["files"]}
    (root / "manifest.json").write_text(json.dumps(man))
    return base


def test_precipitation_section_is_validated(tmp_path):
    make_bundle(tmp_path)
    man = json.loads((tmp_path / "manifest.json").read_text())
    base = make_precip(tmp_path, man)
    assert sch.validate_bundle(tmp_path) == []
    edit(base / "13020101", "precip.json", lambda o: o.update(total_in=[1.0]))
    assert any("monthly arrays differ" in p for p in sch.validate_bundle(tmp_path))
    edit(base / "13020101", "precip.json", lambda o: o.update(total_in=[1.0, 2.0], rivers=[{"slug": "nope", "name": "Nope"}], stats={}))
    assert any("stats" in p for p in sch.validate_bundle(tmp_path))
    (base / "13020101" / "precip.json").unlink()
    assert any("precip.json is missing" in p for p in sch.validate_bundle(tmp_path))


def test_files_index_lists_every_file_and_catches_changes(tmp_path):
    from nmwater.site.bundle import write_files_index

    make_bundle(tmp_path)
    n = write_files_index(tmp_path)
    idx = json.loads((tmp_path / "files.json").read_text())
    assert n == len(idx["files"]) and "manifest.json" in {e["path"] for e in idx["files"]} and "files.json" not in {e["path"] for e in idx["files"]}
    assert sch.validate_bundle(tmp_path) == []
    (tmp_path / "rivers" / "rio-x" / "notes.md").write_text("changed after the index was written")
    assert any("does not match" in p for p in sch.validate_bundle(tmp_path))
    write_files_index(tmp_path)
    (tmp_path / "extra.txt").write_text("x")
    assert any("not in files.json" in p for p in sch.validate_bundle(tmp_path))


# ---------------------------------------------------------------------------- geometry
def test_geo_files_are_validated(tmp_path):
    make_bundle(tmp_path)
    man = json.loads((tmp_path / "manifest.json").read_text())
    base = make_precip(tmp_path, man)
    sq = {"type": "Polygon", "coordinates": [[[-107.0, 35.0], [-106.0, 35.0], [-106.0, 36.0], [-107.0, 36.0], [-107.0, 35.0]]]}
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"huc8": "13020101", "name": "x"}, "geometry": sq}]}
    (tmp_path / "geo").mkdir()
    (tmp_path / "geo" / "state.geojson").write_text(json.dumps(fc))
    (tmp_path / "geo" / "county-lines.geojson").write_text(json.dumps(fc))
    (base / "watersheds.geojson").write_text(json.dumps(fc))
    man = json.loads((tmp_path / "manifest.json").read_text())
    man["geo"] = {"state": "geo/state.geojson", "county_lines": "geo/county-lines.geojson", "bounds": {"west": -109.1, "south": 31.3, "east": -103.0, "north": 37.0}}
    man["precipitation"]["files"]["watersheds"] = "watersheds.geojson"
    (tmp_path / "manifest.json").write_text(json.dumps(man))
    assert sch.validate_bundle(tmp_path) == []
    fc["features"][0]["geometry"]["coordinates"][0][1] = [-90.0, 35.0]                  # a vertex in Louisiana
    (base / "watersheds.geojson").write_text(json.dumps(fc))
    assert any("outside the state's bounds" in p for p in sch.validate_bundle(tmp_path))
    fc["features"][0]["properties"]["huc8"] = "99999999"
    fc["features"][0]["geometry"]["coordinates"][0][1] = [-106.0, 35.0]
    (base / "watersheds.geojson").write_text(json.dumps(fc))
    assert any("has no shape for 13020101" in p for p in sch.validate_bundle(tmp_path))
