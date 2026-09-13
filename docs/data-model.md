# Data model

The archive these tools build holds five kinds of table: observations, sites, site links,
non-timeseries tables, and the catalog itself. The schema is deliberately boring so that it
loads into TimescaleDB later without a migration.

## observations

One row per reading. Long format, because the alternative — a column per variable — cannot
represent forty agencies measuring overlapping things at different intervals.

| Column | Type | Meaning |
|---|---|---|
| `site_uid` | string | `<source>:<native id>`, for example `usgs:08313000` |
| `variable` | string | canonical variable name from `catalog/variables.yaml` |
| `datetime_utc` | timestamp, UTC | see the time convention below |
| `utc_offset_min` | int16 | the original local offset, so local-day aggregates can be rebuilt |
| `value` | float64 | in the canonical unit for the variable |
| `unit` | string | canonical unit, denormalized for convenience |
| `interval` | string | `instant`, `15min`, `hourly`, `daily`, `monthly`, `water_year`, `irregular`, and others |
| `statistic` | string | `instantaneous`, `mean`, `max`, `min`, `total`, `accumulated`, `median`, `observed`, `sample` |
| `qualifier` | string | the provider's own flags, verbatim (USGS `A`, `P`, `e`, `Ice`; NRCS QC flags) |
| `source_param` | string | the original parameter code or element name |
| `source_unit` | string | the original unit, before conversion |
| `ingest_run_id` | string | ties the row back to a ledger run |

Partitioned on disk as `source=<s>/variable=<v>/year=<yyyy>/`. The uniqueness key is
`(site_uid, variable, datetime_utc, interval, statistic)`; `nmwater compact` enforces it,
keeping the newest ingest run.

### The time convention

For `interval` of `daily` or coarser, `datetime_utc` holds the **local calendar date at 00:00Z**
and `utc_offset_min` is null. A daily mean discharge for 3 July 1925 is stored at
`1925-07-03T00:00:00Z` regardless of the station's time zone, because that is what the number
means: a statistic over a local day.

For sub-daily intervals, `datetime_utc` is the **true UTC instant** and `utc_offset_min` records
the local offset it arrived with.

Mixing these up is the single easiest way to corrupt a multi-source archive, which is why the
rule is stated in every source module.

### Values are guarded

The store drops rows with impossible timestamps (before 1850 or beyond next year) and logs how
many. Real data contains year 990 and year 2316 typos; New Mexico's oldest genuine hydrologic
records begin in 1888.

## sites

One row per station, well, reservoir, or area.

`site_uid`, `source`, `native_id`, `name`, `lat`, `lon`, `elevation_m`, `site_type`, `agency`,
`state`, `county_fips`, `huc8`, `huc12`, `basin`, `well_depth_m`, `aquifer`,
`drainage_area_km2`, `active`, and `raw_metadata` holding the provider's full record as JSON.

`site_type` is one of `stream`, `reservoir`, `lake`, `well`, `spring`, `snow`, `met`,
`diversion`, `canal`, `return_flow`, `wwtp`, `outfall`, `grid_cell`, `area`, `other`. HUC and
basin are filled in at `catalog build` by spatial join against the Watershed Boundary Dataset
once `nmwater fetch wbd` has run.

## site_links

The same physical place shows up under different identifiers in different agencies. Otowi
Bridge is `usgs:08313000` to the USGS and `usbr_hydrodata:1095` to Reclamation, and Reclamation
is republishing the USGS record rather than measuring independently. Knowing which is which
decides whether you may average two series or must pick one.

| Column | Meaning |
|---|---|
| `site_uid_a`, `site_uid_b` | the two sites |
| `link_type` | `same_sensor`, `same_location_different_sensor`, or `colocated` |
| `evidence` | how the link was made |
| `distance_m` | for proximity links |
| `confidence` | 0 to 1 |

Links come from three places: exact identifiers found in provider metadata (most agencies
record the USGS site number when they mirror one), proximity matching within 250 m among sites
of the same kind from different sources, and hand-curated rows in `catalog/sites_manual.csv`,
which always win.

## site_regions

Which administrative areas each site falls inside, from Census TIGER/Line: place, county, county
subdivision, tract, block group, tribal area, urban area. Long format, because a site sits in
several nested regions at once and in none of the small ones most of the time.

| Column | Meaning |
|---|---|
| `site_uid` | the site |
| `region_type` | `place`, `county`, `tract`, `block_group`, `tribal_area`, `urban_area`, `county_subdivision` |
| `region_id` | Census GEOID, joins to American Community Survey tables |
| `region_name` | the name as Census publishes it |

This is the demand-side partition of space. `sites.huc8` and `sites.huc12` are the supply-side
partition. They do not line up, and conflating them is the most common way to get a confident
wrong answer from this archive. See [interpretation.md](interpretation.md).

## site_reaches and flowlines

Where `site_regions` says which administrative area a site sits in, `site_reaches` says which
reach of the actual river network it sits on. Built from NHDPlus v2 by `nmwater fetch nhdplus`,
which fetches flowlines for every HUC8 in scope and snaps stream, canal, diversion and
return-flow sites onto the nearest reach within 500 m.

