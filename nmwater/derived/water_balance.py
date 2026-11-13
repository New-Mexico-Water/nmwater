"""Water balance of a river reach, from gauges in the archive: data only; scripts/water_balance.py writes the tables and docs/reports/water-balance.md.

One equation for every reach, in acre-feet over a period (a water year, October to September, or a calendar month):

    unaccounted = inflow - outflow - change in storage - evaporation from the reservoirs in the reach

Inflow and outflow are the gauged flows across the reach's upstream and downstream ends (tributaries that are gauged count as inflow). Change in storage is the stored volume at the end of
the period minus at the start, for the reservoirs inside the reach. Evaporation is the reservoir operator's own estimate. Positive `unaccounted` means measured water went missing: it
was diverted, evaporated or transpired from the river and its fields, seeped into the ground, or a gauge reads high. Negative means more water left than was measured arriving: an ungauged
tributary, a return flow or spring, or a gauge that reads low. It is never "the error": it is everything the gauges do not see, and it includes the error of every gauge in the equation.

What is not here: diversions and returns (the irrigation districts' records are not in the archive before September 2026: see issue #12), evapotranspiration from fields and the river's
cottonwoods (OpenET is not held: issue #18), groundwater and the small ungauged arroyos. They are what `unaccounted` is made of, and the reports say so instead of filling them in.

Method
- Flow terms: daily mean cfs per gauge (river_flow.read_daily: the source's daily mean, else the Denver-day mean of its sub-daily readings), one copy per day by river_flow.PRIORITY, times 1.983471 acre-feet per cfs-day.
- A flow term is summed over the days it has data and scaled to the whole period (mean x days), only when it has at least MIN_COVERAGE of the days. Otherwise the period is `complete = False` and has no balance.
- Storage change needs a reading within STORAGE_TOL_DAYS before the first day of the period and within STORAGE_TOL_DAYS of its last.
- Volume terms (evaporation, reported inflow) are the operator's daily volumes, summed and scaled the same way.
- A comparison column (`reported_inflow`, Reclamation's own inflow for Elephant Butte) is shown but never enters the equation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import river_flow as rf
from .reservoirs import CFS_DAY_AF

log = logging.getLogger("nmwater.derived.water_balance")

MIN_COVERAGE = 0.95          # share of a period's days a flow or volume term needs
STORAGE_TOL_DAYS = 3         # how far from a period's edge a storage reading may be
FIRST_DAY = date(2005, 10, 1)


@dataclass(frozen=True)
class Term:
    """One series in a balance. `uids` are copies of the same thing from different sources (chosen per day by river_flow.PRIORITY); `kind` says how its values combine:
    flow (cfs, becomes acre-feet), volume (acre-feet a day) or storage (acre-feet at a time)."""
    key: str
    label: str
    role: str                 # inflow | outflow | storage | evaporation | reference
    kind: str                 # flow | volume | storage
    uids: tuple[str, ...]
    variable: str = "discharge"
    note: str = ""


@dataclass(frozen=True)
class Reach:
    key: str
    title: str
    upstream: str
    downstream: str
    terms: tuple[Term, ...]
    reading: str              # how to read `unaccounted` for this reach
    omitted: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=())


_FLOODWAY = ("usgs:08358400", "usbr_hydrodata:1542", "codwr:SMFRIONM", "usace_cwms:San Marcial")
_CONVEYANCE = ("usgs:08358300", "usbr_hydrodata:1541", "codwr:SMCRIONM", "usace_cwms:San Marcial CC")
SAN_MARCIAL = (
    Term("san_marcial_floodway", "Rio Grande Floodway at San Marcial", "inflow", "flow", _FLOODWAY),
    Term("san_marcial_conveyance", "Rio Grande Conveyance Channel at San Marcial", "inflow", "flow", _CONVEYANCE,
         note="San Marcial has two channels, the floodway and the Low Flow Conveyance Channel; the water is the two together"),
)
REACHES: dict[str, Reach] = {
    "elephant_butte": Reach(
        key="elephant_butte", title="Elephant Butte Reservoir", upstream="San Marcial (floodway and conveyance channel)", downstream="Elephant Butte Dam",
        terms=(
            *SAN_MARCIAL,
            Term("eb_release_out", "Rio Grande below Elephant Butte Dam", "outflow", "flow", ("usgs:08361000",)),
            Term("eb_storage", "Elephant Butte storage", "storage", "storage", ("usbr_hydrodata:1119",), "reservoir_storage"),
            Term("eb_evaporation", "Elephant Butte evaporation (Reclamation's estimate)", "evaporation", "volume", ("usbr_hydrodata:1119",), "reservoir_evaporation"),
            Term("eb_reported_inflow", "Elephant Butte inflow as Reclamation reports it", "reference", "volume", ("usbr_hydrodata:1119",), "reservoir_inflow_volume",
                 "Reclamation computes this from its own release, storage change and evaporation, so it is the lake's mass balance, not a measurement"),
        ),
        reading="Negative here is water that reached the lake without passing San Marcial: the river's last miles and the delta below the gauges, and the gauges' own error.",
        omitted=("inflow between San Marcial and the lake (ungauged arroyos, the delta)",),
    ),
    "middle_rio_grande": Reach(
        key="middle_rio_grande", title="Middle Rio Grande", upstream="Otowi Bridge, with the Jemez, Galisteo and Rio Puerco", downstream="San Marcial (floodway and conveyance channel)",
        terms=(
            Term("otowi", "Rio Grande at Otowi Bridge", "inflow", "flow", ("usgs:08313000", "codwr:RIOOTANM", "usace_cwms:Otowi"),
                 note="includes the Rio Chama, which joins above Otowi"),
            Term("galisteo", "Galisteo Creek below Galisteo Dam", "inflow", "flow", ("usgs:08317950",)),
            Term("jemez", "Jemez River below Jemez Canyon Dam", "inflow", "flow", ("usgs:08328950", "usgs:08329000", "usbr_hydrodata:1530"),
                 note="the record is at 08329000 until 29 September 2009 and at 08328950 from 30 September 2009"),
            Term("puerco", "Rio Puerco near Bernardo", "inflow", "flow", ("usgs:08353000", "usbr_hydrodata:1539", "usace_cwms:Rio Puerco"),
                 note="USGS has no record from November 2021 to September 2023; the Reclamation and Corps copies of the same gauge fill it"),
            *[Term(t.key, t.label, "outflow", "flow", t.uids, note=t.note) for t in SAN_MARCIAL],
            Term("cochiti_storage", "Cochiti Lake storage", "storage", "storage", ("usbr_hydrodata:2696",), "reservoir_storage"),
            Term("cochiti_evaporation", "Cochiti Lake evaporation (Reclamation's estimate)", "evaporation", "volume", ("usbr_hydrodata:2696",), "reservoir_evaporation"),
        ),
        reading="Positive is water the reach used up or lost: diverted by the Middle Rio Grande Conservancy District and not returned, evaporated and transpired along the river and its fields, "
                "seeped into the ground, or read high by a gauge. Negative is more arriving than measured: drain and wastewater returns, springs and the arroyos below Albuquerque.",
        omitted=("the Rio Salado (its gauge stopped in 1984)", "Tijeras and the Albuquerque arroyos", "MRGCD diversions and returns (not in the archive before September 2026)",
                 "evapotranspiration from fields and riparian forest", "groundwater exchange"),
        notes=("Jemez Canyon and Galisteo Reservoirs are dry flood-control pools; their releases are gauged below the dams, so their storage does not enter the equation.",),
    ),
    "caballo": Reach(
        key="caballo", title="Elephant Butte Dam to Caballo Dam", upstream="below Elephant Butte Dam", downstream="below Caballo Dam",
        terms=(
            Term("eb_release_in", "Rio Grande below Elephant Butte Dam", "inflow", "flow", ("usgs:08361000",)),
            Term("caballo_release", "Rio Grande below Caballo Dam", "outflow", "flow", ("usgs:08362500", "usbr_hydrodata:1113")),
            Term("caballo_storage", "Caballo storage", "storage", "storage", ("usbr_hydrodata:1094",), "reservoir_storage"),
            Term("caballo_evaporation", "Caballo evaporation (Reclamation's estimate)", "evaporation", "volume", ("usbr_hydrodata:1094",), "reservoir_evaporation"),
        ),
        reading="Positive is water lost between the dams: evaporation and seepage from the river and the Caballo pool beyond Reclamation's estimate, diversions, riparian use, or a gauge reading high. "
                "Negative is inflow between the dams: small tributaries and drains, springs, or a gauge reading low.",
        omitted=("ungauged tributaries and drains between the dams", "diversions between the dams, if any"),
        notes=("Nothing downstream of Caballo is balanced: El Paso discharge in the archive stops in 1982 (USGS) and restarts in April 2026 (IBWC), and the Elephant Butte Irrigation District's "
               "diversions are not in the archive (issue #12).",),
    ),
}

# ---------------------------------------------------------------------------- reading
def _empty() -> pd.Series:
    return pd.Series(dtype=float, index=pd.DatetimeIndex([]))


def read_term(con, term: Term, start: date, end: date) -> pd.Series:
    """One value per day for a term, indexed by date: cfs for flow, acre-feet for volume, acre-feet at the day's mean for storage."""
    if term.kind == "flow" and term.variable == "discharge":
        d = rf.read_daily(con, list(term.uids), start, end)
        if d.empty:
            return _empty()
        d = d.rename(columns={"cfs": "value"})
    else:
        ids = ",".join("'" + u.replace("'", "''") + "'" for u in term.uids)
        d = con.sql(f"""SELECT site_uid, datetime_utc::DATE AS date, avg(value) AS value FROM observations_clean
                        WHERE variable = '{term.variable}' AND site_uid IN ({ids})
                          AND datetime_utc >= '{start}' AND datetime_utc < '{end + timedelta(days=1)}' GROUP BY 1, 2""").df()
        if d.empty:
            return _empty()
    return one_copy(d)


