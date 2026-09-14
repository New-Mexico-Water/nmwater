# Avalon annual fill, 2001-present

`reports/avalon_annual_fill.csv`. Regenerate with `just reports`.
Method shared with the other reservoirs: [reservoir-fill.md](reservoir-fill.md).

Avalon Reservoir is on the Pecos River in Eddy County, immediately above Carlsbad and below
Brantley. It is the Carlsbad Irrigation District's diversion structure, the last impoundment
before water goes into the canals.

## Read this before quoting a percentage

**Avalon is operated as a diversion dam, not as storage.** It is drawn down and refilled
constantly, and in many years it is deliberately emptied. Storage fell below 100 acre-feet on 108
days in 2018, 78 days in 2019 and 66 days in 2023. A low percentage at Avalon usually means the
district was moving water, not that the Pecos was dry. Annual minimum is close to meaningless
here; the peak and the mean carry what little signal there is.

## Data provenance

**Storage, elevation.** Bureau of Reclamation, Upper Colorado Region HydroData, site
`usbr_hydrodata:2684` (native id 2684, "AVALON"), at 32.491 N, 104.252 W in HUC 13060011,
Upper Pecos basin. Daily values, local calendar date.

| Series | Observations | Period |
|---|---|---|
| `reservoir_storage` | 21,734 | 1965-09-30 to 2026-09-10 |
| `reservoir_elevation` | 8,953 | 2001-12-31 to 2026-09-10 |
| `reservoir_area` | 8,952 | 2001-12-31 to 2026-09-10 |

The report begins in 2001 because elevation starts 36 years after storage. **This is the largest
gap of any reservoir here**: 12,781 storage observations from 1965 to 2001 cannot be used, because
without a paired elevation there is no way to establish which capacity table was in force.

Source: `https://www.usbr.gov/uc/water/hydrodata/`, public, no key.
Cite as: Bureau of Reclamation, Upper Colorado Region HydroData, accessed 2026.

**Capacity.** RISE catalog item 128691, location 267, "Avalon Reservoir (New Mexico)
Sedimentation Survey ACAP Table 2023", by Vincent Benoit and Nate Bradley, published 2023-12-01,
file `AvalonReservoir_AvalonDam_NM00132_2023_ACAP2_RISE.csv`. 38 elevation rows, 3157 to 3194 ft,
31,135 acre-feet at the top of the table. This is the most recent survey of the six.

Source: `https://data.usbr.gov/rise/api`, header `Accept: application/vnd.api+json`.

**Datum.** Reclamation Project Vertical Datum, equivalent to NGVD29 plus 16.49 ft. The operational
series reproduces the table with no shift applied.

## Full pool

**3,179 ft**, giving 6,066 acre-feet under the 2023 survey. Detected from the record: 72 days at
or above 3,178 ft and one above 3,179 in 26 years, a 72-fold drop in a single foot.

Note the large discrepancy between this and the top of the table. The table extends to 3,194 ft
and 31,135 acre-feet, 15 ft and five times the volume above anything the reservoir actually does.
Quoting 31,135 as Avalon's capacity, as a reader of the raw table might, would be wrong by a
factor of five.

## Method notes specific to this reservoir

One era. The published 2023 table fits the entire 2001-2026 record to within 1 acre-foot, so every
year is marked `published` and no capacity is extrapolated anywhere in this report. Avalon is the
only reservoir of the six where that is true.

That also means no sediment loss is visible: either the table has not changed since 2001, or
Reclamation applied the 2023 survey retroactively to the series. The record cannot distinguish
these, so the 0% capacity loss shown for Avalon is an absence of evidence, not evidence of
absence.

## Validation

Median absolute difference between reported storage and the table across 8,953 paired days is
1 acre-foot on a capacity of 6,066.

## Caveats

- **The diversion-operation warning above governs any use of this series.**
- 2014 peaks at 133.0%. On 20 September 2014 the lake reached 3,180.7 ft holding 8,069 acre-feet,
  about 1.7 ft above the crest, during Pecos flooding. This is a genuine surcharge.
- The pre-2001 storage record exists and is longer than what is reported here, but cannot be given
  a capacity. If Avalon's percentages matter for a historical question, that gap is the thing to
  fix.
- No capacity change is observable, which is not the same as no sedimentation.

## What the record shows

Annual means hold steady near 31% across the record, 28% in the first ten years and 31% in the
last ten, with no trend. That stability reflects operations rather than hydrology: the district
runs the pool at a working level and the reservoir is too small to carry water between years.

It reached full pool or above in 2013, 2014 and 2021.
