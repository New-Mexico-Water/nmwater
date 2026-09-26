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

Providers also mark missing data with a number instead of leaving a gap. The store drops those
markers at ingest, using `missing_codes_default` and each variable's `missing_codes` in
`catalog/variables.yaml`: `-9999` and `-99999` everywhere, and smaller ones only where they are
impossible for that variable (`-99.9` for precipitation and wind speed, `-99.99` for stage). A
variable marked `signed: true`, such as change in storage or computed inflow, is exempt, because a
real value can equal a code. Matching uses a tolerance, since some sources store `-99.9` as
`-99.90000000000001`. `nmwater purge-missing-codes` removes markers from data stored before this
rule existed; the raw responses still hold the originals.

### Quality flags

Being negative is not the same as being wrong, so the store does not delete negative values.
Instead each variable in `catalog/variables.yaml` can declare plausibility bounds, and the catalog
classifies every observation:

| `qc_flag` | Meaning |
|---|---|
| `ok` | Within bounds, or the variable has none. |
| `near_zero` | Below `valid_min` but at or above `noise_floor`: instrument drift around zero. Kept. |
| `implausible` | Below the noise floor, or above `valid_max`. |

Two views expose this. `observations_qc` is every observation with its `qc_flag`.
`observations_clean` is the same without the `implausible` rows. Analysis should use
`observations_clean` unless it needs the raw values; the underlying `observations` view and the
Parquet files are never altered.

Bounds set today, and why:

| Variable | `valid_min` | `noise_floor` | Reason |
|---|---|---|---|
| `swe` | 0 in | -1 in | 98% of negative values from the NWS feed lie within 1 inch of zero: pillow drift |
| `snow_depth` | 0 in | -5 in | ultrasonic sensors drift; below -5 in is junk (some readings reach -739) |
| `precip` | 0 in | none | any negative value is impossible |
| `reservoir_storage` | 0 af | -5 af | rounding at an empty reservoir |

`discharge` has **no lower bound on purpose**. 3,826 negative readings at 36 state ditch and canal
gauges may be real reverse flow or backwater rather than error, and only the operator can say.
Only the `-9999` no-data code is removed from discharge.

