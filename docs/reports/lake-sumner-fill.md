# Lake Sumner annual fill, 1972-present

`reports/lake_sumner_annual_fill.csv`. Regenerate with `just reports`.
Method shared with the other reservoirs: [reservoir-fill.md](reservoir-fill.md).

Lake Sumner, formerly Alamogordo Reservoir, sits on the Pecos River in De Baca County. It is an
irrigation storage reservoir for the Carlsbad Project, upstream of Brantley and Avalon.

## Data provenance

**Storage, elevation.** Bureau of Reclamation, Upper Colorado Region HydroData, site
`usbr_hydrodata:943` (native id 943, "SUMNER"), at 34.629 N, 104.392 W in
HUC 13060001, Upper Pecos basin. Daily values, local calendar date.

| Series | Observations | Period |
|---|---|---|
| `reservoir_storage` | 22,172 | 1965-09-30 to 2026-09-10 |
| `reservoir_elevation` | 18,389 | 1972-12-31 to 2026-09-10 |

The report begins in 1972 because it needs both series paired, and elevation starts seven years
after storage. 1972 contributes a single day. **1997 and 1998 are missing entirely** from the
paired record.

Source: `https://www.usbr.gov/uc/water/hydrodata/`, public, no key.
Cite as: Bureau of Reclamation, Upper Colorado Region HydroData, accessed 2026.

**Capacity.** Reclamation Information Sharing Environment (RISE) catalog item 11031, location
489, "Lake Sumner (New Mexico) Sedimentation Survey ACAP Table 2013", by Kent L. Collins,
published 2014-08-01, file `SumnerLakeReservoir_SumnerLakeDam_NM00130_2013_ACAP2_RISE.csv`.
27 elevation rows, 4197 to 4300 ft, 224,227 acre-feet at the top of the table.

Source: `https://data.usbr.gov/rise/api`, public, requires header `Accept: application/vnd.api+json`.
Fetched by `nmwater fetch usbr_rise --kind acap` into `reservoir_acap`.

**Datum.** The table states its elevations are NAVD88, 1.88 ft above the project datum and 2.11 ft
above NGVD29. Unusually among these reservoirs, the operational elevation series matches the table
with no shift applied, so both are on NAVD88.

## Full pool

**4,277 ft**, giving 90,934 acre-feet under the 2013 survey. Detected from the record: 38 days at
or above 4,277 ft in 54 years, and exactly one above 4,278. That single reading, 4,289.1 ft in
1991, is 12 ft above anything else ever recorded and is treated as spurious.

## Method notes specific to this reservoir

The general method is in [reservoir-fill.md](reservoir-fill.md). Two things matter here.

The 1991 outlier is the reason the generator takes the 99.5th percentile of an era's elevations
rather than the maximum. Using the raw maximum let that one reading define the era's reach and
produced a capacity of 32,142 acre-feet, against a published 90,934.

Lake Sumner rarely approaches its crest, so extrapolating pre-2007 capacities reaches a long way.
Nine eras survive the minimum-length filter, collapsing under the monotone constraint to four
distinct capacities: 110,941, 101,909, 90,934.

## Validation

From 2007 onward, reported storage reproduces from the 2013 table to within 96 acre-feet, about
0.1% of capacity. Those 20 years are marked `published` and involve no extrapolation.

## Caveats

- **Pre-2007 years are the weakest in this report.** The annual offsets oscillate by several
  thousand acre-feet between adjacent years, which sediment cannot do. Some of that segmentation
  is tracking noise in the elevation and storage pairing, not real capacity-table changes. The
  monotone constraint keeps the result sane, but individual pre-2007 years should not be quoted
  to the decimal.
- **1997 and 1998 are absent** and 1972 is one day. Do not read those as dry years.
- 1973 peaks at 101.3%, above the crest. That is a real surcharge, not an error.

## What the record shows

Capacity has fallen from about 110,900 to 90,934 acre-feet over the record, roughly 18%, which is
a fast rate of loss for a 54-year window and consistent with the Pecos sediment load.

The reservoir has emptied. Annual means averaged 41% across the first ten years of record and 20%
across the last ten. 2008 recorded a low of essentially zero, and 2002 a low of 0.1%. The last
year to fill was 1979 at 92%, and only 1992 and 2007 have exceeded 60% since.
