"""Texas Water Development Board Groundwater Database (GWDB) - border counties.

Verified 2026-09-12: https://www.twdb.texas.gov/groundwater/data/GWDBDownload.zip (83 MB, nightly;
1.8 GB unpacked, pipe-delimited). Tables used: WellMain.txt, WaterLevels{Major,Minor,Combination,
OtherUnassigned}.txt, WaterQuality{Major,Minor,Combination,OtherUnassigned}.txt.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import zipfile
from datetime import date

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.twdb")

ZIP_URL = "https://www.twdb.texas.gov/groundwater/data/GWDBDownload.zip"
DEFAULT_COUNTIES = ["El Paso", "Hudspeth", "Culberson", "Reeves", "Loving", "Ward", "Winkler", "Andrews", "Gaines",
                    "Yoakum", "Cochran", "Bailey", "Parmer", "Deaf Smith", "Oldham", "Hartley", "Dallam"]
WL_FILES = ["WaterLevelsMajor.txt", "WaterLevelsMinor.txt", "WaterLevelsCombination.txt", "WaterLevelsOtherUnassigned.txt"]
WQ_FILES = ["WaterQualityMajor.txt", "WaterQualityMinor.txt", "WaterQualityCombination.txt", "WaterQualityOtherUnassigned.txt"]


@register
class TWDB(Source):
    name = "twdb"
    agency = "TWDB"
    description = "Texas GWDB wells, water levels, and chemistry for the NM border counties"
    kinds = ("sites", "levels", "quality")

    @property
    def counties(self) -> set[str]:
        return {c.lower() for c in self.opt("counties", DEFAULT_COUNTIES)}

    def _zip(self, refresh: bool = False) -> zipfile.ZipFile:
        art = self.get(ZIP_URL, kind="bulk", refresh=refresh, window=("1800-01-01", date.today().isoformat()))
        # gzip-wrapped zip in the archive; read via a temp buffer
        with gzip.open(art.path, "rb") as f:
            data = f.read()
        self._bulk_art = art
        return zipfile.ZipFile(io.BytesIO(data))

    def _member(self, zf: zipfile.ZipFile, name: str) -> str | None:
        for n in zf.namelist():
            if n.endswith("/" + name) or n == name:
                return n
        return None

    def _filtered_chunks(self, zf: zipfile.ZipFile, name: str, chunksize: int = 500_000):
        m = self._member(zf, name)
        if not m:
            return
        with zf.open(m) as fh:
            text = io.TextIOWrapper(fh, encoding="latin-1", errors="replace")
            for chunk in pd.read_csv(text, sep="|", dtype=str, chunksize=chunksize, on_bad_lines="skip",
                                     quoting=3, engine="c"):
                if "County" in chunk.columns:
                    yield chunk[chunk["County"].fillna("").str.lower().isin(self.counties)]

    # ------------------------------------------------------------------ discover
    def discover(self) -> pd.DataFrame:
        zf = self._zip(refresh=True)
        frames = list(self._filtered_chunks(zf, "WellMain.txt"))
        if not frames:
            return pd.DataFrame(columns=["native_id"])
        w = pd.concat(frames, ignore_index=True)
        num = lambda s: pd.to_numeric(s, errors="coerce")
        sites = pd.DataFrame({
            "native_id": w["StateWellNumber"].str.strip(),
            "name": (w["StateWellNumber"].str.strip() + " " + w.get("Owner", "").fillna("").str.strip()).str.strip(),
            "lat": num(w["LatitudeDD"]),
            "lon": num(w["LongitudeDD"]),
            "elevation_m": num(w.get("LandSurfaceElevation")) * 0.3048,
            "site_type": "well",
            "agency": "TWDB",
            "state": "TX",
            "basin": w.get("RiverBasin"),
            "well_depth_m": num(w.get("WellDepth")) * 0.3048,
            "aquifer": w.get("Aquifer"),
            "active": w.get("CurrentWaterLevelWell", "").fillna("").str.strip().str.lower().eq("yes"),
            "raw_metadata": [json.dumps({"county": r.get("County"), "usgsSiteId": r.get("USGSSiteNumber"),
                                         "wellUse": r.get("WellUse"), "wellType": r.get("WellType"),
                                         "aquiferCode": r.get("AquiferCode"), "gcd": r.get("GCD"),
                                         "drillingYear": r.get("DrillingYear"), "waterQualityAvailable": r.get("WaterQualityAvailable")},
                                        default=str) for r in w.to_dict("records")],
        })
        return sites

    # ------------------------------------------------------------------ fetch
    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        kinds = opts.get("kinds") or ["levels", "quality"]
        zf = self._zip(refresh=refresh or since is not None)
        summ.n_requests = 1
        art = self._bulk_art
        if art.from_cache:
            summ.n_cached = 1
        if self.already_written(art) and not (refresh or since):
            summ.notes.append("bulk file unchanged and already normalized; use --refresh to re-download")
            return summ
        wanted = set(site_ids) if site_ids else None
        total = 0
        if "levels" in kinds:
            files = WL_FILES[: limit] if limit else WL_FILES
            for name in files:
                for chunk in self._filtered_chunks(zf, name):
                    df = self._normalize_levels(chunk)
                    if df is None or df.empty:
                        continue
                    if wanted:
                        df = df[df["site_uid"].isin({self.uid(s) for s in wanted})]
                    if since:
                        df = df[df["datetime_utc"] >= pd.Timestamp(since, tz="UTC")]
                    total += self.write_obs(df, tag=name.replace(".txt", ""))
        if "quality" in kinds:
            files = WQ_FILES[: limit] if limit else WQ_FILES
            for name in files:
                for i, chunk in enumerate(self._filtered_chunks(zf, name)):
                    if chunk.empty:
                        continue
                    if wanted:
                        chunk = chunk[chunk["StateWellNumber"].str.strip().isin(wanted)]
                    q = self._quality_table(chunk)
                    if since:
                        q = q[q["sample_date"] >= pd.Timestamp(since)]
                    if len(q):
                        self.store.append_table(q, "waterquality", self.name, name.replace(".txt", ""), self.run_id)
                        summ.notes.append(f"{name} chunk {i}: {len(q)} quality rows")
        self.ledger.set_rows(art.request_key, total)
        summ.n_rows = total
        return summ

    def _normalize_levels(self, df: pd.DataFrame) -> pd.DataFrame | None:
        if df.empty:
            return None
        ts = pd.to_datetime(df["MeasurementDate"], errors="coerce", utc=True)
        swn = df["StateWellNumber"].str.strip()
        qual = (df.get("Status", "").fillna("").astype(str) + "|" + df.get("MethodOfMeasurement", "").fillna("").astype(str)
                + "|" + df.get("Remarks", "").fillna("").astype(str).str.strip()).str.strip("|")
        a = pd.DataFrame({"site_uid": "twdb:" + swn, "datetime_utc": ts,
                          "value": pd.to_numeric(df["DepthFromLSD"], errors="coerce"),
                          "source_param": "DepthFromLSD", "source_unit": "ft", "qualifier": qual})
        b = pd.DataFrame({"site_uid": "twdb:" + swn, "datetime_utc": ts,
                          "value": pd.to_numeric(df["WaterElevation"], errors="coerce"),
                          "source_param": "WaterElevation", "source_unit": "ft", "qualifier": qual})
        out = pd.concat([a, b], ignore_index=True)
        out["statistic"] = "instantaneous"
        out["interval"] = "irregular"
        out["utc_offset_min"] = None
        out = out[out["value"].notna() & out["datetime_utc"].notna()]
        return self.xw.apply(out, self.name)

    def _quality_table(self, df: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({
            "site_uid": "twdb:" + df["StateWellNumber"].str.strip(),
            "sample_date": pd.to_datetime(df["SampleDate"], errors="coerce"),
            "sample_time": df.get("SampleTime"),
            "parameter_code": df.get("ParameterCode"),
            "characteristic": df.get("ParameterDescription"),
            "value": pd.to_numeric(df.get("ParameterValue"), errors="coerce"),
            "unit": df.get("ParameterUnitOfMeasure"),
            "flag": df.get("ParameterFlag"),
            "aquifer": df.get("Aquifer"),
            "collector": df.get("CollectionEntity"),
            "lab": df.get("AnalyzedLab"),
            "reliability": df.get("Reliability"),
        })

    def normalize(self, artifact) -> pd.DataFrame | None:
        # reprocess(): re-read the archived zip
        with gzip.open(artifact.path, "rb") as f:
            zf = zipfile.ZipFile(io.BytesIO(f.read()))
        frames = [self._normalize_levels(c) for name in WL_FILES for c in self._filtered_chunks(zf, name)]
        frames = [f for f in frames if f is not None and len(f)]
        return pd.concat(frames, ignore_index=True) if frames else None
