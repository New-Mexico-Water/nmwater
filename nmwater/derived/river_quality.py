"""Water temperature and salinity (specific conductance) along a river.

Two kinds of data, kept apart because they answer different questions:
- continuous sensors (USGS daily statistics): a value every day, so seasonal curves and yearly
  extremes are meaningful. The yearly metric is the highest 7-day average of the daily maximum
  temperature (7DADM, the usual measure of heat stress for fish), or of the daily mean where a site
  publishes no daily maximum (labelled).
- field samples (Water Quality Portal, NMED, USGS field visits): a few values a year at many places.
  They are summarised by decade and segment, and always shown as points with their counts, never as
  continuous lines.

Sites are the river's own stream sites (snapped to its reaches), in the report's segments.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .river_flow import NON_RIVER, River, _plain

MIN_SUMMER_DAYS = 60        # a year counts for 7DADM when June-September has this many days
MIN_SENSOR_YEARS = 3


def read_quality_sites(con, river: River, segments: list[str]) -> pd.DataFrame:
    seg = ",".join("'" + s.replace("'", "''") + "'" for s in segments)
    g = con.execute(f"""
        SELECT r.site_uid, r.huc8_name AS segment, s.source, s.name, r.totdasqkm
        FROM river_segments r JOIN sites s USING (site_uid)
        JOIN site_reaches sr USING (site_uid) JOIN flowlines f ON f.comid = sr.comid
        WHERE f.gnis_id = ? AND r.river_method = 'snap' AND r.site_type = 'stream' AND r.huc8_name IN ({seg})
          AND s.site_uid IN (SELECT site_uid FROM site_variables
                             WHERE variable IN ('water_temp', 'specific_conductance'))""", [river.gnis_id]).df()
    # a boolean Series, not a list: an empty list would select zero columns instead of zero rows
    keep = pd.Series([not NON_RIVER.search(_plain(n).strip()) for n in g["name"]], index=g.index, dtype=bool)
    return g[keep].reset_index(drop=True)


def read_quality(con, sites: pd.DataFrame) -> pd.DataFrame:
    if sites.empty:
        return pd.DataFrame(columns=["site_uid", "variable", "date", "interval", "statistic", "value"])
    ids = ",".join("'" + u.replace("'", "''") + "'" for u in sites["site_uid"])
    return con.sql(f"""
        SELECT site_uid, variable, (datetime_utc AT TIME ZONE 'America/Denver')::DATE AS date, interval,
               coalesce(statistic, 'instantaneous') AS statistic, avg(value) AS value
        FROM observations_clean
        WHERE variable IN ('water_temp', 'specific_conductance') AND site_uid IN ({ids})
          AND interval IN ('daily', 'irregular', 'instantaneous', '15min')
        GROUP BY 1, 2, 3, 4, 5""").df()


@dataclass
class QualityResult:
    sensors: pd.DataFrame        # site, segment, variable, stat, years, first, last (continuous sites)
    heat: pd.DataFrame           # site, segment, year, value (7-day max), metric ("7DADM" or "7-day mean")
    season: pd.DataFrame         # site, doy, p10, p50, p90 (baseline), this_year (current year's value)
    salinity_profile: pd.DataFrame  # segment, decade, median, n_days
    salinity_years: pd.DataFrame    # segment, year, median, n_days, kind (sensor or samples)


def summarise(q: pd.DataFrame, sites: pd.DataFrame, segments: list[str], this_year: int) -> QualityResult:
    if q.empty or sites.empty:
        return QualityResult(pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
                             pd.DataFrame(columns=["segment", "decade", "median", "n_days"]),
                             pd.DataFrame(columns=["segment", "year", "kind", "median", "n_days"]))
    q = q.merge(sites[["site_uid", "name", "segment"]], on="site_uid")
    q["date"] = pd.to_datetime(q["date"])
    q["year"] = q["date"].dt.year

    # continuous: daily statistics from sensors
    daily = q[q["interval"] == "daily"]
    t = daily[daily["variable"] == "water_temp"]
    heat_rows, season_rows, sensor_rows = [], [], []
    for (name, seg), d in t.groupby(["name", "segment"]):
        has_max = (d["statistic"] == "max").any()
        stat = "max" if has_max else "mean"
        s = d[d["statistic"] == stat].groupby("date")["value"].mean().sort_index()
        s = s.asfreq("D")
        roll = s.rolling(7, min_periods=6).mean()
        summer = s[s.index.month.isin([6, 7, 8, 9])]
        years = [y for y, n in summer.groupby(summer.index.year).count().items() if n >= MIN_SUMMER_DAYS]
        if len(years) < MIN_SENSOR_YEARS:
            continue
        sensor_rows.append({"site": name, "segment": seg, "variable": "water_temp", "stat": stat, "years": len(years),
                            "first": int(min(years)), "last": int(max(years))})
        for y in years:
            heat_rows.append({"site": name, "segment": seg, "year": y, "value": float(roll[roll.index.year == y].max()),
                              "metric": "7DADM" if has_max else "7-day mean"})
        doy = pd.Series(np.minimum(s.index.dayofyear, 365), index=s.index)
        base = s[s.index.year < this_year]
        cur = s[s.index.year == this_year]
        if len(base.dropna()) > 365:
            qb = base.groupby(doy[base.index]).quantile([0.1, 0.5, 0.9]).unstack()
            curd = cur.groupby(doy[cur.index]).mean()
            for dd in range(1, 366):
                season_rows.append({"site": name, "doy": dd, "p10": qb.get(0.1, {}).get(dd, np.nan),
                                    "p50": qb.get(0.5, {}).get(dd, np.nan), "p90": qb.get(0.9, {}).get(dd, np.nan),
                                    "this_year": curd.get(dd, np.nan)})

    # salinity: one value per site and day (sensor daily mean, or the day's samples)
    sc = q[q["variable"] == "specific_conductance"]
    sc_day = pd.concat([
        sc[(sc["interval"] == "daily") & (sc["statistic"] == "mean")].assign(kind="sensor"),
        sc[sc["interval"] != "daily"].assign(kind="samples")])
    sc_day = sc_day.groupby(["site_uid", "segment", "date", "kind"])["value"].mean().reset_index()
    sc_day["decade"] = (sc_day["date"].dt.year // 10) * 10
    sc_day["year"] = sc_day["date"].dt.year
    prof = sc_day.groupby(["segment", "decade"])["value"].agg(["median", "size"]).reset_index()
    prof.columns = ["segment", "decade", "median", "n_days"]
    yrs = sc_day.groupby(["segment", "year", "kind"])["value"].agg(["median", "size"]).reset_index()
    yrs.columns = ["segment", "year", "kind", "median", "n_days"]
    order = {s: i for i, s in enumerate(segments)}
    prof = prof.sort_values(["segment", "decade"], key=lambda c: c.map(order) if c.name == "segment" else c)
    return QualityResult(pd.DataFrame(sensor_rows), pd.DataFrame(heat_rows), pd.DataFrame(season_rows), prof, yrs)
