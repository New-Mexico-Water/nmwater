"""Statewide totals: group series, windows, the normal of acre-feet held, and the bundle section's validation."""

import json

import numpy as np
import pandas as pd
import pytest

from nmwater.derived import statewide as sw
from nmwater.site import schema as sch

pytest.importorskip("jsonschema")


def frame(start, end, stor, cap, conf="high"):
    d = pd.date_range(start, end, freq="D")
    return pd.DataFrame({"date": d, "stor": stor if not callable(stor) else [stor(x) for x in d], "primary_af": cap, "confidence": conf})


def test_percent_full_is_capacity_weighted_not_a_mean_of_percents():
    f = {"big": frame("2026-01-01", "2026-01-10", 100.0, 1000.0), "small": frame("2026-01-01", "2026-01-10", 90.0, 100.0)}
    s = sw.group_series(f, ["big", "small"])
    last = s.iloc[-1]
    assert last["storage_af"] == 190.0 and last["capacity_af"] == 1100.0 and bool(last["complete"])
    pct = 100 * last["storage_af"] / last["capacity_af"]
    assert pct == pytest.approx(17.27, abs=0.01)                      # 10% and 90% average 50%: the statewide figure must not be that
    assert abs(pct - 50.0) > 30


def test_a_feed_a_few_days_behind_still_counts_but_a_long_gap_does_not():
    behind = {"a": frame("2026-01-01", "2026-01-10", 10.0, 100.0), "b": frame("2026-01-01", "2026-01-07", 5.0, 50.0)}       # b is 3 days behind
    s = sw.group_series(behind, ["a", "b"])
    assert sw.latest_complete(s) == pd.Timestamp("2026-01-10") and s.loc["2026-01-10", "storage_af"] == 15.0
    stale = {"a": frame("2026-01-01", "2026-01-30", 10.0, 100.0), "b": frame("2026-01-01", "2026-01-10", 5.0, 50.0)}        # b is 20 days behind
    s2 = sw.group_series(stale, ["a", "b"])
    assert sw.latest_complete(s2) == pd.Timestamp("2026-01-17")        # carried forward at most FFILL_DAYS (7) after b's last reading
    assert sw.stale_members(stale, ["a", "b"], pd.Timestamp("2026-01-30")) == [{"key": "b", "last_date": "2026-01-10"}]


def test_membership_that_starts_later_is_not_complete_before_then():
    f = {"old": frame("1990-01-01", "1991-12-31", 10.0, 100.0), "new": frame("1991-01-01", "1991-12-31", 5.0, 50.0)}
    s = sw.group_series(f, ["old", "new"])
    assert not s.loc["1990-06-01", "complete"] and s.loc["1990-06-01", "members"] == 1
    assert s.loc["1991-06-01", "complete"] and s.loc["1991-06-01", "storage_af"] == 15.0


def test_window_change_in_acre_feet_and_percentage_points():
    f = {"a": frame("2026-01-01", "2026-03-31", lambda d: 100.0 + (d - pd.Timestamp("2026-01-01")).days, 1000.0)}
    s = sw.group_series(f, ["a"])
    c = sw.window_change(s, pd.Timestamp("2026-03-31"), 30, True)
    assert c["change_af"] == 30.0 and c["change_points"] == pytest.approx(3.0) and c["from"] == "2026-03-01"
    assert sw.window_change(s, pd.Timestamp("2026-03-31"), 365, True) is None        # no complete day that long ago


def test_normal_is_of_acre_feet_held_and_needs_enough_years():
    years = range(1991, 2027)
    f = {"a": frame("1991-01-01", "2026-09-30", lambda d: 500.0 + (d.year % 6) * 10 + (800.0 if d.year == 2026 else 0.0), 2000.0, conf="low")}   # capacity doubtful: irrelevant
    s = sw.group_series(f, ["a"])
    normal, pctile, klass, floored, median = sw.storage_normal(s)
    assert normal is not None and len(normal["years"]) == 30 and median == pytest.approx(525.0, abs=15)
    assert pctile == 100.0 and klass == "much above normal" and not floored                  # 2026 holds far more than any past year
    young = {"a": frame("2010-01-01", "2020-12-31", 5.0, 10.0)}
    assert sw.storage_normal(sw.group_series(young, ["a"]))[0] is None
    near = {"a": frame("1991-01-01", "2026-09-30", lambda d: 500.0 + (d.year % 6) * 10 + (60.0 if d.year == 2026 else 0.0), 2000.0)}
    _, _, k2, fl2, _ = sw.storage_normal(sw.group_series(near, ["a"]), floor_af=100.0)          # 60 af above typical is under the floor
    assert k2 == "normal" and fl2


