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
    con.execute("CREATE TABLE observations (site_uid VARCHAR, variable VARCHAR, datetime_utc TIMESTAMPTZ, value DOUBLE, interval VARCHAR, statistic VARCHAR, qualifier VARCHAR)")
    dates = pd.date_range(start, periods=weeks, freq="7D", tz="UTC")
    for i, d in enumerate(dates):
        base = i / weeks
        cats = [90 * base + 10, 70 * base + 5, 50 * base, 30 * base, 10 * base]
        for v, x in zip(sw.DROUGHT_VARS.values(), cats + [sum(cats)]):
            con.execute("INSERT INTO observations VALUES ('usdm:state:35', ?, ?, ?, 'weekly', 'index', NULL)", [v, d.to_pydatetime(), float(x)])
    con.execute("INSERT INTO observations VALUES ('usdm:huc8:1', 'dsci', ?, 999, 'weekly', 'index', NULL)", [dates[0].to_pydatetime()])   # another site: must not be mixed in
    con.execute("CREATE VIEW observations_clean AS SELECT * FROM observations")
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


def add_flow(con, start="1990-01-01"):
    """Daily discharge for every gauge's first uid, in the fake catalog."""
    rows = []
    for g in swf.GAUGES:
        s = flow_series(start=start)[g["key"]]
        rows.append(pd.DataFrame({"site_uid": g["uids"][0], "variable": "discharge", "datetime_utc": pd.to_datetime(s.index, utc=True), "value": s.to_numpy(), "interval": "daily", "statistic": "mean", "qualifier": None}))
    df = pd.concat(rows, ignore_index=True)
    con.register("flow_rows", df)
    con.execute("INSERT INTO observations SELECT site_uid, variable, datetime_utc, value, interval, statistic, qualifier FROM flow_rows")


def test_export_statewide_writes_reservoirs_and_drought_together_and_the_bundle_validates(tmp_path):
    """The whole exporter on synthetic inputs (a full export takes 25 minutes, so a failure at its last step must be caught here)."""
    from nmwater.site import statewide as swx
    frames = {}
    for k, cap in (("heron", 400.0), ("caballo", 300.0)):
        f = frame("2026-01-01", "2026-09-28", 100.0, cap)
        f["measure"], f["slug"] = "percent_full", k + "-r"
        frames[k] = f
    reg = {k: {"name": k.title()} for k in frames}
    con = drought_con(weeks=120)
    add_flow(con)
    block = swx.export_statewide(tmp_path, frames, reg, "2026-10-01T00:00:00+00:00", True, con)
    assert block and block["metrics"] == ["reservoirs", "drought", "flow"] and block["as_of"] >= "2026-09-28"
    assert (tmp_path / "statewide" / "flow.json").exists() and (tmp_path / "statewide" / "flow_water_years.csv").exists()
    idx = json.loads((tmp_path / "statewide" / "index.json").read_text())
    assert all(isinstance(m["as_of"], str) for m in idx["metrics"])
    assert (tmp_path / "statewide" / "drought_weekly.csv").exists() and (tmp_path / "statewide" / "drought.json").exists()


# Temperature -------------------------------------------------------------------------------------------------------------------------------------------
from nmwater.derived import statewide_temperature as stt


def temp_daily(start="1981-01-01", end="2026-09-26", warm_last=2.0):
    """A seasonal cycle with a small year-to-year wobble, and a warm final 30 days."""
    d = pd.date_range(start, end, freq="D")
    base = 12.5 + 11 * np.sin(2 * np.pi * (d.dayofyear - 110) / 365.25) + 0.3 * np.sin(d.year * 1.7)
    s = pd.Series(base, index=d)
    s.iloc[-30:] += warm_last
    return s


def test_weighted_mean_ignores_missing_cells_and_reports_coverage():
    a = np.array([[[10.0, 20.0], [np.nan, 40.0]]])
    w = np.array([1.0, 1.0, 1.0, 2.0])
    m, cover = stt.weighted_mean(a, w)
    assert m[0] == pytest.approx((10 + 20 + 80) / 4) and cover[0] == pytest.approx(4 / 5)


