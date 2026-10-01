"""Build the whole site data bundle: `nmwater export-site-data` (also scripts/cron/site_data.sh).

<out>/
  manifest.json          what is in the bundle: producer, generated, data_through, every river's entry, headlines
  rivers/<slug>/...      see river.py
  watersheds/, reservoirs/ (later phases)

The tree is built in a temporary directory and swapped in at the end, so a failed or interrupted run leaves the
previous bundle in place. With --river, only those rivers are rebuilt and the others are kept.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from ..derived import river_flow as rf
from ..reports import river_share as rs
from ..derived import river_watershed as rw
from . import SCHEMA_VERSION
from .precip import export_precip
from .river import clean, export_river

log = logging.getLogger("nmwater.site")


def producer(root: Path) -> dict:
    sha = None
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10).stdout.strip() or None
    except Exception:                                      # not a checkout, or no git
        pass
    return {"name": "nmwater", "git_sha": sha}


def export(db: Path, out: Path, root: Path, grids: Path, cache: Path, rivers: list[str] | None = None, config: dict | None = None,
           river_notes: dict | None = None, exclusions: list[dict] | None = None, descriptions: dict | None = None,
           social: bool = True, csv: bool = True) -> tuple[list[dict], list[str]]:
    """Returns (manifest entries written, rivers that failed)."""
    import duckdb

    cfg = config or {}
    river_notes = river_notes or {}
    descriptions = descriptions or {}
    overrides = cfg.get("rivers") or {}
    site = rs.site_config(cfg)
    con = duckdb.connect(str(db), read_only=True)
    con.execute("SET TimeZone = 'UTC'")
    found = rf.list_rivers(con, 1 if rivers else int(cfg.get("min_sites", 2)), cfg.get("min_years_single_gauge"))
    if rivers:
        want = set(rivers)
        found = [x for x in found if x.name in want or x.label in want]
    skip = set(cfg.get("exclude") or [])
    names = [x for x in found if x.name not in skip and x.label not in skip]
    links = con.sql("SELECT site_uid_a, site_uid_b FROM site_links").df()
    as_of = con.sql("SELECT max(datetime_utc)::DATE FROM observations_clean WHERE variable = 'discharge' "
                    "AND interval = 'daily' AND statistic = 'mean' AND datetime_utc <= now()").fetchone()[0]
    generated = datetime.now(UTC).isoformat(timespec="seconds")
    codes = [c for (c,) in con.sql("SELECT DISTINCT huc8 FROM river_segments WHERE huc8 IS NOT NULL").fetchall()]
    rw.update_climate(grids, grids / "wbd", cache, sorted(codes))     # fill the watershed cache once for every river

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.parent / f".{out.name}.tmp-{datetime.now(UTC):%Y%m%d%H%M%S}"
    (tmp / "rivers").mkdir(parents=True)
    entries: list[dict] = []
    failed: list[str] = []
    try:
        for name in names:
            o = overrides.get(name.label) or overrides.get(name.name) or {}
            try:
                r = rf.build_river(con, name, links, states=o.get("states"), notes=o.get("notes"), as_of=as_of, exclusions=exclusions)
                if r is None:
                    log.info("%s: no gauge with %d+ weeks of daily flow; skipped", name.label, rf.MIN_WEEKS)
                    continue
                r.data_notes = list(river_notes.get(r.river) or [])
                entry = export_river(con, r, name.name, grids, cache, root, tmp / "rivers" / r.slug,
                                     descriptions.get(r.river) or descriptions.get(name.name), generated, site["name"], social, csv, site.get("license"))
                entries.append(entry)
                log.info("%s: %d segments, %d gauges", name.label, entry["segments"], entry["gauges"])
            except Exception as e:                          # one bad river must not stop the rest
                log.exception("%s failed: %s", name.label, e)
                failed.append(name.label)
        if rivers and (out / "rivers").exists():           # partial run: keep the other rivers
            for p in (out / "rivers").iterdir():
                if p.is_dir() and not (tmp / "rivers" / p.name).exists():
                    shutil.copytree(p, tmp / "rivers" / p.name)
            old = json.loads((out / "manifest.json").read_text()).get("rivers", []) if (out / "manifest.json").exists() else []
            done = {e["slug"] for e in entries}
            entries += [e for e in old if e["slug"] not in done]
        precipitation = export_precip(con, tmp, tmp / "rivers", grids, grids / "wbd", generated, csv)
        entries.sort(key=lambda e: (-e["gauges"], e["name"]))
        rated = [e for e in entries if e["segments_rated"]]
        manifest = {"schema_version": SCHEMA_VERSION, "producer": producer(root), "generated": generated,
                    "data_through": as_of, "site": {"name": site["name"]}, "license": rs.license_block(site),
                    "rivers": entries, **({"precipitation": precipitation} if precipitation else {}),
                    "headlines": {"rivers": {"total": len(entries), "rated": len(rated),
                                             "with_segment_below_normal": sum(1 for e in rated if e["segments_below_normal"])}}}
        (tmp / "manifest.json").write_text(json.dumps(clean(manifest), separators=(",", ":"), allow_nan=False), encoding="utf-8")
        old_dir = out.parent / f".{out.name}.old"
        if old_dir.exists():
            shutil.rmtree(old_dir)
        if out.exists():
            out.rename(old_dir)
        tmp.rename(out)
        if old_dir.exists():
            shutil.rmtree(old_dir)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
        con.close()
    return entries, failed
