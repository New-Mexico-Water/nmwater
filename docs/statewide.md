# Statewide figures

What the site means by "the state's" reservoir storage, rain, drought, temperature, snowpack and state-line flow, how each is computed, and what it deliberately
does not claim. Code: `nmwater/derived/statewide.py` (computation), `nmwater/site/statewide.py` (the bundle's `statewide/` files; schemas
`docs/site-data/v2/statewide_*.schema.json`). The website composes the wording.

## Reservoirs (`statewide/reservoirs.json`)

Membership is fixed by the registry (`catalog/reservoirs.yaml`, 24 reservoirs) and by each reservoir's `measure` (see `nmwater/site/reservoirs.py`):

| Group | Members | What is published |
|---|---|---|
| `supply` | the 8 storage reservoirs and forebays with an operator-table capacity and a real full pool: Heron, El Vado, Nambe Falls, Elephant Butte, Caballo, Eagle Nest, Avalon, Navajo | acre-feet held, capacity in force, **percent full** (the headline), change over 7/30/90/180/365 days, a normal for the date and a rating, history since 1986 |
| `flood_control` | the 6 flood-control lakes (Abiquiu, Cochiti, Conchas, Santa Rosa, Sumner, Brantley), against their conservation pools | acre-feet, percent of the conservation pool (above 100% is flood water), changes |
| `dry` | the 4 dry flood-control dams | acre-feet only (empty by design) |
| `owner_reported` | Ute, Costilla, McClure, Nichols, Bluewater, Maloya, whose only capacity is the owner's estimate of normal storage | acre-feet only, never a percent |
| `all` | all 24 | acre-feet only: a sum of different pools has no meaningful percent |

- **Percent full** is the group's storage divided by the group's capacity in force that day (capacity-weighted). It is never a mean of the reservoirs' percents,
  which would let a 1,700-acre-foot forebay count as much as Elephant Butte.
- **Complete days only.** A figure is published for a day only when every member reports; a member's last reading is carried forward at most 7 days so feeds that lag
  each other by a few days still line up. A member whose newest reading is more than 14 days older is named under `stale`.
- **Normal for the date** is of *acre-feet held*, not of percent: the capacity table of each reservoir changed over the decades (sediment, resurveys), but the water was
  measured, so the statewide normal does not depend on how well each capacity is known. Days when every member reports, 1991-2020, at least 15 years, +-3 days; a
  reading within 5% of the group's capacity of the median is called normal whatever its percentile. The same percentile classes as flow and rain.
- **Shares.** One reservoir can dominate: Navajo is about 87% of the supply group's water. The file lists each member's share.
- **Not in the totals:** reservoirs in Colorado, Texas and Arizona that matter to New Mexico (Platoro, Rio Grande Reservoir, Lake Meredith, Red Bluff...), smaller New Mexico
  reservoirs not in the registry (Storrie Lake, Morgan Lake, Stubblefield, Farmington Lake) and tailings ponds. And storage is not all New Mexico's: San Juan-Chama water in Heron,
  El Vado and Abiquiu belongs to its contractors; Elephant Butte and Caballo store Rio Grande Project water allocated to districts in New Mexico and Texas and to Mexico.

## Drought (`statewide/drought.json`)

- **Source.** The U.S. Drought Monitor's statewide series for New Mexico (`usdm:state:35`), weekly, valid Tuesdays, since 2000. Values are stored as the Monitor
  published them; the only things we compute are the changes and the percentile.
- **Categories.** The share of the state in D0 (abnormally dry) or worse, D1 (moderate) or worse, D2 (severe), D3 (extreme) and D4 (exceptional). Each is cumulative, so D0 >= D1 >= ... >= D4;
  the bundle validator checks this for every week. (Before 2026-10-02 the ingest overwrote D0-D3 with D4; fixed, see the commit "USDM: keep D0-D4 as separate variables".)
