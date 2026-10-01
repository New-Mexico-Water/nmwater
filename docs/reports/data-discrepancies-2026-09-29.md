# Data discrepancies found 2026-09-29, and what was done

A systematic pass over what the river pages and the catalog hold, started from one question: why does
the Rio Ruidoso page have no data from mid 2024 to mid 2026? The checks and their results are below,
with a status for each. Everything marked fixed was fixed on 2026-09-29 and 2026-09-30 and verified
against the data afterwards.

## 1. Gauges that were left off river pages

**Cause.** The national stream network (NHDPlus v2) leaves some main-stem reaches unnamed. A site on
an unnamed reach was given the river it drains to (`river_method = downstream`), and the river reports
only used gauges snapped to a reach with the river's own name. A few names are wrong as well.

| Finding | Status |
|---|---|
| Rio Ruidoso at Hollywood (USGS 08387000, daily flow from 1953 to now) was missing, which left the page with one gauge that has a two-year hole; 13 other rivers lost gauges the same way (Navajo River, Rio San Jose, Gallinas, Animas, Gila, Mancos, Mimbres, Pecos, Rio Grande, San Juan, Little Colorado, Little Navajo, Rio Hondo near Taos). | Fixed: a gauge on an unnamed reach counts when the first named reach downstream is the river, it lies in a watershed the river runs through, and its name starts with the river's name followed by a place (`names_the_river`). Navajo River now has 4 gauges and a record from 1912 (was 2 and 1936); Rio San Jose 4 and 1912 (was 2 and 1943). |
| Rio Ruidoso at Ruidoso has no USGS daily value from May 8, 2024 to May 12, 2026: every day to June 2025 is marked "Rat" (rating being developed), a few in June and July 2025 "Eqp" (equipment malfunction), none after. | Not a data loss: USGS publishes no values. Explained in a reviewed note on the page. The segment uses Hollywood for those two years. |
| The "Rio Fernando de Taos" page was really the Rio Pueblo de Taos: NHDPlus labels the Rio Pueblo de Taos below its confluence with the Rio Fernando as "Rio Fernando de Taos", and the upper Rio Pueblo de Taos (USGS 08269000, from 1912) had no page. | Fixed with `catalog/reach_name_fixes.csv`: 11 reaches renamed. The Rio Pueblo de Taos now has a page with its gauges; the Rio Fernando's own gauge (08275000, 1962 to 1980, 18 years) falls below the single-gauge cutoff below, so it has no page. |
| Ute Creek near Logan (USGS 07226500, from 1941) was assigned to the Canadian River; Rio Ojo Caliente at La Madera (08289000, from 1932) to "Arroyo el Rito"; Rio Mora near Terrero (08377900, from 1963) to the Pecos. All sit on unnamed reaches. | Fixed with `reach_name_fixes.csv` (109, 17 and 7 reaches named). Ids starting `fix:` are ours. Ute Creek, Black River, Salt Creek and Willow Creek are now labelled by basin, because more than one river carries each name. |
| Rivers with a single long-record gauge got no page (a page needed two gauges): Delaware River from 1937, Ponil Creek 1915, Rio Lucero 1912, Rio Grande del Rancho, Conchas River, Revuelto Creek, Mogollon Creek and others (38 rivers in all). | Fixed: a river with one gauge gets a page when that gauge has at least 20 years of daily values (`min_years_single_gauge: 20`, the same minimum the Compared-with-normal tab uses). 155 pages now, from 121. |
| The "Rio Pueblo" page (near Penasco) carried background written for the Rio Pueblo de Taos. | Fixed: the research file was renamed to `rio-pueblo-de-taos.yaml`. |

Not changed, listed so they are not mistaken for gaps: 201 of the 1,212 stream sites with a year or
more of daily flow have no reach, so no river, and appear on no river page; and the Rio Grande page
holds the New Mexico gauges only, by configuration (`config/river_reports.yaml`).

