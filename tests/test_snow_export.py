"""The snow part of the bundle: files, ratings, the station-day rule, units' stations, and the checks a schema cannot make (synthetic archive)."""

import json

import duckdb
import pandas as pd
import pytest
from shapely.geometry import box, mapping

from nmwater.derived import snow_normal as sn
from nmwater.site import schema as sc
from nmwater.site import snow as sx

LAST = pd.Timestamp("2026-01-31")
WINTERS = range(2004, 2027)


def winter_days(w):
    return pd.date_range(f"{w - 1}-10-01", f"{w}-03-31")


@pytest.fixture()
def archive(tmp_path):
    pq = tmp_path / "parquet"
    d = pq / "derived"
    (d / "watershed_snow").mkdir(parents=True)
    units = pd.DataFrame({"unit": ["13020101", "13020102", "mtn"], "kind": ["watershed", "watershed", "range"], "name": ["Alpha", "Beta", "High Range"],
                          "nm_fraction": [1.0, 0.5, 1.0], "cells": [500, 400, 90], "grid_fraction": [1.0, 0.7, 1.0]})
    units.to_parquet(d / "watershed_snow_units.parquet", index=False)
    rows = []
    for w in WINTERS:
        for day in winter_days(w):
            if day > LAST:
                continue
            for u, base, step in (("13020101", 6.0, 0.05), ("13020102", 0.01, 0.0), ("mtn", 9.0, 0.05)):
                v = base + (w - 2004) * step if w < 2026 else base * 0.4          # this winter is well under the usual
                rows.append((u, "range" if u == "mtn" else "watershed", day, v, 2 * v))
    df = pd.DataFrame(rows, columns=["unit", "kind", "date", "swe_in", "depth_in"])
    for y, g in df.groupby(df["date"].dt.year):
        (d / "watershed_snow" / f"year={y}").mkdir()
        g.to_parquet(d / "watershed_snow" / f"year={y}" / "snow.parquet", index=False)
    feat = {"type": "Feature", "properties": {"id": "mtn", "name": "High Range", "note": "Somewhere", "cells": 90, "model_smooths": True}, "geometry": mapping(box(-106.0, 36.0, -105.8, 36.2))}
    (d / "snow_ranges.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [feat], "signature": "x"}))
    con = duckdb.connect()
    con.execute("SET TimeZone='UTC'")
    con.execute("CREATE TABLE sites (site_uid VARCHAR, source VARCHAR, state VARCHAR, name VARCHAR, huc8 VARCHAR, lat DOUBLE, lon DOUBLE, elevation_m DOUBLE)")
    con.execute("CREATE TABLE observations_clean (site_uid VARCHAR, variable VARCHAR, datetime_utc TIMESTAMPTZ, value DOUBLE, interval VARCHAR)")
    con.executemany("INSERT INTO sites VALUES (?, 'nrcs', 'NM', ?, ?, ?, ?, ?)", [("nrcs:1:NM:SNTL", "Inside Peak", "13020101", 36.1, -105.9, 3000.0),      # inside the range
                                                                               ("nrcs:2:NM:SNTL", "Far Flat", "13020102", 34.0, -107.0, 2000.0)])
    obs = []
    for sid, bump in (("nrcs:1:NM:SNTL", 1.0), ("nrcs:2:NM:SNTL", 1.0)):
        for w in WINTERS:
            depth, swe = 10.0, 3.0
            for day in winter_days(w):
                if day > LAST:
                    continue
                snow_day = day.day % 10 == 0                          # a snowfall every tenth day: +4 in of depth and +0.5 in of water (in every winter)
                if w == 2026 and day >= pd.Timestamp("2026-01-25"):
                    snow_day = day.day in (25, 26, 27, 28, 29)       # this winter: five snowy days in a row to the end
                depth += 4.0 if snow_day else 0.0
                swe += 0.5 if snow_day else 0.0
                obs.append((sid, "snow_depth", day, depth * bump, "daily"))
                obs.append((sid, "swe", day, swe, "daily"))
    # the newest day has no daily value yet: only hourly readings, of which the 07:00 UTC one on the next UTC day (local midnight, the end of the day) counts
    nxt = LAST + pd.Timedelta(days=1)
    for sid in ("nrcs:1:NM:SNTL", "nrcs:2:NM:SNTL"):
        last_depth = [o for o in obs if o[0] == sid and o[1] == "snow_depth"][-1][3]
        last_swe = [o for o in obs if o[0] == sid and o[1] == "swe"][-1][3]
        for h in range(4, 12):
            at = nxt + pd.Timedelta(days=1, hours=h)
            obs.append((sid, "snow_depth", at, last_depth + 4.0 + (h - 7) * 50, "hourly"))     # only 07:00 may count: the other hours are wild on purpose
            obs.append((sid, "swe", at, last_swe + 0.5 + (h - 7) * 50, "hourly"))
    df_o = pd.DataFrame(obs, columns=["site_uid", "variable", "datetime_utc", "value", "interval"])
    df_o["datetime_utc"] = pd.to_datetime(df_o["datetime_utc"], utc=True)
    con.register("o", df_o)
    con.execute("INSERT INTO observations_clean SELECT * FROM o")
    out = tmp_path / "bundle"
    out.mkdir()
    (tmp_path / "rivers").mkdir()
    block = sx.export_snow(con, out, tmp_path / "rivers", pq, "2026-02-01T00:00:00+00:00", csv=True)
    return out, block, json.loads((out / "snow" / "index.json").read_text())


def test_the_block_and_the_files_exist_and_agree(archive):
    out, block, idx = archive
    assert block["path"] == "snow/" and (block["watersheds"], block["ranges"], block["stations"]) == (2, 1, 2)
    assert block["model_day"] == "2026-01-31" and isinstance(block["stations_day"], str)
    for u in ("13020101", "13020102", "mtn"):
        assert (out / "snow" / u / "snow.json").exists()
    assert (out / "snow" / "ranges.geojson").exists() and (out / "snow" / "recent.csv").exists()
    assert [w["unit"] for w in idx["watersheds"]] == ["13020101", "13020102"] and idx["ranges"][0]["model_smooths"] is True
    man = {"snow": block, "rivers": []}
    assert sc.snow_problems(out, json.loads(json.dumps(man))) == []


def test_schemas_accept_the_files_and_reject_a_broken_one(archive):
    out, _block, idx = archive
    assert sc._validate(idx, "snow_index", "idx") == []
    unit = json.loads((out / "snow" / "13020101" / "snow.json").read_text())
    assert sc._validate(unit, "snow_unit", "u") == [] and len(unit["series"]["swe"]["this"]) == 365 and len(unit["band"]["depth"]["p90"]) == 365
    unit["series"]["swe"]["this"] = unit["series"]["swe"]["this"][:300]
    assert sc._validate(unit, "snow_unit", "u")


def test_water_and_depth_rate_this_winter_against_the_baseline(archive):
    _out, _b, idx = archive
    w = {x["unit"]: x for x in idx["watersheds"]}
    r = w["13020101"]["ratings"]
    assert r["swe"]["cls"] == "much below normal" and r["swe"]["now"] == pytest.approx(2.4, abs=0.01) and r["swe"]["usual"] == pytest.approx(6.55, abs=0.2) and r["swe"]["n"] > 100
    assert r["depth"]["now"] == pytest.approx(4.8, abs=0.01) and r["depth"]["little"] is False
    assert w["13020102"]["ratings"]["swe"]["little"] is True and w["13020102"]["ratings"]["swe"]["cls"] is None                 # 0.004 in against a 0.01 in usual: both under 0.05 in, nothing to rate
    assert idx["ranges"][0]["ratings"]["swe"]["cls"] == "much below normal"


def test_stations_belong_to_a_range_when_inside_it_and_a_watershed_by_their_record(archive):
    out, _b, idx = archive
    st = {s["id"]: s for s in idx["stations"]}
    assert st["nrcs:1:NM:SNTL"]["range"] == "mtn" and st["nrcs:1:NM:SNTL"]["watershed"] == "13020101" and st["nrcs:2:NM:SNTL"]["range"] is None
    rng = json.loads((out / "snow" / "mtn" / "snow.json").read_text())
    assert [s["id"] for s in rng["stations"]] == ["nrcs:1:NM:SNTL"]
    assert idx["ranges"][0]["stations"] == 1 and idx["watersheds"][0]["stations"] == 1


def test_a_stations_day_is_its_daily_value_or_the_midnight_hourly_reading_not_a_partial_day(archive):
    _out, _b, idx = archive
    assert idx["stations_day"] == "2026-02-01" and idx["model_day"] == "2026-01-31"
    s = {x["id"]: x for x in idx["stations"]}["nrcs:1:NM:SNTL"]
    # depth on 31 Jan is the last daily value; 1 Feb adds the hourly reading at local midnight (07:00 UTC on 2 Feb, +4 in): new24 is that rise confirmed by the +0.5 in of water, not the wild neighbouring hours
    assert s["new24"] == pytest.approx(4.0) and s["depth"] < 1000
    assert s["new7"] >= 4.0


def test_new_snow_is_rated_against_other_winters_for_a_place_with_a_station(archive):
    _out, _b, idx = archive
    r = idx["watersheds"][0]["ratings"]["newseason"]
    assert r["now"] is not None and r["stations"] == 1 and r["n"] >= 15 and r["usual"] is not None and r["cls"] in idx["classes"]
    assert idx["ranges"][0]["ratings"]["new7"]["stations"] == 1


def test_the_checks_catch_a_rating_without_a_usual_a_missing_file_and_a_stray_directory(archive):
    out, block, _idx = archive
    man = json.loads(json.dumps({"snow": block, "rivers": []}, default=str))
    p = out / "snow" / "index.json"
    d = json.loads(p.read_text())
    d["watersheds"][0]["ratings"]["swe"]["usual"] = None
    p.write_text(json.dumps(d))
    assert any("rated without a usual" in x for x in sc.snow_problems(out, man))
    (out / "snow" / "13020102" / "snow.json").unlink()
    (out / "snow" / "stray").mkdir()
    problems = sc.snow_problems(out, man)
    assert any("13020102" in x and "missing" in x for x in problems) and any("stray" in x for x in problems)


def test_no_derived_series_means_no_snow_section(tmp_path):
    con = duckdb.connect()
    (tmp_path / "out").mkdir()
    assert sx.export_snow(con, tmp_path / "out", tmp_path, tmp_path / "parquet", "g") is None
    assert sn.DAYS == 365