- **DSCI.** The Drought Severity and Coverage Index, the five cumulative percents added up (0 to 500). It is the Monitor's own number; the validator checks it equals the sum within 1.5.
- **Changes.** The latest week against 1, 4, 13, 26 and 52 weeks earlier, for the DSCI and each category (percentage points).
- **Percentile.** The share of weeks within 21 days of this time of year, 2000-2025 (this year excluded), with a lower DSCI. The record is 26 years, not a 30-year normal, and the file says so.
- **Not a measurement of water.** The Monitor is an expert blend of indicators, drawn by authors each week; it says how dry conditions are, not how much water there is.
- **Licence.** The Monitor's reuse terms are still unread (issue #5); it is listed in the licence audit.

## Temperature (`statewide/temperature.json`)

- **Source.** PRISM Climate Group AN81 4 km mean-temperature grids (daily from 1981, monthly from 1895), averaged over the state polygon (the union of the 33 TIGER counties,
  121,590 square miles by our calculation against the official 121,590). Each cell is weighted by the share of it inside the state and by the cosine of its latitude; cells without
  data are left out of both sums, and the share of the state the grid covers is published (1.0).
- **Windows.** Mean temperature over the last 7, 30, 90, 180 and 365 days ending on PRISM's latest day, against the mean of the same calendar days in each year of 1991-2020
  (30 years; at least 20 are needed). The anomaly is the difference from that mean; the percentile ranks this window among those 30 years, and the class uses the same bands as flow and rain
  (the website calls them colder and warmer).
- **History.** The last 365 days with the baseline mean for each calendar day (smoothed over 15 days), every month since 1895 against the mean of that calendar month, and the
  anomaly of each complete calendar year.
- **Cross-check.** The monthly statewide mean is computed the same way from NOAA's nClimGrid and compared (1991 to the latest month): 428 months, bias +0.02 C,
  mean absolute difference 0.13 C, largest 0.6 C, correlation 0.9998. The figures are in the file and a test fails if they drift past stated limits.
- **Not a thermometer.** PRISM interpolates station data and estimates cells with no station; the newest days are provisional (PRISM revises about six months) and the two products
  can disagree by a few tenths of a degree in the most recent months (PRISM was 0.3-0.6 C cooler than nClimGrid in March-July 2026). Mean temperature is the average of the day's
  minimum and maximum, not a mean of hourly readings.
- **Licence.** PRISM: free to reproduce and redistribute with attribution (credit and access date shown on the page).

## Snowpack (`statewide/snowpack.json`)

- **Source.** NOAA NOHRSC SNODAS (via NSIDC), the 1 km daily snow model, 2003-09-30 on. It is a model that assimilates satellite, airborne and ground observations: an estimate.
  Four days (2003-10-30 has no SWE product; 2012-08-24, 2012-12-19 and 2013-10-01 have an irregular grid) are skipped.
- **Area.** The union of the TIGER counties; a cell is in the state when its centre is, and counts with its true area (the test checks the state comes to 77.8 million acres and that the
  volume is the mean depth times that area). The share of the state with data is published (99.96%).
- **Figures.** Mean snow water equivalent over the state in inches (the depth of water the snow would make if melted, averaged over everything including bare ground), the volume in
  acre-feet, and the share of the state with at least 25 mm of it.
- **Median and percent of median.** For a date, the median over the water years 2004-2025 (October to September, named for the year they end) of the days within 3 days of the same calendar day,
  leaving out the season shown. A percent of median is stated only when the median is at least 0.05 in of mean SWE, which in these data is about 20 November to 5 May. Outside it the file
  says it is out of season and gives the last season's peak (its date, depth, volume, share of the state covered, and how it ranks among the seasons since 2004) instead.
- **History.** The season shown (the most recent one with a snow season, so just after 1 October it is still the winter that ended) day by day with the spread of past years, and the peak of every season since 2004.
- **Cross-check against SNOTEL.** The NRCS SNOTEL stations in New Mexico, summed against their own medians over the same baseline years (the way NRCS reports a basin index), on December to April days:
  correlation 0.70 over 3,168 days, median difference 21 percentage points, SNODAS 7 points higher on average; as season (December-April) averages the correlation is 0.89 over 23 seasons
  with a mean difference of 22 points. They agree on which years are big and which are poor, not on the exact percent: the SNOTEL stations sit at 8,000-11,000 feet, SNODAS also
  counts snow that falls on lowlands and melts within days. Nobody should quote a statewide percent of median to the point.