def test_window_anomaly_is_mean_minus_baseline_mean_and_needs_enough_years():
    s = temp_daily()
    w = stt.window_stats(s, 30)
    assert w["days"] == 30 and w["window_end"] == "2026-09-26" and w["years"] == 30
    assert w["anomaly_c"] == pytest.approx(w["mean_c"] - w["normal_c"]) and w["anomaly_c"] == pytest.approx(2.0, abs=0.5)
    assert w["percentile"] == 100.0 and w["class"] == "much above normal"
    short = stt.window_stats(temp_daily(start="2010-01-01"), 30)
    assert short["normal_c"] is None and short["class"] is None and short["years"] < stt.MIN_YEARS      # no normal from too few years
    assert stt.window_stats(s.iloc[:5], 30) is None


def test_monthly_and_annual_anomalies_and_incomplete_years_are_left_out():
    idx = pd.date_range("1990-01-01", "2026-08-01", freq="MS")
    m = pd.Series(10 + 10 * np.sin(2 * np.pi * (idx.month - 4) / 12), index=idx)
    m[m.index.year == 2026] += 1.5
    ma = stt.monthly_anomaly(m)
    assert ma["anomaly_c"][ma.index.year == 2026].mean() == pytest.approx(1.5, abs=0.05)
    a = stt.annual_anomaly(ma)
    assert 2026 not in a.index and 2025 in a.index                    # 2026 has only eight months


def test_recent_daily_has_a_smooth_normal_for_every_day():
    r = stt.recent_daily(temp_daily(), 365)
    assert len(r) == 365 and r["normal_c"].notna().all()
    assert (r["tmean_c"].iloc[-30:] - r["normal_c"].iloc[-30:]).mean() > 1.0


def test_temperature_section_is_exported_validated_and_catches_inconsistencies(tmp_path, monkeypatch):
    from nmwater.site import statewide as swx
    daily = temp_daily()
    idx = pd.date_range("1895-01-01", "2026-08-01", freq="MS")
    monthly = pd.Series(10 + 10 * np.sin(2 * np.pi * (idx.month - 4) / 12), index=idx)
    monkeypatch.setattr(stt, "nm_polygon", lambda g: None)
    monkeypatch.setattr(stt, "series", lambda g, p=None: (daily, monthly, 1.0))
    monkeypatch.setattr(stt, "crosscheck", lambda g, p, m: {"source": "x", "months": 200, "bias_c": 0.0, "mean_abs_diff_c": 0.1, "max_abs_diff_c": 0.5, "correlation": 0.999})
    grids = tmp_path / "grids"
    (grids / "prism").mkdir(parents=True)
    (grids / "prism" / "tmean_daily_2026.nc").write_bytes(b"")
    root = tmp_path / "bundle"
    root.mkdir()
    make_bundle(root)
    man = json.loads((root / "manifest.json").read_text())
    make_statewide(root, man)
    base = root / "statewide"
    content, entry = swx.temperature_file(grids, "2026-10-01T00:00:00+00:00", base)
    assert [w["days"] for w in content["latest"]["windows"]] == [7, 30, 90, 180, 365] and (base / "temperature_monthly.csv").exists()
    (base / "temperature.json").write_text(json.dumps(content))
    idxj = json.loads((base / "index.json").read_text())
    idxj["metrics"].append(entry)
    (base / "index.json").write_text(json.dumps(idxj))
    man["statewide"]["metrics"].append("temperature")
    man["statewide"]["files"]["csv"].append("temperature_monthly.csv")
    (root / "manifest.json").write_text(json.dumps(man))
    assert [p for p in sch.validate_bundle(root) if "statewide" in p] == []
    bad = lambda frag: any(frag in p for p in sch.validate_bundle(root))
    edit(base, "temperature.json", lambda o: o["latest"]["windows"][1].update(anomaly_c=9.0))
    assert bad("not the mean minus the normal")
    edit(base, "temperature.json", lambda o: o["latest"]["windows"][1].update(anomaly_c=content["latest"]["windows"][1]["anomaly_c"], **{"class": "much below normal"}))
    assert bad("does not follow its percentile")
    edit(base, "temperature.json", lambda o: o["latest"]["windows"][1].update(**{"class": content["latest"]["windows"][1]["class"]}))
    edit(base, "temperature.json", lambda o: o["history"]["monthly"]["anomaly_c"].pop())
    assert bad("arrays differ")


GRIDS = __import__("pathlib").Path("data/grids")


