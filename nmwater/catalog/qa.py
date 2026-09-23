"""QA report: coverage per source, landmark record checks, catalog hygiene."""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb

from ..core.config import DOCS_DIR, Settings

log = logging.getLogger("nmwater.qa")

# (site_uid, variable, expected first date <=, minimum row count, description)
LANDMARKS = [
    ("usgs:08279500", "discharge", "1889-01-01", 45000, "Rio Grande at Embudo daily discharge from 1889"),
    ("usgs:08313000", "discharge", "1895-12-31", 40000, "Rio Grande at Otowi Bridge"),
    ("usgs:08330000", "discharge", "1942-01-01", 28000, "Rio Grande at Albuquerque"),
    ("usgs:08358400", "discharge", "1949-12-31", 25000, "Rio Grande Floodway at San Marcial"),
    ("usgs:08407500", "discharge", "1937-12-31", 30000, "Pecos River near Red Bluff"),
    ("usgs:08364000", "discharge", "1938-12-31", 30000, "Rio Grande at El Paso"),
    ("usbr_hydrodata:1119", "reservoir_storage", "1915-03-21", 40000, "Elephant Butte storage from 1915"),
    ("usbr_hydrodata:1095", "discharge", "1899-10-01", 46000, "Otowi flow from 1899 (Reclamation)"),
    ("usace_cwms:Cochiti", "reservoir_elevation", "1993-01-02", 12000, "Cochiti daily elevation from 1993"),
    ("nrcs:486:NM:SNTL", "swe", "1971-10-01", 15000, "Frisco Divide SNOTEL WTEQ from 1971"),
    ("nrcs:922:NM:SNTL", "swe", "1996-10-01", 10000, "Santa Fe SNOTEL from 1996"),
    ("codwr:LAJCAPCO", "discharge", "1943-03-01", 20000, "La Jara Creek (CO DWR) from 1943"),
]

SITE_COUNT_MIN = {
    "usgs": 42000, "noaa_ghcnd": 2300, "nmwdi_st2": 1100, "usace_cwms": 500, "nrcs": 200, "wqp": 48000,
    "ose_arcgis": 250000, "usbr_hydrodata": 120, "codwr": 100,
}


def run(settings: Settings, out: Path | None = None) -> Path:
    con = duckdb.connect(str(settings.duckdb_path), read_only=True)
    con.execute("SET TimeZone='UTC'")
    lines = ["# QA report", "", f"Catalog: `{settings.duckdb_path}`", ""]

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
    for uid, var, first_by, min_rows, desc in LANDMARKS:
        try:
            r = con.execute(
                "SELECT MIN(first_datetime) AS t0, SUM(n_obs) AS n FROM site_variables WHERE site_uid=? AND variable=?",
                [uid, var],
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
        nohuc = con.execute("SELECT COUNT(*) FROM sites WHERE huc8 IS NULL AND lat IS NOT NULL").fetchone()[0]
        lines.append(f"- located sites without huc8 (run WBD download + catalog build to fill): {nohuc:,}")
        orphans = con.execute(
            "SELECT COUNT(DISTINCT sv.site_uid) FROM site_variables sv LEFT JOIN sites s USING (site_uid) WHERE s.site_uid IS NULL"
        ).fetchone()[0]
        lines.append(f"- observation sites missing from sites table: {orphans:,}")
        dup = con.execute(
            """SELECT COUNT(*) FROM (SELECT site_uid, variable, datetime_utc, interval, statistic, COUNT(*) c
                    FROM observations GROUP BY ALL HAVING c > 1)"""
        ).fetchone()[0]
        lines.append(f"- duplicate observation keys (run `nmwater compact`): {dup:,}")
        flags = con.execute(
            "SELECT variable, qc_flag, COUNT(*) FROM observations_qc WHERE qc_flag <> 'ok' GROUP BY ALL ORDER BY 3 DESC"
        ).fetchall()
        imp = [(v, n) for v, f, n in flags if f == "implausible"]
        nz = [(v, n) for v, f, n in flags if f == "near_zero"]
        lines.append("- implausible values, excluded from observations_clean (bounds in variables.yaml): "
                     + (", ".join(f"{v}={n:,}" for v, n in imp) if imp else "none"))
        lines.append("- near-zero values within instrument noise, kept and flagged near_zero: "
                     + (", ".join(f"{v}={n:,}" for v, n in nz) if nz else "none"))
        unb = con.execute(
            "SELECT COUNT(*) FROM observations WHERE value < 0 AND variable = 'discharge'").fetchone()[0]
        lines.append(f"- negative discharge, deliberately unbounded pending confirmation that canal reverse flow is real: {unb:,}")
    except Exception as e:
        lines.append(f"- hygiene checks failed: {e}")

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