def one_copy(d: pd.DataFrame) -> pd.Series:
    """Per day, the value from the copy earliest in river_flow.PRIORITY (ties by site id)."""
    d = d.assign(gauge="g", source=d.site_uid.str.split(":").str[0])
    d = rf.pick_copy(d)
    s = d.set_index(pd.to_datetime(d.date))["value"].sort_index()
    return s[~s.index.duplicated()]


# ---------------------------------------------------------------------------- periods
def water_year(d: pd.Timestamp) -> int:
    return d.year + (1 if d.month >= 10 else 0)


def periods(first: date, last: date, by: str) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
    """(label, first day, last day) of every water year or month that overlaps first..last."""
    out = []
    if by == "water_year":
        for wy in range(water_year(pd.Timestamp(first)), water_year(pd.Timestamp(last)) + 1):
            out.append((f"WY{wy}", pd.Timestamp(wy - 1, 10, 1), pd.Timestamp(wy, 9, 30)))
    elif by == "month":
        for m in pd.period_range(first, last, freq="M"):
            out.append((str(m), m.start_time, m.end_time.normalize()))
    else:
        raise ValueError(f"by must be water_year or month, not {by!r}")
    return out


def period_total(s: pd.Series, a: pd.Timestamp, b: pd.Timestamp, to_af: float) -> tuple[float, float]:
    """(acre-feet over the period, share of its days with data): the mean of the days present, scaled to every day, so a few missing days do not shrink it."""
    days = (b - a).days + 1
    x = s[(s.index >= a) & (s.index <= b)].dropna()
    cov = len(x) / days
    if cov < MIN_COVERAGE:
        return float("nan"), cov
    return float(x.mean() * days * to_af), cov


