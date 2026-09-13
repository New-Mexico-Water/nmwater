"""Water Quality Portal (EPA/USGS/NWQMC) - stations and physical/chemical results for New Mexico
and the border strips.

Verified 2026-09-12:
  Station/search?countycode=US:35:028&mimeType=csv&zip=no            -> 121 stations
  Result/search?statecode=US:35&countycode=US:35:028&startDateLo=01-01-2020&startDateHi=12-31-2020
                &dataProfile=resultPhysChem&mimeType=csv&zip=no      -> 13,208 rows / 7.5 MB
Dates are MM-DD-YYYY. Results are chunked county x decade and stored in the waterquality group;
core characteristics are also crosswalked into observations.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import date

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.wqp")

BASE = "https://www.waterqualitydata.us/data"
NM_COUNTIES = [f"{c:03d}" for c in range(1, 62, 2)]  # NM county FIPS are odd 001..061
KEEP_COLS = [
    "OrganizationIdentifier", "OrganizationFormalName", "ActivityIdentifier", "ActivityTypeCode", "ActivityMediaName",
    "ActivityMediaSubdivisionName", "ActivityStartDate", "ActivityStartTime/Time", "ActivityStartTime/TimeZoneCode",
    "ActivityDepthHeightMeasure/MeasureValue", "ActivityDepthHeightMeasure/MeasureUnitCode", "ProjectIdentifier",
    "MonitoringLocationIdentifier", "MonitoringLocationName", "HydrologicCondition", "HydrologicEvent",
    "SampleCollectionMethod/MethodName", "ResultIdentifier", "ResultDetectionConditionText", "MethodSpeciationName",
    "CharacteristicName", "ResultSampleFractionText", "ResultMeasureValue", "ResultMeasure/MeasureUnitCode",
    "MeasureQualifierCode", "ResultStatusIdentifier", "StatisticalBaseCode", "ResultValueTypeName",
    "ResultCommentText", "USGSPCode", "ResultDepthHeightMeasure/MeasureValue", "ResultDepthHeightMeasure/MeasureUnitCode",
    "ResultAnalyticalMethod/MethodIdentifier", "ResultAnalyticalMethod/MethodName", "LaboratoryName", "AnalysisStartDate",
    "DetectionQuantitationLimitTypeName", "DetectionQuantitationLimitMeasure/MeasureValue",
    "DetectionQuantitationLimitMeasure/MeasureUnitCode", "ProviderName",
]
SITE_TYPE_MAP = {
    "Stream": "stream", "River/Stream": "stream", "River/Stream Perennial": "stream", "River/Stream Intermittent": "stream",
    "River/Stream Ephemeral": "stream", "Canal Drainage": "canal", "Canal Irrigation": "canal", "Canal Transport": "canal",
    "Well": "well", "Spring": "spring", "Lake, Reservoir, Impoundment": "reservoir", "Lake": "lake", "Reservoir": "reservoir",
    "Facility": "other", "Facility Municipal Sewage (POTW)": "wwtp", "Wetland": "other", "Atmosphere": "met",
    "Playa": "lake", "Seep": "spring", "Land": "other", "Storm Sewer": "outfall", "Facility Industrial": "other",
    "Facility Public Water Supply (PWS)": "other", "Aggregate groundwater use": "area", "Aggregate surface-water-use": "area",
    "Subsurface": "well", "Cave": "other", "Mine/Mine Discharge": "outfall", "Land Runoff": "other",
}
# CharacteristicName -> canonical variable; unit normalization happens in code
CORE = {
    "Temperature, water": "water_temp", "Specific conductance": "specific_conductance", "pH": "ph",
    "Dissolved oxygen (DO)": "dissolved_oxygen", "Turbidity": "turbidity", "Nitrate": "nitrate",
    "Nitrate as N": "nitrate", "Chloride": "chloride", "Sulfate": "sulfate", "Arsenic": "arsenic",
    "Fluoride": "fluoride", "Uranium": "uranium", "Total dissolved solids": "tds",
    "Uranium-238": "uranium",
}
TZ_OFFSET_MIN = {"MST": -420, "MDT": -360, "CST": -360, "CDT": -300, "PST": -480, "PDT": -420, "EST": -300, "EDT": -240,
                 "UTC": 0, "GMT": 0, "Z": 0}


def _unzip_csv(raw: bytes) -> str:
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")] or zf.namelist()
            return zf.read(names[0]).decode("utf-8", "replace")
    return raw.decode("utf-8", "replace")


@register
class WQP(Source):
    name = "wqp"
    agency = "EPA/USGS WQP"
    description = "Water Quality Portal stations and phys/chem results for NM (county x decade chunks)"
    kinds = ("sites", "results")

    def _border_strips(self):
        w, s, e, n = self.scope.bbox
        bw, bs, be, bn = self.scope.bbox_buffered
        return [(bw, n, be, bn), (bw, bs, be, s), (bw, s, w, n), (e, s, be, n)]

    # ------------------------------------------------------------------ discover
    def discover(self) -> pd.DataFrame:
        frames = []
        art = self.get(f"{BASE}/Station/search", params={"statecode": "US:35", "mimeType": "csv", "zip": "yes"},
                       kind="sites", refresh=True)
        frames.append(pd.read_csv(io.StringIO(_unzip_csv(art.read_bytes())), dtype=str, low_memory=False))
        for w, s, e, n in self._border_strips():
            try:
                art = self.get(f"{BASE}/Station/search", params={"bBox": f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}", "mimeType": "csv",
                                                                 "zip": "yes"}, kind="sites", refresh=True)
                frames.append(pd.read_csv(io.StringIO(_unzip_csv(art.read_bytes())), dtype=str, low_memory=False))
            except Exception as ex:
                log.warning("wqp border strip failed: %s", ex)
        df = pd.concat(frames, ignore_index=True).drop_duplicates("MonitoringLocationIdentifier")
        num = lambda s: pd.to_numeric(s, errors="coerce")
        state = df["StateCode"].map({"35": "NM", "48": "TX", "08": "CO", "04": "AZ", "40": "OK", "49": "UT"})
        sites = pd.DataFrame({
            "native_id": df["MonitoringLocationIdentifier"],
            "name": df["MonitoringLocationName"],
            "lat": num(df["LatitudeMeasure"]),
            "lon": num(df["LongitudeMeasure"]),
            "elevation_m": num(df.get("VerticalMeasure/MeasureValue")) * df.get("VerticalMeasure/MeasureUnitCode").map(
                {"ft": 0.3048, "m": 1.0}).fillna(0.3048),
            "site_type": df["MonitoringLocationTypeName"].map(lambda t: SITE_TYPE_MAP.get(str(t), "other")),
            "agency": df["OrganizationIdentifier"],
            "state": state,
            "county_fips": df["StateCode"].fillna("").str.zfill(2) + df["CountyCode"].fillna("").str.zfill(3),
            "huc8": df["HUCEightDigitCode"],
            "well_depth_m": num(df.get("WellDepthMeasure/MeasureValue")) * 0.3048,
            "aquifer": df.get("AquiferName"),
            "drainage_area_km2": num(df.get("DrainageAreaMeasure/MeasureValue")) * 2.589988,
            "active": None,
            "raw_metadata": [json.dumps({"org": r.get("OrganizationFormalName"), "type": r.get("MonitoringLocationTypeName"),
                                         "provider": r.get("ProviderName"), "desc": r.get("MonitoringLocationDescriptionText")},
                                        default=str) for r in df.to_dict("records")],
        })
        return sites

    # ------------------------------------------------------------------ fetch
    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        this_year = date.today().year
        decades = list(range(1880, this_year + 1, 10))
        if since:
            decades = [d for d in decades if d + 9 >= since.year]
        jobs: list[dict] = []
        if site_ids:
            for i in range(0, len(site_ids), 50):
                for d in decades:
                    jobs.append({"siteid": ";".join(site_ids[i:i + 50]), "_tag": f"sites{i}_{d}", "_decade": d})
        else:
            for c in NM_COUNTIES:
                for d in decades:
                    jobs.append({"statecode": "US:35", "countycode": f"US:35:{c}", "_tag": f"c{c}_{d}", "_decade": d})
            for i, (w, s, e, n) in enumerate(self._border_strips()):
                for d in decades:
                    jobs.append({"bBox": f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}", "_tag": f"strip{i}_{d}", "_decade": d})
        if limit:
            # prefer recent decades when testing
            jobs = sorted(jobs, key=lambda j: -j["_decade"])[:limit]

        def one(j: dict) -> int:
            d = j["_decade"]
            lo = date(d, 1, 1) if not since or since < date(d, 1, 1) else since
            hi = date(min(d + 9, this_year), 12, 31)
            params = {k: v for k, v in j.items() if not k.startswith("_")}
            params.update({"startDateLo": lo.strftime("%m-%d-%Y"), "startDateHi": hi.strftime("%m-%d-%Y"),
                           "dataProfile": "resultPhysChem", "mimeType": "csv", "zip": "yes"})
            art = self.get(f"{BASE}/Result/search", params=params, kind="results", refresh=refresh or bool(since),
                           window=(lo.isoformat(), hi.isoformat()))
            summ.n_requests += 1
            if art.from_cache:
                summ.n_cached += 1
                if self.already_written(art):
                    return 0
            text = _unzip_csv(art.read_bytes())
            if not text.strip():
                self.ledger.set_rows(art.request_key, 0)
                return 0
            df = pd.read_csv(io.StringIO(text), dtype=str, low_memory=False)
            if df.empty:
                self.ledger.set_rows(art.request_key, 0)
                return 0
            keep = [c for c in KEEP_COLS if c in df.columns]
            self.store.append_table(df[keep], "waterquality", self.name, f"results_{j['_tag']}", self.run_id)
            obs = self._to_observations(df)
            n = self.write_obs(obs, tag=j["_tag"]) if obs is not None else 0
            self.ledger.set_rows(art.request_key, len(df))
            return len(df)

        res = self.parallel(one, jobs, desc="result chunks")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(jobs) - len(res)
        return summ

    def _to_observations(self, df: pd.DataFrame) -> pd.DataFrame | None:
        d = df[df["CharacteristicName"].isin(CORE)].copy()
        if d.empty:
            return None
        val = pd.to_numeric(d["ResultMeasureValue"], errors="coerce")
        unit = d["ResultMeasure/MeasureUnitCode"].fillna("").str.strip()
        var = d["CharacteristicName"].map(CORE)
        # unit normalization to canonical units
        canon = {"water_temp": "degC", "specific_conductance": "uS/cm", "ph": "pH", "dissolved_oxygen": "mg/L",
                 "turbidity": "FNU", "nitrate": "mg/L", "chloride": "mg/L", "sulfate": "mg/L", "arsenic": "ug/L",
                 "fluoride": "mg/L", "uranium": "ug/L", "tds": "mg/L"}
        factor = pd.Series(1.0, index=d.index)
        offset = pd.Series(0.0, index=d.index)
        u = unit.str.lower()
        factor[(u == "deg f") & (var == "water_temp")] = 5 / 9
        offset[(u == "deg f") & (var == "water_temp")] = -32 * 5 / 9
        mgl_targets = var.map(canon).eq("mg/L")
        ugl_targets = var.map(canon).eq("ug/L")
        factor[mgl_targets & (u == "ug/l")] = 0.001
        factor[ugl_targets & (u == "mg/l")] = 1000.0
        factor[(var == "turbidity") & u.isin(["ntu", "ntru", "fnu", "nfu"])] = 1.0
        ok_units = {
            "water_temp": {"deg c", "deg f"}, "specific_conductance": {"us/cm", "umho/cm", "us/cm @25c", "umho/cm @25c", "us/cm @25 c"},
            "ph": {"std units", "none", "", "ph units", "std unit"}, "dissolved_oxygen": {"mg/l"}, "turbidity": {"ntu", "fnu", "ntru", "nfu"},
            "nitrate": {"mg/l", "ug/l", "mg/l as n", "mg/l asno3"}, "chloride": {"mg/l", "ug/l"}, "sulfate": {"mg/l", "ug/l"},
            "arsenic": {"ug/l", "mg/l"}, "fluoride": {"mg/l", "ug/l"}, "uranium": {"ug/l", "mg/l"}, "tds": {"mg/l", "ug/l"},
        }
        good = pd.Series([uu in ok_units.get(v, set()) for uu, v in zip(u, var)], index=d.index)
        # nitrate reported as NO3 mass -> as N
        as_no3 = (var == "nitrate") & (u == "mg/l asno3")
        factor[as_no3] = 0.2259
        # time handling
        dt = pd.to_datetime(d["ActivityStartDate"], errors="coerce")
        tm = d.get("ActivityStartTime/Time", pd.Series(None, index=d.index)).fillna("")
        tz = d.get("ActivityStartTime/TimeZoneCode", pd.Series(None, index=d.index)).fillna("")
        has_time = tm.str.len() > 0
        off = tz.map(TZ_OFFSET_MIN).fillna(-420)  # NM default MST
        local = pd.to_datetime(d["ActivityStartDate"].fillna("") + " " + tm, errors="coerce")
        utc = local - pd.to_timedelta(off, unit="m")
        ts = pd.Series(pd.NaT, index=d.index, dtype="datetime64[ns, UTC]")
        ts[has_time] = utc[has_time].dt.tz_localize("UTC")
        ts[~has_time] = dt[~has_time].dt.tz_localize("UTC")
        frac = d["ResultSampleFractionText"].fillna("").astype(str)
        det = d["ResultDetectionConditionText"].fillna("").astype(str)
        out = pd.DataFrame({
            "site_uid": "wqp:" + d["MonitoringLocationIdentifier"].astype(str),
            "datetime_utc": ts,
            "utc_offset_min": off.where(has_time, None),
            "value": val * factor + offset,
            "qualifier": (frac + "|" + det + "|" + d["ResultStatusIdentifier"].fillna("").astype(str)).str.strip("|"),
            "source_param": d["CharacteristicName"],
            "source_unit": unit,
            "statistic": "sample",
            "interval": "irregular",
            "variable": var,
            "unit": var.map(canon),
        })
        out = out[good & out["value"].notna() & out["datetime_utc"].notna()]
        return out

    def normalize(self, artifact) -> pd.DataFrame | None:
        if artifact.kind != "results":
            return None
        text = _unzip_csv(artifact.read_bytes())
        if not text.strip():
            return None
        df = pd.read_csv(io.StringIO(text), dtype=str, low_memory=False)
        return self._to_observations(df) if len(df) else None
