# Interpreting the data

This archive makes it easy to put forty agencies' numbers in one table. That is the useful part
and the dangerous part. A SQL join will happily average a snow pillow against a satellite
estimate, add a reservoir's contents to a river's flow, or count the same gauge twice because
two agencies publish it. Nothing in the schema stops you. This page is what the schema cannot
enforce.

Each section states what the data support, what they do not, and how to tell the difference from
inside the catalog.

## Before a number goes into a report

The checks below take a minute each and catch most of the errors this archive makes easy.

1. **Which capacity?** A fill percentage needs a denominator, and `reservoir_capacity` has three.
   `normal_storage_af` for "how full is the supply"; `nid_storage_af` only for flood capacity.
   Say which one you used. At Abiquiu they differ by a factor of eight.
2. **Which vintage?** Capacity shrinks as sediment accumulates. A percentage computed against a
   1916 design figure overstates how empty a reservoir is today. See the sediment section below.
3. **Supply or demand geography?** A gauge inside a city is not measuring that city's water.
   Aggregate flows by watershed, people by place. See the first section below.
4. **Flux or stock?** Storage is summed over space, never over time. Flow is summed over time.
5. **Is it counted twice?** Check `site_links` for `same_sensor` before any statewide total.
6. **Is it provisional?** Anything from the current water year probably is. Check `qualifier`.
7. **Is it below detection?** Non-detects in water quality are not zero. Check the detection
   condition column.
8. **Does the grid share ancestry with the stations?** Agreement between gridMET and the gauges
   under it is not confirmation; it is the same data twice.


## Supply and demand are different geographies

The archive carries two partitions of space, and they do not line up.

**Watersheds** (`sites.huc8`, `sites.huc12`, `sites.basin`, from the Watershed Boundary Dataset)
organize where water comes from. Water moves downhill within them, so a mass balance is only
meaningful inside a watershed boundary.

**Administrative areas** (`site_regions`, from Census TIGER/Line: places, counties, tracts,
block groups, tribal areas, urban areas) organize who uses water, who is counted, and who is
governed. The archive also carries water-specific administrative geography in the reference
tables: 74 irrigation districts, 729 acequias, 600 public water systems, declared groundwater
basins, and compact areas.

Use administrative geography for demand-side questions. How many domestic wells are permitted
inside Rio Rancho, how does drinking-water arsenic vary across census tracts, which communities
sit above a declining aquifer, how many people are served by systems drawing from one basin.
These are questions about people, and people are counted in these units.

Do not use it for supply-side questions. A streamflow gauge inside Albuquerque city limits is
not measuring Albuquerque's water. It is measuring snowmelt from the San Juan Mountains in
Colorado, plus upstream irrigation returns, minus upstream diversions. Attributing that flow to
the place polygon it happens to sit in is a category error, and it is the specific error that
municipal-boundary analysis of hydrology invites.

Albuquerque makes the point sharply: the city sits in the Middle Rio Grande watershed but drinks
San Juan-Chama water, which originates in the Colorado River basin and arrives through a tunnel
under the continental divide. Its supply geography and its demand geography are different
watersheds.

The honest pattern is to aggregate supply by watershed, aggregate demand by administrative unit,
and treat any crossing between them as an explicit modelling assumption rather than a join.

```sql
-- Fine: demand-side counting
SELECT r.region_name, count(*) FILTER (WHERE s.site_type = 'well') AS wells
FROM site_regions r JOIN sites s USING (site_uid)
WHERE r.region_type = 'place' GROUP BY 1;

-- Wrong: this is not "Albuquerque's water"
SELECT sum(value) FROM observations o JOIN site_regions r USING (site_uid)
WHERE r.region_name = 'Albuquerque city' AND o.variable = 'discharge';
```

