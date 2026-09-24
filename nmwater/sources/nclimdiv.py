"""NOAA nClimDiv monthly climate-division and county series, 1895-present.

Directory: https://www.ncei.noaa.gov/monitoring-content/data/us/climdiv/monthly/current/
Files:     climdiv-<elem><dv|cy>-v1.0.0-YYYYMMDD  (fixed width: id, then 12 monthly values)
  divisional id: SS DD EE YYYY   (state code 29 = New Mexico in climdiv numbering; not FIPS)
  county id:     SS CCC EE YYYY  (SS climdiv state code, CCC county FIPS)
Element codes: 01 pcpn, 02 tmpc, 05 pdsi, 06 phdi, 07 zndx, 08 pmdi, 27 tmax, 28 tmin, 71-77 sp01..sp24.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

import pandas as pd

from ..core.constants import NM_COUNTY_FIPS
from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.nclimdiv")

BASE = "https://www.ncei.noaa.gov/monitoring-content/data/us/climdiv/monthly/current/"
DIV_ELEMS = ["pcpn", "tmpc", "tmax", "tmin", "pdsi", "phdi", "zndx", "pmdi",
             "sp01", "sp02", "sp03", "sp06", "sp09", "sp12", "sp24"]
CTY_ELEMS = ["pcpn", "tmpc", "tmax", "tmin", "pdsi", "phdi"]
# climdiv state codes (alphabetical, not FIPS)
STATE_CODES = {"29": "NM", "05": "CO", "02": "AZ", "41": "TX", "34": "OK", "42": "UT"}
NM_DIV_NAMES = {"01": "Northwestern Plateau", "02": "Northern Mountains", "03": "Northeastern Plains",
                "04": "Southwestern Desert", "05": "Central Valley", "06": "Central Highlands",
                "07": "Southeastern Plains", "08": "Southern Desert"}
MISSING = {-99.99, -99.9, -9.99, -99.0}


@register
class NClimDiv(Source):
    name = "nclimdiv"
    agency = "NOAA NCEI"
    description = "nClimDiv monthly precip/temp/PDSI/PHDI/SPI by climate division and county (1895-)"

    def _listing(self, refresh: bool = True) -> dict[str, str]:
        art = self.get(BASE, kind="listing", refresh=refresh)
        html = art.read_text()
        names = re.findall(r'href="(climdiv-[a-z0-9]+-v[0-9.]+-\d{8})"', html)
        return {n.split("-")[1]: n for n in names}

    def _states(self) -> list[str]:
        extra = self.opt("neighbor_states", True)
        return ["29"] + (["05", "02", "41", "34"] if extra else [])

    def discover(self) -> pd.DataFrame:
        rows = []
        for sc in self._states():
            ndiv = 8 if sc == "29" else 10
            for d in range(1, ndiv + 1):
                did = f"{sc}{d:02d}"
                rows.append({"native_id": f"div:{did}", "name": f"Climate division {did} " + NM_DIV_NAMES.get(f"{d:02d}", "") if sc == "29" else f"Climate division {did}",
                             "site_type": "area", "agency": "NOAA NCEI", "state": STATE_CODES.get(sc), "active": True,
                             "raw_metadata": json.dumps({"climdiv_state": sc, "division": d})})
        for fips in NM_COUNTY_FIPS:
            rows.append({"native_id": f"cty:{fips}", "name": f"County {fips}", "site_type": "area",
                         "agency": "NOAA NCEI", "state": "NM", "county_fips": fips, "active": True,
                         "raw_metadata": json.dumps({"climdiv_state": "29", "county": fips[2:]})})
        return pd.DataFrame(rows)

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        files = self._listing()
        jobs = [f"{e}dv" for e in DIV_ELEMS] + [f"{e}cy" for e in CTY_ELEMS]
        jobs = [j for j in jobs if j in files]
        if limit:
            jobs = jobs[:limit]

        def one(j: str) -> int:
            url = BASE + files[j]
            # filename carries the release date, so a new release is a new request key automatically
            art = self.get(url, kind="data", variable=j, refresh=refresh)
            summ.n_requests += 1
            if art.from_cache:
                summ.n_cached += 1
                if self.already_written(art):
                    return 0
            df = self.normalize(art)
            n = self.write_obs(df, tag=j) if df is not None else 0
            self.ledger.set_rows(art.request_key, n)
            return n

        res = self.parallel(one, jobs, desc="files")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(jobs) - len(res)
        return summ

    def normalize(self, artifact) -> pd.DataFrame | None:
        fname = artifact.url.rstrip("/").split("/")[-1]
        m = re.match(r"climdiv-([a-z]+\d*)(dv|cy)-", fname)
        if not m:
            return None
        elem, level = m.group(1), m.group(2)
        states = set(self._states()) if level == "dv" else {"29"}
        rows = []
        for line in artifact.read_text().splitlines():
            if not line.strip():
                continue
            parts = line.split()
            ident = parts[0]
            sc = ident[:2]
            if sc not in states:
                continue
            if level == "dv":
                area, year = ident[2:4], ident[6:10]
                nid = f"div:{sc}{area}"
            else:
                area, year = ident[2:5], ident[7:11]
                nid = f"cty:35{area}"
            vals = parts[1:13]
            for mth, v in enumerate(vals, start=1):
                try:
                    fv = float(v)
                except ValueError:
                    continue
                if fv in MISSING or fv <= -99:
                    continue
                rows.append((nid, f"{year}-{mth:02d}-01", fv))
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["nid", "dt", "value"])
        out = pd.DataFrame({
            "site_uid": self.name + ":" + df["nid"],
            "datetime_utc": pd.to_datetime(df["dt"], utc=True),
            "value": df["value"], "source_param": elem, "interval": "monthly", "statistic": None,
            "qualifier": None, "utc_offset_min": None,
        })
        return self.xw.apply(out, self.name)
