# Elephant Butte annual fill, 1915-present

This is the curated, hand-reviewed Elephant Butte report. Elephant Butte also appears in the
general reservoir reports, [reservoirs/elephant_butte.md](reservoirs/elephant_butte.md), which add
inflow, release and pool detail and reproduce these capacities to within 0.12% in every year. The
full set of 24 reservoirs is indexed at [reservoirs/index.md](reservoirs/index.md).

## What this is

`reports/elephant_butte_annual_fill.csv` gives, for every year since 1915, how much water
Elephant Butte held and what share of capacity that was. `reports/elephant_butte_capacity_eras.csv`
gives the capacity figures the percentages rest on, one row per capacity-table vintage.

Regenerate both with:

```
uv run python scripts/elephant_butte_fill.py
```

It reads only the DuckDB catalog and makes no network calls, so it is safe to re-run at any time.
It needs `reservoir_acap` populated, which comes from `nmwater fetch usbr_rise --kind acap`
followed by `nmwater catalog build`.

## Data provenance

**Storage, elevation, area.** Bureau of Reclamation, Upper Colorado Region HydroData, site
`usbr_hydrodata:1119` (native id 1119, "ELEPHANT BUTTE"), at 33.154 N, 107.192 W in HUC 13030101,
Rio Grande-Mimbres basin. Daily values, local calendar date.

| Series | Observations | Period |
|---|---|---|
| `reservoir_storage` | 40,625 | 1915-03-20 to 2026-09-10 |
| `reservoir_elevation` | 40,630 | 1915-03-20 to 2026-09-10 |
| `reservoir_area` | 40,621 | 1915-03-20 to 2026-09-10 |

This is the longest continuous paired reservoir record in the archive, 112 years with no decade
missing. Three other sources carry Elephant Butte storage and agree with it to within 0.4%:
`usace_cwms:E Butte` from 2005, `usbr_albuq:ELEPHANTBUTTEDAM` for the current fortnight, and
`resopsus:657` to 2021.

Source: `https://www.usbr.gov/uc/water/hydrodata/`, public, no key.
Cite as: Bureau of Reclamation, Upper Colorado Region HydroData, accessed 2026.

**Capacity.** Reclamation Information Sharing Environment (RISE) catalog item 11028, location 323,
"Elephant Butte Reservoir (New Mexico) Sedimentation Survey ACAP Table 2017 and 2019", by
Timothy J. Randle and Vincent Benoit, published 2019-12-31, file
`ElephantButteReservoir_ElephantButteDam_NM00129_2017_ACAP2_RISE.csv`. 89 elevation rows, 4234 to
4414 ft, 2,275,698 acre-feet at the top of the table.

Source: `https://data.usbr.gov/rise/api`, public, requires header
`Accept: application/vnd.api+json`. Fetched by `nmwater fetch usbr_rise --kind acap` into
`reservoir_acap`.

Six earlier Elephant Butte surveys are indexed in the same RISE record but exist only as PDF
reports, not machine-readable tables: 1957, 1969, 1980, 1988, 1999 and 2007.

**Datum.** Reclamation Project Vertical Datum for Elephant Butte Dam, 45.0 ft below NAVD88
(Geoid 12A). The operational elevation series is on the same datum; no shift is applied.

**Independent capacity figure used for validation.** National Inventory of Dams, U.S. Army Corps
of Engineers, `https://nid.sec.usace.army.mil/api/nation/csv`, dam NM00129. Normal storage
2,065,010 acre-feet, design storage 2,593,255. Neither is sediment-corrected; they are used here
only as a check, never as an input.

## Columns

| Column | Meaning |
|---|---|
| `year` | calendar year |
| `capacity_table` | which capacity-table vintage was in force that year |
| `capacity_af` | full-pool capacity at the 4407 ft spillway crest under that table |
| `peak_af`, `mean_af`, `low_af` | highest, average and lowest daily storage that year |
| `peak_pct`, `mean_pct`, `low_pct` | those three divided by `capacity_af` |
| `n_days` | daily observations that year; 1915 and the current year are partial |
| `capacity_confidence` | how well `capacity_af` is pinned. See below. |

**`capacity_confidence` describes the denominator only.** The storage columns are Reclamation's
own published daily values throughout and carry no such caveat.

## The problem this solves

Percent full needs a capacity to divide by, and Elephant Butte's capacity is not one number. The
reservoir has been silting up since 1915, and Reclamation has rewritten its elevation-to-storage
table fourteen times to keep up. Only the most recent, from the 2017 and 2019 surveys, exists in
machine-readable form and is what the archive carries in `reservoir_acap`. The earlier tables
exist as PDF survey reports that have not been parsed.

Using today's capacity for a 1950 percentage is wrong, and so is using the design figure from the
National Inventory of Dams for anything, because that figure is owner-reported and has never been
revised for sediment. See [interpretation.md](../interpretation.md).

So the older capacities are recovered from the operational record itself.

