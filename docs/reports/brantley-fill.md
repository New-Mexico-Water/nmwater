# Brantley annual fill, 1987-present

`reports/brantley_annual_fill.csv`. Regenerate with `just reports`.
Method shared with the other reservoirs: [reservoir-fill.md](reservoir-fill.md).

Brantley Reservoir is on the Pecos River in Eddy County, above Avalon and Carlsbad. It replaced
McMillan Reservoir and began storing water in 1988.

## Read this before quoting a percentage

**Brantley is a flood-control dam and its percentages are not comparable with the other
reservoirs in this report.** Full pool here is the spillway crest, which sits far above the
conservation pool the dam is actually operated to. A Brantley figure of 34% does not mean the
same thing as Elephant Butte at 34%. The `full_pool_basis` column carries this flag. The same
caution would apply to Abiquiu, Cochiti, Jemez Canyon, Santa Rosa and Conchas if they had
capacity tables. See [interpretation.md](../interpretation.md).

## Data provenance

**Storage, elevation.** Bureau of Reclamation, Upper Colorado Region HydroData, site
`usbr_hydrodata:937` (native id 937, "BRANTLEY"), at 32.544 N, 104.381 W in HUC 13060011,
Upper Pecos basin. Daily values, local calendar date.

| Series | Observations | Period |
|---|---|---|
| `reservoir_storage` | 14,072 | 1987-12-31 to 2026-09-10 |
| `reservoir_elevation` | 12,575 | 1987-12-31 to 2026-09-10 |
| `reservoir_area` | 4,310 | 2014-08-07 to 2026-09-10 |

Source: `https://www.usbr.gov/uc/water/hydrodata/`, public, no key.
Cite as: Bureau of Reclamation, Upper Colorado Region HydroData, accessed 2026.

**Capacity.** RISE catalog item 11016, location 282, "Brantley Reservoir (New Mexico)
Sedimentation Survey ACAP Table 2013", by Ronald L. Ferrari, published 2013-08-01, file
`BrantleyLake_BrantleyDam_NM00500_2013_ACAP2_RISE.csv`. 79 elevation rows, 3204 to 3312 ft,
1,298,523 acre-feet at the top of the table. That top figure is the flood pool, not a storage
capacity.

Source: `https://data.usbr.gov/rise/api`, header `Accept: application/vnd.api+json`.

**Datum.** This is the only one of the six tables that carries **no datum statement**. The
operational series reproduces its storage to 13 acre-feet with no shift, so they are evidently on
the same datum, but that is inference from agreement rather than documentation.

## Full pool

**3,264.6 ft**, giving 85,629 acre-feet under the 2013 survey. Detected from the record: the lake
has never in 38 years been recorded above 3,264.6 ft, and the exceedance count falls from 273 days
at 3,263 to 96 at 3,264 to zero at 3,265.

Note the plateau below that, roughly 340 days each between 3,258 and 3,263 ft. That is the
signature of flood-pool operation, water held briefly and released, rather than a storage pool.

## Method notes specific to this reservoir

Four eras. From 2002 the published 2013 table is demonstrably in force, which is worth noting:
the table is labelled 2013 but matches operations from eleven years earlier, so the survey year
and the adoption year are not the same thing.

The three earlier eras, 1987-1992, 1993-1999 and 2000-2001, all sit 2,000 to 5,600 acre-feet above
the modern table and none reached within 7 ft of the crest, so their common capacity of 93,323 is
extrapolated. Two of the three are flagged `low` because the monotone constraint had to move them
by more than 2%.

## Validation

From 2002 onward, reported storage reproduces from the 2013 table to within 13 acre-feet on a
capacity of 85,629, about 0.02%. Those 25 years involve no extrapolation.

## Caveats

- **1987 is a single day at zero storage**, the day the record opens before the reservoir began
  filling. It is not a drought year. Do not include it in any minimum or average.
- The pre-2002 capacity is a single extrapolated figure covering fifteen years and should not be
  read as precise.
- Percentages are against the flood pool. See the warning at the top.

## What the record shows

Capacity fell from about 93,300 to 85,629 acre-feet over 38 years, roughly 8%.

Unlike the Rio Grande reservoirs, Brantley has not declined. Annual means average 24% over the
first ten years of record and 30% over the last ten. It filled to 99.6% in 2015 and 95.0% in 2014,
its two wettest years, both recent.
