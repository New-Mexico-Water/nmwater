"""Water balances for three reaches of the Rio Grande: Elephant Butte Reservoir, the Middle Rio Grande (Otowi to San Marcial) and Elephant Butte Dam to Caballo Dam (issue #57).

    uv run python scripts/water_balance.py               # all reaches, water years and months
    uv run python scripts/water_balance.py --only elephant_butte

Writes
    reports/water_balance_<reach>_water_year.csv     one row per water year (October to September), acre-feet; ignored by git
    reports/water_balance_<reach>_month.csv          one row per month
    docs/reports/water-balance.md                    the method, the sources and the water-year tables (generated; edit nmwater/derived/water_balance.py, not the file)

Reads only the DuckDB catalog. No network calls. The equation, the sign convention and what each reach leaves out are in nmwater/derived/water_balance.py and in the report.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from nmwater.core.config import REPORTS_DIR
from nmwater.derived import water_balance as wb

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "reports" / "water-balance.md"
USGS = "https://waterdata.usgs.gov/monitoring-location/USGS-{}/"
KAF = 1000.0


def link(uid: str) -> str:
    src, native = uid.split(":", 1)
    if src == "usgs":
        return f"[`{uid}`]({USGS.format(native)})"
    return f"`{uid}`"


def kaf(x: float) -> str:
    return "" if pd.isna(x) else ("0" if round(x / KAF) == 0 else f"{x / KAF:,.0f}")


def pct(x: float) -> str:
    return "" if pd.isna(x) else ("0%" if round(x) == 0 else f"{x:+.0f}%")


def terms_table(reach: wb.Reach) -> list[str]:
    out = ["| Role | Series | Archive id (first listed is preferred; the rest fill its gaps) | Note |", "|---|---|---|---|"]
    for t in reach.terms:
        out.append(f"| {t.role} | {t.label} | {', '.join(link(u) for u in t.uids)} | {t.note} |")
    return out


def year_table(df: pd.DataFrame, reach: wb.Reach) -> list[str]:
    ref = next((t for t in reach.terms if t.role == "reference"), None)
    head = ["Water year", "Inflow", "Outflow", "Storage change", "Evaporation", "Unaccounted", "% of inflow"] + (["Reclamation's inflow"] if ref else [])
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in df.itertuples():
        if r.period.endswith("2027") and not r.complete:
            continue
        if not r.complete:
            cells = [r.period, kaf(r.inflow_af), kaf(r.outflow_af), "", "", "no balance: a series has too few days", ""] + ([""] if ref else [])
        else:
            cells = [r.period, kaf(r.inflow_af), kaf(r.outflow_af), kaf(r.storage_change_af), kaf(r.evaporation_af), kaf(r.unaccounted_af), pct(r.unaccounted_pct_of_inflow)]
            cells += [kaf(getattr(r, ref.key))] if ref else []
        out.append("| " + " | ".join(cells) + " |")
    return out


def summary(key: str, df: pd.DataFrame) -> list[str]:
    ok = df[df.complete]
    p = ok.unaccounted_pct_of_inflow
    first, last = ok.period.iloc[0], ok.period.iloc[-1]
    lines = [f"Across the {len(ok)} complete water years ({first} to {last}) the unaccounted water is a median {p.median():+.0f}% of inflow, "
             f"ranging from {p.min():+.0f}% ({ok.loc[p.idxmin(), 'period']}) to {p.max():+.0f}% ({ok.loc[p.idxmax(), 'period']})."]
    if key == "elephant_butte":
        ref = ok.dropna(subset=["eb_reported_inflow"])
        gap = (ref.implied_inflow_af - ref.eb_reported_inflow) / ref.eb_reported_inflow * 100
        lines.append(f"Reclamation's own inflow is the lake's mass balance, so release plus storage change plus evaporation should reproduce it: it does to within {gap.abs().max():.1f}% "
                     f"in every year ({len(ref)} years, median {gap.abs().median():.1f}%). That is a check that the archive holds the right series and units; it says nothing about the gauges upstream.")
        share = (ok.san_marcial_conveyance / ok.inflow_af * 100)
        lines.append(f"The conveyance channel carries {share.median():.0f}% of the gauged inflow in a median year ({share.min():.0f}% to {share.max():.0f}%), so the floodway gauge alone would leave out "
                     "that much. Even with both channels the gauges read below the lake's own balance in most years: the river's last miles above the lake receive water no gauge counts.")
    if key == "middle_rio_grande":
        wet, dry = ok.sort_values("inflow_af").iloc[-3:], ok.sort_values("inflow_af").iloc[:3]
        d, w = dry.unaccounted_pct_of_inflow.median(), wet.unaccounted_pct_of_inflow.median()
        lines.append(f"The share is {d:+.0f}% of inflow in the three driest years and {w:+.0f}% in the three wettest" + (
            ": a larger share goes missing when the river brings less, which fits a demand that does not shrink with the supply, but without the diversion records the archive cannot show that." if d > w
            else ": the share does not rise in dry years in this record."))
    return lines


def render(results: dict[str, pd.DataFrame], generated: str) -> str:
    L = ["# Water balance of three Rio Grande reaches", "",
         "Generated by `scripts/water_balance.py` from the archive; do not edit by hand. The method and every series are in `nmwater/derived/water_balance.py`.", "",
         "## What this is", "",
         "For a stretch of river, water that comes in must go out, be stored or be lost. This report writes that down for three stretches of the Rio Grande with the gauges and reservoir records "
         "the archive holds, one water year at a time, and shows how much water the gauges do **not** explain. That remainder is not an error to be tuned away: it is where the diversions, "
         "the evapotranspiration, the groundwater exchange and the ungauged inflow live, and the size of it is the useful number.", "",
         "```", "unaccounted = inflow - outflow - change in storage - evaporation from the reservoirs in the reach", "```", "",
         "All volumes are acre-feet (shown here in thousands). Flows are the gauges' daily mean cfs times 1.983471 acre-feet per cfs-day. A water year runs October to September and is named for "
         "the year it ends in.", "",
         "**Reading the sign.** Positive: measured water went missing (diverted, evaporated or transpired, seeped away, or a gauge reads high). Negative: more water left than was measured "
         "arriving (an ungauged tributary, a return flow or spring, or a gauge reads low). A period needs at least 95% of its days in every flow and volume series and storage readings within "
         "three days of both ends; otherwise it has no balance, and the table says so rather than extrapolating.", ""]
    for key, reach in wb.REACHES.items():
        df = results[key]
        L += [f"## {reach.title}", "", f"From {reach.upstream} to {reach.downstream}.", "", *terms_table(reach), "", reach.reading, "", "**Left out:** " + "; ".join(reach.omitted) + ".", ""]
        for n in reach.notes:
            L += [n, ""]
        L += [*summary(key, df), ""]
        L += ["Thousands of acre-feet.", "", *year_table(df, reach), ""]
    L += ["## What would close the gap", "",
          "- **Diversions and returns.** The Middle Rio Grande Conservancy District's and the Elephant Butte Irrigation District's records. The archive has the MRGCD's diversion gauges only since "
          "September 2026 (`usbr_albuq`); the longer record is an access request ([#12](https://github.com/New-Mexico-Water/nmwater/issues/12)). With them, the Middle Rio Grande's positive remainder splits "
          "into what was diverted and what the river and its fields consumed.",
          "- **Evapotranspiration.** Satellite ET (OpenET) would put a number on the consumption the diversions feed ([#18](https://github.com/New-Mexico-Water/nmwater/issues/18)).",
          "- **El Paso and below.** The archive's USGS record at El Paso stops in 1982 and the IBWC record starts in April 2026, so the chain cannot be carried past Caballo.", "",
          "## Caveats", "",
          "- Provisional data. The recent years use USGS and Reclamation values that may be revised; re-run after a revision.",
          "- A gauge is read as published: no rating shifts, no re-computation. Small flows are noisy and a percentage of a small inflow is large.",
          "- Reclamation's evaporation is its own estimate, not a measurement.",
          "- Copies of one gauge from other publishers (Reclamation's, the Corps') fill days USGS lacks. Reclamation's copy of the Rio Puerco gauge matches USGS exactly on the days both have, "
          "and USGS has no record there from November 2021 to September 2023. A copy carries its publisher's own review status.", "",
          "## Reproduce", "", "```", "uv run python scripts/water_balance.py", "```", "",
          "or from Python:", "", "```python", "import duckdb", "from nmwater.derived import water_balance as wb",
          "con = duckdb.connect('data/duckdb/nmwater.duckdb', read_only=True)", "wb.run(con, wb.REACHES['elephant_butte'], by='water_year')", "```", "",
          "Needs a catalog built with `nmwater catalog build`. Sources: USGS National Water Information System (gauge pages linked above) and the Bureau of Reclamation's "
          "[HydroData](https://www.usbr.gov/uc/water/hydrodata/) for reservoir storage, evaporation and releases.", "",
          f"_Generated {generated} from the archive._", ""]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(ROOT / "data" / "duckdb" / "nmwater.duckdb"))
    ap.add_argument("--only", nargs="*", choices=list(wb.REACHES), help="balance only these reaches (the report is then not rewritten)")
    ap.add_argument("--no-doc", action="store_true", help="write the CSVs but not the markdown report")
    a = ap.parse_args()
    con = duckdb.connect(a.db, read_only=True)
    con.execute("SET TimeZone='UTC'")
    con.execute("SET threads=2")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, pd.DataFrame] = {}
    for key in a.only or wb.REACHES:
        reach = wb.REACHES[key]
        series = wb.read_reach(con, reach)
        end = wb.newest_day(series)
        for by in ("water_year", "month"):
            df = wb.balance(series, reach, wb.FIRST_DAY, end, by)
            df.to_csv(REPORTS_DIR / f"water_balance_{key}_{by}.csv", index=False, float_format="%.1f")
            if by == "water_year":
                results[key] = df
        ok = results[key][results[key].complete]
        print(f"{key}: {len(ok)} complete water years of {len(results[key])}; unaccounted median {np.nanmedian(ok.unaccounted_pct_of_inflow):+.1f}% of inflow")
    if not a.only and not a.no_doc:
        DOC.write_text(render(results, date.today().isoformat()))
        print(f"wrote {DOC}")


if __name__ == "__main__":
    main()
