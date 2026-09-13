# Usage

Every command is `uv run nmwater <command>`. Sources are named by their module name; run
`nmwater list` to see them. Most commands accept `all` in place of a source name.

## The normal cycle

```bash
nmwater discover <source>     # find the source's sites, write the sites table
nmwater fetch <source>        # download raw, normalize, write observations
nmwater catalog build         # assemble the DuckDB catalog over everything fetched
nmwater report                # coverage, landmark checks, hygiene warnings
```

`discover` must run before `fetch` for sources that iterate over a site list. Sources that pull
statewide files (USGS daily values, nClimDiv, the drought monitor) do not need it, but running
it anyway costs one request and fills in site metadata.

## Commands

### list

```bash
nmwater list
```

Prints every registered source with its agency, the tokens it needs, whether it is enabled in
`config/sources.yaml`, and a one-line description. Missing tokens are shown in red; those
sources skip with a message instead of failing.

### discover

```bash
nmwater discover usgs
nmwater discover all
```

Writes `data/parquet/sites/source=<name>/sites.parquet`. Site rows carry a stable `site_uid` of
the form `<source>:<native id>`, coordinates, type, agency, HUC, basin, and the provider's own
metadata as JSON in `raw_metadata`. Some sources also write reference tables here, such as the
USGS series catalogs that record period of record per site and parameter.

### fetch

```bash
nmwater fetch nrcs                          # full backfill
nmwater fetch nrcs --since 2026-09-01       # only new data
nmwater fetch usgs --kind continuous        # one data kind
nmwater fetch codwr --site LAJCAPCO         # specific native site ids
nmwater fetch wqp --limit 3                 # first 3 units of work, for testing
nmwater fetch prism --refresh               # ignore the archive, re-download
```

| Option | Meaning |
|---|---|
| `--since YYYY-MM-DD` | Pull only data after this date. Sources translate it into their own filter. |
| `--until YYYY-MM-DD` | Stop at this date. Paired with `--since` it names a closed span to fetch. |
| `--kind K` | Restrict to a data kind the source supports; repeatable. See the table below. |
| `--site ID` | Restrict to native site ids; repeatable. |
| `--limit N` | Cap the units of work. Meaning is per source: sites, pages, or chunks. Use for smoke tests. |
| `--refresh` | Ignore archived responses and re-request. Needed when a provider revises published data. |
| `--data-dir PATH` | Write somewhere other than `./data`. |
| `-v` | Debug logging, and re-raise exceptions instead of summarizing them. |

Data kinds by source, where a source has more than one:

| Source | Kinds |
|---|---|
| usgs | `sites`, `catalog`, `daily`, `gwlevels`, `peaks`, `measurements`, `continuous` |
| usace_cwms | `sites`, `catalog`, `data` |
| nrcs | `sites`, `daily`, `hourly`, `periodic`, `forecasts` |
| ibwc | `sites`, `daily`, `bulletin` |
| ose_reports | `report`, `rgcc`, `wateruse` |
| ose_arcgis | `layers`, `pod_levels` |
| ckan | `catalog`, `resource` |
| edi | `packages`, `metadata`, `data` |
| synoptic | `networks`, `sites`, `data` |
| usbr_albuq | `daily`, `monthly`, `raw` |

### The 15-minute archive

`usgs --kind continuous` is the sub-daily record. The full public span starts 2007-10-01 and is
250 to 500 million rows, which is thousands of requests against an hourly rate limit, so it is
opt-in and it defaults to **the most recent year**:

```bash
nmwater fetch usgs --kind continuous                              # last year, every site
nmwater fetch usgs --kind continuous --since 2015-01-01           # 2015 to today
nmwater fetch usgs --kind continuous --since 2011-01-01 --until 2011-12-31   # one past year
nmwater fetch usgs --kind continuous --site 08313000 --since 2007-10-01      # one gauge, all of it
```

Change the default horizon with `continuous_years` in `config/sources.yaml`.

The two date options mean different things on purpose. `--since` on its own means catch up, so
each series resumes from whatever was last fetched rather than re-walking ground already
covered. `--since` with `--until` names a span you want regardless of what came before, which is
how you go back for a flood year after the fact. Either way the ledger keys on the exact
request, so re-asking for a window you already have costs nothing and downloads nothing.

Pulling one year for one gauge is about 100,000 rows in three requests, so a targeted historical
question is cheap. Pulling everything for every site is the thing worth avoiding until you need
it; see [resume_needed.md](resume_needed.md).

### reprocess

```bash
nmwater reprocess usgs                 # re-parse everything in the raw archive
nmwater reprocess usgs --kind daily    # one kind only
nmwater reprocess usgs --replace       # delete this source's observations first
```

Re-runs normalization from the archived responses with no network access. Use it after fixing a
parser or changing the crosswalk. `--replace` is the honest option when a parser bug produced
wrong rows, since it clears the source's partitions before rewriting.

### compact

```bash
nmwater compact            # every source
nmwater compact usgs       # one source
```

Each fetch writes its own part files. Compact merges them per partition and drops duplicate
observations, keeping the row from the newest ingest run. Run it after a large backfill and
before `catalog build`.

### catalog

```bash
nmwater catalog check      # validate variables.yaml and the crosswalk
nmwater catalog build      # build data/duckdb/nmwater.duckdb and the data dictionary
```