def storage_at(s: pd.Series, day: pd.Timestamp, tol: int = STORAGE_TOL_DAYS, after: bool = False) -> float:
    """The storage reading on `day`, or the nearest one within `tol` days before it (`after`: after it)."""
    lo, hi = (day, day + pd.Timedelta(days=tol)) if after else (day - pd.Timedelta(days=tol), day)
    x = s[(s.index >= lo) & (s.index <= hi)].dropna()
    if x.empty:
        return float("nan")
    return float(x.iloc[0] if after else x.iloc[-1])


def balance(series: dict[str, pd.Series], reach: Reach, first: date, last: date, by: str = "water_year") -> pd.DataFrame:
    """The balance of `reach` for every period in first..last. `series`: term key -> daily Series (cfs / acre-feet / acre-feet stored).
    Columns: period, start, end, days, complete, one acre-feet column per term, inflow_af, outflow_af, storage_change_af, evaporation_af, implied_inflow_af, unaccounted_af,
    unaccounted_pct_of_inflow, min_coverage."""
    rows = []
    for label, a, b in periods(first, last, by):
        days = (b - a).days + 1
        row: dict = {"period": label, "start": a.date(), "end": b.date(), "days": days}
        cover: list[float] = []
        for t in reach.terms:
            s = series.get(t.key, _empty())
            if t.kind == "storage":
                continue
            v, c = period_total(s, a, b, CFS_DAY_AF if t.kind == "flow" else 1.0)
            row[t.key] = v
            if t.role != "reference":
                cover.append(c)
        storage = [t for t in reach.terms if t.kind == "storage"]
        dstore = 0.0
        for t in storage:
            s = series.get(t.key, _empty())
            # the closing reading is the last one in the period; the opening one is the last before it
            end_v = storage_at(s, b)
            start_v = storage_at(s, a - pd.Timedelta(days=1))
            row[t.key + "_change"] = end_v - start_v
            dstore += end_v - start_v
        sums = {role: sum(row[t.key] for t in reach.terms if t.role == role) for role in ("inflow", "outflow", "evaporation")}
        row["inflow_af"], row["outflow_af"], row["evaporation_af"] = sums["inflow"], sums["outflow"], sums["evaporation"]
        row["storage_change_af"] = dstore
        row["implied_inflow_af"] = row["outflow_af"] + dstore + row["evaporation_af"]     # what the downstream side says arrived
        row["min_coverage"] = min(cover) if cover else float("nan")
        total = row["inflow_af"] - row["outflow_af"] - row["storage_change_af"] - row["evaporation_af"]
        row["complete"] = bool(np.isfinite(total))
        row["unaccounted_af"] = total if row["complete"] else float("nan")
        row["unaccounted_pct_of_inflow"] = (100 * total / row["inflow_af"]) if row["complete"] and row["inflow_af"] > 0 else float("nan")
        rows.append(row)
    return pd.DataFrame(rows)


def read_reach(con, reach: Reach, first: date = FIRST_DAY, last: date | None = None) -> dict[str, pd.Series]:
    """Every term's daily series, from a little before `first` (for the opening storage reading) to `last` (default today)."""
    end = last or date.today()
    return {t.key: read_term(con, t, first - timedelta(days=STORAGE_TOL_DAYS + 1), end) for t in reach.terms}


def newest_day(series: dict[str, pd.Series]) -> date | None:
    days = [s.index.max() for s in series.values() if len(s)]
    return max(days).date() if days else None


def run(con, reach: Reach, by: str = "water_year", first: date = FIRST_DAY, last: date | None = None) -> pd.DataFrame:
    """Read the reach's series from the catalog and balance them. `last` defaults to the newest day any term has."""
    series = read_reach(con, reach, first, last)
    end = last or newest_day(series)
    return balance(series, reach, first, end, by) if end else pd.DataFrame()
