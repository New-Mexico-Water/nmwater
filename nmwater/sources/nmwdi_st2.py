"""NM Water Data Initiative SensorThings hub (ST2): groundwater levels from PVACD (1946-),
Bernalillo County, CABQ/ABCWUA, EBWPC, OSE-Roswell, EBID, San Acacia reach.

Sites = Locations (native_id = Location @iot.id). Datastreams are mapped to sites through
their Thing. The 330 "OSERealTime" datastreams carry no observations (phenomenonTime null)
and are skipped.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import pandas as pd

from .base import FetchSummary, register
from .sensorthings import SensorThingsSource

log = logging.getLogger("nmwater.nmwdi_st2")


@register
class NMWDIST2(SensorThingsSource):
    name = "nmwdi_st2"
    agency = "NMWDI"
    description = "NM Water Data Initiative SensorThings: groundwater levels, 9 agencies (1946-)"
    kinds = ("sites", "datastreams", "obs")

    def _things(self, refresh: bool = True, limit: int | None = None) -> list[dict]:
        expand = ("Locations,Datastreams($select=@iot.id,name,unitOfMeasurement,phenomenonTime,properties;"
                  "$expand=ObservedProperty($select=name))")
        return self.list_things(expand=expand, refresh=refresh, limit=limit)

    def _datastream_table(self, things: list[dict]) -> pd.DataFrame:
        rows = []
        for t in things:
            locs = t.get("Locations") or []
            loc_id = locs[0]["@iot.id"] if locs else None
            tp = t.get("properties") or {}
            for ds in t.get("Datastreams") or []:
                rows.append({
                    "datastream_id": ds["@iot.id"], "thing_id": t["@iot.id"], "location_id": loc_id,
                    "name": ds.get("name"), "observed_property": (ds.get("ObservedProperty") or {}).get("name"),
                    "unit": (ds.get("unitOfMeasurement") or {}).get("symbol"),
                    "phenomenon_time": ds.get("phenomenonTime"),
                    "agency": (ds.get("properties") or {}).get("agency") or tp.get("agency"),
                    "has_data": bool(ds.get("phenomenonTime")),
                })
        return pd.DataFrame(rows)

    def discover(self) -> pd.DataFrame:
        things = self._things(refresh=True)
        dst = self._datastream_table(things)
        self.store.write_table(dst, "reference", self.name, "datastreams")
        sites = {}
        for t in things:
            tp = t.get("properties") or {}
            for loc in t.get("Locations") or []:
                lid = str(loc["@iot.id"])
                coords = (loc.get("location") or {}).get("coordinates") or [None, None]
                lp = loc.get("properties") or {}
                wd = tp.get("well_depth")
                if isinstance(wd, dict):
                    wd = wd.get("value")
                ds_names = [d.get("name") for d in (t.get("Datastreams") or [])]
                site = sites.get(lid) or {
                    "native_id": lid,
                    "name": loc.get("name"),
                    "lat": coords[1] if len(coords) > 1 else None,
                    "lon": coords[0] if len(coords) > 0 else None,
                    "elevation_m": coords[2] if len(coords) > 2 else None,
                    "site_type": "well",
                    "agency": lp.get("agency") or tp.get("agency"),
                    "state": "NM",
                    "well_depth_m": float(wd) * 0.3048 if wd not in (None, "", 0) else None,
                    "aquifer": tp.get("aquifer") if isinstance(tp.get("aquifer"), str) else None,
                    "active": True,
                    "raw_metadata": None,
                    "_things": [],
                }
                site["_things"].append({"thing_id": t["@iot.id"], "thing_name": t.get("name"),
                                        "description": loc.get("description"), "properties": tp,
                                        "location_properties": lp, "datastreams": ds_names})
                sites[lid] = site
        for s in sites.values():
            s["raw_metadata"] = json.dumps({"things": s.pop("_things")}, default=str)
        return pd.DataFrame(list(sites.values()))

    def _ds_map(self) -> pd.DataFrame:
        p = self.store.root / "reference" / f"source={self.name}" / "datastreams.parquet"
        if not p.exists():
            raise RuntimeError("run `nmwater discover nmwdi_st2` first (datastream map missing)")
        return pd.read_parquet(p)

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        dsm = self._ds_map()
        dsm = dsm[dsm["has_data"]]
        if site_ids:
            dsm = dsm[dsm["location_id"].astype(str).isin([str(s) for s in site_ids])]
        ds_info = dsm.set_index("datastream_id")
        dropped_bad = 0

        def handle(art, df) -> int:
            nonlocal dropped_bad
            summ.n_requests += 1
            if art.from_cache:
                summ.n_cached += 1
                if self.already_written(art):
                    return 0
            if df.empty:
                self.ledger.set_rows(art.request_key, 0)
                return 0
            out = self._normalize_frame(df, ds_info)
            dropped_bad += out.attrs.get("dropped_bad", 0)
            n = self.write_obs(out, tag="st2") if len(out) else 0
            self.ledger.set_rows(art.request_key, n)
            return n

        if site_ids or (limit and limit < 50):
            ds_ids = list(ds_info.index)[: (limit or len(ds_info))] if not site_ids else list(ds_info.index)
            for ds_id in ds_ids:
                for art, df in self.observations_for_datastream(int(ds_id), since=since, refresh=refresh):
                    summ.n_rows += handle(art, df)
        else:
            for art, df in self.iter_observations_expanded(since=since, refresh=refresh, max_pages=limit):
                summ.n_rows += handle(art, df)
        if dropped_bad:
            summ.notes.append(f"dropped {dropped_bad} observations with implausible timestamps (year <1900 or > next year)")
        return summ

    def _normalize_frame(self, df: pd.DataFrame, ds_info: pd.DataFrame) -> pd.DataFrame:
        df = df[df["datastream_id"].isin(ds_info.index)]
        if df.empty:
            return df
        t = self.clean_times(df["phenomenon_time"])
        bad = int(t.isna().sum() - df["phenomenon_time"].isna().sum())
        info = ds_info.loc[df["datastream_id"]]
        out = pd.DataFrame({
            "site_uid": ("nmwdi_st2:" + info["location_id"].astype("Int64").astype(str)).values,
            "datetime_utc": t.values,
            "value": pd.to_numeric(df["result"], errors="coerce").values,
            "source_param": info["observed_property"].values,
            "source_unit": info["unit"].values,
            "qualifier": None,
            "utc_offset_min": None,
            "statistic": "instantaneous",
            "interval": info["name"].str.contains("Manual", case=False, na=False).map(
                {True: "irregular", False: "instant"}).values,
            "_ds": df["datastream_id"].values,
        })
        out = out[out["datetime_utc"].notna() & out["value"].notna()]
        # transducer streams with ~hourly spacing -> 'hourly'
        for ds, g in out[out["interval"] == "instant"].groupby("_ds"):
            if len(g) > 5:
                med = g["datetime_utc"].sort_values().diff().median()
                if pd.notna(med) and pd.Timedelta("50min") <= med <= pd.Timedelta("70min"):
                    out.loc[g.index, "interval"] = "hourly"
        out = out.drop(columns=["_ds"])
        out = self.xw.apply(out, self.name)
        out.attrs["dropped_bad"] = bad
        return out
