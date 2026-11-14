"""The six-hourly snow status: what counts as a change a visitor would see, and when the site is rendered again."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nmwater.site import snow_now as sw

NOW = datetime(2026, 12, 3, 12, 0, tzinfo=UTC)


def stn(i, depth=10.0, swe=2.0, new24=0.0, new7=0.0):
    return {"id": i, "depth": depth, "swe": swe, "new24": new24, "new7": new7}


def content(stations):
    return {"stations": stations}


def bundle(stations, generated=NOW - timedelta(hours=10)):
    return {"generated": generated.isoformat(), "stations": stations}


def test_a_sensor_drifting_inside_a_step_changes_nothing_but_a_storm_does():
    a = sw.fingerprint([stn("b", depth=10.1), stn("a", depth=20.0)])
    assert a == sw.fingerprint([stn("a", depth=20.9), stn("b", depth=11.9)])       # sorted, and under 3 inches either way
    assert a != sw.fingerprint([stn("a", depth=20.0), stn("b", depth=13.1)])
    assert sw.fingerprint([stn("a", new24=0.0)]) != sw.fingerprint([stn("a", new24=3.2)])
    assert sw.fingerprint([stn("a", depth=None, swe=None, new24=None, new7=None)]) == [["a", None, None, None, None]]


def test_a_quiet_summer_is_never_rendered_and_a_new_storm_is():
    quiet = [stn("a", depth=0.0, swe=0.0)]
    assert sw.decide(None, bundle(quiet), content(quiet), NOW) == (False, "nothing a visitor would see has changed")
    storm = [stn("a", depth=7.0, swe=1.0, new24=6.0, new7=6.0)]
    assert sw.decide(None, bundle(quiet), content(storm), NOW) == (True, "a station's snow figures changed")


def test_the_last_render_counts_until_the_nightly_bundle_replaces_it():
    quiet, storm = [stn("a", depth=0.0, swe=0.0)], [stn("a", depth=7.0, swe=1.0, new24=6.0, new7=6.0)]
    state = sw.record(None, content(storm), NOW - timedelta(hours=2), True)
    assert sw.decide(state, bundle(quiet), content(storm), NOW) == (False, "nothing a visitor would see has changed")                       # already on the page
    fresh = bundle(quiet, generated=NOW - timedelta(hours=1))
    assert sw.decide(state, fresh, content(storm), NOW)[0] is True                                                                          # the nightly build wiped it


def test_no_more_than_the_daily_limit_of_renders():
    state = None
    for k in range(sw.MAX_DEPLOYS_PER_DAY):
        state = sw.record(state, content([stn("a", depth=3.0 * k)]), NOW, True)
    assert sw.decide(state, None, content([stn("a", depth=99.0)]), NOW) == (False, f"already rendered {sw.MAX_DEPLOYS_PER_DAY} times today")
    assert sw.decide(state, None, content([stn("a", depth=99.0)]), NOW + timedelta(days=1))[0] is True


def test_a_render_is_counted_only_when_it_started_and_old_days_are_forgotten(tmp_path):
    state = sw.record({"deploys": {"2026-12-01": 3}}, content([stn("a")]), NOW, False)
    assert state["deploys"] == {} and state["rendered"] is None
    p = tmp_path / "s.json"
    p.write_text(json.dumps(sw.record(state, content([stn("a")]), NOW, True)))
    assert sw.load_state(p)["deploys"] == {"2026-12-03": 1} and sw.load_state(tmp_path / "none.json") is None


def test_uploading_puts_the_file_next_to_latest_json_with_a_short_cache():
    seen = {}
    sw.upload(Path("x.json"), run=lambda cmd, **kw: seen.update(cmd=cmd), env={"R2_ENDPOINT": "https://r2", "R2_BUCKET": "b"})
    assert "s3://b/snow-now.json" in seen["cmd"] and "max-age=60" in seen["cmd"]