@pytest.mark.skipif(not (GRIDS / "prism" / "tmean_monthly_2020.nc").exists() or not (GRIDS / "tiger" / "county.gpkg").exists(), reason="needs the PRISM and TIGER grids in data/grids")
def test_state_polygon_area_and_prism_against_nclimgrid_on_the_archive():
    import geopandas as gpd
    poly = stt.nm_polygon(GRIDS)
    sq_mi = gpd.GeoSeries([poly], crs=4326).to_crs(5070).area.iloc[0] / 2.58999e6
    assert sq_mi == pytest.approx(121_590, rel=0.005)                    # the state's land and water area, 121,590 square miles
    monthly = stt.series(GRIDS, poly)[1]
    cc = stt.crosscheck(GRIDS, poly, monthly)
    assert cc and cc["months"] > 300 and cc["correlation"] > 0.999 and cc["mean_abs_diff_c"] < 0.3 and abs(cc["bias_c"]) < 0.2


# Snowpack ----------------------------------------------------------------------------------------------------------------------------------------------
from nmwater.derived import statewide_snow as sns


def snow_df(end="2026-09-26", last_amp=0.15):
    """Daily statewide SWE for 2003-09-30 on: a winter hump peaking in late February whose height varies by year (the last water year is poor)."""
    d = pd.date_range("2003-09-30", end, freq="D")
    wy = np.where(d.month >= 10, d.year + 1, d.year)
    wyd = (d - pd.to_datetime([f"{y - 1}-10-01" for y in wy])).days.to_numpy()
    amp = np.where(wy == 2026, last_amp, 0.3 + 0.1 * (wy % 3))
    swe = amp * np.clip(np.sin(np.pi * (wyd - 45) / 200.0), 0, None)
    return pd.DataFrame({"date": d, "swe_in": swe, "volume_af": swe * 6.5e6 / 12.0 * 12.0 / 12.0, "cover_pct": np.clip(swe * 100, 0, 100), "valid_pct": 99.9})


def test_cell_area_is_the_true_area_of_a_lat_lon_cell():
    a = sns.cell_area_m2(np.array([35.0]), 1.0, 1.0)[0]
    assert a == pytest.approx(111_195 * 91_100, rel=0.01)               # a degree is 111.2 km north-south and 111.3 x cos(35) = 91.2 km east-west


def test_day_stats_weights_by_area_and_refuses_a_grid_that_is_mostly_empty():
    swe = np.array([[0.1, 0.0], [0.3, np.nan]])
    mask = np.array([[True, True], [True, True]])
    area = np.array([[1.0, 1.0], [2.0, 0.1]]) * 1e6
    mean_in, vol_af, cover, valid = sns.day_stats(swe, mask, area)
    assert mean_in == pytest.approx((0.1 * 1 + 0.3 * 2) / 4.0 * sns.M_TO_IN) and vol_af == pytest.approx(0.7e6 / sns.AF_M3)
    assert cover == pytest.approx(100 * 3 / 4.0) and valid == pytest.approx(100 * 4.0 / 4.1)      # 0.1 m and 0.3 m are over the 25 mm threshold, 0 is not; the empty cell is left out
    assert sns.day_stats(np.full((2, 2), np.nan), mask, area) is None
    assert sns.day_stats(swe, np.array([[True, True], [True, True]]), np.array([[1.0, 1.0], [2.0, 5.0]]) * 1e6) is None      # the empty cell is most of the area


def test_band_leaves_out_the_season_shown_and_a_summer_median_is_not_rated():
    df = snow_df()
    b = sns.band(df, exclude_wy=2026)
    assert b.loc[225, "p50"] > 0.1 and b.loc[815, "p50"] == 0.0           # late February has snow, mid-August none
    assert sns.percent_of_median(0.2, b.loc[815, "p50"]) is None and sns.percent_of_median(0.2, 0.1) == pytest.approx(200.0)
    assert (b["years"] > 0).all()


def test_in_summer_the_state_is_out_of_season_and_the_last_season_is_reported():
    s = sns.summarise(snow_df())
    assert s["now"]["in_season"] is False and s["now"]["percent_of_median"] is None
    ls = s["last_season"]
    assert ls["water_year"] == 2026 and ls["complete"] is True and ls["of"] == 23
    assert ls["percent_of_median_peak"] < 60 and ls["rank"] == 23           # the poorest of the 23 seasons
    assert [c["days"] for c in s["compare"]] == [7, 30, 90, 180, 365]
    assert s["history"]["season"]["water_year"] == 2026 and max(v for v in s["history"]["season"]["swe_in"] if v is not None) == pytest.approx(ls["peak_swe_in"], abs=1e-3)