# ---------------------------------------------------------------------------- the bundle section
from tests.test_site_bundle import edit, make_bundle                      # noqa: E402


def make_statewide(root, man):
    base = root / "statewide"
    base.mkdir(parents=True)
    ch = {"days": 30, "from": "2026-08-29", "storage_af": 190.0, "storage_then_af": 200.0, "change_af": -10.0, "percent": 17.3, "percent_then": 18.2, "change_points": -0.9}

    def grp(keys, storage, cap):
        return {"members": len(keys), "keys": keys, "as_of": "2026-09-28", "storage_af": storage, "capacity_af": cap, "percent": None if cap is None else 100 * storage / cap,
                "stale": [], "changes": [ch], "shares": [{"key": k, "slug": k + "-r", "name": k, "storage_af": storage / len(keys), "share_percent": 100 / len(keys)} for k in keys]}
    groups = {"supply": grp(["heron", "caballo"], 190.0, 1100.0), "owner_reported": grp(["ute"], 40.0, None), "all": grp(["heron", "caballo", "ute"], 230.0, None)}
    n = 3
    res = {"schema_version": 2, "id": "reservoirs", "generated": "2026-10-01T00:00:00+00:00", "as_of": "2026-09-28", "groups": groups,
           "history": {"supply": {"dates": ["2026-09-20", "2026-09-27"], "storage_af": [1.0, 2.0], "capacity_af": [10.0, 10.0], "members": [2, 2]}},
           "recent_supply": {"dates": ["2026-09-26", "2026-09-27", "2026-09-28"], "storage_af": [1.0] * n, "capacity_af": [2.0] * n, "percent": [50.0] * n},
           "ffill_days": 7, "stale_days": 14, "definitions": {"supply": "s"}, "exclusions": ["e"]}
    (base / "reservoirs.json").write_text(json.dumps(res))
    entry = {"id": "reservoirs", "title": "Reservoir storage", "file": "reservoirs.json", "as_of": "2026-09-28", "unit": "acre-feet",
             "headline": {"storage_af": 230.0, "supply_storage_af": 190.0}, "sources": ["usgs"]}
    (base / "index.json").write_text(json.dumps({"schema_version": 2, "generated": "2026-10-01T00:00:00+00:00", "as_of": "2026-09-28", "metrics": [entry], "files": {"index": "index.json", "csv": []}}))
    man["statewide"] = {"path": "statewide/", "metrics": ["reservoirs"], "as_of": "2026-09-28", "files": {"index": "index.json", "csv": []}}
    (root / "manifest.json").write_text(json.dumps(man))
    return base


def test_statewide_section_is_validated_and_catches_inconsistencies(tmp_path):
    make_bundle(tmp_path)
    man = json.loads((tmp_path / "manifest.json").read_text())
    base = make_statewide(tmp_path, man)
    assert [p for p in sch.validate_bundle(tmp_path) if "statewide" in p] == []
    bad = lambda frag: any(frag in p for p in sch.validate_bundle(tmp_path))
    edit(base, "reservoirs.json", lambda o: o["groups"]["supply"].update(percent=50.0))
    assert bad("percent is not storage / capacity")
    edit(base, "reservoirs.json", lambda o: o["groups"]["supply"].update(percent=100 * 190.0 / 1100.0))
    edit(base, "reservoirs.json", lambda o: o["groups"]["owner_reported"].update(percent=10.0, capacity_af=400.0))
    assert bad("acre-feet only")
    edit(base, "reservoirs.json", lambda o: o["groups"]["owner_reported"].update(percent=None, capacity_af=None))
    edit(base, "reservoirs.json", lambda o: o["groups"]["all"]["keys"].append("extra"))
    assert bad("do not partition")
    edit(base, "reservoirs.json", lambda o: o["groups"]["all"].update(keys=["heron", "caballo", "ute"]))
    edit(base, "reservoirs.json", lambda o: o["recent_supply"].update(percent=[1.0]))
    assert bad("recent_supply arrays differ")


