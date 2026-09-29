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

### purge-missing-codes

```bash
nmwater purge-missing-codes --dry-run    # count provider no-data markers in stored data
nmwater purge-missing-codes              # remove them; rebuild the catalog afterwards
```

New data has them dropped at ingest. This cleans older data, or applies a code you have just added
to `catalog/variables.yaml`. See "Values are guarded" in [data-model.md](data-model.md).

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

### report-rivers

Builds a streamflow page for every river with gauges: weekly mean cfs per watershed (HUC8)
segment for the whole record, with segment toggles, time-range presets and dates, and
weekly/monthly/yearly averaging. Output goes to `dist/rivers/`, which is not in git (it becomes the
website later):

```
dist/rivers/index.html                      list of rivers, searchable
dist/rivers/manifest.json                   the same list, machine-readable
dist/rivers/assets/                         shared styles and chart code for every page
dist/rivers/<river>/index.html              the river page (tabs, below)
dist/rivers/<river>/data.js                 the page's chart data
dist/rivers/<river>/all_weeks_by_segment.csv
dist/rivers/<river>/last_52_weeks_by_segment.csv, last_52_weeks_by_gauge.csv, copy_agreement.csv
dist/rivers/<river>/normal_last_52_weeks_by_segment.csv, drying_by_year.csv
dist/rivers/<river>/data_issues.json, notes.md   data gaps and disparities; method, gauges, caveats
dist/rivers/<river>/social.png              1200x630 preview image for link sharing
dist/rivers/sitemap.xml, dist/robots.txt    for search engines (the sitemap needs site.base_url)
```

Each river page has tabs (a tab can be linked as `index.html#watershed`):
- **Overview** (`river_overview.py`): the river from the headwaters down, one entry per segment with
  last week's flow and rating, dry days, towns and acequias; then Culture, Habitat, Reservoirs and
  Water users. Beside it, one map (`river_map.py`, drawn from NHDPlus, WBD, NHD and TIGER, no map
  service) that opens on New Mexico and zooms to the river, with buttons for both views and a state
  locator in the corner of the river view. Two kinds of content, always labelled:
  - counted from the archive (`river_context.py`): reservoirs (National Inventory of Dams and the
    reservoir registry), irrigation districts, public water systems, 2020 county water use, and
    acequias in the State Engineer's acequia map within 1.5 km of the river;
  - cited background: `config/river_context/<river>.yaml` (summary, habitats, culture, users,
    reservoir notes) and `config/acequia_governance.yaml` (what acequias are and how they govern
    themselves, and acequias with published evidence of their governance). Each statement has
    numbered sources listed at the bottom of the tab. A file that still has the line
    `# Not yet checked by a reviewer` is ignored until someone checks it against its sources and
    replaces that line with a `# Checked <date>: ...` note. Rivers without a checked file fall back to
    `config/river_descriptions.yaml`.
- **Flow**: weekly, monthly or yearly mean flow per segment for the whole record.
- **Compared with normal**: each week of the last year rated against the same week in 1991-2020
  (USGS WaterWatch classes), per segment and per gauge (`nmwater/reports/river_normal.py`).
- **Drying**: days per year a gauge in the segment read below 0.1 cfs; gauges dry on 90% or more of
  their days (just below a diversion) are left out and named.
- **Temperature & salinity**: water temperature from sensors (this year against other years, the
  hottest 7-day stretch each year) and specific conductance by segment and decade
  (`river_quality.py`). Shown only where the river has such data.
- **Watershed**: PRISM precipitation and temperature, SNODAS snowpack and the Drought Monitor index
  for each segment's watershed (`river_watershed.py`; averages are cached in
  `data/parquet/reference/source=river_reports/huc8_climate.parquet` and only new months and weeks
  are computed on later runs).
- **Data notes**: removed values, reviewed notes and automatic findings.

```
nmwater report-rivers                       # every river (about 4 minutes)
nmwater report-rivers --river "Pecos River" # one river; the other pages are kept
```

