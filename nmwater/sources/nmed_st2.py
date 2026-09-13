"""NMED Drinking Water Bureau SDWIS results via the NM Water Data Initiative SensorThings hub.

54,616 Things (public-water-system sampling points), ~791k Datastreams (one per sampling
point x analyte x method), ~3.2M Observations (results as strings such as "1.8 mg/L" or
"< MRL 0.0001 MG/L", with analyte details in `parameters`).

Results land in the `waterquality` group (table sdwis_results). Analytes with canonical
variables are additionally written to observations (interval irregular, statistic sample),
keyed in the crosswalk as "<ANALYTE>|<unit>".
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

import pandas as pd

from .base import FetchSummary, register
from .sensorthings import SensorThingsSource

log = logging.getLogger("nmwater.nmed_st2")

RESULT_RE = re.compile(r"^\s*(?P<flag>[<>]=?)?\s*(?:(?P<lvl>[A-Z]{2,4})\s+)?(?P<num>-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*(?P<unit>[A-Za-z/%°µ]+(?:/[A-Za-z]+)?)?\s*$")

SITE_TYPE_BY_CODE = {"WL": "well", "SP": "spring", "IN": "stream", "IG": "stream", "RS": "reservoir"}


def parse_result(s) -> tuple[float | None, str | None, str | None]:
    """'< MRL 0.0001 MG/L' -> (0.0001, 'MG/L', '<'); '1.8 mg/L' -> (1.8, 'mg/L', None)."""
    if s is None:
        return None, None, None
    if isinstance(s, (int, float)):
        return float(s), None, None
    m = RESULT_RE.match(str(s))
    if not m:
        return None, None, None
    try:
        v = float(m.group("num"))
    except ValueError:
        return None, None, None
    return v, m.group("unit"), m.group("flag")


@register
class NMEDST2(SensorThingsSource):
    name = "nmed_st2"
    agency = "NMED DWB"
    description = "NMED SDWIS drinking-water chemistry via SensorThings (3.2M results, 54k sampling points)"
    kinds = ("sites", "datastreams", "obs")

    # -- discovery -------------------------------------------------------------
    def discover(self) -> pd.DataFrame:
        things = self.list_things(expand="Locations($select=location)",
                                  select="@iot.id,name,description,properties", refresh=True)
        rows = []
        for t in things:
            p = t.get("properties") or {}
            locs = t.get("Locations") or []
            coords = ((locs[0].get("location") or {}).get("coordinates") if locs else None) or [None, None]
            tc = p.get("type_code")
            src = p.get("source_type_code")
            stype = SITE_TYPE_BY_CODE.get(tc) or SITE_TYPE_BY_CODE.get(src) or "other"
            desc = (t.get("description") or "").upper()
            if stype == "other":
                if "WELL" in desc:
                    stype = "well"
                elif "SPRING" in desc:
                    stype = "spring"
                elif "INTAKE" in desc or "RIVER" in desc:
                    stype = "stream"
            rows.append({
                "native_id": str(t["@iot.id"]),
                "name": f"{p.get('identification_cd') or t.get('name')} {t.get('description') or ''}".strip(),
                "lat": coords[1] if len(coords) > 1 else None,
                "lon": coords[0] if coords else None,
                "site_type": stype, "agency": "NMED DWB", "state": "NM",
                "active": p.get("activity_status_code") == "A",
                "raw_metadata": json.dumps({"pws_id": p.get("tinwsf0is_number"), "sampling_point_id": p.get("identification_cd"),
                                            "sampling_point_type": tc, "source_type": src,
                                            "properties": p}, default=str),
            })
        # Datastream map (needed to attribute observations to sampling points)
        ds = self.list_datastreams(select="@iot.id,name,unitOfMeasurement,phenomenonTime",
                                   expand="Thing($select=@iot.id)", refresh=True)
        dsm = pd.DataFrame([{
            "datastream_id": d["@iot.id"], "thing_id": (d.get("Thing") or {}).get("@iot.id"), "name": d.get("name"),
            "unit": (d.get("unitOfMeasurement") or {}).get("symbol"), "phenomenon_time": d.get("phenomenonTime"),
        } for d in ds])
        if len(dsm):
            parts = dsm["name"].str.split("_", n=2, expand=True)
            dsm["sampling_point_id"] = parts[0]
            dsm["analyte"] = parts[1] if 1 in parts else None
            dsm["method"] = parts[2] if 2 in parts else None
        self.store.write_table(dsm, "reference", self.name, "datastreams")
        log.info("nmed_st2: %d things, %d datastreams", len(rows), len(dsm))
        return pd.DataFrame(rows)

    def _ds_map(self) -> pd.DataFrame:
        p = self.store.root / "reference" / f"source={self.name}" / "datastreams.parquet"
        if not p.exists():
            raise RuntimeError("run `nmwater discover nmed_st2` first (datastream map missing)")
        return pd.read_parquet(p).set_index("datastream_id")

    # -- fetch -----------------------------------------------------------------
    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        dsm = self._ds_map()
        dropped = 0

        def handle(art, df) -> int:
            nonlocal dropped
            summ.n_requests += 1
            if art.from_cache:
                summ.n_cached += 1
                if self.already_written(art):
                    return 0
            if df.empty:
                self.ledger.set_rows(art.request_key, 0)
                return 0
            wq, obs, bad = self._normalize_frame(df, dsm)
            dropped += bad
            if len(wq):
                self.store.append_table(wq, "waterquality", self.name, "sdwis_results", self.run_id)
            if len(obs):
                self.write_obs(obs, tag="sdwis")
            self.ledger.set_rows(art.request_key, len(wq))
            return len(wq)

        if site_ids:
            ds_ids = dsm.index[dsm["thing_id"].astype(str).isin([str(s) for s in site_ids])]
            for ds_id in ds_ids:
                for art, df in self.observations_for_datastream(int(ds_id), since=since, refresh=refresh, want_params=True):
                    summ.n_rows += handle(art, df)
        else:
            # --limit = number of 5,000-row pages (full archive is ~640 pages)
            for art, df in self.iter_observations_expanded(since=since, refresh=refresh, max_pages=limit,
                                                           want_params=True):
                summ.n_rows += handle(art, df)
        if dropped:
            summ.notes.append(f"dropped {dropped} results with implausible sample dates")
        return summ

    def _normalize_frame(self, df: pd.DataFrame, dsm: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, int]:
        info = dsm.reindex(df["datastream_id"])
        parsed = df["result"].map(parse_result)
        params = df.get("parameters")
        params = params.map(lambda p: p if isinstance(p, dict) else {}) if params is not None else pd.Series([{}] * len(df))
        t = self.clean_times(df["phenomenon_time"])
        bad = int(t.isna().sum() - df["phenomenon_time"].isna().sum())
        unit_ds = info["unit"].values
        wq = pd.DataFrame({
            "site_uid": ("nmed_st2:" + info["thing_id"].astype("Int64").astype(str)).values,
            "datetime_utc": t.values,
            "analyte": [p.get("analyte_name") or a for p, a in zip(params, info["analyte"].values)],
            "analyte_code": [p.get("analyte_code") for p in params],
            "method": [p.get("method") or m for p, m in zip(params, info["method"].values)],
            "value": [x[0] for x in parsed],
            "unit": [x[1] or u for x, u in zip(parsed, unit_ds)],
            "detection_flag": [x[2] or p.get("less_than_ind") for x, p in zip(parsed, params)],
            "reporting_level": [p.get("reporting_level") for p in params],
            "sample_number": [p.get("sample_number") for p in params],
            "result_text": df["result"].astype(str).values,
            "datastream_id": df["datastream_id"].values,
            "obs_id": df["obs_id"].values,
            "raw_properties": [json.dumps(p, default=str) for p in params],
        })
        wq = wq[wq["datetime_utc"].notna() & wq["site_uid"].notna() & (wq["site_uid"] != "nmed_st2:<NA>")]
        # canonical observations for mapped analytes (detects excluded)
        cand = wq[wq["value"].notna() & wq["detection_flag"].isna()]
        obs = pd.DataFrame({
            "site_uid": cand["site_uid"], "datetime_utc": cand["datetime_utc"], "value": cand["value"],
            "source_param": cand["analyte"].astype(str).str.upper() + "|" + cand["unit"].astype(str).str.lower(),
            "source_unit": cand["unit"], "qualifier": cand["method"], "utc_offset_min": None,
            "statistic": "sample", "interval": "irregular",
        })
        obs = self.xw.apply(obs, self.name)
        return wq, obs, bad
