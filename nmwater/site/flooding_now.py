"""The hourly flood status: statewide/flooding.json made from the latest National Weather Service readings, uploaded next to latest.json as flooding-now.json,
and the decision whether the home page must be rendered again.

The home page is static HTML, so a flood that starts (or ends) between two nightly builds shows only when the page is built again. The site's build reads
flooding-now.json (scripts/fetch-data.ts) and uses it in place of the bundle's flooding.json when it is newer. Building the whole site takes about three minutes of
GitHub Actions and Cloudflare Pages allows 500 deploys a month on the free plan, so the page is rendered again only when what the band says has changed (a gauge
reaches or leaves a category, a river falls out of flood, the band appears or clears), or when a flood is on and the page is more than MAX_AGE_HOURS old; and never
more than MAX_DEPLOYS_PER_DAY times a day. Run by scripts/cron/flood_hourly.sh.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("nmwater.flooding_now")

MAX_DEPLOYS_PER_DAY = 6
MAX_AGE_HOURS = 6.0
WEB_REPO = "New-Mexico-Water/nmwater-web"
WORKFLOW = "ci.yml"


def fingerprint(content: dict | None) -> list[list]:
    """What the band says, without the times: each gauge, the category it reached and whether it is in flood now. Readings that move inside a category do not change it."""
    if not content:
        return []
    return sorted([g["key"], g["category"], g["current"]] for g in content["gauges"])


def _utc(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def decide(state: dict | None, bundle: dict | None, content: dict, now: datetime,
           max_per_day: int = MAX_DEPLOYS_PER_DAY, max_age_hours: float = MAX_AGE_HOURS) -> tuple[bool, str]:
    """Whether to render the home page again, and why. `state`: what the last run recorded ({"fingerprint", "rendered": ISO time the page last got this status, "deploys": {date: n}});
    `bundle`: the nightly bundle's flooding.json, which is what a scheduled build shows. A status older than the bundle's is ignored: the nightly build has replaced it."""
    shown_fp, shown_at = fingerprint(bundle), _utc(bundle["generated"]) if bundle else None
    if state and state.get("rendered") and (shown_at is None or _utc(state["rendered"]) > shown_at):
        shown_fp, shown_at = state["fingerprint"], _utc(state["rendered"])
    fp = fingerprint(content)
    today = now.astimezone(timezone.utc).date().isoformat()
    if (state or {}).get("deploys", {}).get(today, 0) >= max_per_day:
        return False, f"already rendered {max_per_day} times today"
    if fp != shown_fp:
        return True, "the band changed" if fp else "the band clears"
    if fp and shown_at is not None and (now - shown_at).total_seconds() > max_age_hours * 3600:
        return True, f"a flood is on and the page is over {max_age_hours:g} hours old"
    return False, "nothing the band says has changed"


def load_state(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def record(state: dict | None, content: dict, now: datetime, rendered: bool) -> dict:
    """The new state. `rendered` says a build was started for this status, which is when it counts toward the day's limit."""
    s = {"fingerprint": (state or {}).get("fingerprint", []), "rendered": (state or {}).get("rendered"), "deploys": dict((state or {}).get("deploys", {}))}
    today = now.astimezone(timezone.utc).date().isoformat()
    s["deploys"] = {d: n for d, n in s["deploys"].items() if d >= today}                       # only today's count matters
    if rendered:
        s["fingerprint"], s["rendered"] = fingerprint(content), now.astimezone(timezone.utc).isoformat(timespec="seconds")
        s["deploys"][today] = s["deploys"].get(today, 0) + 1
    return s


def upload(path: Path, run=subprocess.run, env: dict | None = None) -> None:
    """Put the file at the bucket's root, next to latest.json (the same settings as scripts/cron/publish_site_data.sh)."""
    e = env if env is not None else os.environ
    cmd = ["aws", "s3", "--endpoint-url", e["R2_ENDPOINT"], "--region", "auto", "cp", str(path), f"s3://{e['R2_BUCKET']}/flooding-now.json",
           "--content-type", "application/json", "--cache-control", "max-age=60", "--only-show-errors"]
    run(cmd, check=True, env={**os.environ, **e})


def render_site(run=subprocess.run, repo: str = WEB_REPO) -> None:
    """Start the site's build and deploy (the workflow's manual trigger; it builds from the latest bundle and picks up flooding-now.json)."""
    run(["gh", "workflow", "run", WORKFLOW, "--repo", repo], check=True)


def hourly(con, bundle_flooding: Path, out: Path, state_path: Path, publish: bool, redeploy: bool, now: datetime | None = None,
           run=subprocess.run, env: dict | None = None) -> dict:
    """One run: make the status from the catalog (the latest fetch is in it: its observations are a view over the stored files), check it against the schema, write it to `out`,
    upload it when `publish`, and start the site's build when it must be rendered again and `redeploy` (a build reads the uploaded file, so it needs `publish`).
    The state is recorded only after the build was started, so a failed upload or trigger is tried again next hour. Raises when there is nothing to say or the file is not valid."""
    from . import statewide as sw
    from .river import clean
    from .schema import _flooding_statewide

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    got = sw.flooding_file(con, now.isoformat(timespec="seconds"))
    if got is None:
        raise RuntimeError("the catalog has no NWS flood categories or readings")
    content, entry = got
    problems = _flooding_statewide(content, entry)
    if problems:
        raise ValueError("flooding.json does not match the schema: " + "; ".join(problems[:3]))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(clean(content), separators=(",", ":"), allow_nan=False), encoding="utf-8")
    bundle = json.loads(bundle_flooding.read_text()) if bundle_flooding.exists() else None
    state = load_state(state_path)
    go, why = decide(state, bundle, content, now)
    res = {"gauges": len(content["gauges"]), "checked": content["checked"], "latest_reading": content["latest_reading"], "render": go, "why": why, "published": False, "started": False}
    if publish:
        upload(out, run, env)
        res["published"] = True
    if go and redeploy and publish:
        render_site(run)
        res["started"] = True
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(record(state, content, now, res["started"])))
    return res