## 2. Values that cannot be right

All of this is flagged, not deleted: the stored observations are untouched, and `observations_clean`
leaves out what is now `implausible`.

| Finding | Status |
|---|---|
| Seven reservoirs send gauge height under the pool-elevation code (Bonito Lake, Costilla, Lake Maloya, Nichols, Two Rivers Dam, Continental, Rio Grande Reservoir), medians of 6 to 78 ft at lakes that stand at 3,000 to 10,500 ft. | Fixed: relabelled `stage` (2,177,717 rows) by `catalog/crosswalk_sites.csv` and `scripts/relabel_series.py`; new data is relabelled at ingest. Row counts before and after are identical. |
| Ten reservoirs (Abiquiu, Cochiti, Lake Sumner, Galisteo, Jemez Canyon, Santa Rosa, Conchas, Rio Hondo, Two Rivers, Avalon) have sent a second pool series since 2022-10-27 under SHEF code `HPIRZZZ`: 1.33 million readings, median 53 ft, alongside the real elevations under `HPIRGZ` and `HPIRGZZ`. About a quarter of those reservoirs' "elevation" rows. | Fixed: that code is relabelled `stage` (1,329,981 rows). Found only because the range check flagged it. |
| Impossible values in 13 variables: elevations of 0, -901 and 7.79 million ft; stage of ±10 million; relative humidity from -3,451 to 96,548 %; water temperature from -2,327 to 22,100 °C; precipitation up to 128,737 in; snow-water equivalent of 347 to 1,583 in at 12 NRCS sites; groundwater depth of 3,807 to 8,495 ft; groundwater elevation of 1,007,500 ft; pH of -4,954; Corps discharge of 4.87 million cfs. | Fixed by plausibility bounds in `catalog/variables.yaml` (table in [data-model.md](../data-model.md#quality-flags)). Now flagged `implausible`: water temperature 35,954 rows, relative humidity 25,825, stage 4,324, swe 2,157, groundwater elevation 2,087, precipitation 737, dissolved oxygen 514, reservoir elevation 341, pH 103, turbidity 58, groundwater depth 19, discharge 10; 140,805 of 828 million observations in all, counting the older air temperature bounds. |
| Dam fill reports read these series. | Checked: regenerated; storage, peaks and percentages are identical (only the data end date moved, to 2026-09-28). |

## 3. Left as it is, and why

- **Negative discharge.** The registry records a decision not to bound discharge below (3,826 negative
  readings at 36 ditch and canal gauges may be real reverse flow). Only an upper bound was added.
- **Wrong values inside a range.** A 9,500 ft reading at a 4,700 ft lake passes a static bound. The dam
  reports despike each reservoir separately; the catalog does not yet.
- **Probable unit errors** (the NRCS snow-water values of 347 to 1,583 in "look like millimetres") are
  flagged, not converted: nothing in the source says so.
- **One future-dated Texas groundwater reading** (2026-12-08, one well, stored under two variables) remains.
- **Three recent USGS series** are shorter than the USGS catalog's count (Alamitos Creek below Canoncito
  Ditch and below FR 161H near Holman, discharge; Mora Creek at Holman, water temperature). Not
  investigated.
- **Freshness.** Every USGS daily series in the USGS catalog is in the archive; other feeds end 4 to 5
  days before USGS. The NRCS air-temperature series that look stale are monthly and semi-monthly summaries that are current (the other NRCS variables were not checked the same way).

## How it was checked

USGS daily values were compared with USGS's own catalog (647 series, none missing); a re-pull of every
USGS daily value since 2024 changed nothing for the Ruidoso gauge, which showed the gap is USGS's.
Relabelled series were compared by row count before and after. The reach and gauge-name rules were
tested on the cases in `tests/test_river_flow.py` and `tests/test_catalog_fixes.py`. Plausibility
bounds were chosen from the observed values in the table above and from the elevations in scope.
