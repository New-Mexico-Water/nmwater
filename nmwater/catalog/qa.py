"""QA report: coverage per source, landmark record checks, catalog hygiene."""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb

from ..core.config import DOCS_DIR, Settings
from ..core.models import OBS_KEY

log = logging.getLogger("nmwater.qa")

# (site_uid, variable, interval or None for any, expected first date <=, minimum row count, description)
# Expectations are each record's true start, checked against the provider's own catalog (issue #3), not the age of the
# gauge: a gauge can be older than its daily record. The interval keeps annual peaks and 15-minute readings from
# standing in for a daily series.
LANDMARKS = [
    ("usgs:08279500", "discharge", "daily", "1889-01-01", 45000, "Rio Grande at Embudo daily discharge from 1889"),
    ("usgs:08313000", "discharge", "daily", "1895-12-31", 40000, "Rio Grande at Otowi Bridge daily discharge from 1895"),
    # USGS publishes daily discharge here only from 1965-10-01 (22,288 days); the 1942 record is annual peaks.
    ("usgs:08330000", "discharge", "daily", "1965-10-01", 22000, "Rio Grande at Albuquerque daily discharge from 1965"),
    ("usgs:08358400", "discharge", "daily", "1949-12-31", 25000, "Rio Grande Floodway at San Marcial"),
    ("usgs:08407500", "discharge", "daily", "1937-12-31", 30000, "Pecos River near Red Bluff"),
    # USGS has no daily discharge at El Paso (two sampled values only); its peaks run 1884-1982. Recent daily flow is IBWC's.
    ("usgs:08364000", "discharge", "water_year", "1884-12-31", 80, "Rio Grande at El Paso annual peak flows"),
    ("usbr_hydrodata:1119", "reservoir_storage", None, "1915-03-21", 40000, "Elephant Butte storage from 1915"),
    # Reclamation's series has gaps in the source (1909, 1914, 1918), so under the 46,000 days between its ends.
    ("usbr_hydrodata:1095", "discharge", "daily", "1899-10-01", 43000, "Otowi flow from 1899 (Reclamation)"),
    ("usace_cwms:Cochiti", "reservoir_elevation", None, "1993-01-02", 12000, "Cochiti daily elevation from 1993"),
    # NRCS lists the station from 1971-10-01 but its snow water element starts 1978-10-01 (hourly data from 1978-11-01).
    ("nrcs:486:NM:SNTL", "swe", "hourly", "1978-12-31", 150000, "Frisco Divide SNOTEL snow water equivalent from 1978"),
    ("nrcs:922:NM:SNTL", "swe", None, "1996-10-01", 10000, "Santa Fe SNOTEL from 1996"),
    ("codwr:LAJCAPCO", "discharge", None, "1943-03-01", 20000, "La Jara Creek (CO DWR) from 1943"),
]

SITE_COUNT_MIN = {
    "usgs": 42000, "noaa_ghcnd": 2300, "nmwdi_st2": 1100, "usace_cwms": 500, "nrcs": 200, "wqp": 48000,
    "ose_arcgis": 250000, "usbr_hydrodata": 120, "codwr": 100,
}


def _built(path: Path) -> str:
    """The catalog file's modification date, so the committed report says how fresh it is without naming a path on one machine."""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d")


def duplicate_keys(con, key=OBS_KEY) -> tuple[int, list[str]]:
    """Observation rows that share the store's key (core.models.OBS_KEY, depth included), and the source:variable pairs that could not be checked. Counted one source and variable
    at a time: a duplicate can only sit within one of them, and one GROUP BY over every observation (about 700 million rows) ran out of memory on 2026-11-12. Before 2026-11-12 the key
    here left out depth_cm, so every soil sensor read at more than one depth counted as a duplicate (12.5 million false ones)."""
    cols = ", ".join(c for c in key if c not in ("variable",))
    total, unchecked = 0, []
    for source, variable in con.execute("SELECT DISTINCT source, variable FROM site_variables ORDER BY 1, 2").fetchall():
        try:
            total += con.execute(f"SELECT COUNT(*) FROM (SELECT {cols}, COUNT(*) c FROM observations WHERE source = ? AND variable = ? GROUP BY ALL HAVING c > 1)",
                                 [source, variable]).fetchone()[0]
        except duckdb.OutOfMemoryException:
            unchecked.append(f"{source}:{variable}")
    return total, unchecked


def range_stats(con):
    """One row per variable in the archive: the bounds from variables.yaml, the lowest and highest raw value, and how many values fall below the lower bound (impossible),
    sit in the noise band (kept, flagged near_zero), exceed the upper bound, or are negative at all. Mirrors QC_FLAG_SQL in build.py."""
    return con.execute(
        """SELECT o.variable, v.unit, v.valid_min, v.noise_floor, v.valid_max, COUNT(*) AS n, MIN(o.value) AS lo, MAX(o.value) AS hi,
                  COUNT(*) FILTER (WHERE v.valid_min IS NOT NULL AND o.value < v.valid_min AND NOT (v.noise_floor IS NOT NULL AND o.value >= v.noise_floor)) AS below,
                  COUNT(*) FILTER (WHERE v.valid_min IS NOT NULL AND o.value < v.valid_min AND v.noise_floor IS NOT NULL AND o.value >= v.noise_floor) AS near_zero,
                  COUNT(*) FILTER (WHERE v.valid_max IS NOT NULL AND o.value > v.valid_max) AS above,
                  COUNT(*) FILTER (WHERE o.value < 0) AS negative
           FROM observations o LEFT JOIN variables v ON v.variable = o.variable
           WHERE o.value IS NOT NULL GROUP BY ALL ORDER BY o.variable"""
    ).fetchdf()


