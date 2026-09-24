"""NM Office of the State Engineer ArcGIS Online feature services (services2.arcgis.com/qXZbWTdPDbTjl7Dy).

discover(): pages every configured layer (2,000 features/request), writes each as a reference
table (attributes + WKT geometry) and derives sites for points of diversion, real-time meters,
springs and dams. fetch(): writes one gw_depth_to_water observation per POD that records a
depth to water at drilling (qualifier 'at_drilling').
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.ose_arcgis")

# layer name -> (reference table name, site builder key or None, extra query params)
LAYERS: dict[str, tuple[str, str | None]] = {
    "OSE_Points_of_Diversion": ("points_of_diversion", "pod"),
    "OSE_RTMs": ("real_time_meters", "rtm"),
    "OSE_Conveyances": ("conveyances", None),
    "NM_Springs": ("springs", "spring"),
    "OSE_Dams": ("dams", "dam"),
    "OSE_Aquifer_Test_Wells_view_pub": ("aquifer_test_wells", None),
    "Acequia_ProjectCP": ("acequias", None),
    "NM_Irrigation_Districts": ("irrigation_districts", None),
    "Surface_Water_Basins": ("surface_water_basins", None),
    "DeclaredGroundwaterBasins": ("declared_groundwater_basins", None),
    "GroundwaterBasinsALL": ("groundwater_basins_all", None),
    "ISC_Compact_Areas": ("isc_compact_areas", None),
    "MeteredBasins": ("metered_basins", None),
    "AWRM": ("awrm_basins", None),
    "AdjudicationAreas": ("adjudication_areas", None),
    "MRGCD_Index": ("mrgcd_index", None),
    "EBID_Parcels": ("ebid_parcels", None),
    "New_Mexico_Public_Water_Systems": ("public_water_systems", None),
    "Sub_BasinsHUC8": ("sub_basins_huc8", None),
    "NM_Sub_Basins": ("nm_sub_basins", None),
    "WUR_Locales": ("wur_locales", None),
    "water_use_2015": ("water_use_2015_county", None),
    "water_use_2020": ("water_use_2020_county", None),
    "Water_Use_2015_and_2020": ("water_use_2015_2020", None),
    "All_OSE_WaterRight_Records": ("water_right_records", None),
}
BIG_LAYERS = {"OSE_Points_of_Diversion", "All_OSE_WaterRight_Records", "EBID_Parcels", "OSE_Conveyances"}

USE_SITE_TYPE = {
    "IRR": "diversion", "IRRIGATION": "diversion", "DOM": "well", "DOMESTIC": "well", "MUN": "well",
    "STK": "well", "IND": "well", "COM": "well", "MIN": "well", "PRO": "well", "SAN": "well",
}


def _wkt(geom: dict | None) -> str | None:
    if not geom:
        return None
    try:
        from shapely.geometry import shape

        return shape(geom).wkt
    except Exception:
        return json.dumps(geom)


def _ms_to_date(v):
    try:
        if v in (None, "", 0):
            return None
        return pd.Timestamp(int(v), unit="ms", tz="UTC")
    except (TypeError, ValueError, OverflowError):
        return None


def _missing(v) -> bool:
    """None, NaN or NaT: a value a builder cannot turn into an identifier."""
    return v is None or bool(pd.isna(v))


@register
class OSEArcGIS(Source):
    name = "ose_arcgis"
    agency = "NM OSE"
    description = "OSE ArcGIS: points of diversion (280k), real-time meters, conveyances, springs, dams, basins, water use"
    kinds = ("layers", "pod_levels")

    @property
    def base(self) -> str:
        return (self.cfg.base_url or "https://services2.arcgis.com/qXZbWTdPDbTjl7Dy/arcgis/rest/services").rstrip("/")

    def _layer_features(self, layer: str, refresh: bool = False, page_size: int = 2000,
                        max_pages: int = 100000, want_geom: bool = True) -> list[dict]:
        url = f"{self.base}/{layer}/FeatureServer/0/query"
        feats: list[dict] = []
        offset = 0
        pages = 0
        while pages < max_pages:
            params = {"where": "1=1", "outFields": "*", "f": "geojson", "resultOffset": offset,
                      "resultRecordCount": page_size, "outSR": 4326, "returnGeometry": "true" if want_geom else "false"}
            art = self.get(url, params=params, kind="layer", refresh=refresh, variable=layer)
            raw = art.read_bytes()
            try:
                doc = json.loads(raw.decode("utf-8"))
            except UnicodeDecodeError:
                doc = json.loads(raw.decode("latin-1"))
            if "error" in doc:
                raise RuntimeError(f"{layer}: {doc['error']}")
            page = doc.get("features", [])
            feats.extend(page)
            pages += 1
            exceeded = (doc.get("properties") or {}).get("exceededTransferLimit") or doc.get("exceededTransferLimit")
            if len(page) < page_size or not exceeded:
                break
            offset += page_size
        return feats

    def _layer_frame(self, layer: str, feats: list[dict]) -> pd.DataFrame:
        rows = []
        for f in feats:
            p = dict(f.get("properties") or {})
            p["geometry_wkt"] = _wkt(f.get("geometry"))
            rows.append(p)
        df = pd.DataFrame(rows)
        df.columns = [str(c) for c in df.columns]
        return df

    def discover(self) -> pd.DataFrame:
        sites_frames = []
        only = self.opt("layers")  # optional subset for testing
        for layer, (tname, builder) in LAYERS.items():
            if only and layer not in only:
                continue
            try:
                feats = self._layer_features(layer, refresh=layer not in BIG_LAYERS or bool(self.opt("refresh_big", False)))
            except Exception as e:
                log.warning("ose_arcgis: layer %s failed: %s", layer, e)
                continue
            df = self._layer_frame(layer, feats)
            self.store.write_table(df, "reference", self.name, tname)
            log.info("ose_arcgis: %s -> %d features", layer, len(df))
            if builder == "pod":
                sites_frames.append(self._pod_sites(df))
            elif builder == "rtm":
                sites_frames.append(self._rtm_sites(df))
            elif builder == "spring":
                sites_frames.append(self._spring_sites(df))
            elif builder == "dam":
                sites_frames.append(self._dam_sites(df))
        if not sites_frames:
            return pd.DataFrame(columns=["native_id"])
        return pd.concat(sites_frames, ignore_index=True)

    # -- site builders --------------------------------------------------------
    @staticmethod
    def _pod_id(r) -> str:
        return f"{r.get('pod_basin') or ''}-{r.get('pod_nbr') or ''}{('-' + str(r.get('pod_suffix'))) if r.get('pod_suffix') else ''}"

    def _pod_sites(self, df: pd.DataFrame) -> pd.DataFrame:
        from shapely import wkt as _swkt

        rows = []
        for r in df.to_dict("records"):
            g = r.get("geometry_wkt")
            lat = lon = None
            if g:
                try:
                    pt = _swkt.loads(g)
                    lon, lat = pt.x, pt.y
                except Exception:
                    pass
            use = str(r.get("use_of_wel") or r.get("use_") or "").upper()
            src = str(r.get("grnd_wtr_s") or "")
            stype = "well" if (r.get("depth_well") or 0) > 0 or src == "G" else USE_SITE_TYPE.get(use, "diversion")
            depth = r.get("depth_well")
            rows.append({
                "native_id": f"pod:{self._pod_id(r)}",
                "name": r.get("pod_name") or (r.get("ditch_name") or None) or self._pod_id(r),
                "lat": lat, "lon": lon,
                "elevation_m": (float(r["elevation"]) * 0.3048) if r.get("elevation") else None,
                "site_type": stype, "agency": "NM OSE", "state": "NM",
                "well_depth_m": float(depth) * 0.3048 if depth else None,
                "aquifer": r.get("aquifer") or None,
                "active": str(r.get("pod_status") or "").upper() == "ACT",
                "raw_metadata": json.dumps({k: v for k, v in r.items() if k != "geometry_wkt" and v not in (None, "", " ")
                                            and not k.startswith(("own_", "addr", "contact_"))}, default=str),
            })
        return pd.DataFrame(rows)

    def _rtm_sites(self, df: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for r in df.to_dict("records"):
            sid = r.get("Station_ID")
            # A meter with no station id arrives as NaN, which is neither None nor "" nor 0 and used
            # to crash int(sid), aborting discovery for the whole source (2026-09-12) and leaving
            # 158,029 point-of-diversion observations without site rows.
            if _missing(sid) or sid in ("", 0):
                continue
            rows.append({
                "native_id": f"rtm:{int(sid)}",
                "name": r.get("Gauge_name") or r.get("Ditch_Name") or r.get("OSE_File"),
                "lat": r.get("lat_ddd"), "lon": r.get("long_ddd"),
                "site_type": "well" if str(r.get("SW_or_GW") or "").upper().startswith("G") else "diversion",
                "agency": "NM OSE", "state": "NM", "active": str(r.get("Meter_status") or "").lower() == "complete",
                "raw_metadata": json.dumps({k: v for k, v in r.items() if k != "geometry_wkt" and v not in (None, "", " ")}, default=str),
            })
        return pd.DataFrame(rows)

    def _spring_sites(self, df: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for r in df.to_dict("records"):
            if _missing(r.get("SiteID")):
                continue
            rows.append({
                "native_id": f"spring:{r.get('SiteID')}",
                "name": r.get("SiteName"), "lat": r.get("LatitudeDD"), "lon": r.get("LongitudeD"),
                "elevation_m": r.get("ElevationM"), "site_type": "spring", "agency": "Springs Stewardship Institute / OSE",
                "state": r.get("StateProvi") or "NM",
                "huc8": str(r.get("HUC")) if r.get("HUC") else None,
                "raw_metadata": json.dumps({k: r.get(k) for k in ("County", "SpringType", "LithoPrima", "EmergenceE", "SourceGeo",
                                                                   "LandUnit", "infoSource", "SurveyCoun", "AKA", "GlobalID")}, default=str),
            })
        return pd.DataFrame(rows)

    def _dam_sites(self, df: pd.DataFrame) -> pd.DataFrame:
        from shapely import wkt as _swkt

        rows = []
        for r in df.to_dict("records"):
            info = {}
            for k, v in re.findall(r"<td>([A-Z_0-9]+)</td>\s*<td>(.*?)</td>", str(r.get("PopupInfo") or ""), re.S):
                info[k] = re.sub(r"<[^>]+>", "", v).strip()
            lat = lon = None
            try:
                pt = _swkt.loads(r.get("geometry_wkt"))
                lon, lat = pt.x, pt.y
            except Exception:
                pass
            nid = info.get("NATIONAL_ID") or info.get("OSE_DAM_FILE_NO") or info.get("ID") or r.get("OID")
            if _missing(nid):
                continue
            rows.append({
                "native_id": f"dam:{nid}", "name": r.get("Name") or info.get("DAM_NAME"), "lat": lat, "lon": lon,
                "site_type": "reservoir", "agency": "NM OSE Dam Safety", "state": "NM",
                "county_fips": None, "raw_metadata": json.dumps(info, default=str),
            })
        return pd.DataFrame(rows)

    # -- fetch: drilling-time water levels from PODs --------------------------------
    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        p = self.store.root / "reference" / f"source={self.name}" / "points_of_diversion.parquet"
        if not p.exists():
            summ.notes.append("run `nmwater discover ose_arcgis` first")
            return summ
        df = pd.read_parquet(p)
        if limit:
            df = df.head(limit)
        dtw = pd.to_numeric(df.get("depth_wate"), errors="coerce")
        keep = df[dtw.notna() & (dtw > 0)]
        when = keep.get("log_file_d").map(_ms_to_date)
        when = when.where(when.notna(), keep.get("finish_dat").map(_ms_to_date))
        obs = pd.DataFrame({
            "site_uid": ["ose_arcgis:pod:" + self._pod_id(r) for r in keep.to_dict("records")],
            "datetime_utc": pd.to_datetime(when, utc=True, errors="coerce").dt.floor("D"),
            "value": pd.to_numeric(keep["depth_wate"], errors="coerce"),
            "source_param": "depth_wate", "source_unit": "ft", "qualifier": "at_drilling",
            "utc_offset_min": None, "statistic": "instantaneous", "interval": "irregular",
        })
        obs = obs[obs["datetime_utc"].notna()]
        if since is not None:
            obs = obs[obs["datetime_utc"] >= pd.Timestamp(since, tz="UTC")]
        obs = self.xw.apply(obs, self.name)
        summ.n_rows = self.write_obs(obs, tag="pod-dtw")
        summ.notes.append(f"{len(keep)} PODs with depth-to-water at drilling; {int(when.isna().sum())} without a date were skipped")
        return summ