Two more cautions on the boundaries themselves. Census designated places are statistical
constructs with no government, drawn for counting, and 423 of New Mexico's 528 places are CDPs;
they are fine for demography and meaningless for jurisdiction. And place boundaries cover very
little of the state's area, so a site with no place is the normal case, not a gap.

## Fluxes, stocks, and states cannot be mixed

Every canonical variable declares a `kind` in `catalog/variables.yaml`, and it governs what
arithmetic is legal.

- **Flux** is a rate or an amount per interval: discharge, precipitation, diversion,
  evapotranspiration, reservoir release. Summing over time is meaningful. A year of daily mean
  discharge summed gives annual volume, once units are handled.
- **Stock** is an amount held at an instant: reservoir storage, snow water equivalent,
  accumulated precipitation. Summing over time is meaningless. Differencing consecutive values
  gives a flux, which is how snowmelt is derived from a snow pillow.
- **State** is a condition: stage, water temperature, depth to water, soil moisture. Averaging
  is sometimes meaningful, summing never is.
- **Index** is a constructed indicator: Palmer indices, standardized precipitation index, drought
  category. These have no units and no physical additivity.

The frequent mistake is treating reservoir storage as though it were inflow. Storage is a stock.
The change in storage over a period, plus releases, plus evaporation, approximates inflow, and
Reclamation's published inflow is computed that way rather than measured.

## Accumulated is not incremental

NRCS storage gauges report `PREC`, precipitation accumulated since 1 October. USGS reports
`00045`, an incremental total for the interval. Both are "precipitation" in plain speech and
both are inches. Averaging them together produces a number with no meaning, because one is a
running total that reaches twenty and the other is a daily quantity near zero.

The archive keeps them as separate canonical variables, `precip_accumulated_wy` and `precip`,
and the crosswalk marks the relationship `related_not_comparable`. To compare, difference the
accumulation within a water year, and expect small negative steps where the gauge was serviced.

## Check the equivalence flag before combining series

`catalog/crosswalk.csv` assigns every source field one of three values, and it is the fastest
way to know whether two series may be pooled.

- **identical** — same quantity, same method. Pool freely.
- **equivalent_method** — same quantity, different method. Comparable with care, and the
  differences are often the interesting part. Snow water equivalent from a pillow, a manual
  course, and a model grid are all `swe`, and they disagree systematically.
- **related_not_comparable** — do not pool. The crosswalk says why in its caveat.

```sql
SELECT source, source_param, variable, equivalence, caveat
FROM crosswalk WHERE variable = 'swe';
```

## Mirrored series are the same measurement published twice

Agencies republish each other. Reclamation's HydroData carries the Rio Grande at Otowi Bridge,
but the USGS operates that gauge; Reclamation is passing the record through. NRCS mirrors USGS
gauges, USACE mirrors SNOTEL sites, the National Weather Service publishes its own identifier
for USGS gauges.

Counting both double-counts. `site_links` records these relationships with a `link_type`:
`same_sensor` means one is republishing the other and you must pick one; `colocated` means two
instruments near each other that may genuinely differ. Before any statewide total, deduplicate.

```sql
SELECT * FROM site_links WHERE link_type = 'same_sensor' LIMIT 20;
```

## Provisional data will change

Most real-time hydrologic data are published provisionally and revised later, sometimes by a
lot, after rating curves are updated and ice effects are reviewed. The `qualifier` column carries
the provider's own flag verbatim: USGS `P` is provisional and `A` is approved; `e` is estimated;
`Ice` means the record is affected by ice.

Anything from the current water year should be assumed provisional. PRISM revises its grids for
six months. Re-fetch recent windows with `--refresh` before publishing conclusions that depend
on them.

## Gridded products are estimates, and they are not independent

Grids fill the space between stations, which is why the archive carries them. They are model or
interpolation output, not measurement.

They also share ancestry with the station data. gridMET's precipitation is interpolated from
PRISM; the University of Arizona snow product assimilates the same SNOTEL sites you already
have; SNODAS assimilates station observations too. So agreement between a grid and the stations
underneath it is not confirmation. It is partly circular.

