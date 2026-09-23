# Reservoir reports: method, columns and caveats

Annual storage, fill, flood-pool use, inflow and release for 24 New Mexico reservoirs, from
1915 where the record allows. **Start at the [reservoir index](reservoirs/index.md)**, which
lists every reservoir with its dam type, purpose, how it is operated, and a link to its own notes
file. This page covers what is common to all of them.

```
just reports
```

runs `scripts/elephant_butte_fill.py` (the curated Elephant Butte report) and then
`scripts/reservoir_fill.py` (everything else, driven by `catalog/reservoirs.yaml`). Both read only
the DuckDB catalog and make no network calls.

| Output | What it holds |
|---|---|
| `reports/reservoir_annual_fill.csv` | every reservoir-year, all 24 reservoirs |
| `reports/reservoirs/<key>_annual_fill.csv` | one per reservoir |
| `reports/reservoir_capacity_eras.csv` | each capacity-table era, with capacity per pool and confidence |
| `reports/reservoir_pools.csv` | every pool definition and where it came from |
| `docs/reports/reservoirs/<key>.md` | one notes file per reservoir, generated |
| `docs/reports/reservoirs/index.md` | the index |

The notes files are generated. To change the text in them, edit the reservoir's `summary`,
`uses` or `cautions` in `catalog/reservoirs.yaml` and rerun; edits to the markdown are overwritten.

## Reading these numbers depends on what kind of dam it is

Every reservoir carries a `role`, and the role changes what "full" means.

| Role | Examples | Full pool | How to read it |
|---|---|---|---|
| storage | Heron, El Vado, Elephant Butte, Caballo, Navajo | spillway crest or top of conservation | Percent full is the headline. It measures water banked for later use. |
| flood control | Cochiti, Abiquiu, Conchas, Santa Rosa, Sumner, Brantley | top of conservation | The dam normally holds a conservation or recreation pool and keeps a much larger flood pool empty. Percent full describes the conservation pool; above 100% is flood water being held. `days_flood_storage` and `peak_flood_pct` carry the flood story. |
| dry flood control | Jemez Canyon, Galisteo, Diamond A, Rocky | top of flood control | Empty by design. Any storage is a flood. Small percentages are normal; peak storage and days impounding are the signal. |
| diversion | Avalon | spillway crest | A canal forebay, drawn down routinely. Low storage reflects operations, not drought. |
| municipal | McClure, Nichols, Lake Maloya | owner-reported normal storage | City supply reservoirs, small, often near full. |

This matters for the systems picture. A flood-control reservoir's storage is not an idle number:
it records snowmelt timing, flood peaks and the operator's decisions about releases. At Cochiti
the flood-pool record shows the mid-1980s wet cycle holding water above the recreation pool on
267, 276 and 311 days in 1985, 1986 and 1987, and these are the columns where managed spring
pulses for the bosque would appear. The inflow and release columns, for the reservoirs that have
them, are some of the most useful single numbers in the archive for the state of each river.

## Columns

| Column | Meaning |
|---|---|
| `reservoir`, `role`, `year` | registry key, role (above), calendar year |
| `n_days` | days with storage that year; the first and latest years are usually partial |
| `capacity_table`, `capacity_confidence` | which capacity applied that year and how well it is known (below) |
| `full_pool_af` | the denominator for percentages, as in force that year |
| `peak_af`, `mean_af`, `low_af`, `peak_date` | daily storage statistics, acre-feet |
| `peak_pct`, `mean_pct`, `low_pct` | those as a percent of `full_pool_af` |
| `flood_pool_af`, `peak_flood_pct` | capacity to the top of flood control, and peak storage as a share of it |
| `days_flood_storage` | flood-control dams: days above the conservation pool by more than ordinary fluctuation; dry dams: days holding any flood |
| `entitlement_af`, `peak_entitlement_pct` | Santa Rosa only: the annual irrigation entitlement and peak storage against it |
| `inflow_af`, `release_af` | annual volumes, only for years with at least 330 days of data |
| `peak_inflow_cfs`, `peak_release_cfs` and their dates | highest daily mean flows |
| `n_days_inflow`, `n_days_release` | days of flow data that year |

Years are calendar years, not water years. Flow volumes are sums of daily mean cfs times
1.98347 acre-feet per cfs-day. Computed inflow is a mass balance and is negative on some days;
those days are kept, so the annual sum is the net volume.

## Where each number comes from

**Storage and elevation** are the operators' own daily values: Reclamation's HydroData, the Corps'
CWMS, and USGS. Where a reservoir has several records they are spliced day by day in a fixed
priority order, listed in each notes file with the agreement between sources where they overlap.
Sub-daily series are averaged to local calendar days after isolated spikes are removed: a reading
far from both neighbours on the same side, which a reservoir cannot do in an hour. The Corps'
Galisteo series has a single hourly reading of 72,874 acre-feet between zeros that would
otherwise pass for a flood.

**Release** comes first from the USGS gauge just below each dam, where one exists, because those
records are long and continuous (Elephant Butte from 1916, Sumner from 1912, El Vado from 1935).
They measure what leaves the reservoir plus minor local inflow. The operator's reported release
fills gaps.

