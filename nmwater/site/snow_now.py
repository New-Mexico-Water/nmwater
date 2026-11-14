"""The six-hourly snow-station status: station readings and new snow made from the latest SNOTEL fetch, uploaded next to latest.json as snow-now.json, and the decision whether the
site must be rendered again. Run by scripts/cron/snow_sixhourly.sh.

What changes between two nightly builds, and what does not:
- SNODAS (water in the snow and depth by watershed and range, the maps, the ratings of those) is one file a day for yesterday: it stays in the nightly bundle.
- Stations report every hour. This file carries each station's latest depth and water in the snow, its new snow through the last complete day (NRCS's daily row dated D is the
  reading at the local midnight that ends D; until it arrives the hourly reading at that midnight stands in, so yesterday's snow is on the site by about 00:30 and not 02:00),
  and the new-snow ratings of the watersheds and ranges that have stations.
- New snow stays a daily-resolution estimate: hourly depth is too noisy to count snowfall from (see docs/reports/new-snow-estimate.md), so the latest hourly figure is only shown as
  a reading, never added up.

The site is static, so the figures show only after a build (about three minutes of GitHub Actions; Cloudflare Pages allows 500 deploys a month and the flood job and the nightly
build use some). The page is rendered again only when a station's figures moved by enough to be visible, and never more than MAX_DEPLOYS_PER_DAY times a day.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from ..derived import snotel_snow as ss
from ..derived import snow_normal as sn
from . import snow as sx
from .river import clean

log = logging.getLogger("nmwater.snow_now")

SCHEMA_VERSION = 2
MAX_DEPLOYS_PER_DAY = 4
READING_MAX_AGE_H = 36                  # a station whose latest hourly reading is older than this shows its daily value only
DEPTH_STEP_IN = 3.0                     # a station's depth moves the fingerprint by this much
SWE_STEP_IN = 0.5
WEB_REPO = "New-Mexico-Water/nmwater-web"
WORKFLOW = "ci.yml"


def snotel_sites(con) -> list[str]:
    """The native ids (station triplets) of the New Mexico SNOTEL stations, for `nmwater fetch nrcs --site`."""
    return [r[0] for r in con.sql("SELECT native_id FROM sites WHERE source = 'nrcs' AND state = 'NM' AND site_uid LIKE '%:SNTL' ORDER BY native_id").fetchall()]


def latest_readings(con, ids: list[str], now: datetime) -> dict[str, dict]:
    """{station: {depth, swe, at}} from the newest hourly depth and water readings no older than READING_MAX_AGE_H."""
    q = ",".join("'" + i + "'" for i in ids)
    since = (now - pd.Timedelta(hours=READING_MAX_AGE_H)).isoformat(timespec="seconds")
    d = con.sql(f"SELECT site_uid, variable, arg_max(value, datetime_utc) AS v, max(datetime_utc) AS at FROM observations_clean WHERE interval = 'hourly' "
                f"AND variable IN ('snow_depth', 'swe') AND site_uid IN ({q}) AND datetime_utc >= TIMESTAMPTZ '{since}' GROUP BY 1, 2").df()
    out: dict[str, dict] = {}
    for r in d.itertuples():
        o = out.setdefault(r.site_uid, {})
        o["depth" if r.variable == "snow_depth" else "swe"] = float(r.v)
        o["at"] = max(o.get("at", r.at), r.at)
    return out


def fingerprint(stations: list[dict]) -> list[list]:
    """What a visitor can see move: per station the day, depth and water in coarse steps, and the new-snow figures to the inch. Small drifts of a sensor change nothing."""
    def step(v, size):
        return None if v is None or pd.isna(v) else int(v // size)

    return sorted([s["id"], step(s.get("depth"), DEPTH_STEP_IN), step(s.get("swe"), SWE_STEP_IN),
                   None if s.get("new24") is None else round(s["new24"]), None if s.get("new7") is None else round(s["new7"])] for s in stations)


def make(con, parquet_dir: Path, generated: str, now: datetime) -> dict | None:
    """The status, or None when the derived series that define the units do not exist yet."""
    got = sx._read_units(parquet_dir)
    if got is None:
        return None
    units, daily = got
    units = units[units["unit"].isin(sx._rain_watersheds(con, set(units.loc[units["kind"] == "watershed", "unit"]))) | (units["kind"] == "range")]
    from shapely.geometry import shape

    model_day = pd.to_datetime(daily["date"]).max()
    cached = json.loads((parquet_dir / "derived" / "snow_ranges.geojson").read_text())["features"]
    shapes = {f["properties"]["id"]: shape(f["geometry"]) for f in cached}
    ids = list(units["unit"])
    st, depth_by, swe_by, new_by, stn_day, by_ws, by_rg = sx.station_state(con, ids, shapes, model_day)
    live = latest_readings(con, list(st["id"]), now)
    rows = []
    for s in st.itertuples():
        lv = live.get(s.id, {})
        tot = {k: (None if s.id not in new_by or pd.isna(ss.window_total(new_by[s.id], stn_day, k)) else round(float(ss.window_total(new_by[s.id], stn_day, k)), 2)) for k in ss.KINDS}
        rows.append({"id": s.id, "name": s.name, "lat": round(float(s.lat), 4), "lon": round(float(s.lon), 4), "elev_ft": int(s.elev_ft),
                     "watershed": s.huc8 if s.huc8 in set(ids) else None, "range": s.range,
                     "swe": lv.get("swe", sx._val(swe_by.get(s.id), stn_day)), "depth": lv.get("depth", sx._val(depth_by.get(s.id), stn_day)),
                     "reading_at": lv["at"].astimezone(UTC).isoformat(timespec="seconds") if "at" in lv else None, **tot})
    new = {}
    for u in units.itertuples():
        members = (by_rg if u.kind == "range" else by_ws)[u.unit]
        new[u.unit] = {k: (ss.rate_new_snow({m: new_by[m] for m in members}, stn_day, k) if members else
                           {"now": None, "usual": None, "pct": None, "cls": None, "little": False, "n": 0, "stations": 0}) for k in ss.KINDS}
    return {"schema_version": SCHEMA_VERSION, "generated": generated, "stations_day": str(stn_day.date()), "baseline": f"{sn.BASELINE[0]}-{sn.BASELINE[1]}",
            "latest_reading": max((r["reading_at"] for r in rows if r["reading_at"]), default=None), "stations": rows, "new_snow": new}


def decide(state: dict | None, bundle: dict | None, content: dict, now: datetime, max_per_day: int = MAX_DEPLOYS_PER_DAY) -> tuple[bool, str]:
    """Whether to render the site again, and why. `state`: what the last run recorded ({"fingerprint", "rendered", "deploys": {date: n}}); `bundle`: the nightly bundle's snow/index.json,
    which is what a scheduled build shows. A recorded render older than the bundle is ignored: the nightly build has replaced it."""
    shown_fp, shown_at = (fingerprint(bundle["stations"]), _utc(bundle["generated"])) if bundle else ([], None)
    if state and state.get("rendered") and (shown_at is None or _utc(state["rendered"]) > shown_at):
        shown_fp = state["fingerprint"]
    today = now.astimezone(UTC).date().isoformat()
    if (state or {}).get("deploys", {}).get(today, 0) >= max_per_day:
        return False, f"already rendered {max_per_day} times today"
    fp = fingerprint(content["stations"])
    if fp != shown_fp:
        return True, "a station's snow figures changed"
    return False, "nothing a visitor would see has changed"


def _utc(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def load_state(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def record(state: dict | None, content: dict, now: datetime, rendered: bool) -> dict:
    s = {"fingerprint": (state or {}).get("fingerprint", []), "rendered": (state or {}).get("rendered"), "deploys": dict((state or {}).get("deploys", {}))}
    today = now.astimezone(UTC).date().isoformat()
    s["deploys"] = {d: n for d, n in s["deploys"].items() if d >= today}
    if rendered:
        s["fingerprint"], s["rendered"] = fingerprint(content["stations"]), now.astimezone(UTC).isoformat(timespec="seconds")
        s["deploys"][today] = s["deploys"].get(today, 0) + 1
    return s


def upload(path: Path, run=subprocess.run, env: dict | None = None) -> None:
    """Put the file at the bucket's root, next to latest.json (the same settings as scripts/cron/publish_site_data.sh)."""
    e = env if env is not None else os.environ
    run(["aws", "s3", "--endpoint-url", e["R2_ENDPOINT"], "--region", "auto", "cp", str(path), f"s3://{e['R2_BUCKET']}/snow-now.json",
         "--content-type", "application/json", "--cache-control", "max-age=60", "--only-show-errors"], check=True, env={**os.environ, **e})


