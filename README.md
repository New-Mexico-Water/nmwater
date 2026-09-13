# nmwater — New Mexico hydrologic data archive

Tools to discover, download, normalize, and catalog the hydrologic record of New Mexico and the
basins that flow through it: streamflow, reservoirs, snowpack, precipitation and weather,
groundwater levels, evapotranspiration, water use, water quality, drought indices, and gridded
climate. The goal is a complete local archive that supports a systems view of the state's
water (sources, sinks, storage, and fluxes over time) and later publication as a website and
open datasets.

## Layout

```
config/        scope.yaml (geography, out-of-state gauges), sources.yaml (per-source settings)
catalog/       variables.yaml (canonical variables), crosswalk.csv + crosswalk.d/ (source field -> variable),
               sources.yaml + sources.d/ (provenance), sites_manual.csv (hand-curated site links)
nmwater/       package: core (http/ledger/store/geo/config), sources (one module per data provider),
               catalog (registry, crosswalk, DuckDB build), cli
data/          raw/ (every response as received, gzipped, immutable), parquet/ (normalized tables),
               grids/ (NetCDF clipped to NM), duckdb/nmwater.duckdb, ledger.sqlite   [gitignored]
docs/          data dictionary export, QA logs, notes on manual downloads
```

## Setup

```bash
uv sync --all-extras
cp .env.example .env     # add tokens: USGS_API_KEY, NOAA_NCEI_TOKEN, EARTHDATA_*, OPENET_API_KEY, SYNOPTIC_TOKEN, NASS_API_KEY
```

## Commands

```bash
uv run nmwater list                       # registered sources, token status
uv run nmwater discover usgs              # write the sites table for a source (or 'all')
uv run nmwater fetch usgs                 # backfill: archive raw, normalize, write parquet
uv run nmwater fetch usgs --since 2026-09-01     # incremental
uv run nmwater fetch usgs --kind continuous      # opt-in 15-minute data
uv run nmwater fetch usbr_hydrodata --site 1119  # restrict to native site ids
uv run nmwater reprocess usgs             # re-normalize from the raw archive, offline
uv run nmwater compact                    # merge part files, drop duplicate observations
uv run nmwater catalog check              # validate variables.yaml + crosswalk
uv run nmwater catalog build              # build data/duckdb/nmwater.duckdb + docs/data_dictionary.md
uv run nmwater status                     # ledger summary per source
uv run nmwater query "SELECT ... FROM observations"
```

## Data model

`observations` (long format, one row per reading): `site_uid`, `variable`, `datetime_utc`,
`utc_offset_min`, `value`, `unit`, `interval`, `statistic`, `qualifier`, `source_param`,
`source_unit`, `ingest_run_id`. Partitioned as
`parquet/timeseries/source=<s>/variable=<v>/year=<yyyy>/`.

`sites`: one row per station/well/reservoir/area with location, type, agency, HUC, basin, and the
provider's full metadata as JSON. `site_variables`: period of record and counts per site and variable.

Convention: for `interval` daily or coarser, `datetime_utc` holds the local calendar date at
00:00Z and `utc_offset_min` is null. Sub-daily rows carry the true UTC instant.

Canonical units are the ones New Mexico producers use (cfs, acre-feet, feet, inches) with
Celsius for temperature and millimetres for gridded ET; `catalog/variables.yaml` lists SI
factors. Every source field is mapped in `catalog/crosswalk.csv` (and `crosswalk.d/*.csv`)
with a unit factor, statistic, interval, an `equivalence` flag (`identical`,
`equivalent_method`, `related_not_comparable`), and a caveat. That table is the authoritative
answer to "are these two differently named fields the same measurement?".

## Adding a source

Create `nmwater/sources/<name>.py` with a `@register`ed subclass of `Source` implementing
`discover()`, `fetch()`, and `normalize()`; add `catalog/crosswalk.d/<name>.csv` and
`catalog/sources.d/<name>.yaml`; add a block to `config/sources.yaml` if the defaults are not
right. Use `self.get()` for every request so the response is archived and ledgered.

## Provenance and licenses

See `catalog/sources.yaml`, `catalog/sources.d/`, and the generated `docs/data_dictionary.md`.
PRISM data require attribution to the PRISM Climate Group, Oregon State University. Most
federal data are public domain; state and research-network data carry their own citation
requests recorded per source.