def test_in_winter_the_percent_of_median_is_stated_and_the_season_is_not_complete():
    s = sns.summarise(snow_df(end="2026-02-20"))
    assert s["now"]["in_season"] is True and s["now"]["percent_of_median"] == pytest.approx(100 * s["now"]["swe_in"] / s["now"]["median_in"])
    assert s["last_season"]["complete"] is False and s["last_season"]["water_year"] == 2026
    assert all(v is None for v in s["history"]["season"]["swe_in"][s["history"]["season"]["dates"].index("2026-02-21"):])      # nothing after the as-of date


def test_just_after_october_first_the_season_just_ended_is_still_the_one_shown():
    s = sns.summarise(snow_df(end="2026-10-03"))
    assert s["now"]["in_season"] is False and s["last_season"]["water_year"] == 2026 and s["last_season"]["complete"] is True


def test_snowpack_section_is_exported_validated_and_catches_inconsistencies(tmp_path, monkeypatch):
    from nmwater.site import statewide as swx
    monkeypatch.setattr(stt, "nm_polygon", lambda g: None)
    monkeypatch.setattr(sns, "daily", lambda g, p, c, rebuild=False: snow_df())
    monkeypatch.setattr(sns, "snotel_percent_of_median", lambda con, ix: pd.Series(np.nan, index=ix))
    grids = tmp_path / "grids"
    (grids / "snodas").mkdir(parents=True)
    (grids / "snodas" / "snodas_20260926.nc").write_bytes(b"")
    root = tmp_path / "bundle"
    root.mkdir()
    make_bundle(root)
    man = json.loads((root / "manifest.json").read_text())
    make_statewide(root, man)
    base = root / "statewide"
    content, entry = swx.snowpack_file(grids, object(), tmp_path / "c.parquet", "2026-10-01T00:00:00+00:00", base)
    assert (base / "snowpack_daily.csv").exists()
    (base / "snowpack.json").write_text(json.dumps(content))
    idx = json.loads((base / "index.json").read_text())
    idx["metrics"].append(entry)
    (base / "index.json").write_text(json.dumps(idx))
    man["statewide"]["metrics"].append("snowpack")
    man["statewide"]["files"]["csv"].append("snowpack_daily.csv")
    (root / "manifest.json").write_text(json.dumps(man))
    assert [p for p in sch.validate_bundle(root) if "statewide" in p] == []
    bad = lambda frag: any(frag in p for p in sch.validate_bundle(root))
    edit(base, "snowpack.json", lambda o: o["latest"]["now"].update(percent_of_median=50.0))
    assert bad("in season only when")
    edit(base, "snowpack.json", lambda o: o["latest"]["now"].update(percent_of_median=None))
    edit(base, "snowpack.json", lambda o: o["latest"]["last_season"].update(rank=99))
    assert bad("rank is outside")
    edit(base, "snowpack.json", lambda o: o["latest"]["last_season"].update(rank=content["latest"]["last_season"]["rank"]))
    edit(base, "snowpack.json", lambda o: o["history"]["season"]["p50"].pop())
    assert bad("season arrays differ")


@pytest.mark.skipif(not (GRIDS / "snodas" / "snodas_20100205.nc").exists() or not (GRIDS / "tiger" / "county.gpkg").exists(), reason="needs the SNODAS and TIGER grids in data/grids")
def test_snodas_day_volume_matches_mean_depth_times_state_area_on_the_archive():
    import xarray as xr
    poly = stt.nm_polygon(GRIDS)
    with xr.open_dataset(GRIDS / "snodas" / "snodas_20100205.nc") as ds:
        lat, lon = ds["lat"].values, ds["lon"].values
        a = ds["swe"].isel(time=0).load().values.astype(float) if "time" in ds["swe"].dims else ds["swe"].load().values.astype(float)
    mask = sns.state_mask(poly, lat, lon)
    area = sns.cell_area_m2(lat, abs(float(lat[1] - lat[0])), abs(float(lon[1] - lon[0])))[:, None] * np.ones((1, len(lon)))
    mean_in, vol_af, cover, valid = sns.day_stats(a, mask, area)
    state_acres = float(area[mask].sum()) / 4046.8564224
    assert state_acres == pytest.approx(77.8e6, rel=0.01)                  # 121,590 square miles
    assert vol_af == pytest.approx(mean_in / 12 * state_acres * valid / 100, rel=0.01)
    assert 1.2 < mean_in < 1.8 and 40 < cover < 60                         # a big snow year (the largest of 2004-2026)


