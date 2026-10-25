import json
from datetime import datetime, timedelta, timezone

import duckdb
import pandas as pd

from nmwater.site import flooding_now as fn

NOW = datetime(2026, 10, 25, 18, 0, tzinfo=timezone.utc)


def gauge(key, cat="major", cur=None):
    return {"key": key, "category": cat, "current": cur}


def status(gauges, generated=NOW - timedelta(hours=1)):
    return {"generated": generated.isoformat(), "gauges": gauges}


def test_the_fingerprint_is_what_the_band_says_without_times_or_readings():
    a = status([gauge("b", "minor", "minor"), gauge("a", "major")])
    a["gauges"][0]["peak_stage_ft"] = 9.0
    b = status([gauge("a", "major"), gauge("b", "minor", "minor")])
    b["gauges"][1]["peak_stage_ft"] = 9.9
    assert fn.fingerprint(a) == fn.fingerprint(b) == [["a", "major", None], ["b", "minor", "minor"]]
    assert fn.fingerprint(None) == [] and fn.fingerprint(status([])) == []


def test_the_page_is_rendered_again_when_a_gauge_reaches_a_category_leaves_flood_or_the_band_clears():
    bundle = status([gauge("a", "major", "major")], NOW - timedelta(hours=2))
    same = status([gauge("a", "major", "major")])
    assert fn.decide(None, bundle, same, NOW) == (False, "nothing the band says has changed")
    assert fn.decide(None, bundle, status([gauge("a", "major", None)]), NOW) == (True, "the band changed")             # no longer in flood now
    assert fn.decide(None, bundle, status([gauge("a", "major", "major"), gauge("b", "minor")]), NOW) == (True, "the band changed")
    assert fn.decide(None, bundle, status([]), NOW) == (True, "the band clears")
    assert fn.decide(None, None, status([gauge("a")]), NOW) == (True, "the band changed")                                 # no bundle file yet: the band would appear
    assert fn.decide(None, None, status([]), NOW) == (False, "nothing the band says has changed")


def test_a_flood_that_is_on_is_rendered_again_when_the_page_is_old_but_a_quiet_week_never_is():
    fresh, old = status([gauge("a")], NOW - timedelta(hours=2)), status([gauge("a")], NOW - timedelta(hours=7))
    assert fn.decide(None, fresh, status([gauge("a")]), NOW)[0] is False
    go, why = fn.decide(None, old, status([gauge("a")]), NOW)
    assert go and "over 6 hours old" in why
    assert fn.decide(None, status([], NOW - timedelta(days=3)), status([]), NOW)[0] is False


def test_what_the_last_run_rendered_counts_until_the_nightly_bundle_is_newer():
    bundle = status([gauge("a")], NOW - timedelta(hours=10))
    rendered = {"fingerprint": [["a", "major", "major"]], "rendered": (NOW - timedelta(hours=3)).isoformat(), "deploys": {}}
    # the last run put "a in flood now" on the page after the bundle was made, so the same status needs nothing
    assert fn.decide(rendered, bundle, status([gauge("a", "major", "major")]), NOW)[0] is False
    # a nightly bundle made after that render is what the page shows now: compare with it
    newer = status([gauge("a", "major", None)], NOW - timedelta(hours=1))
    assert fn.decide(rendered, newer, status([gauge("a", "major", "major")]), NOW)[0] is True


def test_the_page_is_rendered_at_most_a_few_times_a_day_and_the_count_starts_again_each_day():
    s = fn.record(None, status([gauge("a")]), NOW, True)
    for _ in range(fn.MAX_DEPLOYS_PER_DAY - 1):
        s = fn.record(s, status([gauge("a")]), NOW, True)
    assert s["deploys"] == {"2026-10-25": fn.MAX_DEPLOYS_PER_DAY}
    assert fn.decide(s, None, status([gauge("b")]), NOW) == (False, f"already rendered {fn.MAX_DEPLOYS_PER_DAY} times today")
    tomorrow = fn.record(s, status([gauge("a")]), NOW + timedelta(days=1), False)
    assert tomorrow["deploys"] == {} and fn.decide(tomorrow, None, status([gauge("b")]), NOW + timedelta(days=1))[0] is True


def test_a_run_that_did_not_start_a_build_leaves_what_the_page_shows_unchanged():
    s = fn.record(None, status([gauge("a")]), NOW, True)
    s2 = fn.record(s, status([gauge("b")]), NOW + timedelta(hours=1), False)
    assert s2["fingerprint"] == s["fingerprint"] and s2["rendered"] == s["rendered"]


