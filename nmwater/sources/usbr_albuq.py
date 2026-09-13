"""Reclamation Albuquerque Area Office real-time .dat files (Rio Grande and Pecos projects).

    https://www.usbr.gov/uc/elpaso/water/RioGrandeProject/RealTimeData/<NAME>.dat
Each file is a fixed-width table: 19-char "Date* Time*" prefix (MST, no DST) then 10-char right-aligned
columns, a header line of column names and a line of units, covering a rolling ~7-day window of
15-minute values. Every run archives the current window (keyed by fetch date) so history accumulates.
MRGCDDIV.dat is the only public route to MRGCD diversions.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.usbr_albuq")

FILES = {
    # file: (site name, lat, lon, columns that belong to the dam site itself)
    "PLATORODAM": ("Platoro Reservoir (Conejos River, CO)", 37.3505, -106.5378),
    "HERONDAM": ("Heron Reservoir", 36.6973, -106.6992),
    "ELVADODAM": ("El Vado Reservoir", 36.5967, -106.7325),
    "ABIQUIUDAM": ("Abiquiu Reservoir", 36.2373, -106.4283),
    "NAMBEDAM": ("Nambe Falls Reservoir", 35.8489, -105.8956),
    "COCHITIDAM": ("Cochiti Lake", 35.6231, -106.3231),
    "MRGCDDIV": ("MRGCD diversions", None, None),
    "ELEPHANTBUTTEDAM": ("Elephant Butte Reservoir", 33.1522, -107.1886),
    "RIOGRANDEBELOWELEPHANTBUTTEDAM": ("Rio Grande below Elephant Butte Dam", 33.1450, -107.1900),
    "CABALLODAM": ("Caballo Reservoir", 32.8994, -107.2967),
    "RIOGRANDEBELOWCABALLODAM": ("Rio Grande below Caballo Dam", 32.8900, -107.2950),
    "SANTAROSADAM": ("Santa Rosa Lake", 35.0319, -104.6825),
    "SUMNERDAM(NAVD88)": ("Sumner Lake", 34.6288, -104.3924),
    "BRANTLEYDAM": ("Brantley Lake", 32.5442, -104.3814),
    "AVALONDAM": ("Avalon Reservoir", 32.4844, -104.2597),
}
DAM_COLUMNS = {"Elevation", "Storage", "Outflow", "Flow", "GH", "EastGate", "WestGate", "Inflow"}
# separate gauge/canal sites embedded in the dam files
COLUMN_SITES = {
    "Narrows": ("Rio Grande at Narrows (Elephant Butte inflow)", "stream"),
    "Otowi": ("Rio Grande at Otowi Bridge (USBR feed)", "stream"),
    "AzoteaOut": ("Azotea Tunnel outlet (San Juan-Chama import)", "canal"),
    "LaPuente": ("Rio Chama near La Puente", "stream"),
    "PDLFlow": ("Pecos River near Puerto de Luna", "stream"),
    "Kaiser": ("Pecos River at Kaiser (Brantley inflow)", "stream"),
    "CIDCanal": ("Carlsbad Irrigation District main canal", "diversion"),
    "Cochtiti": ("MRGCD Cochiti diversion", "diversion"),
    "Cochiti": ("MRGCD Cochiti diversion", "diversion"),
    "Angostura": ("MRGCD Angostura diversion", "diversion"),
    "Isleta": ("MRGCD Isleta diversion", "diversion"),
    "SanAcacia": ("MRGCD San Acacia diversion", "diversion"),
}
ROW_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
PREFIX = 20  # "YYYY-MM-DD HH:MM:SS" + 1 separator space; then 10-char right-aligned fields
WIDTH = 10


def parse_dat(text: str) -> tuple[list[str], list[str], pd.DataFrame]:
    lines = text.splitlines()
    hi = next((i for i, ln in enumerate(lines) if ln.startswith("Date*")), None)
    if hi is None:
        return [], [], pd.DataFrame()
    hdr, units = lines[hi], lines[hi + 1] if hi + 1 < len(lines) else ""
    ncol = max(1, (max(len(hdr) - 1, len(units)) - PREFIX + WIDTH - 1) // WIDTH)
    # header names are right-aligned one column to the right of the data fields
    names = [hdr[PREFIX + 1 + i * WIDTH: PREFIX + 1 + (i + 1) * WIDTH].strip() for i in range(ncol)]
    unit_l = [units[PREFIX + i * WIDTH: PREFIX + (i + 1) * WIDTH].strip().strip("()") for i in range(ncol)]
    rows = []
    for ln in lines[hi + 2:]:
        if not ROW_RE.match(ln):
            continue
        vals = [ln[PREFIX + i * WIDTH: PREFIX + (i + 1) * WIDTH].strip() for i in range(ncol)]
        rows.append([ln[:PREFIX].strip()] + vals)
    df = pd.DataFrame(rows, columns=["datetime"] + names) if rows else pd.DataFrame()
    return names, unit_l, df


@register
class USBRAlbuquerque(Source):
    name = "usbr_albuq"
    agency = "USBR"
    description = "Albuquerque Area Office real-time 15-min dam/diversion .dat files incl. MRGCD diversions (rolling)"
    kinds = ("sites", "data")

    @property
    def base(self) -> str:
        return self.cfg.base_url or "https://www.usbr.gov/uc/elpaso/water/RioGrandeProject/RealTimeData"

    def _site_for(self, fname: str, col: str) -> tuple[str, str, str]:
        """-> (native_id, name, site_type)"""
        base_name, lat, lon = FILES[fname]
        if fname == "MRGCDDIV" or col not in DAM_COLUMNS:
            meta = COLUMN_SITES.get(col, (f"{base_name} {col}", "stream"))
            return f"{fname}:{col}", meta[0], meta[1]
        stype = "stream" if fname.startswith("RIOGRANDEBELOW") else "reservoir"
        return fname, base_name, stype

    def discover(self) -> pd.DataFrame:
        rows = {}
        for fname in FILES:
            try:
                art = self.get(f"{self.base}/{fname}.dat", kind="data", refresh=True, key_extra=date.today().isoformat())
            except Exception as e:  # noqa: BLE001
                log.warning("%s: %s failed: %s", self.name, fname, e)
                continue
            names, units, _ = parse_dat(art.read_text())
            base_name, lat, lon = FILES[fname]
            for col, unit in zip(names, units):
                if not col:
                    continue
                nid, nm, stype = self._site_for(fname, col)
                r = rows.setdefault(nid, {"native_id": nid, "name": nm, "lat": lat if nid == fname else None,
                                          "lon": lon if nid == fname else None, "site_type": stype,
                                          "agency": "USBR Albuquerque Area Office" if stype != "diversion" or fname != "MRGCDDIV" else "MRGCD via USBR",
                                          "state": "CO" if fname == "PLATORODAM" else "NM", "active": True,
                                          "_cols": {}})
                r["_cols"][col] = unit
        out = []
        for r in rows.values():
            cols = r.pop("_cols")
            r["raw_metadata"] = json.dumps({"columns": cols, "file": r["native_id"].split(":")[0]})
            out.append(r)
        return pd.DataFrame(out)

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        files = list(FILES)
        if site_ids:
            files = [f for f in files if any(s == f or s.startswith(f + ":") for s in site_ids)]
        if limit:
            files = files[:limit]

        def one(fname) -> int:
            art = self.get(f"{self.base}/{fname}.dat", kind="data", refresh=True, key_extra=date.today().isoformat())
            summ.n_requests += 1
            df = self.normalize(art)
            n = self.write_obs(df, tag=fname) if df is not None else 0
            self.ledger.set_rows(art.request_key, n)
            return n

        res = self.parallel(one, files, desc="files")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(files) - len(res)
        return summ

    def normalize(self, artifact) -> pd.DataFrame | None:
        fname = artifact.url.rsplit("/", 1)[1].replace(".dat", "")
        if fname not in FILES:
            return None
        names, units, df = parse_dat(artifact.read_text())
        if df.empty:
            return None
        ts = pd.to_datetime(df["datetime"], errors="coerce", utc=True) + pd.Timedelta(hours=7)  # MST -> UTC
        frames = []
        for col, unit in zip(names, units):
            if not col or col not in df.columns:
                continue
            nid, _, _ = self._site_for(fname, col)
            v = pd.to_numeric(df[col], errors="coerce")
            frames.append(pd.DataFrame({
                "site_uid": self.uid(nid), "datetime_utc": ts, "value": v, "qualifier": "provisional",
                "source_param": col, "source_unit": unit, "statistic": "instantaneous", "interval": "15min",
                "utc_offset_min": -420,
            }))
        out = pd.concat(frames, ignore_index=True)
        out = out[out["value"].notna() & out["datetime_utc"].notna()]
        return self.xw.apply(out, self.name)
