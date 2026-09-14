# Nambe Falls annual fill, 1999-present

`reports/nambe_falls_annual_fill.csv`. Regenerate with `just reports`.
Method shared with the other reservoirs: [reservoir-fill.md](reservoir-fill.md).

Nambe Falls Reservoir is on the Rio Nambe in Santa Fe County, a small irrigation reservoir for the
Pueblo of Nambe. At 1,752 acre-feet it is by far the smallest reservoir in this report, about a
thousandth of Elephant Butte.

## Data provenance

**Storage, elevation.** U.S. Geological Survey site 08294200, "NAMBE FALLS RESERVOIR NEAR NAMBE,
NM" (`usgs:08294200`), at 35.8456 N, 105.9061 W in HUC 13020101, Rio Grande-Elephant Butte basin.
Daily values, local calendar date. This is the only reservoir here whose primary record comes from
USGS rather than Reclamation; the Reclamation HydroData site `usbr_hydrodata:2687` carries a
shorter paired record and was not used.

| Series | Observations | Period |
|---|---|---|
| `reservoir_storage` | 15,595 | 1983-12-31 to 2026-09-10 |
| `reservoir_elevation` | 9,433 | 1999-09-30 to 2026-09-10 |

The report begins in 1999 because elevation starts sixteen years after storage.

Source: USGS Water Data, `https://api.waterdata.usgs.gov/ogcapi/v1/`.
Cite as: U.S. Geological Survey, National Water Information System, accessed 2026.

**Capacity.** RISE catalog item 11033, location 3511, "Nambe Falls Reservoir (New Mexico)
Sedimentation Survey ACAP Table 2013", by Ronald L. Ferrari, published 2013-07-01, file
`NambeFallsReservoir_NambeFallsDam_NM00412_2013_ACAP2_RISE.csv`. 47 elevation rows, 6761 to
6840 ft, 2,616 acre-feet at the top of the table.

Source: `https://data.usbr.gov/rise/api`, header `Accept: application/vnd.api+json`.

**Datum.** The table is on the project vertical datum in NGVD29, 3.47 ft below NAVD88. The USGS
elevation series reproduces the table's storage with no shift applied, so the USGS gauge datum
evidently matches the project datum. That agreement is empirical, not documented, and is worth
confirming given that the two agencies are different.

## Full pool

**6,827 ft**, giving 1,752 acre-feet under the 2013 survey. Detected from the record: 2,435 days
at or above 6,826 ft, 133 at 6,827, and none above 6,828 in 27 years. An 18-fold drop in one foot.

## Method notes specific to this reservoir

Three eras. The published table is in force from 2021. Before that, 1999-2000 and 2001-2020 sit
433 and 270 acre-feet above it. Both reached the crest, so both are `high` confidence despite
being derived, and the monotone fit did not have to move either.

## Validation

From 2021 onward, reported storage reproduces from the 2013 table to within 1 acre-foot.

## Caveats

- **Small numbers move the percentage a lot.** Capacity is 1,752 acre-feet. An error of 50
  acre-feet, trivial at any other reservoir here, is 3 percentage points at Nambe Falls.
- 2013 peaks at 102.3%, 2015 at 100.7% and 2022 at 100.5%. These are real surcharges above the
  crest, not errors.
- The capacity vintages before 2021 are derived from only two eras across 22 years, so the timing
  of the underlying table changes is coarse.

## What the record shows

Capacity fell from about 2,171 to 1,752 acre-feet over 27 years, roughly 19%. For a small
headwater reservoir on a steep sediment-producing catchment that is a fast rate.

Unlike every other reservoir in this report, Nambe Falls stays full. Annual means average 77%
across the record and 83% across the last ten years, and it has touched or exceeded full pool in
five separate years: 2005, 2013, 2014, 2015 and 2022. It is operated as a seasonal irrigation
supply that refills each spring rather than as multi-year carryover storage, so its percentages
describe a different kind of system than Elephant Butte's.