| Column | Meaning |
|---|---|
| `site_uid` | the site |
| `comid` | NHDPlus common identifier for the reach - the network's primary key |
| `gnis_name` | the named stream, e.g. "Rio Grande", "Rio Chama", "Purgatoire River" |
| `streamorde` | Strahler stream order |
| `totdasqkm` | total drainage area upstream of this reach, km2 |
| `huc8` | the watershed the reach belongs to |
| `snap_distance_m` | how far the site's coordinates are from the reach; treat as a confidence signal |

`flowlines` carries the network's own attributes per COMID (length, from/to node, hydrologic
sequence, path length, divergence) without geometry; the reach geometry itself lives in
`data/grids/nhdplus/flowlines.gpkg` for GIS tools.

This is what makes "is gauge A upstream of gauge B" and "what is this river actually called"
answerable in SQL rather than by reading station names. It is also the join key a National Water
Model integration would need later - COMID is NWM v2's `feature_id` (v3 uses a revised
hydrofabric; confirm the version before joining).

```sql
-- every site on the Rio Grande, ordered downstream to upstream by drainage area
SELECT s.site_uid, s.name, r.totdasqkm, r.snap_distance_m
FROM site_reaches r JOIN sites s USING (site_uid)
WHERE r.gnis_name = 'Rio Grande' ORDER BY r.totdasqkm DESC;
```

## Non-timeseries tables

Some data are not time series and are not forced into that shape.

- `waterquality` — laboratory results from the Water Quality Portal, NMED drinking water, and
  others, keeping the provider's columns plus the characteristic, fraction, detection condition,
  and method. Core analytes with canonical variables are *also* written to observations.
- `wateruse` — withdrawal and depletion by county, basin, and category from the OSE five-year
  reports and the USGS compilations.
- `reference` — station catalogs, ArcGIS layers, dam inventories, package indexes, and the grid
  registry.
- `forecasts` — NRCS water-supply forecasts and NWPS river forecasts, which are predictions and
  must never be mixed with observations.

## The variable catalog

`catalog/variables.yaml` defines 59 canonical variables. Each carries a definition, canonical
unit, physical quantity, `kind` (`flux`, `stock`, `state`, `index`, `concentration`), `medium`
(`surface_water`, `reservoir`, `groundwater`, `snow`, `atmosphere`, `soil`, `use`, `quality`),
a sign convention where direction matters, and a comparability note.

Canonical units follow what New Mexico's own agencies use — cubic feet per second, acre-feet,
feet, inches — with Celsius for temperature and millimetres for gridded evapotranspiration. The
`units` block gives an SI factor for each, so converting the whole archive is one multiplication.
Mixing conventions inside a column is what this avoids.

`kind` is what makes a water balance possible: you may sum fluxes over time, you may difference
stocks, and you may not add the two.

## The crosswalk

`catalog/crosswalk.csv`, plus any number of `catalog/crosswalk.d/*.csv` fragments loaded after
it, map source-native fields onto canonical variables. Later rows override earlier ones with
the same `(source, source_param)` key, which is how a source module ships its own mappings
without editing the shared file.

| Column | Meaning |
|---|---|
| `source`, `source_param` | the key |
| `source_name`, `source_unit` | what the provider calls it |
| `variable` | canonical variable, or empty for deliberately unmapped |
| `factor`, `offset` | `canonical = source * factor + offset` |
| `statistic`, `interval` | defaults when the response does not say |
| `equivalence` | `identical`, `equivalent_method`, or `related_not_comparable` |
| `caveat` | free text, carried into the data dictionary |

`equivalence` is the part that earns its keep:

- **identical** — the same quantity measured the same way. USGS parameter 00060 and Reclamation
  datatype 19 are both daily mean discharge in cubic feet per second.
- **equivalent_method** — the same quantity by a different method, comparable with care. A
  NRCS snow pillow, a manual snow course, and the gridded SNODAS product all estimate snow water
  equivalent. Reservoir storage from two agencies may rest on capacity tables of different
  vintages, which matters at Elephant Butte where sedimentation has changed the relationship.
- **related_not_comparable** — do not combine. NRCS `PREC` is precipitation accumulated since
  1 October; USGS 00045 is an incremental total. Averaging them is meaningless; differencing the
  first gives you the second.

An empty `variable` records a field that was examined and deliberately not mapped, with the
reason in `caveat`. That is different from a field nobody has looked at, which shows up in the
QA report as an unmapped parameter.

## Provenance

`catalog/sources.yaml` and `catalog/sources.d/*.yaml` record, per source: agency, description,
URL, license, citation, refresh cadence, and caveats — including the blocked ones, with what
was tried and what would unblock them. This is exported into `docs/data_dictionary.md` at
catalog build and is the file to read before republishing anything.

## Loading into TimescaleDB later

The observations schema is already hypertable-shaped: a timestamp column, a small set of
dimension columns, and a float value. The intended migration is

```sql
CREATE TABLE observations (...);                          -- same columns
SELECT create_hypertable('observations', 'datetime_utc');
CREATE INDEX ON observations (site_uid, variable, datetime_utc DESC);
```

then `COPY` from the Parquet partitions. Nothing in the pipeline needs to change.