def _g(x) -> str:
    return "-" if x is None or x != x else f"{x:,.4g}"


def range_lines(df, registry=None) -> list[str]:
    """The "Plausible ranges" section as markdown lines: a table of every variable's allowed range against what the archive holds, then the variables with data and no bounds
    and, among them, those with negative values (with the reason recorded in variables.yaml as `unbounded_below_reason`, or `signed` for quantities that can be negative)."""
    out = ["", "## Plausible ranges", "",
           "Bounds are `valid_min` / `noise_floor` / `valid_max` in catalog/variables.yaml. A value below `valid_min` is impossible unless it is in the noise band; a value above `valid_max` is impossible. "
           "Impossible values are excluded from `observations_clean`; nothing is deleted. \"Seen\" is the raw lowest and highest value.", "",
           "| variable | unit | allowed | seen | impossible below | impossible above | noise band |", "|---|---|---|---|---|---|---|"]
    for r in df.itertuples(index=False):
        lo_set, hi_set = r.valid_min == r.valid_min and r.valid_min is not None, r.valid_max == r.valid_max and r.valid_max is not None
        allowed = f"{_g(r.valid_min)} to {_g(r.valid_max)}" if lo_set and hi_set else f"at least {_g(r.valid_min)}" if lo_set else f"at most {_g(r.valid_max)}" if hi_set else "no bounds"
        out.append(f"| {r.variable} | {r.unit if isinstance(r.unit, str) else '-'} | {allowed} | {_g(r.lo)} to {_g(r.hi)} | {int(r.below):,} | {int(r.above):,} | {int(r.near_zero):,} |")
    nob = df[df.apply(lambda r: (r.valid_min != r.valid_min or r.valid_min is None) and (r.valid_max != r.valid_max or r.valid_max is None), axis=1)]
    out += ["", f"- variables with data and no bounds at all, so no check: {len(nob)} of {len(df)}" + (f" ({', '.join(nob.variable)})" if len(nob) else "")]
    open_low = df[(df.valid_min.isna()) & (df.negative > 0)]
    if len(open_low):
        parts = []
        for r in open_low.itertuples(index=False):
            v = registry.vars.get(r.variable) if registry is not None else None
            why = "negative values expected (signed)" if v is not None and v.signed else (v.extra.get("unbounded_below_reason") if v is not None else None) or "no reason recorded"
            parts.append(f"{r.variable} ({int(r.negative):,}: {why})")
        out.append("- no lower bound although negative values occur: " + "; ".join(parts))
    return out