Background: ditch flow is commonly computed from water depth through a rating curve, which cannot
sense direction and drifts when the channel changes, and ditch flows of only a few cfs make a small
offset look large. This is context from one Taos-area study, not evidence about how the State
Engineer computes its values; see [references.md](references.md#cruz-et-al-2019).

Averaging `near_zero` snow values as if they were zero biases a season low; clamp them to zero
deliberately if that is what an analysis wants, and say so.

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

**Points of diversion are not proximity-linked.** The 279,990 OSE points of diversion in `sites`
(`ose_arcgis:pod:*`) are well and diversion permit locations, not monitoring sites. Matching them by
distance added 182,070 low-confidence `colocated` pairs, mostly to water-quality and USGS wells
within 250 m, so they are excluded from that step. Exact-identifier links are unaffected.

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
return-flow sites onto the nearest reach within 500 m. OSE points of diversion are snapped only
when marked surface water (`grnd_wtr_s = S`), so a well is never labelled with a nearby river.

| Column | Meaning |
|---|---|
| `site_uid` | the site |
| `comid` | NHDPlus common identifier for the reach - the network's primary key |
| `gnis_name` | the snapped reach's own name, e.g. "Rio Grande", "Rio Chama"; null on unnamed reaches |
| `river_name` | the name to use for "which river is this on": `gnis_name`, else the first named reach downstream (an unnamed tributary or ditch is labelled with what it drains to), else, for sites with no coordinates, the hand-curated `catalog/site_rivers.csv` |
| `river_steps` | reaches walked downstream to find `river_name`; 0 = the reach's own name, null for manual rows |
| `river_method` | `snap`, `downstream` or `manual`. Manual rows carry no `comid`, and their evidence is in `site_rivers.csv` (BEMP's Rio Grande bosque sites are inferred from their names) |
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

`waterbodies` and `site_waterbodies` are the companion tables for reservoirs and lakes, also from
NHDPlus: an actual polygon per named waterbody (Elephant Butte, Navajo, Cochiti, Heron, and the
rest, each with a real surface area) rather than only the point gauges the station sources
already carry. `match_type` on `site_waterbodies` says whether a site's coordinates fell inside
the polygon (`within`) or were matched to the nearest one within 2 km (`nearest`, for dam-crest
and outlet gauges that sit just outside the digitized shoreline); `distance_m` is 0 for the
former and the actual gap for the latter.

```sql
-- every sensor associated with Elephant Butte, across every agency that operates one
SELECT site_uid, match_type, distance_m FROM site_waterbodies
WHERE gnis_name = 'Elephant Butte Reservoir' ORDER BY match_type, distance_m;
```

## reservoir_capacity

The number `reservoir_storage` observations were missing until now: how much a reservoir can
hold, from the National Inventory of Dams. Matched to a waterbody comid where a reservoir polygon
exists within 3 km of the dam, which is what makes fill percentage over time possible without
name matching.

| Column | Meaning |
|---|---|
| `nid_id`, `dam_name` | the dam's identifier and name in the national registry |
| `nid_storage_af` | design/maximum storage capacity, acre-feet - the flood-control ceiling |
| `normal_storage_af` | normal (conservation-pool) capacity, acre-feet - what the dam is operated to hold day to day |
| `max_storage_af` | maximum storage actually recorded |
| `hazard_class`, `purpose`, `year_completed` | registry attributes |
| `comid` | the matched waterbody, joining to `site_waterbodies.comid` |
| `match_distance_m` | distance from the dam point to the matched polygon |

**`nid_storage_af` and `normal_storage_af` are not interchangeable.** Flood-control dams are built
to sit mostly empty and only fill during a flood: Abiquiu is rated at 1,369,000 acre-feet design
capacity but normally holds around 170,000. Dividing storage by the wrong one answers a different
question than the one usually intended. Use `normal_storage_af` for an ordinary "how full is it"
and `nid_storage_af` only when flood capacity specifically is the question. See
[interpretation.md](interpretation.md).

```sql
-- Elephant Butte, storage as a percent of normal operating capacity, by year
WITH cap AS (
  SELECT DISTINCT comid, normal_storage_af FROM reservoir_capacity WHERE dam_name = 'Elephant Butte Dam'
)
SELECT date_trunc('year', o.datetime_utc) AS year,
       round(avg(o.value)) AS mean_storage_af,
       round(100.0 * avg(o.value) / cap.normal_storage_af, 1) AS pct_of_normal_capacity
FROM observations o
JOIN site_waterbodies sw ON sw.site_uid = o.site_uid
JOIN cap ON cap.comid = sw.comid
WHERE o.variable = 'reservoir_storage'
GROUP BY 1, cap.normal_storage_af ORDER BY 1;
```

## reservoir_acap

Reclamation's area-capacity tables, from bathymetric sedimentation resurveys. This is the
sediment-corrected capacity that `reservoir_capacity` is not: one row per elevation step,
tagged with the year the survey was flown, for seven New Mexico reservoirs.

| Column | Meaning |
|---|---|
| `reservoir`, `location_id` | reservoir name and RISE location id |
| `survey_year`, `survey_label` | vintage of the survey; the label keeps multi-year surveys ("2017 and 2019") intact |
| `elevation_ft` | water surface elevation, in the datum named by `vertical_datum_note` |
| `capacity_af` | storage capacity at that elevation, acre-feet |
| `area_acres` | water surface area at that elevation |
| `interp_c`, `interp_m` | Reclamation's nonlinear interpolation coefficients between rows |
| `vertical_datum_note` | the survey's datum statement, verbatim |

**Read `vertical_datum_note` before joining on elevation.** Elephant Butte's table is in
Reclamation Project Vertical Datum, 45.0 feet below NAVD88.

Prefer this table over `reservoir_capacity` for any percent-full figure. The National Inventory
of Dams carries design storage that is never revised for the sediment wedge; at Elephant Butte
that is 2,593,255 acre-feet against a 2017-survey capacity of 2,011,169 at the spillway crest.
See [interpretation.md](interpretation.md).

```sql
-- Elephant Butte, percent full against the 2017 sediment survey.
-- Linear interpolation between the two bracketing ACAP rows at the chosen full-pool elevation.
WITH t AS (
  SELECT elevation_ft, capacity_af FROM reservoir_acap
  WHERE reservoir = 'Elephant Butte Reservoir'
),
full_pool AS (              -- 4407 ft = spillway crest / top of conservation pool
  SELECT (SELECT max(capacity_af) FROM t WHERE elevation_ft <= 4407)
       + (4407 - (SELECT max(elevation_ft) FROM t WHERE elevation_ft <= 4407))
       * ((SELECT min(capacity_af) FROM t WHERE elevation_ft >= 4407)
          - (SELECT max(capacity_af) FROM t WHERE elevation_ft <= 4407))
       / ((SELECT min(elevation_ft) FROM t WHERE elevation_ft >= 4407)
          - (SELECT max(elevation_ft) FROM t WHERE elevation_ft <= 4407)) AS cap_af
)
SELECT o.datetime_utc::date AS day,
       round(o.value) AS storage_af,
       round(f.cap_af) AS capacity_af_2017_survey,
       round(100.0 * o.value / f.cap_af, 2) AS pct_full
FROM observations o CROSS JOIN full_pool f
WHERE o.site_uid = 'usbr_hydrodata:1119' AND o.variable = 'reservoir_storage'
ORDER BY o.datetime_utc DESC LIMIT 1;
```

## reservoir_ratings

The operators' current elevation-to-storage tables, from the Corps' CWMS Data API
(`nmwater fetch usace_cwms --kind ratings`). One row per point, at 0.01 ft resolution for most
reservoirs. CWMS holds the Corps' own tables and mirrors Reclamation's, the Interstate Stream
Commission's, USGS's and Colorado's; the agency is the suffix of the rating id.

| Column | Meaning |
|---|---|
| `location`, `rating_id` | CWMS location name and full rating id, e.g. `Cochiti.Elev;Stor.Linear.Step;USACE` |
| `rating_agency` | who maintains the table |
| `effective_date` | when this table took effect, as CWMS records it; some are nominal import dates |
| `elev_datum` | the table's vertical datum, from its parameter label, where stated |
| `elevation_ft`, `storage_af` | the table |

CWMS serves only the currently effective table per reservoir, not the tables it replaced. Earlier
capacities are recovered from the operational record; see
[reports/reservoir-fill.md](reports/reservoir-fill.md).

## reservoir_levels

Named pools and levels (`nmwater fetch usace_cwms --kind levels`): top of conservation, top of
flood control, spillway crest, top of dam, entitlements, recreation pools. Some carry dated
history, such as Cochiti's recreation-pool capacity under eight survey tables from 1973 to 2020.

| Column | Meaning |
|---|---|
| `location`, `level_id`, `level_name`, `parameter` | e.g. `Cochiti`, `Cochiti.Elev.Inst.0.Top of Flood`, `Top of Flood`, `Elev` |
| `level_date` | effective date; 1900-01-01 means as built |
| `value`, `unit` | converted to feet, acre-feet, acres or cfs |
| `value_si`, `unit_si` | as published |
| `comment` | the operator's note, often naming the survey table or entitlement year |

**Check the datum before using a level.** Santa Rosa publishes its conservation and flood pools in
NGVD29 but its table and its other levels in NAVD88. Some entries are clerical errors (one
Brantley storage level is an elevation). Compare a level's published storage with the table's
storage at its elevation before trusting it; `reports/reservoir_pools.csv` does this.

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