def render_site(run=subprocess.run, repo: str = WEB_REPO) -> None:
    run(["gh", "workflow", "run", WORKFLOW, "--repo", repo], check=True)


def sixhourly(con, parquet_dir: Path, bundle_snow: Path, out: Path, state_path: Path, publish: bool, redeploy: bool, now: datetime | None = None,
              run=subprocess.run, env: dict | None = None) -> dict:
    """One run: make the status from the catalog (the latest fetch is in it), write it to `out`, upload it when `publish`, start the site's build when it must be rendered again and
    `redeploy` (a build reads the uploaded file, so it needs `publish`). The state is recorded only after the build was started, so a failed upload or trigger is tried again next run."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    content = make(con, parquet_dir, now.isoformat(timespec="seconds"), now)
    if content is None:
        raise RuntimeError("no watershed snow series yet (nmwater watershed-snow)")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(clean(content), separators=(",", ":"), allow_nan=False), encoding="utf-8")
    bundle = json.loads(bundle_snow.read_text()) if bundle_snow.exists() else None
    state = load_state(state_path)
    go, why = decide(state, bundle, content, now)
    res = {"stations": len(content["stations"]), "stations_day": content["stations_day"], "latest_reading": content["latest_reading"], "render": go, "why": why,
           "published": False, "started": False}
    if publish:
        upload(out, run, env)
        res["published"] = True
    if go and redeploy and publish:
        render_site(run)
        res["started"] = True
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(record(state, content, now, res["started"])))
    return res
