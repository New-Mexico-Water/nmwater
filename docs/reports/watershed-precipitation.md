# Watershed precipitation

Precipitation averaged over each watershed (HUC8) in New Mexico: **daily from 1981-01-01, monthly
for 1895 through 1980**. It exists so that rainfall in one place can later be lined up in time with
river flow and reservoir fill somewhere else.

- Build: `nmwater watershed-precip` (also run by `nmwater update`); code in
  `nmwater/derived/watershed_precip.py`; tests in `tests/test_watershed_precip.py`.
- Output: `data/parquet/derived/watershed_precip/year=YYYY/precip.parquet` and
  `data/parquet/derived/watersheds.parquet`; in the catalog, the views `watershed_precip` and
  `watersheds`. The watersheds are also listed in
  [`watershed-precipitation-watersheds.csv`](watershed-precipitation-watersheds.csv).
- Size: 1,348,012 rows (1,269,580 daily, 78,432 monthly), 3.3 MB. A full rebuild takes about 12 seconds.

## What is in it

`watershed_precip`

| Column | Meaning |
|---|---|
| `huc8` | watershed code (USGS Watershed Boundary Dataset) |
| `date` | daily rows: the PRISM day's date (see the time convention); monthly rows: the first of the month |
| `interval` | `daily` (1981 on) or `monthly` (1895 to 1980; there is no monthly row once daily exists) |
| `precip_in` | mean depth over the watershed, inches (rain plus melted snow) |
| `period_start_utc`, `period_end_utc` | the exact period the value covers |
| `source` | `prism_daily` or `prism_monthly` |

`watersheds`: `huc8`, `name`, `states`, `area_km2`, `nm_fraction` (share of the watershed inside New
Mexico), `grid_fraction` (share of it that the precipitation grid covers), `has_precip`,
`in_river_reports`.

**Coverage.** 102 watersheds are listed: every HUC8 with at least 1% of its area in New Mexico (80),
plus the HUC8s that the river pages use because a gauge sits in them (the rest, mostly Colorado,
Texas and Arizona). 76 have precipitation rows. The other 26 have less than half their area in the
grid and get none (`has_precip = false`); an average over a sliver would mislead. Of the 80 New Mexico
watersheds, 71 have precipitation and 9 do not (Playas Lake, San Bernardino Valley, Sulphur Springs
Draw, Running Water Draw, Upper Beaver, Chinle, Mustang Draw, Palo Duro, El Paso-Las Cruces), each
with less than half its area in the grid, because it lies mostly in Mexico, Texas, Oklahoma, Arizona or Utah. 22 of the 76 that have precipitation are only partly covered
(`grid_fraction` below 0.95; for example the Animas at 0.52, Alamosa-Trinchera 0.69, Upper San Juan 0.91):
the value is the average over the covered part. **Filter on `grid_fraction` before using a watershed that
crosses the state line.** Two causes: the grids are clipped to the New Mexico bounding box (Colorado
headwaters such as the Rio Grande Headwaters, 0.3% covered, are outside it), and PRISM has no data in
Mexico (no re-clipping would fix that part).

## Provenance

- Grids: PRISM Climate Group, Oregon State University, 4 km, https://prism.oregonstate.edu, accessed
  2026, already in `data/grids/prism` (`nmwater fetch prism`). Free to reproduce and redistribute with
  attribution.
- Daily 1981 to present: PRISM's daily time series (AN81d/AN91d, precipitation version D2). Monthly 1895 to 1980:
  PRISM's monthly time series, which PRISM builds from its long-term-consistency dataset (LT81m) before
  1981 and from AN81m/AN91m after. Source: *Descriptions of PRISM Spatial Datasets for the Conterminous
  United States* (PRISM Climate Group, revised February 2026).
- Watersheds: USGS Watershed Boundary Dataset HUC8 polygons (`data/grids/wbd`); the New Mexico outline
  from TIGER counties.

## Method

1. Each watershed polygon is rasterised at 8 x 8 sub-cells per grid cell, so every cell carries the
   fraction of itself that lies inside the watershed.
2. The watershed mean for a day or month is the sum over cells of (precipitation x fraction) divided by
   the sum of fractions over cells that have data. Cells without data (PRISM covers the contiguous United States only, so Mexico) are left out of
   both. Millimetres are converted to inches.
3. `grid_fraction` is the sum of the fractions over cells with data divided by the polygon's area in
   cell areas.