# State-line flow ---------------------------------------------------------------------------------------------------------------------------------------
from nmwater.derived import statewide_flow as swf


def flow_series(last="2026-09-23", stale_gila=False, start="1975-01-01"):
    """Daily cfs for the six gauges: a seasonal flow with a per-gauge size and a year-to-year wobble; the Gila can end months early (a stale gauge)."""
    out = {}
    for i, g in enumerate(swf.GAUGES):
        end = "2026-05-01" if (stale_gila and g["key"] == "gila") else last
        d = pd.date_range("1977-10-01" if g["key"] == "san_juan" else start, end, freq="D")
        out[g["key"]] = pd.Series(50.0 * (i + 1) * (1.2 + np.sin(2 * np.pi * (d.dayofyear - 120) / 365.25)) + 3.0 * np.sin(d.year * 1.3), index=d).clip(lower=0.5)
    return out


def test_every_exit_is_one_gauge_on_one_river_and_no_gauge_is_listed_twice():
    exits = [g for g in swf.GAUGES if g["role"] == "exit"]
    assert len(exits) == 5 and [g for g in swf.GAUGES if g["role"] == "entrance"][0]["key"] == "lobatos"
    rivers = [g["river"] for g in exits]
    assert len(set(rivers)) == 5
    uids = [u for g in swf.GAUGES for u in g["uids"]]
    assert len(uids) == len(set(uids))                                    # no station counted for two gauges
    assert "usgs:08361000" not in uids                                    # Elephant Butte's release flows into Caballo: using both would count the Rio Grande twice


def test_window_volume_is_mean_flow_times_days_times_the_cfs_day_factor():
    ser = flow_series()
    used, stale, end = swf.select(ser)
    assert used == ["rio_grande", "pecos", "canadian", "san_juan", "gila"] and stale == [] and end == pd.Timestamp("2026-09-23")
    w = swf.window_stats(ser, used, end, 30)
    for g in w["by_gauge"]:
        assert g["volume_af"] == pytest.approx(ser[g["key"]]["2026-08-25":"2026-09-23"].mean() * 30 * 1.983471)
    assert w["complete"] and w["total"]["volume_af"] == pytest.approx(sum(g["volume_af"] for g in w["by_gauge"]))
    assert w["start"] == "2026-08-25" and w["end"] == "2026-09-23" and w["total"]["years"] == 30


def test_the_sum_is_rated_against_years_in_which_every_exit_has_a_value():
    ser = flow_series()
    ser["gila"] = ser["gila"]["2000-01-01":]                              # the Gila record starts in 2000: baseline years before it cannot enter the sum
    used, _, end = swf.select(ser)
    w = swf.window_stats(ser, used, end, 90)
    gila = next(g for g in w["by_gauge"] if g["key"] == "gila")
    assert gila["years"] == 21 and w["total"]["years"] == 21              # 2000-2020 for the Gila and so for the sum
    assert next(g for g in w["by_gauge"] if g["key"] == "pecos")["years"] == 30


def test_a_gauge_that_is_much_staler_than_the_rest_is_named_and_left_out_of_the_sum():
    ser = flow_series(stale_gila=True)
    used, stale, end = swf.select(ser)
    assert "gila" not in used and [x["key"] for x in stale] == ["gila"] and stale[0]["last_date"] == "2026-05-01"
    assert end == pd.Timestamp("2026-09-23")                              # END is not dragged back to May by the stale gauge


def test_a_window_with_too_many_missing_days_is_not_stated_and_the_total_follows():
    ser = flow_series()
    ser["pecos"] = ser["pecos"].drop(ser["pecos"]["2026-09-01":"2026-09-20"].index)                 # 20 of the last 30 days missing
    used, _, end = swf.select(ser)
    w = swf.window_stats(ser, used, end, 30)
    assert "pecos" not in [g["key"] for g in w["by_gauge"]] and w["complete"] is False and w["total"] is None
    assert swf.window_stats(ser, used, end, 365)["total"] is not None      # a longer window still has 75% coverage