## Method

**1. Find when the table changed.** On the day a new table is adopted, reported storage jumps
while the lake level barely moves. The detector flags days where the storage change cannot be
explained by the elevation change times the local dV/dh:

```
uv run python scripts/elephant_butte_fill.py --show-adoptions
```

Almost every adoption lands on 31 December. The detector also fires on transient data-entry
errors, which show up as a jump one day and an equal jump back the next; those are not table
changes. The reviewed list is frozen in `ADOPTIONS` in the script rather than re-detected on
every run, so the report cannot silently change shape.

**2. Measure each era's offset from the 2017 table.** For every day,
`off(h) = reported_storage - acap2017(h)`. Physically this is the volume of sediment deposited
between that era's survey and 2017, below elevation `h`. It rises with `h` and flattens out once
above the sediment wedge.

**3. Extrapolate that offset to the spillway crest** at 4407 ft to get the era's full-pool
capacity, by fitting a line to the offset over the top 20 ft of elevation the lake reached in
that era. How far that reaches is the whole question, and it is what `capacity_confidence`
reports.

**4. Force the sequence to be non-increasing.** Capacity can only fall, because sediment
accumulates and Elephant Butte has never been dredged. The raw extrapolations violate this for
the low-confidence eras, so a weighted pool-adjacent-violators fit pulls the sequence monotone,
with weights of `1/(1 + gap to crest)` so the eras that actually reached the spillway dominate.
The `monotonic_shift_pct` column records how far each era had to move; anything beyond about 2%
is a sign the raw extrapolation was poor.

## Confidence levels

| Level | Means |
|---|---|
| `published` | 2020 on. The 2017/2019 ACAP table is in the archive, no extrapolation. |
| `high` | The lake reached within 10 ft of the crest in that era, so capacity is very nearly measured. |
| `medium` | Within 30 ft. A modest reach. |
| `low` | Never came close. Capacity is a real extrapolation and could be off by a percent or two. |

The low-confidence eras are 1946-1955, 1955-1973, 1969-1973 and 2009-2019. In each the reservoir
stayed 50 to 80 ft below the spillway for the entire period, so nothing in the record constrains
what it would have held when full.

## Validation

Two independent checks, neither used in the fitting:

| Era | Derived capacity | Independent figure | Agreement |
|---|---|---|---|
| 1988 survey | 2,064,866 | 2,065,010, National Inventory of Dams normal storage | 0.007% |
| original 1915 | 2,643,340 | 2,593,255, NID design storage | 1.9% |

The first is the strong one. It also settles what the dam registry's "normal storage" figure
actually is: the 1988 resurvey, not a design number and not a current one.

A third check sits inside the data. Interpolating the 2017 table at each day's observed elevation
reproduces Reclamation's published surface area exactly and published storage to within 0.08%,
confirming that the operational storage series really is computed from this table.

## Things to know before quoting a number from this

- **1942 and 1986 exceed 100%.** The lake rose above the spillway crest, to 4409.2 and 4407.2 ft.
  These are correct, not errors. Full pool is a crest, not a ceiling.
- **Full pool is a choice.** Everything here uses the 4407 ft spillway crest. That elevation is
  not in the archive; it is corroborated by the record, where days at or above 4407 fall
  thirteenfold from the count at 4406, which is the signature of an uncontrolled spillway. If a
  report uses a different definition of full, the percentages move.
- **The first and last years are partial.** 1915 starts on 20 March; the current year runs to the
  last observation.
- **Storage is a stock, not a supply.** A reservoir at 30% may still deliver a full allocation in
  a good runoff year. Fill percentage describes the tank, not the water available.
- **Percent full is robust to sediment; volume is not.** Sediment shrinks the numerator and the
  denominator together, so a percentage computed against the contemporaneous table lands within a
  tenth of a point of one computed on a consistent modern basis. The acre-foot columns are where
  the sediment story actually shows.

## What would improve it

Parsing the pre-2007 sedimentation survey reports would replace every derived capacity with a
published one and retire the confidence column entirely. Those reports are indexed in the
`usbr_rise` catalog items for 1957, 1969, 1980, 1988, 1999 and 2007, as PDFs. That is the open
item in [TODO.md](../TODO.md).

Adding the spillway crest elevation to the archive as a documented constant, rather than a
literal in this script, would make the report fully self-contained.

## Findings worth carrying forward

- The reservoir has been essentially full three times: the mid-1920s, 1942, and a fifteen-year
  high stand from 1985 to 1999. Nothing since 2001 approaches any of them.
- The 1950s drought went deeper than the present one. 1954 bottomed at 9,900 acre-feet, 0.4% of
  capacity, and 1951 through 1955 averaged under 11%. The current drought is longer but has not
  yet gone as low.
- Capacity at the crest has fallen from about 2,643,000 acre-feet to 2,011,169, roughly 24% of
  the original reservoir lost to sediment in 111 years.
