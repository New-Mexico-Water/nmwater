# El Vado annual fill, 1975-present

`reports/el_vado_annual_fill.csv`. Regenerate with `just reports`.
Method shared with the other reservoirs: [reservoir-fill.md](reservoir-fill.md).

El Vado Lake is on the Rio Chama in Rio Arriba County, the San Juan-Chama Project's main storage
alongside Heron, upstream of Abiquiu and the middle Rio Grande.

## Data provenance

**Storage, elevation.** Bureau of Reclamation, Upper Colorado Region HydroData, site
`usbr_hydrodata:2685` (native id 2685, "EL VADO"), at 36.595 N, 106.737 W in HUC 13020102,
Rio Grande-Elephant Butte basin. Daily values, local calendar date.

| Series | Observations | Period |
|---|---|---|
| `reservoir_storage` | 17,062 | 1974-12-30 to 2026-09-10 |
| `reservoir_elevation` | 18,882 | 1974-12-31 to 2026-09-10 |
| `reservoir_area` | 2,080 | 2020-12-31 to 2026-09-10 |

Source: `https://www.usbr.gov/uc/water/hydrodata/`, public, no key.
Cite as: Bureau of Reclamation, Upper Colorado Region HydroData, accessed 2026.

**Capacity.** RISE catalog item 11019, location 324, "El Vado Reservoir (New Mexico)
Sedimentation Survey ACAP Table 2007", by Ronald L. Ferrari, published 2008-07-01, file
`ElVadoLake_ElVadoDam_NM10008_2007_ACAP2_RISE.csv`. 82 elevation rows, 6766 to 6904 ft,
197,370 acre-feet at the top of the table.

Source: `https://data.usbr.gov/rise/api`, header `Accept: application/vnd.api+json`.

**Datum, and an unresolved discrepancy.** The table says its elevations are on the El Vado Dam
project datum, 7.8 ft below NGVD29 and about 12.0 ft below NAVD88. But the operational elevation
series only reproduces the table's storage after a **1.45 ft** shift, found empirically by
minimising the residual. 1.45 is not 12, and the two cannot both be right.

The shift is applied in `RESERVOIRS["El Vado"]["elev_shift_ft"]` in `scripts/reservoir_fill.py`.
It brings agreement to 0.03%, so something real is being corrected, but the documented datum
relationship does not explain it. **Reconcile this before publishing El Vado figures outside this
repository.**

## Full pool

**6,900 ft**, giving 184,452 acre-feet under the 2007 survey. The crest detection is unusually
clean here: 773 days at or above 6,899 ft, 195 at 6,900, and none at all above 6,901 in 52 years.

## Method notes specific to this reservoir

Five eras. Two are marked `published`, 1987-2008 and 2021-2026, where reported storage reproduces
from the 2007 table directly. The 1975-1986 era sits 7,846 acre-feet above it and reached the
crest, so its capacity of 194,158 is `high` confidence.

The 2009-2020 eras are anomalous: offsets of 2,512 and 1,517 acre-feet that appear and then
disappear. Sediment does not reverse. Those years are flagged `low` and, because they sit between
two `published` eras, the monotone constraint pins their capacity to 184,452, which is the right
behaviour even though the cause is unexplained. They may be another symptom of the datum problem.

## Validation

1987-2008 and 2021-2026 reproduce from the published table within about 170 and 25 acre-feet
respectively, on a capacity of 184,452.

## Caveats

- **The datum discrepancy above is the most serious open issue in any of these reports.**
- **2022 onward is not a drought signal.** El Vado was drawn down for dam rehabilitation, so the
  near-empty readings of 2022 to 2024 reflect construction, not hydrology. A report that lumps
  them in with Rio Grande drought years will be wrong.
- The 2009-2020 offset anomaly is unexplained.

## What the record shows

Capacity fell from about 194,200 to 184,500 acre-feet over 52 years, roughly 5%, the slowest loss
of the six reservoirs. The Rio Chama above El Vado carries far less sediment than the Rio Grande
or the Pecos.

The reservoir filled repeatedly through 2010, peaking at 100.1% in 2009 and 99.8% in 2010, and
averaged 52% across its first ten years. The last ten years average 17%, though the rehabilitation
drawdown accounts for much of that.
