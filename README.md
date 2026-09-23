# nmwater — tools for building a New Mexico hydrologic data archive

Software that downloads, normalizes, and catalogs New Mexico's water record from 39 federal,
state, tribal, and research sources, and assembles it into a local archive you can query.

This repository contains no data. It contains the pipeline that fetches it: a source module per
provider, a canonical variable catalog, a crosswalk that reconciles how forty agencies name the
same measurements, and a command-line tool that drives the whole thing. Running it produces the
archive on your own disk, which is hundreds of gigabytes and is deliberately not committed here.

What it collects: streamflow, reservoir operations, snowpack, precipitation and weather,
groundwater levels, evapotranspiration, water use, water quality, and drought indices, for New
Mexico and for the upstream and downstream gauges that define what enters and leaves the state.

The goal is a systems view. Sources, sinks, storage, and fluxes land in one table with consistent
units and explicit semantics, so a question about what the whole state's water did in a given
year can be answered across agency boundaries rather than one agency at a time.

- **Quickstart** below gets you from clone to a first query.
- **[docs/usage.md](docs/usage.md)** is the full command and workflow reference.
- **[docs/data-model.md](docs/data-model.md)** explains the schema, units, and the crosswalk.
- **[docs/interpretation.md](docs/interpretation.md)** is what the schema cannot enforce: which
  comparisons are valid, which are category errors, and how to tell from inside the catalog.
- **[docs/sources.md](docs/sources.md)** lists every source with period of record and caveats.
- **[docs/adding-a-source.md](docs/adding-a-source.md)** is the guide to writing a new one.
- **[docs/TODO.md](docs/TODO.md)** is the open follow-up list: known data-quality problems,
  incomplete pulls, access requests, and tooling gaps.

## Quickstart

```bash
git clone https://github.com/New-Mexico-Water/nmwater.git
cd nmwater
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

The code is licensed under the **GNU General Public License, version 3 or later**. See
[LICENSE](LICENSE) for the full text and [NOTICE](NOTICE) for the copyright statement.

The data are a separate matter, and the GPL does not cover them. Most federal sources are public
domain, PRISM is copyright Oregon State University and requires attribution to the PRISM Climate
Group, Synoptic restricts redistribution of some member networks, and several state and research
datasets carry their own citation requests. Every source's terms, citation, and caveats are
recorded in `catalog/sources.yaml` and `catalog/sources.d/` and exported into
`docs/data_dictionary.md`. Read them before republishing data obtained with these tools.
