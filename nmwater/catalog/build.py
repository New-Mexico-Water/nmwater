"""Build the DuckDB catalog over the Parquet store: sites, site_variables, observations views,
catalog tables (variables, crosswalk, sources), coverage stats, and a data dictionary export."""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import pandas as pd
import yaml

from ..core.config import CATALOG_DIR, DOCS_DIR, Settings
from ..core.geo import assign_hucs, assign_regions
from ..core.store import Store
from .crosswalk import Crosswalk
from .links import build_links
from .variables import VariableRegistry

log = logging.getLogger("nmwater.catalog")


def build(settings: Settings) -> Path:
    settings.ensure_dirs()
    store = Store(settings.parquet_dir)
    reg = VariableRegistry()
    xw = Crosswalk(registry=reg)
    dbp = settings.duckdb_path
    if dbp.exists():
        dbp.unlink()
    con = duckdb.connect(str(dbp))
    pq = settings.parquet_dir.as_posix()

    # Sites: union, HUC assignment (if WBD present), single table -----------------------
    sites = store.read_sites()
    if len(sites):
        sites = assign_hucs(sites, settings.grids_dir)
        sites_path = settings.parquet_dir / "sites_all.parquet"
        sites.to_parquet(sites_path, index=False)
        con.execute(f"CREATE TABLE sites AS SELECT * FROM read_parquet('{sites_path.as_posix()}')")
        regions = assign_regions(sites, settings.grids_dir)
        if len(regions):
            rpath = settings.parquet_dir / "site_regions.parquet"
            regions.to_parquet(rpath, index=False)
            con.execute(f"CREATE TABLE site_regions AS SELECT * FROM read_parquet('{rpath.as_posix()}')")
        else:
            con.execute("CREATE TABLE site_regions (site_uid VARCHAR, region_type VARCHAR, "
                        "region_id VARCHAR, region_name VARCHAR)")
        links = build_links(sites, manual=CATALOG_DIR / "sites_manual.csv")
        links_path = settings.parquet_dir / "site_links.parquet"
        links.to_parquet(links_path, index=False)
        con.execute(f"CREATE TABLE site_links AS SELECT * FROM read_parquet('{links_path.as_posix()}')")
    else:
        con.execute("CREATE TABLE sites (site_uid VARCHAR, source VARCHAR, native_id VARCHAR, name VARCHAR)")
        con.execute("CREATE TABLE site_links (site_uid_a VARCHAR, site_uid_b VARCHAR, link_type VARCHAR)")

    # Observations view over hive partitions ------------------------------------------
    ts_glob = f"{pq}/timeseries/source=*/variable=*/year=*/*.parquet"
    has_ts = any((settings.parquet_dir / "timeseries").glob("source=*/variable=*/year=*/*.parquet"))
    if has_ts:
        con.execute(
            f"""CREATE VIEW observations AS
                SELECT site_uid, variable, datetime_utc, utc_offset_min, value, unit, interval, statistic,
                       qualifier, source_param, source_unit, ingest_run_id, source, year
                FROM read_parquet('{ts_glob}', hive_partitioning=true, union_by_name=true)"""
        )
        con.execute(
            """CREATE TABLE site_variables AS
               SELECT site_uid, source, variable, interval, statistic,
                      MIN(datetime_utc) AS first_datetime, MAX(datetime_utc) AS last_datetime,
                      COUNT(*) AS n_obs, ANY_VALUE(source_param) AS source_param, ANY_VALUE(unit) AS unit
               FROM observations GROUP BY ALL"""
        )
        con.execute(
            """CREATE TABLE coverage AS
               SELECT source, variable, year, COUNT(DISTINCT site_uid) AS n_sites, COUNT(*) AS n_obs
               FROM observations GROUP BY ALL ORDER BY source, variable, year"""
        )
    else:
        con.execute("CREATE TABLE site_variables (site_uid VARCHAR, variable VARCHAR, n_obs BIGINT)")
        con.execute("CREATE TABLE coverage (source VARCHAR, variable VARCHAR, year INTEGER, n_sites BIGINT, n_obs BIGINT)")

    # Misc groups: each parquet under parquet/<group>/source=*/... becomes a view ----------
    for group in ("wateruse", "waterquality", "reference", "forecasts"):
        gdir = settings.parquet_dir / group
        if gdir.exists() and any(gdir.rglob("*.parquet")):
            con.execute(
                f"""CREATE VIEW {group} AS SELECT * FROM read_parquet('{pq}/{group}/source=*/**/*.parquet',
                    hive_partitioning=true, union_by_name=true)"""
            )

    # NHDPlus stream network: which reach a site sits on, and the network's own attributes.
    # Written by nmwater fetch nhdplus into the reference group under source=nhdplus; promoted
    # to first-class tables here (same treatment as site_regions and site_links) because they
    # are structural, not ad hoc reference data. Geometry for the reaches themselves stays in
    # data/grids/nhdplus/flowlines.gpkg for GIS tools; these tables are attributes only.
    reaches_pq = settings.parquet_dir / "reference" / "source=nhdplus" / "site_reaches.parquet"
    if reaches_pq.exists():
        con.execute(f"CREATE TABLE site_reaches AS SELECT * FROM read_parquet('{reaches_pq.as_posix()}')")
    else:
        con.execute("CREATE TABLE site_reaches (site_uid VARCHAR, comid BIGINT, gnis_name VARCHAR, "
                    "streamorde BIGINT, totdasqkm DOUBLE, huc8 VARCHAR, snap_distance_m DOUBLE)")
    flow_pq = settings.parquet_dir / "reference" / "source=nhdplus" / "flowline_attributes.parquet"
    if flow_pq.exists():
        con.execute(f"CREATE TABLE flowlines AS SELECT * FROM read_parquet('{flow_pq.as_posix()}')")
    wb_sites_pq = settings.parquet_dir / "reference" / "source=nhdplus" / "site_waterbodies.parquet"
    if wb_sites_pq.exists():
        con.execute(f"CREATE TABLE site_waterbodies AS SELECT * FROM read_parquet('{wb_sites_pq.as_posix()}')")
    else:
        con.execute("CREATE TABLE site_waterbodies (site_uid VARCHAR, comid BIGINT, gnis_name VARCHAR, "
                    "areasqkm DOUBLE, huc8 VARCHAR, match_type VARCHAR, distance_m DOUBLE)")
    wb_attr_pq = settings.parquet_dir / "reference" / "source=nhdplus" / "waterbody_attributes.parquet"
    if wb_attr_pq.exists():
        con.execute(f"CREATE TABLE waterbodies AS SELECT * FROM read_parquet('{wb_attr_pq.as_posix()}')")

    # Dam capacity (National Inventory of Dams), matched to a waterbody comid where possible so
    # it joins to reservoir_storage observations via site_waterbodies without name matching.
    cap_pq = settings.parquet_dir / "reference" / "source=nid" / "reservoir_capacity.parquet"
    if cap_pq.exists():
        con.execute(f"CREATE TABLE reservoir_capacity AS SELECT * FROM read_parquet('{cap_pq.as_posix()}')")
    else:
        con.execute("CREATE TABLE reservoir_capacity (nid_id VARCHAR, dam_name VARCHAR, state VARCHAR, "
                    "river VARCHAR, nid_storage_af DOUBLE, max_storage_af DOUBLE, normal_storage_af DOUBLE, "
                    "hazard_class VARCHAR, comid BIGINT, match_distance_m DOUBLE)")

    # Sediment-corrected area-capacity tables from Reclamation resurveys. Unlike
    # reservoir_capacity (NID design storage, never revised for sediment), each row here is a
    # measured storage-elevation pair from a bathymetric survey in a stated year, so a percent-
    # full figure computed against it is honest. Join on reservoir name / location_id.
    acap_pq = settings.parquet_dir / "reference" / "source=usbr_rise" / "reservoir_acap.parquet"
    if acap_pq.exists():
        con.execute(f"CREATE TABLE reservoir_acap AS SELECT * FROM read_parquet('{acap_pq.as_posix()}')")
    else:
        con.execute("CREATE TABLE reservoir_acap (item_id BIGINT, location_id VARCHAR, reservoir VARCHAR, "
                    "survey_year BIGINT, survey_label VARCHAR, elevation_ft DOUBLE, capacity_af DOUBLE, "
                    "area_acres DOUBLE, interp_c DOUBLE, interp_m DOUBLE, vertical_datum_note VARCHAR)")

    # Catalog tables --------------------------------------------------------------------
    con.register("_vars", reg.to_frame())
    con.execute("CREATE TABLE variables AS SELECT * FROM _vars")
    con.register("_xw", xw.to_frame())
    con.execute("CREATE TABLE crosswalk AS SELECT * FROM _xw")
    rows = []
    for src_yaml in [CATALOG_DIR / "sources.yaml", *sorted((CATALOG_DIR / "sources.d").glob("*.yaml"))]:
        if not src_yaml.exists():
            continue
        with src_yaml.open() as f:
            sd = yaml.safe_load(f) or {}
        for name, d in (sd.get("sources") or {}).items():
            rows.append({"source": name, **{k: (v if isinstance(v, str) else yaml.safe_dump(v)) for k, v in d.items()}})
    if rows:
        con.register("_src", pd.DataFrame(rows))
        con.execute("CREATE TABLE sources AS SELECT * FROM _src")
    links = CATALOG_DIR / "sites_manual.csv"
    if links.exists():
        con.execute(f"CREATE TABLE site_links_manual AS SELECT * FROM read_csv_auto('{links.as_posix()}')")

    # Data dictionary export -------------------------------------------------------------
    DOCS_DIR.mkdir(exist_ok=True)
    reg.to_frame().to_csv(DOCS_DIR / "data_dictionary_variables.csv", index=False)
    xw.to_frame().to_csv(DOCS_DIR / "data_dictionary_crosswalk.csv", index=False)
    _write_dictionary_md(reg, xw, con, has_ts)
    con.close()
    return dbp


