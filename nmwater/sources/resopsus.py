"""ResOpsUS (Steyaert et al. 2022): daily historical reservoir operations, 1930s-2020, 679 US dams.

Zenodo record 6612040, file ResOpsUS.zip (~300 MB). The zip is streamed once into the raw archive
(kept, it is the raw artifact); attributes and time series for dams in scope are extracted.
Time series columns: date, storage (MCM), inflow (cms), outflow (cms), elevation (m), evaporation
(cms or MCM depending on agency; see agency_attributes). Keyed by GRanD DAM_ID.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

from ..core.grids import record_grid, stream_download
from ..core.http import request_key
from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.resopsus")

ZIP_URL = "https://zenodo.org/api/records/6612040/files/ResOpsUS.zip/content"
CITATION = ("Steyaert, J.C., Condon, L.E., Turner, S.W.D., Voisin, N. (2022). ResOpsUS, a dataset of historical "
            "reservoir operations in the contiguous United States. Sci Data 9, 34. https://doi.org/10.5281/zenodo.6612040")
EXTRA_NAMES = {"Platoro", "Costilla Dam", "Eagle Nest", "McClure", "Bluewater", "Galisteo"}


@register
class ResOpsUS(Source):
    name = "resopsus"
    agency = "University of Arizona / PNNL"
    description = "ResOpsUS daily reservoir storage/inflow/outflow/elevation/evaporation for NM-basin dams (to 2020)"

    def _zip_path(self) -> Path:
        return self.settings.raw_dir / self.name / "ResOpsUS.zip"

    def _ensure_zip(self, refresh: bool, summ: FetchSummary) -> Path:
        zp = self._zip_path()
        key = request_key("GET", ZIP_URL, None)
        rec = self.ledger.get(key)
        if zp.exists() and rec and rec.status == "ok" and not refresh:
            summ.n_cached += 1
            return zp
        status, sha, nb = stream_download(self.http, self.name, ZIP_URL, zp)
        summ.n_requests += 1
        record_grid(self.ledger, self.run_id, self.name, ZIP_URL, None, zp, sha, nb, variable="zip", key=key)
        return zp

    def _attributes(self, zp: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        with zipfile.ZipFile(zp) as z:
            attrs = pd.read_csv(z.open("ResOpsUS/attributes/reservoir_attributes.csv"), dtype=str)
            agencies = pd.read_csv(z.open("ResOpsUS/attributes/agency_attributes.csv"), dtype=str)
            inv = pd.read_csv(z.open("ResOpsUS/attributes/time_series_inventory.csv"), dtype=str)
        return attrs, agencies, inv

    def _in_scope(self, attrs: pd.DataFrame) -> pd.DataFrame:
        lat = pd.to_numeric(attrs["LAT"], errors="coerce")
        lon = pd.to_numeric(attrs["LONG"], errors="coerce")
        m = pd.Series([self.scope.in_bbox(a, b) for a, b in zip(lat, lon)], index=attrs.index)
        m |= attrs["STATE"].str.strip().eq("New Mexico")
        m |= attrs["DAM_NAME"].isin(EXTRA_NAMES)
        return attrs[m]

    def discover(self) -> pd.DataFrame:
        summ = FetchSummary(self.name)
        zp = self._ensure_zip(False, summ)
        attrs, agencies, inv = self._attributes(zp)
        self.store.write_table(attrs, "reference", self.name, "reservoir_attributes")
        self.store.write_table(agencies, "reference", self.name, "agency_attributes")
        self.store.write_table(inv, "reference", self.name, "time_series_inventory")
        sel = self._in_scope(attrs)
        rows = []
        for r in sel.itertuples(index=False):
            rows.append({"native_id": str(r.DAM_ID), "name": r.DAM_NAME, "lat": float(r.LAT), "lon": float(r.LONG),
                         "site_type": "reservoir", "agency": r.AGENCY_CODE,
                         "state": {"New Mexico": "NM", "Colorado": "CO", "Texas": "TX", "Arizona": "AZ"}.get(r.STATE, r.STATE),
                         "active": False,
                         "raw_metadata": json.dumps({"GRAND_ID": r.DAM_ID, "start": r.TIME_SERIES_START,
                                                     "end": r.TIME_SERIES_END, "notes": r.INCONSISTENCIES_NOTED})})
        return pd.DataFrame(rows)

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        zp = self._ensure_zip(refresh, summ)
        attrs, agencies, inv = self._attributes(zp)
        sel = self._in_scope(attrs)
        if site_ids:
            sel = sel[sel["DAM_ID"].isin(site_ids)]
        if limit:
            sel = sel.head(limit)
        evap_units = {}
        for r in agencies.to_dict("records"):
            code = r.get("AGENCY_CODE")
            note = " ".join(str(v) for v in r.values() if v)
            evap_units[code] = "mcm" if "million" in note.lower() or "mcm" in note.lower() else "cms"
        n = 0
        with zipfile.ZipFile(zp) as z:
            for r in sel.itertuples(index=False):
                member = f"ResOpsUS/time_series_all/ResOpsUS_{r.DAM_ID}.csv"
                try:
                    raw = z.read(member)
                except KeyError:
                    summ.notes.append(f"no time series for DAM_ID {r.DAM_ID} ({r.DAM_NAME})")
                    continue
                df = pd.read_csv(io.BytesIO(raw))
                df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True)
                long = df.melt(id_vars=["date"], var_name="source_param", value_name="value").dropna(subset=["value"])
                long = long[long["date"].notna()]
                if since is not None:
                    long = long[long["date"] >= pd.Timestamp(since, tz="UTC")]
                ev = evap_units.get(r.AGENCY_CODE, "cms")
                long.loc[long["source_param"] == "evaporation", "source_param"] = f"evaporation_{ev}"
                out = pd.DataFrame({"site_uid": self.uid(str(r.DAM_ID)), "datetime_utc": long["date"], "value": long["value"],
                                    "source_param": long["source_param"], "interval": "daily", "statistic": None,
                                    "qualifier": None, "utc_offset_min": None})
                out = self.xw.apply(out, self.name)
                n += self.write_obs(out, tag=str(r.DAM_ID))
        summ.n_rows = n
        return summ
