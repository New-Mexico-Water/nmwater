"""Colorado Division of Water Resources HydroBase REST (CDSS) - Rio Grande (Division 3) and
San Juan (Division 7) headwaters feeding New Mexico.

Verified 2026-09-12:
  surfacewater/surfacewaterstations/?format=json&division=3&pageSize=50000  -> 227 (div 3), 163 (div 7)
  surfacewater/surfacewatertsday/?format=json&abbrev=LAJCAPCO&pageSize=50000  -> 28,868 daily rows 1943-2026
  telemetrystations/telemetrystation/?format=json&division=3&pageSize=50000  -> 159 (div 3), 71 (div 7)
  telemetrystations/telemetrytimeseriesday/?abbrev=2000511A&parameter=DISCHRG
  structures/divrec/divrecday/?wdid=2000511&min-dataMeasDate=01/01/2024   -> daily diversion records 1950-
  groundwater/waterlevels/wells/?division=3 -> 1,070 wells;  groundwater/waterlevels/wellmeasurements/?wellId=
Date filters use MM/DD/YYYY. No auth. pageSize up to 50000; PageCount in the envelope.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.codwr")

PAGE = 50000


@register
class CODWR(Source):
    name = "codwr"
    agency = "CO DWR"
    description = "Colorado DWR HydroBase: Rio Grande/San Juan headwater gauges, diversions, wells"
    kinds = ("sites", "swday", "telday", "divrec", "gw")

    @property
    def base(self) -> str:
        return self.cfg.base_url or "https://dwr.state.co.us/Rest/GET/api/v2"

    @property
    def divisions(self) -> list[int]:
        return [int(d) for d in self.opt("divisions", [3, 7])]

    # ------------------------------------------------------------------ helpers
    def _json_pages(self, path: str, params: dict, kind: str, refresh: bool = False, **kw) -> list[dict]:
        """Fetch every page of a CDSS endpoint and return the concatenated ResultList."""
        out: list[dict] = []
        page = 1
        while True:
            p = {"format": "json", "pageSize": PAGE, "pageIndex": page, **params}
            art = self.get(f"{self.base}/{path}", params=p, kind=kind, refresh=refresh, **kw)
            try:
                d = art.read_json()
            except Exception:  # noqa: BLE001
                break
            out.extend(d.get("ResultList") or [])
            if page >= int(d.get("PageCount") or 1):
                break
            page += 1
        return out

    @staticmethod
    def _dt(s: str | None) -> str | None:
        return s[:10] if s else None

    # ------------------------------------------------------------------ discover
    def discover(self) -> pd.DataFrame:
        rows = []
        sw_abbrevs: set[str] = set()
        for div in self.divisions:
            for s in self._json_pages("surfacewater/surfacewaterstations/", {"division": div}, "sites", refresh=True):
                ab = s.get("abbrev")
                if not ab:
                    continue
                sw_abbrevs.add(ab)
                rows.append({
                    "native_id": ab, "name": s.get("stationName"), "lat": s.get("latitude"), "lon": s.get("longitude"),
                    "site_type": "stream", "agency": s.get("dataSource") or "CO DWR", "state": s.get("state") or "CO",
                    "basin": "Rio Grande Headwaters" if div == 3 else "San Juan",
                    "active": (self._dt(s.get("endDate")) or "") >= str(date.today().year - 1),
                    "raw_metadata": json.dumps({"kind": "surfacewater", "division": div, "stationNum": s.get("stationNum"),
                                                "usgsSiteId": s.get("usgsSiteId"), "county": s.get("county"),
                                                "waterDistrict": s.get("waterDistrict"), "startDate": self._dt(s.get("startDate")),
                                                "endDate": self._dt(s.get("endDate")), "measUnit": s.get("measUnit")}),
                })
            for t in self._json_pages("telemetrystations/telemetrystation/", {"division": div}, "sites", refresh=True):
                ab = t.get("abbrev")
                if not ab:
                    continue
                stype = {"Stream Gage": "stream", "Diversion Structure": "diversion", "Storage Structure": "reservoir"}.get(
                    t.get("stationType") or "", "other")
                meta = {"kind": "telemetry", "division": div, "usgsSiteId": t.get("usgsStationId"), "wdid": t.get("wdid"),
                        "stationType": t.get("stationType"), "structureType": t.get("structureType"),
                        "waterSource": t.get("waterSource"), "parameter": t.get("parameter"), "county": t.get("county"),
                        "porStart": self._dt(t.get("stationPorStart")), "porEnd": self._dt(t.get("stationPorEnd")),
                        "has_surfacewater_station": ab in sw_abbrevs}
                if ab in sw_abbrevs:
                    # the surface-water station list also carries diversion/storage structures typed as
                    # streams; merge telemetry facts and correct the site type
                    for r in rows:
                        if r["native_id"] == ab:
                            m = json.loads(r["raw_metadata"])
                            m["telemetry"] = meta
                            m["stationType"] = t.get("stationType")
                            m["wdid"] = t.get("wdid")
                            r["raw_metadata"] = json.dumps(m)
                            if stype != "other":
                                r["site_type"] = stype
                            break
                    continue
                rows.append({
                    "native_id": ab, "name": t.get("stationName"), "lat": t.get("latitude"), "lon": t.get("longitude"),
                    "site_type": stype, "agency": t.get("dataSourceAbbrev") or "DWR", "state": "CO",
                    "basin": "Rio Grande Headwaters" if div == 3 else "San Juan",
                    "active": (t.get("stationStatus") == "Active"), "raw_metadata": json.dumps(meta),
                })
            for w in self._json_pages("groundwater/waterlevels/wells/", {"division": div}, "sites", refresh=True):
                wid = w.get("wellId")
                if wid is None:
                    continue
                rows.append({
                    "native_id": f"well-{wid}", "name": w.get("wellName") or w.get("locationNumber"),
                    "lat": w.get("latitude"), "lon": w.get("longitude"),
                    "elevation_m": (w.get("elevation") or 0) * 0.3048 or None, "site_type": "well",
                    "agency": w.get("measurementBy") or "CO DWR", "state": "CO",
                    "basin": "Rio Grande Headwaters" if div == 3 else "San Juan",
                    "well_depth_m": (w.get("wellDepth") or 0) * 0.3048 or None, "aquifer": w.get("aquifers") or None,
                    "active": (self._dt(w.get("porEnd")) or "") >= str(date.today().year - 3),
                    "raw_metadata": json.dumps({"kind": "well", "division": div, "wellId": wid, "usgsSiteId": w.get("usgsSiteId"),
                                                "county": w.get("county"), "porStart": self._dt(w.get("porStart")),
                                                "porEnd": self._dt(w.get("porEnd")), "porCount": w.get("porCount"),
                                                "wdid": w.get("wdid"), "designatedBasin": w.get("designatedBasin")}),
                })
        df = pd.DataFrame(rows).drop_duplicates("native_id")
        return df

    # ------------------------------------------------------------------ fetch
    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        sites = self.sites()
        if sites.empty:
            summ.notes.append("run `nmwater discover codwr` first")
            return summ
        if site_ids:
            sites = sites[sites["native_id"].isin(site_ids)]
        meta = {r.native_id: json.loads(r.raw_metadata) for r in sites.itertuples(index=False)}
        since_s = since.strftime("%m/%d/%Y") if since else None
        do_refresh = refresh or since is not None
        kinds = opts.get("kinds") or ["swday", "telday", "divrec", "gw"]

        jobs: list[tuple[str, str, dict]] = []  # (kind, native_id, params)
        for nid, m in meta.items():
            k = m.get("kind")
            if k == "surfacewater" and "swday" in kinds:
                jobs.append(("swday", nid, {"abbrev": nid}))
            if k in ("surfacewater", "telemetry") and "divrec" in kinds and m.get("wdid") \
                    and m.get("stationType") == "Diversion Structure":
                jobs.append(("divrec", nid, {"wdid": m["wdid"]}))
            if k == "telemetry":
                if "telday" in kinds and m.get("parameter"):
                    jobs.append(("telday", nid, {"abbrev": nid, "parameter": m["parameter"]}))
            elif k == "well" and "gw" in kinds and (m.get("porCount") or 0) > 0:
                jobs.append(("gw", nid, {"wellId": m["wellId"]}))
        if limit:
            jobs = jobs[:limit]

        paths = {"swday": "surfacewater/surfacewatertsday/", "telday": "telemetrystations/telemetrytimeseriesday/",
                 "divrec": "structures/divrec/divrecday/", "gw": "groundwater/waterlevels/wellmeasurements/"}
        since_keys = {"swday": "min-measDate", "telday": "min-measDate", "divrec": "min-dataMeasDate",
                      "gw": "min-measurementDate"}

        def one(job) -> int:
            kind, nid, params = job
            p = dict(params)
            if since_s:
                p[since_keys[kind]] = since_s
            total = 0
            page = 1
            while True:
                q = {"format": "json", "pageSize": PAGE, "pageIndex": page, **p}
                # CDSS enforces a daily data quota for anonymous callers and answers 403
                # "Exceeded Daily Data Limit" once it is reached. A free key raises the quota;
                # without one, re-run on a later day and the ledger resumes the failed series.
                if self.settings.tokens.get("CODWR_API_KEY"):
                    q["apiKey"] = self.settings.tokens["CODWR_API_KEY"]
                art = self.get(f"{self.base}/{paths[kind]}", params=q, kind=kind, refresh=do_refresh,
                               site_uid=self.uid(nid), window=(since.isoformat() if since else "1800-01-01", date.today().isoformat()))
                summ.n_requests += 1
                if art.from_cache:
                    summ.n_cached += 1
                if not self.already_written(art):
                    df = self.normalize(art)
                    n = self.write_obs(df, tag=f"{kind}-{nid}") if df is not None else 0
                    self.ledger.set_rows(art.request_key, n)
                    total += n
                try:
                    pc = int(art.read_json().get("PageCount") or 1)
                except Exception:  # noqa: BLE001
                    pc = 1
                if page >= pc or page >= 50:
                    break
                page += 1
            return total

        res = self.parallel(one, jobs, desc="series")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(jobs) - len(res)
        return summ

    # ------------------------------------------------------------------ normalize
    def normalize(self, artifact) -> pd.DataFrame | None:
        try:
            d = artifact.read_json()
        except Exception:  # noqa: BLE001
            return None
        rl = d.get("ResultList") or []
        if not rl:
            return None
        df = pd.DataFrame(rl)
        k = artifact.kind
        if k == "swday":
            ab = df["abbrev"].astype(str)
            param = df["measType"].astype(str)
            # streamflow measured at a diversion structure is a diversion, not river discharge
            is_div = ab.isin(self._diversion_abbrevs())
            param = param.where(~(is_div & (param == "Streamflow")), "Streamflow:Diversion")
            out = pd.DataFrame({
                "site_uid": "codwr:" + ab,
                "datetime_utc": pd.to_datetime(df["measDate"].str[:10], errors="coerce", utc=True),
                "value": pd.to_numeric(df["value"], errors="coerce"),
                "qualifier": (df.get("flagA").fillna("").astype(str) + "|" + df.get("flagB").fillna("").astype(str)).str.strip("|"),
                "source_param": param,
                "source_unit": df.get("measUnit"),
                "statistic": "mean", "interval": "daily", "utc_offset_min": None,
            })
        elif k == "telday":
            out = pd.DataFrame({
                "site_uid": "codwr:" + df["abbrev"].astype(str),
                "datetime_utc": pd.to_datetime(df["measDate"].str[:10], errors="coerce", utc=True),
                "value": pd.to_numeric(df["measValue"], errors="coerce"),
                "qualifier": df.get("flagA"),
                "source_param": df["parameter"].astype(str),
                "source_unit": df.get("measUnit"),
                "statistic": "mean", "interval": "daily", "utc_offset_min": None,
            })
        elif k == "divrec":
            nid = artifact.params.get("wdid") if artifact.params else None
            # map wdid back to the telemetry abbrev used as native_id
            abbrev = self._abbrev_for_wdid(nid)
            units = df.get("measUnits").fillna("").astype(str).str.upper()
            out = pd.DataFrame({
                "site_uid": "codwr:" + abbrev,
                "datetime_utc": pd.to_datetime(df["dataMeasDate"].str[:10], errors="coerce", utc=True),
                "value": pd.to_numeric(df["dataValue"], errors="coerce"),
                "qualifier": (df.get("obsCode").fillna("").astype(str) + "|" + df.get("approvalStatus").fillna("").astype(str)).str.strip("|"),
                "source_param": "DIVREC:" + units,
                "source_unit": units,
                "statistic": "mean", "interval": "daily", "utc_offset_min": None,
            })
            out.loc[units == "AF", "statistic"] = "total"
        elif k == "gw":
            wid = df["wellId"].astype(str)
            ts = pd.to_datetime(df["measurementDate"].str[:10], errors="coerce", utc=True)
            a = pd.DataFrame({"site_uid": "codwr:well-" + wid, "datetime_utc": ts,
                              "value": pd.to_numeric(df["depthWaterBelowLandSurface"], errors="coerce"),
                              "source_param": "depthWaterBelowLandSurface", "source_unit": "ft"})
            b = pd.DataFrame({"site_uid": "codwr:well-" + wid, "datetime_utc": ts,
                              "value": pd.to_numeric(df["elevationOfWater"], errors="coerce"),
                              "source_param": "elevationOfWater", "source_unit": "ft"})
            out = pd.concat([a, b], ignore_index=True)
            out["qualifier"] = pd.concat([df["dataSource"], df["dataSource"]], ignore_index=True).astype(str)
            out["statistic"] = "instantaneous"
            out["interval"] = "irregular"
            out["utc_offset_min"] = None
        else:
            return None
        out = out[out["value"].notna() & out["datetime_utc"].notna() & (out["value"] > -999)]  # -999 = missing
        return self.xw.apply(out, self.name)

    def _site_index(self) -> None:
        if hasattr(self, "_wdid_map"):
            return
        self._wdid_map: dict[str, str] = {}
        self._div_abbrevs: set[str] = set()
        for r in self.sites().itertuples(index=False):
            try:
                m = json.loads(r.raw_metadata)
            except Exception:  # noqa: BLE001
                continue
            if m.get("wdid"):
                self._wdid_map.setdefault(str(m["wdid"]), r.native_id)
            if m.get("stationType") == "Diversion Structure" or r.site_type == "diversion":
                self._div_abbrevs.add(r.native_id)

    def _abbrev_for_wdid(self, wdid: str | None) -> str:
        self._site_index()
        return self._wdid_map.get(str(wdid), f"wdid-{wdid}")

    def _diversion_abbrevs(self) -> set[str]:
        self._site_index()
        return self._div_abbrevs