def _write_dictionary_md(reg: VariableRegistry, xw: Crosswalk, con: duckdb.DuckDBPyConnection, has_ts: bool) -> None:
    lines = ["# Data dictionary", "", "Generated by `nmwater catalog build`.", "", "## Canonical variables", ""]
    lines.append("| variable | label | unit | kind | medium | definition |")
    lines.append("|---|---|---|---|---|---|")
    for v in reg.vars.values():
        lines.append(f"| `{v.name}` | {v.label} | {v.unit} | {v.kind} | {v.medium} | {v.definition} |")
    lines += ["", "## Crosswalk (source field -> canonical variable)", ""]
    lines.append("| source | source_param | source_name | source_unit | variable | factor | equivalence | caveat |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for e in xw.entries.values():
        lines.append(
            f"| {e.source} | `{e.source_param}` | {e.source_name} | {e.source_unit} | `{e.variable}` | {e.factor} | {e.equivalence} | {e.caveat} |"
        )
    if has_ts:
        lines += ["", "## Coverage", ""]
        df = con.execute(
            "SELECT source, variable, MIN(year) AS first_year, MAX(year) AS last_year, SUM(n_obs) AS n_obs, MAX(n_sites) AS max_sites FROM coverage GROUP BY ALL ORDER BY source, variable"
        ).fetchdf()
        lines.append("| source | variable | first_year | last_year | n_obs | max_sites |")
        lines.append("|---|---|---|---|---|---|")
        for r in df.itertuples(index=False):
            lines.append(f"| {r.source} | `{r.variable}` | {r.first_year} | {r.last_year} | {int(r.n_obs):,} | {r.max_sites} |")
    (DOCS_DIR / "data_dictionary.md").write_text("\n".join(lines) + "\n")
