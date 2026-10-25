import pandas as pd

from nmwater.derived import flooding as fl

TH = {"stage_units": "ft", "flow_units": "cfs", "action_stage": 7.0, "action_flow": 8535.0, "minor_stage": 8.5, "minor_flow": 14400.0,
      "moderate_stage": 10.0, "moderate_flow": 21500.0, "major_stage": 12.0, "major_flow": 35000.0}


def readings(stages, flows=None):
    t = pd.date_range("2026-10-22 12:00", periods=len(stages), freq="15min", tz="UTC")
    return pd.DataFrame({"time": t, "stage": stages, "flow": flows if flows is not None else [None] * len(stages)})


def test_the_highest_category_held_for_two_readings_is_reached():
    r = fl.assess(readings([6.0, 8.0, 10.2, 12.5, 14.07, 13.0, 6.0]), TH)
    assert r["reached"] == "major" and r["peak_stage"] == 14.07 and r["current"] is None


def test_a_single_spike_is_not_a_flood():
    r = fl.assess(readings([6.0, 6.1, 15.0, 6.0, 6.1]), TH)
    assert r["reached"] is None


def test_current_is_the_category_still_held_at_the_last_readings():
    r = fl.assess(readings([6.0, 9.0, 9.2, 9.3]), TH)
    assert r["reached"] == "minor" and r["current"] == "minor"


def test_action_stage_is_a_category_but_not_flooding():
    r = fl.assess(readings([7.5, 7.6, 7.4]), TH)
    assert r["reached"] == "action" and "action" not in fl.FLOODING


def test_a_gauge_with_only_flow_levels_is_judged_on_flow_in_cfs():
    th = {"flow_units": "kcfs", "minor_flow": 5.0, "major_flow": 20.0}
    r = fl.assess(readings([None] * 3, [4000.0, 6000.0, 6500.0]), th)
    assert r["reached"] == "minor" and r["peak_flow"] == 6500.0


def test_no_readings_or_no_levels_gives_nothing():
    assert fl.assess(readings([]), TH) is None
    assert fl.assess(readings([9.0, 9.0]), {"stage_units": "ft"}) is None


def _frames(stages_by_gauge):
    th, obs = [], []
    for uid, stages in stages_by_gauge.items():
        th.append({"site_uid": uid, "lid": uid.split(":")[1], **TH})
        r = readings(stages)
        r["site_uid"] = uid
        obs.append(r)
    return pd.DataFrame(th), pd.concat(obs, ignore_index=True)


def test_summary_lists_only_flooding_worst_first_and_counts_every_gauge_checked():
    th, obs = _frames({"nwps:A": [6.0, 6.1, 6.0, 6.0], "nwps:B": [6.0, 9.0, 9.1, 9.2], "nwps:C": [6.0, 13.0, 13.2, 6.0], "nwps:D": [7.6, 7.7, 7.6, 7.6]})
    names = {"nwps:B": {"name": "B river", "state": "NM", "river": "B", "usgs": "1"}}
    now = obs["time"].max() + pd.Timedelta(minutes=30)
    s = fl.summarise(th, obs, names, now)
    assert s["checked"] == 4
    assert [g["key"] for g in s["gauges"]] == ["nwps:C", "nwps:B"]
    c, b = s["gauges"]
    assert (c["category"], c["current"]) == ("major", None)
    assert (b["category"], b["current"], b["river"], b["state"], b["last_stage_ft"]) == ("minor", "minor", "B", "NM", 9.2)


def test_a_gauge_that_stopped_reporting_is_not_said_to_be_in_flood_now():
    th, obs = _frames({"nwps:B": [6.0, 9.0, 9.1, 9.2]})
    s = fl.summarise(th, obs, {}, obs["time"].max() + pd.Timedelta(hours=30))
    assert s["gauges"][0]["category"] == "minor" and s["gauges"][0]["current"] is None


def test_a_river_that_fell_back_to_action_stage_is_no_longer_in_flood():
    th, obs = _frames({"nwps:B": [6.0, 9.0, 9.1, 7.4, 7.5]})
    s = fl.summarise(th, obs, {}, obs["time"].max())
    assert s["gauges"][0]["category"] == "minor" and s["gauges"][0]["current"] is None


def test_only_the_last_three_days_of_readings_are_read():
    import duckdb

    assert fl.WINDOW_DAYS == 3
    con = duckdb.connect()
    con.execute("CREATE TABLE flood_thresholds AS SELECT 'nwps:A' AS site_uid, 'A' AS lid")
    con.execute("CREATE TABLE observations_clean (site_uid VARCHAR, datetime_utc TIMESTAMPTZ, variable VARCHAR, value DOUBLE)")
    now = pd.Timestamp("2026-10-25 12:00", tz="UTC")
    for hours in (80, 71, 70, 2):               # 80 h ago is outside the window, 71 h ago is inside
        con.execute("INSERT INTO observations_clean VALUES ('nwps:A', ?, 'stage', 9.0)", [(now - pd.Timedelta(hours=hours)).to_pydatetime()])
    th, obs = fl.read(con, now)
    assert len(th) == 1 and len(obs) == 3