**Capacity** comes from the operator's current elevation-to-storage table, fetched from the
Corps' CWMS Data API (`reservoir_ratings`). CWMS carries the Corps' own tables and mirrors
Reclamation's, the Interstate Stream Commission's and USGS's. Each table is validated by looking
up every day's reported elevation after the table's effective date and comparing with reported
storage. All 18 validated tables reproduce it to within a few acre-feet, most to zero.

**Pools** come from CWMS location levels (`reservoir_levels`): top of conservation, top of flood
control, spillway crest, top of dam, entitlements. A pool is an elevation; its capacity is looked
up in the table. Where the operator publishes a pool's capacity under successive tables or
entitlements, the published figure for each date is used instead (`published_pool`). Where no pool
is published, the registry gives a full-pool elevation with the evidence for it, usually a sharp
cliff in how often the lake has stood at each level.

**Six reservoirs have no validated table**: Ute, Costilla, McClure, Nichols, Bluewater and Maloya.
Their storage is still the operator's, but their percentages are against the owner-reported normal
storage in the National Inventory of Dams, which is not corrected for sediment and which observed
storage can exceed. They are flagged `owner_reported`.

## Capacity history

The current table only describes the reservoir as it is now. Every reservoir here is filling
with sediment, and operators replace their tables after resurveys, so a percentage for 1985 needs
the 1985 capacity.

1. **Departure.** For each day with both elevation and storage, departure is reported storage
   minus the current table's storage at that elevation. Zero means the current table was in force.
   A steady positive departure means an older, larger table was.
2. **Eras.** Departure varies with elevation, because it is the sediment volume below that level.
   So each year is compared with the previous one only over the elevations both occupied, and a
   new era opens when the departure there moves by more than three times the record's own noise
   (at least 0.1% of the pool). If two years share no elevation band, their medians are compared
   directly. Eras shorter than 400 days merge into the one before.
3. **Capacity per era.** For each pool, the era's capacity is the current table's capacity at the
   pool elevation plus the era's departure there: measured locally if the lake stood within 2 ft
   of the pool during that era, otherwise extrapolated from the nearest 20 ft it did reach.
4. **Monotone.** Capacity at a fixed elevation can only fall. A weighted pool-adjacent-violators
   fit enforces that per pool, with measured capacities pinned and the rest weighted by how close
   the lake got to the pool.
5. **Published pools override.** Where the operator publishes dated pool capacities, those are
   used for percentages from their dates.

Elephant Butte uses 14 hand-reviewed adoption dates instead of detection. The general engine
reproduces the curated report's capacities to within 0.12% in every year, which is the main check
that it works.

## Confidence

`capacity_confidence` describes `full_pool_af` only. Storage and flow values are the operators'
throughout.

| Level | Means |
|---|---|
| `published_pool` | the operator's published capacity for that pool and date |
| `current_table` | reported storage reproduces from the validated current table |
| `high` | an older table, but the lake reached the pool elevation or came within 10 ft |
| `medium` | within 30 ft |
| `low` | farther, or the monotone fit had to move the estimate more than 2% |
| `before_elevation_record` | storage exists but elevation does not yet; the earliest derived capacity is used |
| `current_table_unverified` | a table exists but no paired record to validate it |
| `owner_reported` | no usable table; the dam registry's normal storage |

## What changed from the first version of these reports

- **Heron is now included.** Reclamation's 2010 survey table in RISE stops at 7,102 ft, below the
  operating range. The complete operator table in CWMS reproduces Heron's storage exactly.
- **El Vado's datum discrepancy is resolved.** The earlier version used Reclamation's 2007 survey
  and needed an unexplained 1.45 ft shift. Operations switched to a table effective 2021, which
  reproduces storage exactly with no shift.
- **Brantley is measured against its conservation pool**, about 43,000 acre-feet, not its spillway.
  Against the spillway it looked far emptier than it is.
- **Lake Sumner is treated as a flood-control reservoir** and measured against the conservation
  pool the Corps publishes, lowered in 2012 for dam maintenance.
- **Capacity comes from the operators' current tables** rather than Reclamation's sedimentation
  surveys. The survey tables remain in `reservoir_acap` as corroboration.

## Known weaknesses

- **Pre-2012 Lake Sumner** is measured against the reduced 2012 pool because the earlier pool is
  not published, so those years overstate fullness and flood storage.
- **El Vado 1979 to 2009** shows negative departures, meaning the old tables gave less storage than
  today's at the same elevation. Sediment cannot do that; an elevation datum change in the older
  record probably can. Those eras are flagged `low` and their capacity is pinned to the current
  value between two validated eras.
- **Jemez Canyon** held a permanent pool in its early decades and has run dry more recently, so its
  flood-storage counts before then include ordinary storage.
- **Eagle Nest** has not risen above about 62% of full pool since elevation reporting began in 2013,
  though it routinely held far more before. Whether that is drought or an operating restriction is
  unconfirmed.
- **Full-pool elevations detected from the record** (Heron, El Vado, Nambe Falls, Elephant Butte,
  Caballo, Avalon) are evidence-based but not sourced from a published pool definition.
- **Conchas has no release series**: its releases go mostly into a canal whose gauge record ended
  in 1992.
- **Calendar years** are used throughout. Water years (October to September) would suit flow
  volumes better and are a planned option.
