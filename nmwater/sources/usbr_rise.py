"""Bureau of Reclamation RISE API (JSON:API). Used only for what HydroData lacks: RISE items whose
sourceCode is 'uchdb2' are the Upper Colorado HDB series already served by usbr_hydrodata and are skipped.

    {base}/location?stateId=NM&itemsPerPage=100     -> locations + catalogRecord ids
    {base}/catalog-record/{id}                       -> catalogItem ids
    {base}/catalog-item/{id}                         -> parameterName/Unit/Timestep, sourceCode
    {base}/result?itemId=&order[dateTime]=asc&itemsPerPage=10000&page=N
Header Accept: application/vnd.api+json is mandatory.
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import date

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.usbr_rise")
HDR = {"Accept": "application/vnd.api+json"}
LOC_TYPE_MAP = {"lake/reservoir": "reservoir", "reservoir": "reservoir", "dam": "reservoir", "stream": "stream",
                "stream gage": "stream", "river": "stream", "well": "well", "irrigation district": "area",
                "power plant": "other", "powerplant": "other", "building": "other", "canal": "canal",
                "diversion": "diversion", "pumping plant": "other", "weather station": "met"}
STEP_INTERVAL = {"daily": "daily", "day": "daily", "hourly": "hourly", "hour": "hourly", "monthly": "monthly",
                 "month": "monthly", "yearly": "annual", "year": "annual", "instant": "instant",
                 "15 minute": "15min", "15-minute": "15min", "minute": "instant", "one minute": "instant",
                 "annual": "annual", "biannually": "irregular"}
SKIP_SOURCES = {"uchdb2"}  # (case-insensitive) Upper Colorado HDB: already in usbr_hydrodata


@register
class USBRRise(Source):
    name = "usbr_rise"
    agency = "USBR"
    description = "Reclamation RISE items not in HydroData (sediment surveys, evaporation, wells, Pecos gages)"
    kinds = ("sites", "catalog", "data", "acap")

    @property
    def base(self) -> str:
        return self.cfg.base_url or "https://data.usbr.gov/rise/api"

    def _get(self, path: str, params=None, kind="catalog", refresh=False, **kw):
        url = path if path.startswith("http") else f"https://data.usbr.gov{path}" if path.startswith("/") else f"{self.base}/{path}"
        return self.get(url, params=params, headers=HDR, kind=kind, refresh=refresh, **kw).read_json()

    # ---------------------------------------------------------------- discovery
    def discover(self) -> pd.DataFrame:
        locs = []
        page = 1
        while True:
            d = self._get("location", {"stateId": self.scope.state_abbr, "itemsPerPage": 100, "page": page}, refresh=True)
            locs += d.get("data", [])
            if len(d.get("data", [])) < 100:
                break
            page += 1
        rec_ids = []
        loc_of_rec = {}
        for loc in locs:
            for r in loc["relationships"].get("catalogRecords", {}).get("data", []):
                rec_ids.append(r["id"])
                loc_of_rec[r["id"]] = loc["attributes"]["_id"]

        def rec(rid):
            d = self._get(rid, refresh=True)["data"]
            return rid, d["attributes"], [i["id"] for i in d["relationships"].get("catalogItems", {}).get("data", [])]

        recs = self.parallel(rec, rec_ids, desc="catalog-records")
        rec_meta = {rid: a for rid, a, _ in recs}
        item_ids = [(rid, iid) for rid, _, items in recs for iid in items]

        def item(x):
            rid, iid = x
            a = self._get(iid, refresh=True)["data"]["attributes"]
            return {
                "item_id": a["_id"], "record_id": int(rid.rsplit("/", 1)[1]), "location_id": loc_of_rec.get(rid),
                "record_title": rec_meta.get(rid, {}).get("recordTitle"), "item_title": a.get("itemTitle"),
                "parameter_name": a.get("parameterName"), "parameter_unit": a.get("parameterUnit"),
                "parameter_timestep": a.get("parameterTimestep"), "parameter_transformation": a.get("parameterTransformation"),
                "parameter_group": a.get("parameterGroup"), "source_code": a.get("sourceCode"),
                "location_source_code": a.get("locationSourceCode"), "parameter_source_code": a.get("parameterSourceCode"),
                "temporal_start": a.get("temporalStartDate"), "temporal_end": a.get("temporalEndDate"),
                "is_modeled": a.get("isModeled"), "data_structure": a.get("dataStructure"),
                "item_type": (a.get("itemType") or {}).get("_id"), "update_frequency": (a.get("updateFrequency") or {}).get("updatefrequency"),
            }

        items = self.parallel(item, item_ids, desc="catalog-items")
        items_df = pd.DataFrame(items)
        self.store.write_table(items_df, "reference", self.name, "catalog_items")
        rows = []
        for loc in locs:
            a = loc["attributes"]
            coords = (a.get("locationCoordinates") or {}).get("coordinates") or [None, None]
            ltype = (a.get("locationTypeName") or "").lower()
            stype = "other"
            for k, v in LOC_TYPE_MAP.items():
                if k in ltype:
                    stype = v
                    break
            if "well" in (a.get("locationName") or "").lower():
                stype = "well"
            lid = a["_id"]
            its = items_df[items_df["location_id"] == lid] if len(items_df) else items_df
            rows.append({
                "native_id": str(lid), "name": a.get("locationName"),
                "lat": coords[1] if coords and len(coords) > 1 else None,
                "lon": coords[0] if coords else None,
                "elevation_m": float(a["elevation"]) * 0.3048 if a.get("elevation") else None,
                "site_type": stype, "agency": "USBR", "state": self.scope.state_abbr, "active": a.get("locationStatusId") == 1,
                "raw_metadata": json.dumps({
                    "locationTypeName": a.get("locationTypeName"), "projectNames": a.get("projectNames"),
                    "tags": [t.get("tag") for t in (a.get("locationTags") or [])], "timezone": a.get("timezone"),
                    "verticalDatum": (a.get("verticalDatum") or {}).get("_id"),
                    "items": its[["item_id", "parameter_name", "parameter_unit", "parameter_timestep", "source_code",
                                  "temporal_start", "temporal_end"]].to_dict("records") if len(its) else [],
                }, default=str),
            })
        return pd.DataFrame(rows)

    # ---------------------------------------------------------------- fetch
    def _items(self) -> pd.DataFrame:
        p = self.store.root / "reference" / "source=usbr_rise" / "catalog_items.parquet"
        if not p.exists():
            return pd.DataFrame()
        return pd.read_parquet(p)

    @staticmethod
    def param_key(name: str | None, unit: str | None) -> str:
        return f"{name} [{unit}]"

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        items = self._items()
        if items.empty:
            summ.notes.append("run `nmwater discover usbr_rise` first")
            return summ
        wanted = set(opts.get("kinds") or ("data",))
        if "acap" in wanted:
            self._fetch_acap(items, summ, refresh)
            if not wanted & {"data"}:
                return summ
        items = items[~items["source_code"].fillna("").str.lower().isin(SKIP_SOURCES)
                      & (items["item_type"] == "DATA") & ~items["is_modeled"].fillna(False).astype(bool)
                      & (items["data_structure"].fillna("").str.lower().str.contains("time series"))]
        items = items[[self.xw.lookup(self.name, self.param_key(n, u)) is not None
                       for n, u in zip(items["parameter_name"], items["parameter_unit"])]]
        if site_ids:
            items = items[items["location_id"].astype(str).isin(site_ids)]
        if limit:
            keep = items["location_id"].drop_duplicates().head(limit)
            items = items[items["location_id"].isin(keep)]

        def one(r) -> int:
            params = {"itemId": int(r.item_id), "order[dateTime]": "asc", "itemsPerPage": 10000, "page": 1}
            if since:
                params["dateTime[after]"] = since.isoformat()
            total = 0
            while True:
                art = self.get(f"{self.base}/result", params=params, headers=HDR, kind="data",
                               site_uid=self.uid(str(r.location_id)), variable=self.param_key(r.parameter_name, r.parameter_unit),
                               window=(params.get("dateTime[after]", "1800-01-01"), date.today().isoformat()),
                               refresh=refresh or bool(since))
                summ.n_requests += 1
                if art.from_cache:
                    summ.n_cached += 1
                d = art.read_json()
                if not (art.from_cache and self.already_written(art)):
                    df = self._normalize_results(d, r)
                    n = self.write_obs(df, tag=str(r.item_id)) if df is not None else 0
                    self.ledger.set_rows(art.request_key, n)
                    total += n
                if len(d.get("data", [])) < 10000 or not (d.get("links") or {}).get("next"):
                    break
                params["page"] += 1
            return total

        rows = list(items.itertuples(index=False))
        res = self.parallel(one, rows, desc="items")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(rows) - len(res)
        return summ


    # ------------------------------------------------------------------- ACAP
    # Area-capacity tables from Reclamation sedimentation resurveys. These are the only
    # sediment-corrected capacity figures we have: NID publishes design/owner-reported
    # storage that is never revised for the sediment wedge, while an ACAP table is the
    # storage-elevation relationship measured by an actual bathymetric survey in a stated
    # year. Reservoir "percent full" is only meaningful against one of these, matched to
    # the vintage of the storage reading - see docs/interpretation.md.
    #
    # Items are ordinary RISE catalog items of type GEN whose itemTitle contains
    # "ACAP Table"; the payload is a CSV linked from the item's `binaryFilePath`, with a
    # metadata preamble (the vertical datum note lives there and matters: Elephant Butte
    # is published in Reclamation Project Vertical Datum, 45.0 ft below NAVD88) and a
    # "###Data###" marker before the header row NUMBER,BASE,V,A,C,M.
    #   BASE = elevation ft, V = capacity acre-ft, A = surface area acres,
    #   C, M = coefficients of the nonlinear interpolation Reclamation uses between rows.
    ACAP_RE = re.compile(r"ACAP\s*Table", re.I)

    def _fetch_acap(self, items: pd.DataFrame, summ: FetchSummary, refresh: bool) -> None:
        sel = items[items["item_title"].fillna("").str.contains(self.ACAP_RE)]
        if sel.empty:
            summ.notes.append("no ACAP items in catalog")
            return
        frames = []
        for r in sel.to_dict("records"):
            iid = int(r["item_id"])
            try:
                meta = self._get(f"catalog-item/{iid}", kind="acap", refresh=refresh)
                url = (meta.get("data", {}).get("attributes", {}) or {}).get("binaryFilePath")
                summ.n_requests += 1
                if not url:
                    continue
                art = self.get(url, kind="acap", refresh=refresh)
                summ.n_requests += 1
                if art.from_cache:
                    summ.n_cached += 1
                df = self._parse_acap(art.read_bytes(), r)
                if df is not None and not df.empty:
                    frames.append(df)
            except Exception as e:  # one bad table must not sink the rest
                log.warning("usbr_rise acap %s: %s", iid, e)
                summ.n_errors += 1
        if not frames:
            return
        out = pd.concat(frames, ignore_index=True)
        self.store.write_table(out, "reference", self.name, "reservoir_acap")
        summ.n_rows += len(out)
        summ.notes.append(f"{out['item_id'].nunique()} ACAP tables, {len(out)} elevation rows, "
                          f"{out['reservoir'].nunique()} reservoirs")

    def _parse_acap(self, raw: bytes, item: dict) -> pd.DataFrame | None:
        text = raw.decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        start = next((i for i, ln in enumerate(lines) if ln.lower().lstrip('" ').startswith("###data###")), None)
        if start is None:
            return None
        datum = next((ln for ln in lines[:start] if "datum" in ln.lower()), "")
        df = pd.read_csv(io.StringIO("\n".join(lines[start + 1:])))
        df.columns = [str(c).strip().upper() for c in df.columns]
        if not {"BASE", "V"} <= set(df.columns):
            return None
        for c in ("BASE", "V", "A", "C", "M"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df[df["BASE"].notna() & df["V"].notna()]
        title = str(item.get("item_title") or "")
        yrs = re.findall(r"(1[89]\d\d|20\d\d)", title)
        return pd.DataFrame({
            "item_id": int(item["item_id"]),
            "location_id": item.get("location_id"),
            "reservoir": re.sub(r"\s*\(.*", "", str(item.get("record_title") or "")
                                ).replace(" Sedimentation Survey Data", "").strip(),
            "survey_year": int(yrs[-1]) if yrs else None,
            "survey_label": " and ".join(yrs) if yrs else None,
            "elevation_ft": df["BASE"].astype(float),
            "capacity_af": df["V"].astype(float),
            "area_acres": df["A"].astype(float) if "A" in df else float("nan"),
            "interp_c": df["C"].astype(float) if "C" in df else float("nan"),
            "interp_m": df["M"].astype(float) if "M" in df else float("nan"),
            "vertical_datum_note": datum.strip('" ,')[:500] or None,
        })

    def _normalize_results(self, d: dict, r) -> pd.DataFrame | None:
        data = d.get("data") or []
        if not data:
            return None
        at = [x["attributes"] for x in data]
        step = ((at[0].get("resultAttributes") or {}).get("timeStep") or r.parameter_timestep or "").lower()
        interval = STEP_INTERVAL.get(step, "irregular")
        ts = pd.to_datetime([a["dateTime"] for a in at], utc=True, errors="coerce")
        if interval in ("daily", "monthly", "annual"):
            # RISE stamps daily values at 07:00Z (midnight MST): take the local date
            ts = pd.to_datetime((ts - pd.Timedelta(hours=7)).strftime("%Y-%m-%d"), utc=True)
        out = pd.DataFrame({
            "site_uid": self.uid(str(r.location_id)),
            "datetime_utc": ts,
            "value": pd.to_numeric([a.get("result") for a in at], errors="coerce"),
            "qualifier": [a.get("status") for a in at],
            "source_param": self.param_key(r.parameter_name, r.parameter_unit),
            "source_unit": r.parameter_unit,
            "interval": interval,
            "utc_offset_min": None,
        })
        out = out[out["value"].notna() & out["datetime_utc"].notna()]
        return self.xw.apply(out, self.name)

    def normalize(self, artifact) -> pd.DataFrame | None:
        if artifact.kind != "data" or not artifact.params:
            return None
        items = self._items()
        row = items[items["item_id"] == int(artifact.params["itemId"])]
        if row.empty:
            return None
        return self._normalize_results(artifact.read_json(), next(row.itertuples(index=False)))
