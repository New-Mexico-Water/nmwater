# Reservoir annual fill: every New Mexico reservoir the archive can do honestly

## What this is

`reports/reservoir_annual_fill.csv` gives, for six reservoirs, how much water each held in each
year and what share of capacity that was, measured against the capacity table actually in force
that year. `reports/reservoir_capacity_eras.csv` gives the capacity figures behind it, one row
per reservoir and table vintage. Per-reservoir CSVs sit alongside them.

```
just reports
```

runs both generators in the right order. They read only the DuckDB catalog and make no network
calls. They need `reservoir_acap` populated, from `nmwater fetch usbr_rise --kind acap` followed
by `nmwater catalog build`.

## Coverage

| Reservoir | Years | Records | Vintages | Capacity then | Capacity now | Lost | Years on the published table |
|---|---|---|---|---|---|---|---|
| Elephant Butte | 1915-2026 | 112 | 14 | 2,643,340 | 2,011,169 | 24% | 7 |
| Lake Sumner | 1972-2026 | 53 | 9 | 110,941 | 90,934 | 18% | 20 |
| El Vado | 1975-2026 | 52 | 5 | 194,158 | 184,452 | 5% | 28 |
| Brantley | 1987-2026 | 38 | 4 | 93,323 | 85,629 | 8% | 25 |
| Nambe Falls | 1999-2026 | 28 | 3 | 2,171 | 1,752 | 19% | 6 |
| Avalon | 2001-2026 | 26 | 1 | 6,066 | 6,066 | 0% | 26 |

Capacity is in acre-feet at full pool. "Lost" is sediment, over the length of the record only,
not since the dam was built.

## Why only six

The method needs three things at once, and most reservoirs fail at least one.

1. **A paired elevation and storage record.** 64 sites have both.
2. **A published sediment-corrected capacity table.** Only 7 New Mexico reservoirs have a
   Reclamation area-capacity (ACAP) table in `reservoir_acap`.
3. **A table that actually reaches full pool.**

**Heron fails the third and is excluded.** Reclamation's published table stops at 7,102 ft and
74,615 acre-feet, while Heron routinely operates up to 7,186 ft; the median observed elevation in
our record is above the top of the table. The file is genuinely truncated, not mis-parsed. Heron
needs a complete table before percent full can be computed at all.

**The other 94 storage sites are not a substitute.** 81 of them can be joined to a capacity
through the National Inventory of Dams, but that is design storage which is never corrected for
sediment. Those sites can carry storage in acre-feet honestly. They cannot carry a trustworthy
percentage. See [interpretation.md](../interpretation.md).

## Two generators, on purpose

`scripts/elephant_butte_fill.py` handles Elephant Butte alone, with 14 capacity-table adoption
dates that were detected, reviewed by hand and validated against an independent figure. That
result is authoritative. See [elephant-butte-fill.md](elephant-butte-fill.md).

`scripts/reservoir_fill.py` handles the other five by segmenting eras automatically, and folds
the curated Elephant Butte rows into the combined CSV rather than recomputing them. Running the
automatic segmenter on Elephant Butte reproduces the curated capacities, which is the main check
that the automatic path works.

## Method

For each reservoir:

1. **Segment the record into capacity-table eras.** The offset between reported storage and the
   modern table is a function of elevation, because it is the sediment volume below that level.
   So two years under the same table show different offsets if the lake sat at different heights,
   and comparing raw annual offsets invents era boundaries out of wet and dry years. Each year is
   instead compared with the previous one **only over the band of elevation they both occupied**.
   Eras shorter than 400 days merge into the one before, so a partial final year cannot invent a
   vintage out of one season.

2. **Decide whether the published table is in force.** If reported storage reproduces from the
   modern ACAP table within tolerance, that table is in force and its capacity at the crest is
   the answer. Extrapolating in that case would be strictly worse.

3. **Otherwise extrapolate.** Fit the era's offset against elevation over the top 20 ft it
   reached and evaluate at the crest. The top of an era's range is taken at the 99.5th percentile,
   not the maximum, because a single bad reading otherwise defines the reach. Lake Sumner has
   exactly one such value, 12 ft above anything else in 54 years.

4. **Force the sequence non-increasing.** Capacity can only fall. A weighted pool-adjacent-
   violators fit pulls it monotone, with published capacities pinned so the fit cannot move a
   measured value, and the rest weighted by how close that era got to the crest.

## Full pool is a choice, and for Brantley it is a loaded one

Every percentage here is against the **spillway crest**, detected as the elevation above which
the lake essentially never goes. An uncontrolled spillway shows a sharp cliff in the exceedance
counts; at Elephant Butte the count falls thirteenfold in a single foot at 4,407 ft. Inspect the
detection for any reservoir with:

```
uv run python scripts/reservoir_fill.py --crest
```

**Brantley is a flood-control dam** and its crest is a flood pool far above the conservation pool
it is actually operated to. Its percentages are therefore low by design and are not comparable
with the others. The `full_pool_basis` column flags this. The same caveat would apply to Abiquiu,
Cochiti, Jemez Canyon, Santa Rosa and Conchas if they had ACAP tables.

## Confidence

`capacity_confidence` describes the denominator only. Storage columns are the operators' own
published values throughout and carry no such caveat.

| Level | Means |
|---|---|
| `published` | The modern ACAP table is demonstrably in force. No extrapolation. |
| `high` | Within 10 ft of the crest in that era, and the monotone fit barely moved it. |
| `medium` | Within 30 ft. |
| `low` | Never came close, or the monotone fit had to move it more than 2%. |

## Known weaknesses

- **Lake Sumner's offsets oscillate** by several thousand acre-feet between adjacent years, which
  is not physical, so its 1972-2006 segmentation is partly tracking noise rather than real table
  changes. The monotone constraint collapses it to four distinct capacity values, which is
  probably about right, but individual pre-2007 years should not be quoted precisely. Its 2007
  onward years are on the published table and are solid.
- **El Vado needed a 1.45 ft elevation shift** to make its storage reproduce from its table,
  found empirically. The table's own datum note says the project datum sits about 12 ft below
  NAVD88, which does not match the shift that works. Somebody should reconcile those before El
  Vado is published outside this repo. Its 2009-2020 offsets are also anomalous; those years are
  flagged `low` and their capacity is pinned between two published eras.
- **Nambe Falls and Avalon are tiny** (1,752 and 6,066 acre-feet). A few hundred acre-feet of
  error moves their percentages a lot.
- **Crest elevations are detected, not sourced.** They are inputs the archive cannot supply.
  They should become documented constants with a citation.

## What would improve this

Parsing the pre-2007 sedimentation survey reports would replace every derived capacity with a
published one and retire the confidence column. Obtaining Heron's complete table would add a
seventh reservoir. Both are in [TODO.md](../TODO.md).
