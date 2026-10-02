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

## What cannot be totalled from what we hold

| Metric | Why not |
|---|---|
| Groundwater | wells differ in aquifer, depth and datum; depth to water cannot be averaged, and a change in storage needs specific yield and aquifer area we do not hold |
| Evapotranspiration volume | only reference ET is available (a demand ceiling); actual ET is not ingested |
| Compact balances (Rio Grande, Pecos, Colorado River) | the accounting exists only as annual PDFs; the Lobatos gauge had a USGS gap (live copy used for flow) |
| Acequia and ditch diversions | the OSE telemetry is a sample of stations, not a census, and diverted water returns to the river |
| Water use | annual, county level, modelled, 2000-2020 / 2015: static |
| Summed river flow | rivers nest (Otowi, Albuquerque and San Marcial are one river); a sum double counts. Flow at the state line uses one gauge per exiting river instead |