Two consequences. Sparse regions are weakest: the Gila and the Sacramento Mountains have few
stations, so grid values there rest on extrapolation. And long-term trends are suspect, because
the station network that feeds the interpolation changed over the record. PRISM's own
documentation warns against using it for trend calculation, despite the 1895 start date.

## Resolution limits what a question can be about

Every gridded product has a smallest meaningful unit, and asking below it produces numbers that
look precise and mean nothing.

GRACE satellite gravity is the clearest case. It measures total water storage change, which no
other instrument does, but its mascons are roughly three degrees. All of New Mexico is three to
five independent values. A GRACE statement about the state is defensible; a GRACE statement
about the Middle Rio Grande valley is spatial leakage from neighbouring cells.

Similarly, the National Water Model is spatially complete but uncalibrated across much of the
arid Southwest, and it does not represent the Rio Grande below Elephant Butte, which functions
as an irrigation canal. Use it as a prior for ungauged reaches, never as an observation.

## Reservoir storage rests on a capacity table that changes

Storage is not measured. Elevation is measured, and storage is read off an elevation-capacity
curve. That curve changes when a reservoir is resurveyed, because sediment displaces water.

Elephant Butte has lost substantial capacity to sedimentation since 1915. A storage series
spanning that period is not a consistent measurement of the same thing, and two agencies
publishing storage for the same reservoir may be using tables of different vintages. This is why
the crosswalk marks reservoir storage `equivalent_method` rather than `identical`, and why the
Reclamation sedimentation surveys are archived alongside.

## "Capacity" is at least two different numbers

`reservoir_capacity`, from the National Inventory of Dams, gives every reservoir three storage
figures, and they answer different questions. `nid_storage_af` is the design or maximum flood
capacity: what the dam could hold at its highest safe pool. `normal_storage_af` is the
conservation pool: what the reservoir is actually operated to hold day to day. `max_storage_af`
is the highest level ever recorded.

For flood-control dams these diverge enormously, because the dam was built empty on purpose.
Abiquiu is rated at 1,369,000 acre-feet design capacity and normally holds around 170,000; Cochiti
is 722,000 against 50,130. A storage series showing Abiquiu at 15% of `nid_storage_af` would look
alarmingly low and be operating exactly as intended, because 15% of the design ceiling is close to
a full conservation pool. Dividing by the wrong denominator does not just shift a number, it
answers a different question: "how close to a flood emergency" versus "how full is the water
supply."

The safe default for an ordinary fill percentage is `normal_storage_af`. Use `nid_storage_af`
only when the question is genuinely about flood capacity, and say so.

### Capacity figures do not track sediment, and the archive can prove it

Does the capacity account for sediment fill? Mostly no. The National Inventory of Dams records
whatever the dam's owner or state regulator last reported; it does not systematically update for
sedimentation, and it does not say which survey a figure came from. `nid_storage_af` is usually
the original design value. `normal_storage_af` may or may not reflect a resurvey, depending on the
owner.

The archive's own storage record shows what sediment has done at Elephant Butte. The maximum
storage ever recorded in each decade:

| Decade | Maximum storage, acre-feet |
|---|---|
| 1920s | 2,215,676 |
| 1940s | 2,302,800 |
| 1980s | 2,118,100 |
| 1990s | 2,049,300 |
| 2000s | 1,739,255 |

The reservoir filled to the same spillway in the 1940s and the 1980s, and held roughly 185,000
acre-feet less the second time. The 1916 design figure NID carries, 2,593,255, has never been
observed. NID's normal-storage figure of 2,065,010 is close to the 1990s full-pool maximum, which
suggests it reflects a later resurvey, but NID does not say so and that is an inference.

Three consequences for anything you publish:

- A fill percentage against the design figure understates fullness for every old reservoir, and
  by more each decade.
