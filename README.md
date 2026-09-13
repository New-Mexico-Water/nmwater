# nmwater — New Mexico hydrologic data archive

A local, queryable archive of New Mexico's water record and of the basins that feed and drain
it: streamflow, reservoir operations, snowpack, precipitation and weather, groundwater levels,
evapotranspiration, water use, water quality, and drought indices, pulled from 39 federal,
state, tribal, and research sources and normalized into one schema.

The point is a systems view. Sources, sinks, storage, and fluxes end up in a single table with
consistent units and explicit semantics, so you can ask what the whole state's water did in a
given year and get an answer that spans agencies.

- **Quickstart** below gets you from clone to a first query.
- **[docs/usage.md](docs/usage.md)** is the full command and workflow reference.
- **[docs/data-model.md](docs/data-model.md)** explains the schema, units, and the crosswalk.
- **[docs/sources.md](docs/sources.md)** lists every source with period of record and caveats.
- **[docs/adding-a-source.md](docs/adding-a-source.md)** is the guide to writing a new one.

## Quickstart

```bash
git clone https://github.com/deserat/water_newmexico.git
cd water_newmexico
uv sync --all-extras
```

Copy the environment template and fill in what you have. Everything runs without any key; the
sources that need one are skipped with a message naming it.

```bash
cp .env.example .env
$EDITOR .env          # USGS_API_KEY and NMWATER_CONTACT are the two worth setting first
```

See what is registered, then pull one small source end to end:

```bash
uv run nmwater list                       # sources, agencies, token status
uv run nmwater discover usbr_hydrodata    # find its sites
uv run nmwater fetch usbr_hydrodata       # download, normalize, store (~5 min, ~300 MB)
uv run nmwater catalog build              # build the DuckDB catalog
```

Ask it something. Elephant Butte Reservoir storage has been recorded since March 1915:

```bash
uv run nmwater query "
  SELECT date_trunc('year', datetime_utc) AS year,
         round(avg(value)) AS mean_storage_af
  FROM observations
  WHERE site_uid = 'usbr_hydrodata:1119' AND variable = 'reservoir_storage'
  GROUP BY 1 ORDER BY 1 LIMIT 10"
```

Then widen the net. A full pull of everything is hundreds of gigabytes and several days of
wall time, so start with the phases:

```bash
just phase1     # federal station backbone (USGS, Reclamation, USACE, NRCS, NOAA)
just phase2     # New Mexico state, regional, and neighbor-state sources
just phase3     # gridded climate, drought, water use, reference layers
uv run nmwater status  # what has been fetched, how much, how many rows
uv run nmwater report  # coverage, landmark record checks, data hygiene
```

## How it works

Every HTTP response is written to `data/raw/` gzipped and recorded in a SQLite ledger before
anything parses it. That makes runs resumable, makes re-parsing possible offline
(`nmwater reprocess`), and means the provenance of every value is a file you can open.

Parsed values land in `data/parquet/timeseries/` in long format, partitioned by source,
variable, and year, under a schema designed to load into TimescaleDB unchanged. `catalog build`
assembles a DuckDB database over it with sites, coverage, cross-source site links, and the
variable and crosswalk tables.

Units and meaning are not left to chance. `catalog/variables.yaml` defines 59 canonical
variables; `catalog/crosswalk.csv` maps 417 source-native fields onto them with a conversion
factor and an equivalence flag that records whether two differently named fields are the same
measurement, the same quantity by a different method, or merely related. That is what keeps a
NRCS water-year accumulated precipitation total from being averaged together with a daily
rainfall increment.

## Requirements

Python 3.13 or newer, [uv](https://docs.astral.sh/uv/), and disk proportional to ambition:
about 6 GB for the station backbone, roughly 150-300 GB with the gridded products, and more if
you enable the deferred high-volume datasets. A `just` install is optional; the recipes are
short enough to copy.

## License and attribution

The code is MIT. The data are not uniformly free: most federal sources are public domain, PRISM
requires attribution to the PRISM Climate Group at Oregon State University, and several state
and research datasets carry their own citation requests. Every source's terms, citation, and
caveats are recorded in `catalog/sources.yaml` and `catalog/sources.d/` and exported into
`docs/data_dictionary.md`. Check them before republishing.