`check` is fast and worth running after editing either catalog file. It reports variables whose
units are undeclared and crosswalk rows pointing at variables that do not exist.

`build` creates the DuckDB catalog: a `sites` table with HUC and basin assigned, an
`observations` view over the Parquet partitions, `site_variables` with period of record and row
counts, `coverage` by source and year, `site_links` connecting the same physical station across
agencies, and the `variables`, `crosswalk`, and `sources` tables. It also exports
`docs/data_dictionary.md` and the two CSV dictionaries.

### status

```bash
nmwater status
nmwater status usgs
```

Reads the ledger: requests, bytes, rows, and first and last fetch time per source and status,
plus total bytes on disk and the last fifteen runs. This is how you tell whether a source
finished, partially failed, or was never started.

### report

```bash
nmwater report
```

Writes and prints `docs/qa/report.md`: coverage per source, site-count checks against expected
inventories, landmark record checks (Embudo streamflow from 1889, Elephant Butte storage from
1915, Frisco Divide snowpack from 1971, and others), and hygiene warnings for duplicate keys,
missing coordinates, sites without a HUC, unknown variables, and negative values in quantities
that cannot be negative. Requires `catalog build` first.

### Querying by municipality or tract

`nmwater fetch tiger` downloads Census boundaries and `catalog build` joins every located site
against them, producing `site_regions`.

```sql
SELECT r.region_name AS place, count(*) FILTER (WHERE s.site_type = 'well') AS wells
FROM site_regions r JOIN sites s USING (site_uid)
WHERE r.region_type = 'place' GROUP BY 1 ORDER BY wells DESC;
```

`region_id` is the Census GEOID, so it joins directly to American Community Survey population and
housing tables. Read [interpretation.md](interpretation.md) first: this geography answers
demand-side questions and misleads on supply-side ones.

### query

```bash
nmwater query "SELECT count(*) FROM observations"
```

Runs SQL against the DuckDB catalog in read-only mode and prints the result. For anything
larger than a quick look, open `data/duckdb/nmwater.duckdb` directly in DuckDB, Python, or R.

## Workflows

### First full backfill

The `justfile` groups sources into phases by value and by risk. USGS legacy endpoints retire in
February 2027, so the federal backbone is worth pulling first.

```bash
just phase1     # USGS, Reclamation, USACE, NRCS, NOAA networks, Water Quality Portal
just phase2     # NM state agencies, neighbor states, research networks
just phase3     # gridded climate, drought, water use, reference layers
just catalog    # compact, then build
```

Phases are independent and restartable. Running them in separate terminals is fine; the ledger
uses SQLite in WAL mode and tolerates concurrent writers.

### Keeping current

```bash
just update 2026-09-01          # every source, only data after that date
nmwater fetch usgs --since 2026-09-01
```

Incremental pulls reuse the ledger to skip windows already completed. For sources whose
providers revise published values (PRISM revises for six months, USGS promotes provisional data
to approved), add `--refresh` periodically for the affected window.

### Resuming after failures

Nothing needs to be tracked by hand. A failed request is recorded with `status = error`, and a
later run retries exactly those. Three situations are normal:

- **Provider quotas.** Colorado DWR enforces a daily data limit; re-run on later days.
- **Rate limits.** The USGS API allows 1,000 requests per hour and the fetcher paces itself, but
  a large job still takes hours.
- **Transient server errors.** USACE returns 408 on long windows; the fetcher halves the window
  and retries automatically.

See [resume_needed.md](resume_needed.md) for the sources that need more than one run.

### Testing a change safely

```bash
nmwater fetch <source> --limit 2 -v         # small live pull, verbose
nmwater reprocess <source> --replace        # rebuild from the archive after a parser fix
uv run pytest -q                            # offline unit tests
```

Because the raw archive is immutable and complete, a parser can be rewritten and replayed
without touching the network.

## Configuration

`config/scope.yaml` defines the geography: the New Mexico bounding box, a buffer that catches
gauges just over the state line, the HUC regions that touch the state, and an explicit
allow-list of out-of-state sites that define basin inflows and outflows (Rio Grande headwaters
in Colorado, the El Paso and Fort Quitman gauges in Texas, the Gila into Arizona, the Canadian
into Oklahoma).

`config/sources.yaml` holds per-source settings: `concurrency`, `min_interval_s`, `timeout_s`,
`max_retries`, `base_url`, `verify_ssl`, and arbitrary source-specific options read through
`self.opt()`. The `defaults` block applies to anything not named.

Credentials live in `.env`, never in config. `.env.example` lists each one with the URL where
you request it. All are optional; sources needing a missing token skip with a clear message.

## Where things land

```
data/raw/<source>/<kind>/<hash>.<ext>.gz    every response as received, plus a .meta.json
data/parquet/timeseries/source=/variable=/year=/   observations
data/parquet/sites/source=/sites.parquet    site metadata
data/parquet/{waterquality,wateruse,reference,forecasts}/   non-timeseries tables
data/grids/<dataset>/                       NetCDF clipped to New Mexico
data/duckdb/nmwater.duckdb                  the catalog
data/ledger.sqlite                          every request and its outcome
```

`data/` is gitignored in full. It is rebuildable from the code and the ledger, and it gets
large.
