"""Bosque Ecosystem Monitoring Program (BEMP): monthly depth-to-groundwater (5 wells/site),
canopy/open precipitation, and groundwater/ditch/river quality, Rio Grande bosque 1997-2017.

Public Excel releases linked from https://bemp.org/data-sets/ (post-2017 data by request).
Sites carry no coordinates in the releases; locations are described by name/pueblo only.
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import date

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.bemp")

WELLS = ("north", "east", "center", "south", "west")
CM_TO_FT = 0.0328084

# Water-quality columns in the groundwater/ditch/river quality workbook -> waterquality characteristic
WQ_COLS = {
    "air temp (C)": ("air_temperature", "degC"),
    "turbidity (NTU)": ("turbidity", "NTU"),
    "DO (mg/L)": ("dissolved_oxygen", "mg/L"),
    "Do (%)": ("dissolved_oxygen_saturation", "percent"),
    "Temp (C)": ("water_temperature", "degC"),
    "Specific Conductance (uS/cm)": ("specific_conductance", "uS/cm"),
    "Conductivity (uS)": ("conductivity", "uS/cm"),
    "pH": ("ph", "pH"),
    "Barom P (hPa)": ("barometric_pressure", "hPa"),
    "Chloride (mg/L)": ("chloride", "mg/L"),
    "Bromide (mg/L)": ("bromide", "mg/L"),
    "Nitrate-N (mg/L)": ("nitrate_as_n", "mg/L"),
    "Phosphate-P (mg/L)": ("phosphate_as_p", "mg/L"),
    "Sulfate (mg/L)": ("sulfate", "mg/L"),
    "NH4-N (mg/L)": ("ammonium_as_n", "mg/L"),
    "Flouride (mg/L)": ("fluoride", "mg/L"),
    "Nitrite (mg/L)": ("nitrite", "mg/L"),
    "Depth to water table (cm)": ("depth_to_water", "cm"),
}
# subset of WQ columns that also go to observations via the crosswalk
WQ_OBS = {
    "Specific Conductance (uS/cm)": "wq_spc",
    "pH": "wq_ph",
    "DO (mg/L)": "wq_do",
    "Temp (C)": "wq_temp",
    "turbidity (NTU)": "wq_turb",
    "Chloride (mg/L)": "wq_cl",
    "Nitrate-N (mg/L)": "wq_no3",
    "Sulfate (mg/L)": "wq_so4",
    "Flouride (mg/L)": "wq_f",
}


@register
class BEMP(Source):
    name = "bemp"
    agency = "BEMP"
    description = "Bosque Ecosystem Monitoring Program monthly riparian groundwater, precip, quality (1997-2017)"

    def _links(self, refresh: bool = True) -> dict[str, str]:
        page = self.opt("page_url", "https://bemp.org/data-sets/")
        art = self.get(page, kind="sites", refresh=refresh)
        html = art.read_text()
        links = re.findall(r'href="(https?://[^"]+\.(?:xlsx|xls))"', html)
        out: dict[str, str] = {}
        for u in links:
            low = u.lower()
            if "depth-to-groundwater" in low:
                out["gw"] = u
            elif "precipitation" in low:
                out["precip"] = u
            elif "groundwater-ditch" in low and "quality" in low:
                out["wq"] = u
        for k, v in (self.opt("overrides") or {}).items():
            out[k] = v
        return out

    def _xlsx(self, key: str, url: str, refresh: bool) -> pd.ExcelFile:
        art = self.get(url, kind="data", refresh=refresh, site_uid=None, variable=key,
                       window=("1997-01-01", date.today().isoformat()))
        return pd.ExcelFile(io.BytesIO(art.read_bytes())), art

    @staticmethod
    def _read_sheet(x: pd.ExcelFile, sheet: str, first_col: str) -> pd.DataFrame:
        raw = x.parse(sheet, header=None)
        hdr = None
        for i in range(min(15, len(raw))):
            if str(raw.iloc[i, 0]).strip().lower() == first_col:
                hdr = i
                break
        if hdr is None:
            return pd.DataFrame()
        df = raw.iloc[hdr + 1:].copy()
        df.columns = [str(c).strip() for c in raw.iloc[hdr].tolist()]
        return df.reset_index(drop=True)

    # ------------------------------------------------------------------ sites
    def discover(self) -> pd.DataFrame:
        links = self._links()
        if "gw" not in links:
            raise RuntimeError("BEMP depth-to-groundwater workbook link not found on data-sets page")
        x, _ = self._xlsx("gw", links["gw"], refresh=False)
        info = self._read_sheet(x, "site information", "site # north to south")
        wells = self._read_sheet(x, "individual wells", "site name")
        names = {}
        for r in info.itertuples(index=False):
            try:
                names[int(r[2])] = (str(r[1]).strip(), str(r[3]).strip(), int(r[0]))
            except (TypeError, ValueError):
                continue
        for r in wells[["site name", "site"]].drop_duplicates().itertuples(index=False):
            try:
                sid = int(r[1])
            except (TypeError, ValueError):
                continue
            names.setdefault(sid, (str(r[0]).strip(), None, None))
        rows = []
        for sid, (nm, loc, order) in sorted(names.items()):
            meta = {"bemp_site_no": sid, "location": loc, "order_north_to_south": order,
                    "note": "BEMP releases carry no coordinates; ask BEMP for site locations"}
            rows.append({"native_id": f"{sid}", "name": f"BEMP {nm} (site mean of 5 wells)", "site_type": "well",
                         "agency": "BEMP", "state": "NM", "basin": "Rio Grande-Elephant Butte",
                         "raw_metadata": json.dumps({**meta, "role": "site_mean"})})
            for w in WELLS:
                rows.append({"native_id": f"{sid}-{w}", "name": f"BEMP {nm} {w} well", "site_type": "well",
                             "agency": "BEMP", "state": "NM", "basin": "Rio Grande-Elephant Butte",
                             "raw_metadata": json.dumps({**meta, "role": "well", "well": w})})
            rows.append({"native_id": f"{sid}-ditch", "name": f"BEMP {nm} ditch/drain", "site_type": "canal",
                         "agency": "BEMP", "state": "NM", "basin": "Rio Grande-Elephant Butte",
                         "raw_metadata": json.dumps({**meta, "role": "ditch"})})
            for g in ("open", "canopy"):
                rows.append({"native_id": f"{sid}-{g}", "name": f"BEMP {nm} {g} rain gauge", "site_type": "met",
                             "agency": "BEMP", "state": "NM", "basin": "Rio Grande-Elephant Butte",
                             "raw_metadata": json.dumps({**meta, "role": f"precip_{g}"})})
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------ fetch
    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        links = self._links(refresh=True)
        do_refresh = refresh or since is not None
        for key in ("gw", "precip", "wq"):
            if key not in links:
                summ.notes.append(f"no {key} workbook link found")
                continue
            try:
                x, art = self._xlsx(key, links[key], do_refresh)
            except Exception as e:  # noqa: BLE001
                summ.n_errors += 1
                summ.notes.append(f"{key}: {e}")
                continue
            summ.n_requests += 1
            if art.from_cache:
                summ.n_cached += 1
                if self.already_written(art):
                    continue
            n = 0
            if key == "gw":
                df = self._norm_gw(x)
                n = self._filter_write(df, since, site_ids, "gw")
            elif key == "precip":
                df = self._norm_precip(x)
                n = self._filter_write(df, since, site_ids, "precip")
            else:
                obs, wq = self._norm_wq(x)
                n = self._filter_write(obs, since, site_ids, "wq")
                if wq is not None and len(wq):
                    self.store.write_table(wq, "waterquality", self.name, "gw_ditch_river_quality")
                    summ.notes.append(f"waterquality: {len(wq)} result rows")
            self.ledger.set_rows(art.request_key, n)
            summ.n_rows += n
        return summ

    def _filter_write(self, df: pd.DataFrame | None, since, site_ids, tag: str) -> int:
        if df is None or df.empty:
            return 0
        if since is not None:
            df = df[df["datetime_utc"] >= pd.Timestamp(since, tz="UTC")]
        if site_ids:
            keep = {self.uid(s) for s in site_ids}
            df = df[df["site_uid"].isin(keep) | df["site_uid"].str.rsplit("-", n=1).str[0].isin(keep)]
        return self.write_obs(df, tag=tag)

    # ------------------------------------------------------------------ normalizers
    @staticmethod
    def _ymd(df: pd.DataFrame, day_col: str | None = "day") -> pd.Series:
        y = pd.to_numeric(df["year"], errors="coerce")
        m = pd.to_numeric(df["month"], errors="coerce")
        d = pd.to_numeric(df[day_col], errors="coerce") if day_col and day_col in df.columns else 1
        s = pd.to_datetime(pd.DataFrame({"year": y, "month": m, "day": d}), errors="coerce")
        return s.dt.tz_localize("UTC")

    def _norm_gw(self, x: pd.ExcelFile) -> pd.DataFrame:
        wells = self._read_sheet(x, "individual wells", "site name")
        ts = self._ymd(wells)
        frames = []
        for w in WELLS:
            if w not in wells.columns:
                continue
            frames.append(pd.DataFrame({
                "site_uid": (self.name + ":" + wells["site"].astype(str).str.replace(r"\.0$", "", regex=True) + "-" + w),
                "datetime_utc": ts, "value": pd.to_numeric(wells[w], errors="coerce"),
                "source_param": "gw_cm", "interval": "monthly", "statistic": "instantaneous",
            }))
        if "ditch" in wells.columns:
            frames.append(pd.DataFrame({
                "site_uid": (self.name + ":" + wells["site"].astype(str).str.replace(r"\.0$", "", regex=True) + "-ditch"),
                "datetime_utc": ts, "value": pd.to_numeric(wells["ditch"], errors="coerce"),
                "source_param": "ditch_cm", "interval": "monthly", "statistic": "instantaneous",
            }))
        mean = self._read_sheet(x, "mean depth to groundwater", "site name")
        mcol = next((c for c in mean.columns if c.lower().startswith("mean depth")), None)
        if mcol:
            frames.append(pd.DataFrame({
                "site_uid": (self.name + ":" + mean["site #"].astype(str).str.replace(r"\.0$", "", regex=True)),
                "datetime_utc": self._ymd(mean, None), "value": pd.to_numeric(mean[mcol], errors="coerce"),
                "source_param": "gw_mean_cm", "interval": "monthly", "statistic": "mean",
            }))
        out = pd.concat(frames, ignore_index=True)
        out = out[out["value"].notna() & out["datetime_utc"].notna()]
        return self.xw.apply(out, self.name)

    def _norm_precip(self, x: pd.ExcelFile) -> pd.DataFrame:
        df = self._read_sheet(x, "canopy and open monthly precip", "site name")
        ts = self._ymd(df, None)  # monthly total assigned to the month of the reading
        sid = df["site"].astype(str).str.replace(r"\.0$", "", regex=True)
        frames = []
        for col, g, p in (("canopy (mm)", "canopy", "precip_canopy_mm"), ("open (mm)", "open", "precip_open_mm")):
            if col in df.columns:
                frames.append(pd.DataFrame({
                    "site_uid": (self.name + ":" + sid + "-" + g), "datetime_utc": ts,
                    "value": pd.to_numeric(df[col], errors="coerce"),
                    "source_param": p, "interval": "monthly", "statistic": "total",
                }))
        out = pd.concat(frames, ignore_index=True)
        out = out[out["value"].notna() & out["datetime_utc"].notna()]
        return self.xw.apply(out, self.name)

    def _norm_wq(self, x: pd.ExcelFile) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
        sheet = next((s for s in x.sheet_names if "gwditch" in s.lower().replace(" ", "")), None)
        if sheet is None:
            return None, None
        df = x.parse(sheet)
        df.columns = [str(c).strip() for c in df.columns]
        y = pd.to_numeric(df.get("Year"), errors="coerce")
        m = pd.to_numeric(df.get("Month"), errors="coerce")
        d = pd.to_numeric(df.get("Day"), errors="coerce")
        ts = pd.to_datetime(pd.DataFrame({"year": y, "month": m, "day": d}), errors="coerce").dt.tz_localize("UTC")
        siteno = df["Site No"].astype(str).str.replace(r"\.0$", "", regex=True)
        well = df.get("Well", pd.Series("", index=df.index)).astype(str).str.strip().str.lower()
        suffix = well.where(well.isin(WELLS) | well.isin(["ditch", "river"]), "")
        native = siteno + suffix.map(lambda w: f"-{w}" if w else "")
        long_rows = []
        for col, (charac, unit) in WQ_COLS.items():
            if col not in df.columns:
                continue
            v = pd.to_numeric(df[col], errors="coerce")
            sub = pd.DataFrame({
                "site_uid": (self.name + ":" + native), "bemp_site": df.get("Site"), "well": well,
                "datetime_utc": ts, "characteristic": charac, "value": v, "unit": unit,
                "source_column": col, "time_local": df.get("Time").astype(str) if "Time" in df.columns else None,
                "notes": df.get("notes"),
            })
            long_rows.append(sub[v.notna() & ts.notna()])
        wq = pd.concat(long_rows, ignore_index=True) if long_rows else None
        obs_frames = []
        for col, p in WQ_OBS.items():
            if col not in df.columns:
                continue
            obs_frames.append(pd.DataFrame({
                "site_uid": (self.name + ":" + native), "datetime_utc": ts,
                "value": pd.to_numeric(df[col], errors="coerce"),
                "source_param": p, "interval": "irregular", "statistic": "sample",
            }))
        obs = pd.concat(obs_frames, ignore_index=True) if obs_frames else None
        if obs is not None:
            obs = obs[obs["value"].notna() & obs["datetime_utc"].notna()]
            obs = self.xw.apply(obs, self.name)
        return obs, wq

    def normalize(self, artifact) -> pd.DataFrame | None:
        key = (artifact.params or {}).get("variable") if artifact.params else None
        x = pd.ExcelFile(io.BytesIO(artifact.read_bytes()))
        low = artifact.url.lower()
        if "depth-to-groundwater" in low:
            return self._norm_gw(x)
        if "precipitation" in low:
            return self._norm_precip(x)
        if "quality" in low:
            return self._norm_wq(x)[0]
        return None