4. The river pages' older monthly series (cell centres, equal weights) is unchanged; this one weights
   by area.

## Time convention (read before lining things up)

A PRISM day runs from **12:00 UTC to 12:00 UTC** and carries the date it **ends** on. PRISM's document:
"a day ending at 1200 UTC on 1 January is labeled 1 January". That is about 5 AM to 5 AM Mountain
Standard Time (6 AM to 6 AM in summer), so the row dated June 5 is mostly the rain of June 4. The
`period_start_utc` and `period_end_utc` columns say so on every row.

This was checked against rain gauges: for 454 CoCoRaHS stations (7 AM readers, report dated the day
they are read) with at least 1,200 daily reports in 2019 to 2024, the median correlation with PRISM at
the station's cell is 0.93 for the same date, and between -0.09 and 0.04 for dates shifted by one or
two days (wet-day pairs only). USGS daily mean flow is a local-calendar-day mean (midnight to midnight
local time). Local day D is about three quarters PRISM day D+1 (18 to 19 of its 24 hours) and one
quarter PRISM day D, so rain that falls on a local day shows up mostly under the next PRISM date.
Monthly rows are nominal calendar months.

## Validation

- **Complete.** All 76 watersheds have all 16,705 days from 1981-01-01 to 2026-09-26 (no gaps, no
  negative values; largest watershed-average day 3.68 in) and all 1,032 months from 1895-01 to 1980-12.
- **Daily agrees with monthly.** Summing PRISM's daily grids over each month reproduces PRISM's monthly
  grids: for 27,789 watershed-months from 1981 to 2025 with at least 0.5 in, the ratio is 1.000 (5th
  and 95th percentiles also 1.000); correlation 0.9999; every decade's total within 0.1%. So there is no
  step where the series changes from monthly to daily. The latest months match as well (January to
  August 2026, ratios 1.000).
- **Matches the river pages.** Against the monthly values the river pages already use, for the 54
  watersheds with at least 95% grid coverage, 7,128 months in 2015 to 2025: mean difference 0.005 in,
  largest 0.118 in, correlation 0.99997.
- **Date alignment.** See the time convention.

## Caveats

- PRISM re-maps roughly the latest six months as stations report, so recent days can change. The build
  records each year's grid file and recomputes a year whenever it changes; `nmwater update` does this.
- Before 1981 the rows come from PRISM's long-term-consistency monthly dataset, which uses only
  long-term station networks, so they rest on a restricted set of stations; PRISM warns that
  multi-decade trends need care.
- A watershed average hides where in the watershed the rain fell; the Rio Grande's Upper Rio Grande
  watershed spans the Sangre de Cristo and the Taos Plateau.
- The current month is partial: sum daily rows only for complete months.

## Using it

Daily precipitation in one watershed beside daily flow at a gauge elsewhere (Rio Grande at Otowi,
precipitation in the Upper Rio Grande watershed):

```sql
SELECT p.date, p.precip_in, o.value AS cfs
FROM watershed_precip p
JOIN observations_clean o ON o.datetime_utc::DATE = p.date
 AND o.site_uid = 'usgs:08313000' AND o.variable = 'discharge'
 AND o.interval = 'daily' AND o.statistic = 'mean'
WHERE p.huc8 = '13020101' AND p.interval = 'daily'
ORDER BY p.date;
```

For lagged comparisons, join on `p.date + k = o.date` and remember the day convention above. One monthly
series for the whole record (monthly rows before 1981, daily summed after):

```sql
SELECT month, sum(precip_in) AS precip_in FROM (
  SELECT date_trunc('month', date)::DATE AS month, precip_in FROM watershed_precip WHERE huc8 = '13020101')
GROUP BY month ORDER BY month;
```

Annual totals (keep complete years only), and the watersheds that are only partly covered:

```sql
SELECT year(date) AS yr, sum(precip_in) AS annual_in FROM watershed_precip WHERE huc8 = '13020101'
GROUP BY yr HAVING count(*) IN (12, 365, 366) ORDER BY yr;

SELECT name, states, grid_fraction FROM watersheds
WHERE has_precip AND nm_fraction >= 0.01 AND grid_fraction < 0.95 ORDER BY grid_fraction;
```

Watersheds for the reservoirs' upstream areas are not all here: the Colorado headwaters that feed the
Rio Grande, the Conejos and the San Juan are outside the grid. Covering them means re-fetching PRISM
for a larger area (the original download is clipped as it arrives, so it is not kept).