Which rivers, and per-river settings (for example, the Rio Grande is New Mexico segments only), are
in `config/river_reports.yaml`.

**Search and sharing** (`river_share.py`). Every page gets a description written from its data
(including last week's status), Open Graph and Twitter card tags, and JSON-LD: the page with its
breadcrumb, and the river's data as a schema.org `Dataset` with its CSV files, so dataset search can
find them. The preview image (`social.png`) is rendered with `rsvg-convert` (librsvg); without it
the image is skipped with a warning. Set `site.base_url` in `config/river_reports.yaml` to the
public address once the site has one: canonical links, `og:url`, `og:image` and the sitemap need
absolute URLs and are left out until then.

**Accessibility.** The pages aim at WCAG 2.1 AA: a skip link and landmarks, tabs with the ARIA tab
pattern and arrow keys, colours checked for contrast in light and dark themes (text 4.5:1, chart
lines and controls 3:1), every chart usable from the keyboard (arrow keys move through values,
which are read out to screen readers; Escape closes the readout) with a "Show the data as a table"
disclosure, labelled map views, reduced motion respected, and no sideways scrolling at 320 px. Rivers are identified by their NHD GNIS id, so two rivers with the
same name get separate pages labelled by basin, such as "Rio Hondo (Upper Pecos)". The method is in
the docstring of `nmwater/reports/river_flow.py`. The tree is built in a temporary folder and swapped
in at the end, so a failed run leaves the previous pages in place.

**Data gaps, disparities and bad values.** Each page has a "Data gaps and disparities" section
(also in `notes.md` and `data_issues.json`). It lists, in order:
- **removed values**: daily values left out of every number because they are known to be wrong.
  A daily mean above that water year's USGS instantaneous peak is removed automatically;
  other known errors are listed by hand in `config/river_exclusions.yaml`, with a reason;
- **reviewed notes** from `config/river_notes.yaml`, each marked verified or inferred;
- **automatic findings** from `nmwater/reports/river_issues.py` (seasonal records, gaps,
  discontinued gauges, agency copies that disagree, spikes, flat lines, zero-flow runs and more),
  recomputed on every build.

**Scheduling.** `scripts/cron/river_reports.sh` wraps the command for cron: it holds a lock so
runs never overlap, appends to `data/logs/river_reports.log`, and exits non-zero if anything failed.
With `--update` it runs `nmwater update` (which rebuilds the catalog) first, so one cron line keeps
both the data and the pages current:

```
30 5 * * *  /home/vance/projects/water_newmexico/scripts/cron/river_reports.sh --update
```

Install with `crontab -e`. The same script works as the command of a Cloud Scheduler / Cloud Run
job later.

### Querying by river

`nmwater fetch nhdplus` (after `nmwater fetch wbd`) pulls the NHDPlus v2 stream network for every
HUC8 in scope and snaps stream, canal, diversion and return-flow sites onto their nearest reach,
producing `site_reaches`.

```sql
SELECT s.site_uid, s.name, r.totdasqkm
FROM site_reaches r JOIN sites s USING (site_uid)
WHERE r.gnis_name = 'Rio Chama' ORDER BY r.totdasqkm DESC;
```

`totdasqkm` orders sites downstream to upstream by cumulative drainage area, which is a more
reliable ordering than trying to parse it out of station names. `comid` is the reach's identifier
in the national network and the join key a National Water Model integration would need.

The same fetch also pulls reservoir and lake polygons (`waterbodies`) and matches reservoir/lake
sites onto them (`site_waterbodies`), so every sensor an agency operates on a given reservoir can
be found by the reservoir's actual name rather than by guessing at station-name spelling:

```sql
SELECT site_uid, match_type, distance_m FROM site_waterbodies
WHERE gnis_name = 'Elephant Butte Reservoir' ORDER BY match_type, distance_m;
```

### Reservoir fill percentage over time

`nmwater fetch nid` pulls the National Inventory of Dams and matches each dam to a reservoir
polygon, so `reservoir_capacity.normal_storage_af` joins to `reservoir_storage` observations
through `site_waterbodies` with no name matching:

```sql
WITH cap AS (
  SELECT DISTINCT comid, normal_storage_af FROM reservoir_capacity WHERE dam_name = 'Elephant Butte Dam'
)
SELECT date_trunc('year', o.datetime_utc) AS year,
       round(100.0 * avg(o.value) / cap.normal_storage_af, 1) AS pct_of_normal_capacity
FROM observations o
JOIN site_waterbodies sw ON sw.site_uid = o.site_uid
JOIN cap ON cap.comid = sw.comid
WHERE o.variable = 'reservoir_storage'
GROUP BY 1, cap.normal_storage_af ORDER BY 1;
```

Use `normal_storage_af`, not `nid_storage_af`, for an ordinary fill percentage - see
[data-model.md](data-model.md#reservoir_capacity) for why they differ by a factor of eight at
Abiquiu.

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
nmwater update --dry-run          # show each source's start date and policy
nmwater update                    # fetch the delta everywhere, compact, rebuild the catalog
nmwater update usgs nrcs          # only these sources
nmwater update --since 2026-09-01 # one fixed start date for everything
just update                       # update, then regenerate the reservoir reports
```

`update` starts each source 30 days (`--margin-days`) before the earlier of its last successful
fetch and its newest observation, then fetches, compacts and rebuilds the catalog. The margin
re-pulls a month that providers revise after first publication: USGS promotes provisional data to
approved, NRCS and the Corps edit recent values, PRISM revises grids for six months. Compaction
keeps the newest copy of any re-pulled observation, so the overlap costs time, not duplicates.

`update` runs each fetch as a revision pass. Some sources normally treat `--since` as "catch up":
a manual `nmwater fetch usgs --kind continuous --since 2026-08-01` skips ahead to whatever the
ledger says was already fetched. Under `update` those sources (USGS 15-minute, the Corps, NRCS and
the NWS DCP archive) honour the start date exactly, so the overlap is really re-pulled.

Policies are in `config/sources.yaml` under `update:`. Reference layers (census boundaries,
NHDPlus, the dam registry, watershed boundaries) and static or blocked sources are skipped with a
stated reason; `kinds:` chooses what a delta covers, for example USGS includes the 15-minute
archive and the Corps includes its capacity tables and pool levels. Sources that have never
completed a fetch are skipped.

Two sources re-download whole files for any delta: Reclamation HydroData and NOAA GHCN-Daily
publish each series as one full-history file. Only new rows are written, but the download is the
full file. Colorado caps data per day and PRISM limits each file to two downloads a day, so a
large delta may need a second day for those.

#### The update log

Every run appends one row per source to `reports/update_log.csv`, plus a `_catalog_build` row and
a `_total` row, all sharing an `update_id`. It is committed, so it accumulates into a record of how
fast the archive grows and what keeping it current costs.

| Column | Meaning |
|---|---|
| `update_id` | UTC timestamp identifying one run |
| `source`, `policy`, `since`, `status` | what was attempted and how it ended (`ok`, `partial`, `error`, `skipped`) |
| `fetch_seconds`, `compact_seconds` | wall-clock time |
| `n_requests`, `n_cached`, `n_errors` | requests made, answered from the raw archive, failed |
| `bytes_downloaded` | bytes received from the network, from the ledger |
| `rows_fetched` | rows written by the fetch, before deduplication |
| `duplicates_removed` | rows dropped by compaction: mostly the re-pulled margin |
| `rows_before`, `rows_after`, `net_new_rows` | rows in the source's Parquet before and after |
| `raw_bytes_*`, `parquet_bytes_*`, `grid_bytes_*` | size on disk of the raw archive, Parquet and grids for the source, before and after |
| `disk_bytes_delta` | total change on disk |
| `notes` | the source's own summary, or why it was skipped |

Growth per update is `net_new_rows` and `disk_bytes_delta`; consumption is `bytes_downloaded`,
`n_requests` and the two time columns. `rows_fetched` minus `net_new_rows` is roughly what the
margin re-pulled.

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