def test_water_years_total_only_when_every_gauge_has_the_year():
    ser = flow_series()
    ser["canadian"] = ser["canadian"].drop(ser["canadian"]["2010-03-01":"2010-07-01"].index)         # a gap in water year 2010
    used, _, end = swf.select(ser)
    h = swf.water_years(ser, used, 2025)
    i = h["years"].index(2010)
    assert h["by_gauge"]["canadian"][i] is None and h["total"][i] is None           # 245 of 365 days present is below the 90% a water year needs: no figure, and so no total
    assert h["years"][0] == 1978 and h["years"][-1] == 2025 and len(h["total"]) == 48
    j = h["years"].index(2011)
    assert h["total"][j] == pytest.approx(sum(h["by_gauge"][k][j] for k in used))


def test_summary_has_water_year_to_date_and_an_entrance_rated_on_its_own_end_date():
    ser = flow_series()
    ser["lobatos"] = ser["lobatos"][:"2026-09-20"]
    s = swf.summarise(ser)
    assert s["as_of"] == "2026-09-23" and s["entrance_as_of"] == "2026-09-20" and [w["days"] for w in s["entrance"]] == [7, 30, 90, 180, 365]
    assert s["water_year_to_date"]["days"] == (pd.Timestamp("2026-09-23") - pd.Timestamp("2025-10-01")).days + 1
    assert all(w["by_gauge"][0]["key"] == "lobatos" for w in s["entrance"])
    assert "lobatos" not in s["members"] and s["history"]["entrance"] is not None


def test_flow_section_is_exported_validated_and_catches_inconsistencies(tmp_path):
    from nmwater.site import statewide as swx
    con = drought_con(weeks=10)
    add_flow(con)
    root = tmp_path / "bundle"
    root.mkdir()
    make_bundle(root)
    man = json.loads((root / "manifest.json").read_text())
    make_statewide(root, man)
    base = root / "statewide"
    content, entry = swx.flow_file(con, "2026-10-01T00:00:00+00:00", base)
    assert (base / "flow_water_years.csv").exists() and content["members"] == ["rio_grande", "pecos", "canadian", "san_juan", "gila"]
    (base / "flow.json").write_text(json.dumps(content))
    idx = json.loads((base / "index.json").read_text())
    idx["metrics"].append(entry)
    (base / "index.json").write_text(json.dumps(idx))
    man["statewide"]["metrics"].append("flow")
    man["statewide"]["files"]["csv"].append("flow_water_years.csv")
    (root / "manifest.json").write_text(json.dumps(man))
    assert [p for p in sch.validate_bundle(root) if "statewide" in p] == []
    bad = lambda frag: any(frag in p for p in sch.validate_bundle(root))
    t0 = content["windows"][1]["total"]["volume_af"]
    edit(base, "flow.json", lambda o: o["windows"][1]["total"].update(volume_af=t0 * 1.5))
    assert bad("total is not the sum of its gauges")
    edit(base, "flow.json", lambda o: o["windows"][1]["total"].update(volume_af=t0))
    edit(base, "flow.json", lambda o: o["windows"][2]["by_gauge"][0].update(volume_af=o["windows"][2]["by_gauge"][0]["volume_af"] * 2))
    assert bad("volume is not mean flow x days")
    edit(base, "flow.json", lambda o: o["windows"][2]["by_gauge"][0].update(volume_af=content["windows"][2]["by_gauge"][0]["volume_af"]))
    edit(base, "flow.json", lambda o: o["windows"][0]["by_gauge"][0].update(**{"class": "much below normal", "percentile": 99.0}))
    assert bad("class does not follow its percentile")
    edit(base, "flow.json", lambda o: o["windows"][0]["by_gauge"][0].update(**{"class": content["windows"][0]["by_gauge"][0]["class"], "percentile": content["windows"][0]["by_gauge"][0]["percentile"]}))
    edit(base, "flow.json", lambda o: o["history"]["total"].__setitem__(-1, 123.0))
    assert bad("is not the sum of its gauges")
    edit(base, "flow.json", lambda o: o["history"]["total"].__setitem__(-1, content["history"]["total"][-1]))
    edit(base, "flow.json", lambda o: o["members"].append("lobatos"))
    assert bad("members are not all exit gauges")