def run(settings: Settings, out: Path | None = None) -> Path:
    con = duckdb.connect(str(settings.duckdb_path), read_only=True)
    con.execute("SET TimeZone='UTC'")
    con.execute("SET preserve_insertion_order=false")        # lets large aggregates spill instead of holding every row in order
    lines = ["# QA report", "", f"Catalog: `{settings.duckdb_path.name}`, built {_built(settings.duckdb_path)}. Regenerate with `nmwater report`.", ""]

    # Coverage by source
    lines += ["## Coverage by source", "", "| source | sites | variables | observations | first | last |", "|---|---|---|---|---|---|"]
    try:
        cov = con.execute(
            """SELECT s.source, COUNT(DISTINCT s.site_uid) AS sites,
                      COUNT(DISTINCT sv.variable) AS variables, COALESCE(SUM(sv.n_obs),0) AS n_obs,
                      MIN(sv.first_datetime) AS t0, MAX(sv.last_datetime) AS t1
               FROM sites s LEFT JOIN site_variables sv USING (site_uid) GROUP BY 1 ORDER BY 1"""
        ).fetchdf()
        for r in cov.itertuples(index=False):
            lines.append(f"| {r.source} | {r.sites:,} | {r.variables} | {int(r.n_obs):,} | {str(r.t0)[:10]} | {str(r.t1)[:10]} |")
    except Exception as e:
        lines.append(f"| (coverage query failed: {e}) | | | | | |")

    # Site count expectations
    lines += ["", "## Site count checks", "", "| source | expected >= | actual | ok |", "|---|---|---|---|"]
    counts = dict(con.execute("SELECT source, COUNT(*) FROM sites GROUP BY 1").fetchall())
    for src, mn in SITE_COUNT_MIN.items():
        n = counts.get(src, 0)
        lines.append(f"| {src} | {mn:,} | {n:,} | {'yes' if n >= mn else 'NO' if n else 'not loaded'} |")

    # Landmarks
    lines += ["", "## Landmark records", "", "| check | first | rows | ok |", "|---|---|---|---|"]
    n_fail = 0
    for uid, var, interval, first_by, min_rows, desc in LANDMARKS:
        try:
            r = con.execute(
                "SELECT MIN(first_datetime) AS t0, SUM(n_obs) AS n FROM site_variables WHERE site_uid=? AND variable=? AND (? IS NULL OR interval=?)",
                [uid, var, interval, interval],
            ).fetchone()
        except Exception:
            r = (None, None)
        t0, n = (r[0], r[1]) if r else (None, None)
        if t0 is None:
            lines.append(f"| {desc} (`{uid}`) | - | 0 | not loaded |")
            continue
        ok = str(t0)[:10] <= first_by and (n or 0) >= min_rows
        n_fail += 0 if ok else 1
        lines.append(f"| {desc} (`{uid}`) | {str(t0)[:10]} | {int(n or 0):,} | {'yes' if ok else 'NO'} |")

    # Hygiene: variables in data not in registry; sites without coordinates; duplicate observations
    lines += ["", "## Hygiene", ""]
    try:
        unknown = con.execute(
            "SELECT DISTINCT sv.variable FROM site_variables sv LEFT JOIN variables v ON v.variable = sv.variable WHERE v.variable IS NULL"
        ).fetchdf()
        lines.append(f"- variables in data missing from variables.yaml: {', '.join(unknown['variable']) if len(unknown) else 'none'}")
        nocoord = con.execute("SELECT source, COUNT(*) FROM sites WHERE lat IS NULL OR lon IS NULL GROUP BY 1").fetchall()
        lines.append("- sites without coordinates: " + (", ".join(f"{s}={n:,}" for s, n in nocoord) if nocoord else "none"))
        # In New Mexico (the site's state is NM) a missing watershed is a problem to fix; outside it, the archive has no watershed layer to place a site in.
        nohuc_nm, nohuc_out = con.execute(
            "SELECT COUNT(*) FILTER (WHERE state = 'NM' OR state IS NULL), COUNT(*) FILTER (WHERE state IS NOT NULL AND state <> 'NM') FROM sites WHERE huc8 IS NULL AND lat IS NOT NULL"
        ).fetchone()
        lines.append(f"- located sites without huc8, in New Mexico (WBD regions missing or a bad coordinate): {nohuc_nm:,}")
        lines.append(f"- located sites without huc8, in another state (expected; outside the watershed layers): {nohuc_out:,}")
        zeros = con.execute("SELECT COUNT(*) FROM sites WHERE lat = 0 AND lon = 0").fetchone()[0]
        lines.append(f"- sites at latitude 0, longitude 0 (a missing location written as a point; should be none): {zeros:,}")
        orphans = con.execute(
            "SELECT COUNT(DISTINCT sv.site_uid) FROM site_variables sv LEFT JOIN sites s USING (site_uid) WHERE s.site_uid IS NULL"
        ).fetchone()[0]
        lines.append(f"- observation sites missing from sites table: {orphans:,}")
    except Exception as e:
        lines.append(f"- hygiene checks failed: {e}")

    # Each heavy check has its own try: one that fails (memory, a missing view) must not silently drop the checks after it, as the duplicate-key query did on 2026-11-12.
    try:
        dups, unchecked = duplicate_keys(con)
        lines.append(f"- duplicate observation keys, by the store's key {', '.join(OBS_KEY)} (run `nmwater compact` if not zero): {dups:,}"
                     + (f"; not checked, out of memory: {', '.join(unchecked)}" if unchecked else ""))
    except Exception as e:
        lines.append(f"- duplicate observation keys: check failed ({str(e)[:160]})")
    try:
        rs = range_stats(con)
        imp = [(r.variable, int(r.below + r.above)) for r in rs.itertuples(index=False) if r.below + r.above > 0]
        nz = [(r.variable, int(r.near_zero)) for r in rs.itertuples(index=False) if r.near_zero > 0]
        imp.sort(key=lambda t: -t[1]); nz.sort(key=lambda t: -t[1])
        lines.append("- implausible values, excluded from observations_clean (bounds in variables.yaml; table below): "
                     + (", ".join(f"{v}={n:,}" for v, n in imp) if imp else "none"))
        lines.append("- near-zero values within instrument noise, kept and flagged near_zero: "
                     + (", ".join(f"{v}={n:,}" for v, n in nz) if nz else "none"))
        try:
            from .variables import VariableRegistry
            reg = VariableRegistry()
        except Exception:
            reg = None
        lines += range_lines(rs, reg)
    except Exception as e:
        lines.append(f"- plausible ranges: check failed ({str(e)[:160]})")

    # Crosswalk equivalence summary
    try:
        eq = con.execute("SELECT equivalence, COUNT(*) FROM crosswalk GROUP BY 1").fetchall()
        lines += ["", "## Crosswalk", "", "- " + ", ".join(f"{k}: {n}" for k, n in eq)]
    except Exception:
        pass

    con.close()
    out = out or (DOCS_DIR / "qa" / "report.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    return out