- A storage series that spans a resurvey is not a consistent measurement. When Reclamation
  adopts a new elevation-capacity table, the same lake level maps to a different volume, so a
  step in the series may be a table change rather than water.
- Where a resurveyed capacity matters, the authority is Reclamation's own sedimentation surveys,
  which the archive indexes: 78 survey reports and area-capacity tables for New Mexico
  reservoirs sit in the `usbr_rise` catalog items (Heron 2010, El Vado 2007, Ute 1992, Nambe
  Falls 2013, Avalon 2023, Lake Sumner 2013, and others). They are documents, not yet parsed
  numbers; see `docs/TODO.md`.

The practical rule: report the capacity figure and its vintage next to any percentage, and prefer
`max(value)` from the storage record itself as a sanity bound. A reservoir cannot be 20% full of
a capacity it has exceeded in living memory.

## Vertical datums differ

Groundwater and reservoir elevations are reported against a datum, and New Mexico has records in
both NGVD29 and NAVD88, which differ by roughly a metre. The difference is larger than most of
the trends people want to detect.

The crosswalk records the datum per source field in its caveat. Depth to water below land
surface (`gw_depth_to_water`) avoids the problem entirely and is the safer variable for trend
work; elevation (`gw_level_elevation`) is what you need for a potentiometric surface, and there
you must reconcile datums first.

## Closed basins do not drain anywhere

Several New Mexico basins have no outlet: the Estancia, Tularosa, Jornada del Muerto, the Plains
of San Agustin, and the bootheel valleys. Water entering them leaves by evaporation and
infiltration alone.

A drainage-based water balance that assumes outflow will be wrong there. The Watershed Boundary
Dataset marks non-contributing area, and it matters: a HUC-based calculation that ignores it
invents a river that does not exist.

## Forecasts are not observations

NRCS water-supply forecasts and National Weather Service river forecasts live in the `forecasts`
table, deliberately separate from `observations`. They are predictions, usually with an
exceedance probability attached, and mixing them into a historical series contaminates it.

## Water quality carries censored values

Laboratory results below a detection limit are not zero and not missing. The Water Quality
Portal and NMED both record a detection condition alongside the value, and the archive keeps
those columns. Treating a non-detect as zero biases means downward; dropping non-detects biases
them upward. Handle censoring explicitly.

Also watch units and fractions: the same analyte appears as total and dissolved, in mg/L and
ug/L, and nitrate appears both as nitrate and as nitrate-as-nitrogen, which differ by a factor
of about 4.4.

## Absence of data is not absence of water

Station networks are biased toward places that are accessible, funded, and contested. Gauges
cluster on rivers with water rights to administer, wells cluster where someone drilled. Sparse
coverage in the Gila or on tribal lands reflects monitoring history, not hydrology.

The `coverage` table shows what exists by source, variable, and year. Consult it before reading
a thin map as a dry landscape.

## Quick checks the catalog supports

```sql
-- what does this variable actually mean, and in what unit
SELECT * FROM variables WHERE variable = 'reservoir_storage';

-- which sources feed it, by what method, with what caveats
SELECT source, source_param, equivalence, caveat FROM crosswalk WHERE variable = 'reservoir_storage';

-- period of record and row counts for a site
SELECT * FROM site_variables WHERE site_uid = 'usgs:08313000';

-- is this site a republication of another agency's gauge
SELECT * FROM site_links WHERE site_uid_a = 'usbr_hydrodata:1095' OR site_uid_b = 'usbr_hydrodata:1095';

-- provisional share of recent data
SELECT qualifier, count(*) FROM observations
WHERE variable = 'discharge' AND datetime_utc > '2026-01-01' GROUP BY 1 ORDER BY 2 DESC;
```

And run `nmwater report`, which checks landmark records, flags duplicate keys, negative values in
quantities that cannot be negative, sites without coordinates, and variables appearing in data
that the catalog does not define.