- **Not a measurement.** No snow is measured at most of the state; SNODAS is also revised only by its own later runs. The state's snow is small compared with the Colorado Rockies' (mean SWE
  at the best day on record, 5 February 2010, was 1.5 in).
- **Licence.** SNODAS is listed as public domain with the NSIDC citation (see the licence audit); we have not re-read the NSIDC terms.

## Flow at the state line (`statewide/flow.json`)

This is **five gauges, not the state's outflow.** Many other streams cross the border (the Zuni River toward Arizona, the Dry Cimarron toward Oklahoma, ephemeral washes) and groundwater and
pipelines move water too; none are counted.

- **Gauges (one per river, so nothing is counted twice).** Rio Grande below Caballo Dam (Reclamation, with the USGS series that ended in 2013), Pecos River at Red Bluff NM, Canadian River at Logan NM,
  San Juan River at Four Corners, Gila River below Blue Creek near Virden NM; and, entering the state, the Rio Grande near Lobatos CO (USGS until December 2024, then the Colorado Division
  of Water Resources' copy of the same station). The Rio Grande below Elephant Butte Dam is not used: its water flows into Caballo, so adding both would count it twice.
- **Where they are.** Red Bluff and Four Corners are at the state line; Logan is about 40 miles from the Texas line and Virden about 20 from Arizona; Caballo is about 90 river miles above Texas and
  **most of the water released there is diverted for irrigation before the line**, so the Rio Grande's figure is what the dam lets go, not what reaches Texas. These caveats are in the file.
- **Volume.** Mean flow over the last N days x N x 1.983471 acre-feet per cfs-day (so a few missing days do not shrink it), stated when at least 75% of the days have data. The windows end on the latest
  day on which every exit has data (23 September 2026 now); the Lobatos entrance has its own end date.
- **Normal.** The median volume over the same calendar days in each year of 1991-2020. For the sum a year counts only when every exit has a value that year (29 of 30 years now). The percentile
  and class use the same bands as flow and rain.
- **Stale gauges.** An exit whose newest reading is more than 14 days older than the newest exit's is left out of the sum and named in the file, and the sum is stated only when every remaining exit is rated.
- **Water years.** October to September, named for the year they end, since 1978 (the San Juan record at Four Corners starts in 1977); a year needs 90% of its days at every gauge to be totalled.
- **Not computed:** compact accounting (Rio Grande, Pecos, Canadian, Colorado River): it rests on annual reports and delivery schedules we do not hold. The Colorado line of the Rio Grande Compact is measured
  at Lobatos only in part. We do not subtract the entrance from the exits.
- **Managed rivers.** Every one of these is regulated (Navajo, Heron, El Vado, Elephant Butte, Caballo, Avalon and Brantley, Conchas and Ute dams), so "below normal" reflects storage, releases and diversions as much as weather.
- **Licence.** USGS and Reclamation data are public; the Colorado Division of Water Resources data is listed in the licence audit (terms read by hand still pending, issue #5-style item for Colorado).

## What cannot be totalled from what we hold

| Metric | Why not |
|---|---|
| Groundwater | wells differ in aquifer, depth and datum; depth to water cannot be averaged, and a change in storage needs specific yield and aquifer area we do not hold |
| Evapotranspiration volume | only reference ET is available (a demand ceiling); actual ET is not ingested |
| Compact balances (Rio Grande, Pecos, Colorado River) | the accounting exists only as annual PDFs; the Lobatos gauge had a USGS gap (live copy used for flow) |
| Acequia and ditch diversions | the OSE telemetry is a sample of stations, not a census, and diverted water returns to the river |
| Water use | annual, county level, modelled, 2000-2020 / 2015: static |
| Summed river flow | rivers nest (Otowi, Albuquerque and San Marcial are one river); a sum double counts. Flow at the state line uses one gauge per exiting river instead |
