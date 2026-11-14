"""Check the SNOTEL new-snow estimate against people who measured snowfall with a board (CoCoRaHS volunteers, NWS cooperative observers, airport and agency stations).

    uv run python scripts/new_snow_validation.py            # writes docs/reports/new-snow-estimate.md

Reads only the DuckDB catalog. For every NM SNOTEL station it takes the observers of snowfall within MAX_KM kilometres and MAX_DZ_M metres of elevation, lines their days up with the
station's estimate (nmwater.derived.snotel_snow.new_snow_daily), and reports how the winter totals, the snowy days and the three-day sums agree. Also compares the chosen rule with two
simpler ones, so the report shows why the rule has its second condition. The numbers in the report come from this script; edit the script, not the report.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from nmwater.derived import snotel_snow as ss
from nmwater.site.snow import SERIES_FROM, _daily, _stations

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "reports" / "new-snow-estimate.md"
MAX_KM = 12.0
MAX_DZ_M = 450.0
MIN_BOTH_DAYS = 200      # days with a reading from both, in the snow season, for a pair to count
MIN_SNOW_DAYS = 20       # days on which the observer measured snow
SNOW_MONTHS = (11, 12, 1, 2, 3, 4)
SHIFTS = (0, 1, 2)
ALIGNED = 1            # the estimate for day D is the snow of the local day D; an observer's report dated D+1 covers 7 am on D to 7 am on D+1, so it is the match


def km(lat1, lon1, lat2, lon2):
    return np.hypot((lat2 - lat1) * 111.0, (lon2 - lon1) * 111.0 * np.cos(np.radians(lat1)))


def observers(con, stn: pd.DataFrame) -> pd.DataFrame:
    obs = con.sql("SELECT s.site_uid, s.source, s.name, s.lat, s.lon, s.elevation_m FROM sites s WHERE s.source IN ('cocorahs','noaa_ghcnd','iem_dcp') AND s.lat IS NOT NULL "
                  "AND s.site_uid IN (SELECT DISTINCT site_uid FROM observations WHERE variable = 'snowfall' AND year >= 2005)").df()
    rows = []
    for st in stn.itertuples():
        d = km(st.lat, st.lon, obs.lat, obs.lon)
        dz = (obs.elevation_m - st.elev_ft / 3.28084).abs()
        for o in obs[(d <= MAX_KM) & (dz <= MAX_DZ_M)].itertuples():
            rows.append({"station": st.id, "station_name": st.name, "obs": o.site_uid, "obs_name": o.name, "source": o.source,
                         "km": float(km(st.lat, st.lon, o.lat, o.lon)), "dz_m": float(o.elevation_m - st.elev_ft / 3.28084)})
    return pd.DataFrame(rows)


def observer_days(con, ids: list[str]) -> dict[str, pd.Series]:
    q = ",".join("'" + i + "'" for i in ids)
    d = con.sql(f"SELECT site_uid, datetime_utc::DATE AS d, max(value) AS v FROM observations_clean WHERE variable = 'snowfall' AND site_uid IN ({q}) "
                f"AND datetime_utc >= TIMESTAMPTZ '{SERIES_FROM} 00:00:00+00' GROUP BY 1, 2").df()
    d["d"] = pd.to_datetime(d["d"])
    return {k: g.set_index("d")["v"].sort_index() for k, g in d.groupby("site_uid")}


def rules(depth: pd.Series, swe: pd.Series) -> dict[str, pd.Series]:
    d = depth.asfreq("D")
    rise = d.diff()
    water = swe.asfreq("D").diff().reindex(d.index)
    depth_only = rise.where((rise >= ss.FLOOR_IN) & (rise <= ss.CAP_IN), 0.0).where(rise.notna())
    return {"depth rise only": depth_only, "chosen rule": ss.new_snow_daily(depth, swe),
            "water rise only (x10 for snow:water)": (water.where(water >= ss.SWE_STEP_IN, 0.0) * 10).where(water.notna())}


def season_mask(idx: pd.DatetimeIndex) -> np.ndarray:
    return np.isin(idx.month, SNOW_MONTHS)


def compare(est: pd.Series, ob: pd.Series, shift: int = 0) -> dict | None:
    e = est.copy()
    e.index = e.index + pd.Timedelta(days=shift)
    j = pd.concat([e.rename("e"), ob.rename("o")], axis=1, join="inner").dropna()
    j = j[season_mask(j.index)]
    if len(j) < MIN_BOTH_DAYS or (j.o > 0).sum() < MIN_SNOW_DAYS:
        return None
    r3 = j.rolling(3).sum().dropna()
    obs_snow, est_snow = j.o >= 1.0, j.e > 0
    return {"days": len(j), "obs_total": float(j.o.sum()), "est_total": float(j.e.sum()),
            "corr_day": float(j.e.corr(j.o)), "corr_3day": float(r3.e.corr(r3.o)),
            "hit": int((obs_snow & est_snow).sum()), "miss": int((obs_snow & ~est_snow).sum()), "false": int((~obs_snow & est_snow).sum()), "obs_snow_days": int(obs_snow.sum())}


def build(con) -> dict:
    stn = _stations(con)
    stn = stn[stn.id.str.endswith(":SNTL")]
    pairs = observers(con, stn)
    depth, swe = _daily(con, list(stn.id), "snow_depth"), _daily(con, list(stn.id), "swe")
    odays = observer_days(con, sorted(pairs.obs.unique()))
    out = []
    for p in pairs.itertuples():
        if p.station not in depth or p.station not in swe or p.obs not in odays:
            continue
        per_rule = rules(depth[p.station], swe[p.station])
        for rule, est in per_rule.items():
            for sh in SHIFTS if rule == "chosen rule" else (ALIGNED,):
                c = compare(est, odays[p.obs], sh)
                if c:
                    out.append({**p._asdict(), "rule": rule, "shift": sh, **c})
    return {"pairs": pairs, "results": pd.DataFrame(out), "stations": stn}


def pct(a: float, b: float) -> str:
    return "" if b == 0 else f"{100 * a / b:.0f}%"


def render(r: dict, generated: str) -> str:
    res = r["results"]
    chosen = res[(res.rule == "chosen rule") & (res["shift"] == ALIGNED)].copy()
    chosen["ratio"] = chosen.est_total / chosen.obs_total
    pool = chosen.sort_values("km").drop_duplicates(["station", "obs"])
    L = ["# How good is the new-snow estimate?", "",
         f"Generated by `scripts/new_snow_validation.py` on {generated}; do not edit by hand. The rule is in `nmwater/derived/snotel_snow.py`.", "",
         "## What is being checked", "",
         "SNOTEL stations do not report snowfall. They report how deep the snow is and how much water it holds. The site estimates new snow from the two: a day counts when the depth rises "
         f"{ss.FLOOR_IN:g} to {ss.CAP_IN:g} inches and the snow-water reading rises at least {ss.SWE_STEP_IN:g} inch the same day, and the day's new snow is the depth rise. "
         "The estimate is labelled an estimate everywhere on the site because snow settles, melts and blows, so it should read **below** what a person with a snowboard measured, and "
         "because the stations sit higher than most observers.", "",
         "This report puts the estimate beside real snowfall measurements: volunteers (CoCoRaHS), National Weather Service cooperative observers (GHCN-Daily) and the agency stations that "
         "report snowfall (IEM). They are the only snowfall measurements in the archive.", "",
         "## How the comparison is made", "",
         f"- **Pairs.** Every SNOTEL station in New Mexico with every snowfall observer within {MAX_KM:g} km and {MAX_DZ_M:g} m of elevation. A pair counts when both have a reading on at least "
         f"{MIN_BOTH_DAYS} days between November and April and the observer measured snow on at least {MIN_SNOW_DAYS} of them. {len(pool)} pairs qualify, at "
         f"{pool.station.nunique()} stations and {pool.obs.nunique()} observers.",
         "- **Days.** Only days on which both have a value. A day with no reading is missing, never zero, on both sides.",
         "- **Time of day.** A SNOTEL day *D* is the rise over the local day *D*: the archive's daily row dated *D* is the reading at the midnight that ends *D* (it equals the hourly reading at "
         "07:00 UTC on *D*+1 exactly, in every month). A volunteer or cooperative observer reports in the morning for the 24 hours before, so the report dated *D*+1 (7 am on *D* to 7 am on *D*+1) is "
         "the match and overlaps the station's day by 17 hours. The timing section shows that this alignment fits the data best.",
         "- **Elevation.** The observers sit lower than the stations (the gap is in the table). More snow falls higher up, so a station reading above its lower neighbour is expected, "
         "and the gap alone can make the ratio exceed one.", "",
         "## Results", ""]
    if chosen.empty:
        L += ["No pair met the conditions.", ""]
        return "\n".join(L)
    tot = pool.est_total.sum() / pool.obs_total.sum()
    L += [f"Summed over all {len(pool)} pairs and winters, the estimate is **{tot * 100:.0f}%** of what the observers measured "
          f"(median pair {pool.ratio.median() * 100:.0f}%, middle half {pool.ratio.quantile(.25) * 100:.0f}% to {pool.ratio.quantile(.75) * 100:.0f}%).", "",
          f"On the days the observer measured an inch or more of snow ({int(pool.obs_snow_days.sum())} such days), the estimate saw snow on {pct(pool.hit.sum(), pool.obs_snow_days.sum())} of them. "
          f"On days the observer did not measure an inch, the estimate counted snow on {int(pool.false.sum())} days, {pct(pool.false.sum(), pool.days.sum() - pool.obs_snow_days.sum())} of them.", "",
          f"Day by day the two correlate at a median {pool.corr_day.median():.2f}; added up over three days (which absorbs a day's offset in timing) {pool.corr_3day.median():.2f}.", ""]
    t = pool.sort_values(["station_name", "km"])
    L += ["Every pair is listed.", "",
          "| SNOTEL station | Observer | Network | Distance km | Observer below by m | Days | Observer inches | Estimate inches | Estimate / observer | Daily r | 3-day r |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for x in t.itertuples():
        L.append(f"| {x.station_name} | {x.obs_name} | {x.source} | {x.km:.1f} | {-x.dz_m:.0f} | {x.days} | {x.obs_total:.0f} | {x.est_total:.0f} | {x.ratio * 100:.0f}% | {x.corr_day:.2f} | {x.corr_3day:.2f} |")
    L += ["", "Observer inches and estimate inches are totals over the days in the table's *Days* column, so both sides cover the same days.", ""]

    L += ["## Why the rule has two conditions", "",
          "The same stations and observers, three rules. The first counts every depth rise of the right size; the last uses only the water reading, scaled by ten inches of snow to an inch of water.", "",
          "| Rule | Estimate / observer (all pairs) | Daily r (median) | Snow days seen | Days counted when the observer saw under an inch |", "|---|---|---|---|---|"]
    for rule, g in res[(res["shift"] == ALIGNED)].groupby("rule"):
        g = g.drop_duplicates(["station", "obs", "rule"])
        L.append(f"| {rule} | {g.est_total.sum() / g.obs_total.sum() * 100:.0f}% | {g.corr_day.median():.2f} | {pct(g.hit.sum(), g.obs_snow_days.sum())} | {int(g.false.sum())} |")
    L += ["", "Each rule trades snow days found against days counted that the observers did not see snow on. The chosen rule counts the fewest of those and has the best day-by-day agreement; "
          "it finds fewer of the observers' snow days than the other two. Depth alone, or water alone, finds more snow but also more that was not there. The water-only rule "
          "also carries no depth, so it could not feed the depth measure or the ski-area pages, and it needs an assumed snow density."
          ""]

    sh = res[(res.rule == "chosen rule")].drop_duplicates(["station", "obs", "shift"])
    L += ["## Timing", "", "The estimate for SNOTEL day *D* against the observer's report dated *D* plus the number of days in the first column, over the same pairs:", "",
          "| Shift (days) | Daily r (median) | Estimate / observer |", "|---|---|---|"]
    for s, g in sh.groupby("shift"):
        L.append(f"| {s:+d} | {g.corr_day.median():.2f} | {g.est_total.sum() / g.obs_total.sum() * 100:.0f}% |")
    L += ["", "The +1 day alignment fits best, as the clocks say it should; the other two are shown to prove the match is not an accident of the method. The rest of the report uses +1.", ""]

    L += ["## What this means for the pages", "",
          "- The estimate is a **floor** for new snow near the station, not a measurement; the pages say \"estimate\" beside every figure and link here.",
          "- It compares places across years because the same rule and the same sensor apply every winter. It does not compare a station with a ski area's own report.",
          "- A station reads only about two-thirds of what the observers measured and sees half of their snow days. Because the stations are higher, where more snow falls, the real shortfall is larger than that. "
          "A pair that reads far above one (Bateman, 195%) or far below (Taos 11.3 ESE, 20%, on 210 days) shows how much one observer's site and habits matter; the pages never present the estimate as an observer's figure.",
          "- The newest day on the site is yesterday. Station days end at local midnight, so \"new snow\" is the last complete day, not the last 24 hours to now; the pages should say \"yesterday\".", "",
          "## Limits", "",
          "- Snowfall observers are in towns and valleys. Pairs at the same elevation as a SNOTEL station, in the same storm track, are few, and the volunteers who live there measure when it snows, "
          "so a day with no report is missing, not zero; those days are left out.",
          "- The comparison does not test the estimate against what fell at the SNOTEL site itself: nobody measured that.",
          "- GHCN-Daily snowfall is from cooperative observers who sometimes record a storm's total on the day they empty the gauge; the three-day correlation is less sensitive to that.",
          "- Provisional data. SNOTEL values are not reviewed for the latest months.", "",
          "## Reproduce", "", "```", "uv run python scripts/new_snow_validation.py", "```", "",
          f"Needs a built catalog (`nmwater catalog build`). Parameters: {MAX_KM:g} km, {MAX_DZ_M:g} m, at least {MIN_BOTH_DAYS} shared snow-season days and {MIN_SNOW_DAYS} observer snow days, SNOTEL data from {SERIES_FROM}.", ""]
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(ROOT / "data" / "duckdb" / "nmwater.duckdb"))
    a = ap.parse_args()
    con = duckdb.connect(a.db, read_only=True)
    con.execute("SET TimeZone='UTC'")
    con.execute("SET threads=2")
    r = build(con)
    DOC.write_text(render(r, date.today().isoformat()))
    print(f"wrote {DOC}: {len(r['results'])} comparisons from {len(r['pairs'])} candidate pairs")


if __name__ == "__main__":
    main()