def drought_con(weeks=60, start="2024-01-02"):
    """An in-memory catalog with a statewide USDM series: cumulative categories that rise, so the latest week is the worst."""
    import duckdb
    con = duckdb.connect()
    con.execute("CREATE TABLE observations (site_uid VARCHAR, variable VARCHAR, datetime_utc TIMESTAMPTZ, value DOUBLE)")
    dates = pd.date_range(start, periods=weeks, freq="7D", tz="UTC")
    for i, d in enumerate(dates):
        base = i / weeks
        cats = [90 * base + 10, 70 * base + 5, 50 * base, 30 * base, 10 * base]
        for v, x in zip(sw.DROUGHT_VARS.values(), cats + [sum(cats)]):
            con.execute("INSERT INTO observations VALUES ('usdm:state:35', ?, ?, ?)", [v, d.to_pydatetime(), float(x)])
    con.execute("INSERT INTO observations VALUES ('usdm:huc8:1', 'dsci', ?, 999)", [dates[0].to_pydatetime()])   # another site: must not be mixed in
    return con


def test_drought_weekly_keeps_every_category_and_only_the_state():
    w = sw.drought_weekly(drought_con())
    assert list(w.columns) == ["d0", "d1", "d2", "d3", "d4", "dsci"] and len(w) == 60
    assert (w["d0"] >= w["d1"]).all() and (w["d1"] >= w["d2"]).all() and (w["d3"] >= w["d4"]).all()     # D0-D3 are not lost to D4
    assert w["dsci"].max() < 500 and (w["d4"] < w["d0"]).all()


def test_drought_summary_changes_and_percentile_of_the_time_of_year():
    w = sw.drought_weekly(drought_con(weeks=120))
    s = sw.drought_summary(w)
    assert s["as_of"] == w.index[-1].date().isoformat() and s["worst_category"] == "D4"
    assert [c["weeks"] for c in s["changes"]] == [1, 4, 13, 26, 52]
    c4 = next(c for c in s["changes"] if c["weeks"] == 4)
    assert c4["change_dsci"] == pytest.approx(w["dsci"].iloc[-1] - w["dsci"].iloc[-5])
    assert c4["change_d0"] == pytest.approx(w["d0"].iloc[-1] - w["d0"].iloc[-5])
    assert s["percentile"] == pytest.approx(100.0)                      # the latest is worse than every earlier same-time-of-year week
    assert s["sample_years"] >= 1 and s["baseline"] == "2000-2025"
    assert sw.drought_summary(pd.DataFrame(columns=list(sw.DROUGHT_VARS))) is None


def test_drought_section_is_exported_validated_and_catches_inconsistencies(tmp_path):
    from nmwater.site import statewide as swx
    make_bundle(tmp_path)
    man = json.loads((tmp_path / "manifest.json").read_text())
    make_statewide(tmp_path, man)
    content, entry = swx.drought_file(drought_con(weeks=120), "2026-10-01T00:00:00+00:00", tmp_path / "statewide")
    base = tmp_path / "statewide"
    (base / "drought.json").write_text(json.dumps(content))
    idx = json.loads((base / "index.json").read_text())
    idx["metrics"].append(entry)
    (base / "index.json").write_text(json.dumps(idx))
    man["statewide"]["metrics"].append("drought")
    man["statewide"]["files"]["csv"].append("drought_weekly.csv")
    (tmp_path / "manifest.json").write_text(json.dumps(man))
    assert (base / "drought_weekly.csv").exists()
    assert [p for p in sch.validate_bundle(tmp_path) if "statewide" in p] == []
    bad = lambda frag: any(frag in p for p in sch.validate_bundle(tmp_path))
    edit(base, "drought.json", lambda o: o["history"]["d1"].__setitem__(-1, o["history"]["d0"][-1] + 1))
    assert bad("not cumulative")
    edit(base, "drought.json", lambda o: o["history"]["d1"].__setitem__(-1, content["history"]["d1"][-1]))
    edit(base, "drought.json", lambda o: o["history"]["dsci"].__setitem__(-1, 7.0))
    assert bad("not the sum of the categories")
    edit(base, "drought.json", lambda o: o["history"]["dsci"].__setitem__(-1, content["history"]["dsci"][-1]))
    edit(base, "drought.json", lambda o: o["history"]["d0"].pop())
    assert bad("arrays differ")