def test_upload_puts_the_file_beside_latest_json_with_a_short_cache():
    calls = []
    fn.upload(__file__ and __import__("pathlib").Path("/tmp/x.json"), run=lambda cmd, **k: calls.append((cmd, k)), env={"R2_ENDPOINT": "https://r2.example", "R2_BUCKET": "bkt"})
    cmd = calls[0][0]
    assert cmd[:2] == ["aws", "s3"] and "s3://bkt/flooding-now.json" in cmd and "max-age=60" in cmd and calls[0][1]["check"] is True


def catalog(stage_rows):
    """An in-memory catalog with one gauge: the tables flooding_file reads."""
    con = duckdb.connect()
    con.execute("CREATE TABLE flood_thresholds AS SELECT 'nwps:A' AS site_uid, 'A' AS lid, 'ft' AS stage_units, 'cfs' AS flow_units, 7.0 AS action_stage, 8.5 AS minor_stage, "
                "10.0 AS moderate_stage, 12.0 AS major_stage, 8535.0 AS action_flow, 14400.0 AS minor_flow, 21500.0 AS moderate_flow, 35000.0 AS major_flow")
    con.execute("CREATE TABLE observations_clean (site_uid VARCHAR, source VARCHAR, datetime_utc TIMESTAMPTZ, variable VARCHAR, value DOUBLE)")
    for t, v in stage_rows:
        con.execute("INSERT INTO observations_clean VALUES ('nwps:A', 'nwps', ?, 'stage', ?)", [t.to_pydatetime(), v])
    con.execute("CREATE TABLE sites AS SELECT 'nwps:A' AS site_uid, 'Test River at A' AS name, 'NM' AS state, '{\"usgsId\": \"01\"}' AS raw_metadata, 36.5 AS lat, -108.1 AS lon")
    con.execute("CREATE TABLE site_reaches AS SELECT 'nwps:A' AS site_uid, 'Test River' AS river_name, 777 AS comid")
    con.execute("CREATE TABLE flowlines AS SELECT 777 AS comid, '00999' AS gnis_id")
    con.execute("CREATE TABLE site_regions AS SELECT 'nwps:A' AS site_uid, '35001' AS region_id, 'county' AS region_type")
    return con


def test_hourly_writes_uploads_and_starts_one_build_for_a_new_flood_then_not_again(tmp_path):
    now = pd.Timestamp("2026-10-25 18:00", tz="UTC")
    rows = [(now - pd.Timedelta(minutes=15 * i), 11.0) for i in range(8)]                       # 11 ft: moderate flood, two readings in a row, reporting now
    con = catalog(rows)
    calls = []
    run = lambda cmd, **k: calls.append(cmd)                                                      # noqa: E731
    env = {"R2_ENDPOINT": "https://r2.example", "R2_BUCKET": "bkt"}
    out, state = tmp_path / "flooding-now.json", tmp_path / "state.json"
    r = fn.hourly(con, tmp_path / "no-bundle.json", out, state, True, True, now.to_pydatetime(), run, env)
    assert r["gauges"] == 1 and r["render"] and r["published"] and r["started"]
    assert [c[0] for c in calls] == ["aws", "gh"] and calls[1][:3] == ["gh", "workflow", "run"]       # the upload first, so the build finds the file
    d = json.loads(out.read_text())
    g0 = d["gauges"][0]
    assert g0["category"] == "moderate" and g0["current"] == "moderate" and g0["state"] == "NM"
    assert (g0["gnis_id"], g0["lat"], g0["lon"]) == ("00999", 36.5, -108.1)
    calls.clear()                                                                                 # an hour later the same status: published again (fresh readings), no build
    r = fn.hourly(con, tmp_path / "no-bundle.json", out, state, True, True, (now + pd.Timedelta(hours=1)).to_pydatetime(), run, env)
    assert r["published"] and not r["render"] and [c[0] for c in calls] == ["aws"]


def test_hourly_with_nothing_to_publish_does_not_start_a_build(tmp_path):
    now = pd.Timestamp("2026-10-25 18:00", tz="UTC")
    con = catalog([(now - pd.Timedelta(minutes=15 * i), 11.0) for i in range(8)])
    calls = []
    r = fn.hourly(con, tmp_path / "b.json", tmp_path / "o.json", tmp_path / "s.json", False, True, now.to_pydatetime(), lambda c, **k: calls.append(c), {})
    assert r["render"] and not r["published"] and not r["started"] and calls == []
