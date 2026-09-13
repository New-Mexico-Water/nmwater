"""NM Office of the State Engineer / Interstate Stream Commission report mirror:
Water Use by Categories (1975-2020), TR-7 (streamflow and reservoir content 1888-1954), Rio Grande
Compact Commission annual reports, and the machine-readable water-use spreadsheets.

Verified 2026-09-12: www.ose.nm.gov serves an incomplete TLS chain (curl exit 60); requests need
certificate verification disabled for this host. RGCC PDFs exist for some years only (2013 yes;
1990, 2022 404). CKAN resource downloads are Cloudflare-challenged (403) -> docs/manual_downloads.md.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from ._manual import manual_dir, note_manual
from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.ose_reports")

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
OSE = "https://www.ose.nm.gov"
TR = f"{OSE}/Library/TechnicalReports"
REPORTS = {
    "TR-7 streamflow and reservoir content 1888-1954": f"{TR}/TechReport_007_NMOSE_1888-1954strmflw_res%20-%20Comp.pdf",
    "TR-32 consumptive use and water requirements": f"{TR}/TechReport-032.pdf",
    "TR-41 water use 1975": f"{TR}/TechReport-041.PDF",
    "TR-44 water use 1980": f"{TR}/TechReport-044.pdf",
    "TR-46 water use 1985": f"{TR}/TechReport-046.pdf",
    "TR-47 water use 1990": f"{TR}/TechReport-047.pdf",
    "TR-49 water use 1995": f"{TR}/TechReport-049.PDF",
    "TR-50 irrigated agriculture 1993-1995": f"{TR}/TechReport-050.PDF",
    "TR-51 water use 2000": f"{TR}/TechReport-051.pdf",
    "TR-52 water use 2005 (404 on OSE; NRC mirror)": "https://www.nrc.gov/docs/ML1030/ML103080984.pdf",
    "TR-52-S water use 2005 supplement": f"{TR}/TechReport-052-S.pdf",
    "TR-54 water use 2010": f"{TR}/TechReport%2054NM%20Water%20Use%20by%20Categories%20.pdf",
    "2015 water use report": "https://www.ose.state.nm.us/WUC/wucTechReports/2015/pdf/2015%20WUR%20final_05142019.pdf",
    "TR-56 water use 2020": "https://mainstreamnm.org/wp-content/uploads/2025/01/2020-Water-Use-By-Categories-2020_final_printable.pdf",
    "Pecos River Master Manual": f"{OSE}/Compacts/Pecos/PDF/pecos_river_master_manual.pdf",
}
RGCC = f"{OSE}/Compacts/RioGrande/RGCC%20Reports/RGCC%20{{year}}.pdf"
CKAN_XLSX = {
    "wateruse2020.xlsx": "https://catalog.newmexicowaterdata.org/dataset/95483621-599e-49e4-b9a5-c426b677b41a/resource/db554f92-f41b-4b76-9377-da6b98a0b125/download/wateruse2020.xlsx",
    "wateruse2015.xlsx": "https://catalog.newmexicowaterdata.org/dataset/572676c6-9763-4ebf-8336-fe73ed92a666/resource/a1b77aca-94d1-4c4d-9360-494fa894444e/download/wateruse2015.xlsx",
    "2015-wur-_water-data-act.mdb": "https://catalog.newmexicowaterdata.org/dataset/572676c6-9763-4ebf-8336-fe73ed92a666/resource/8fd030db-7858-4673-9af8-f359bd9e9aba/download/2015-wur-_water-data-act.mdb",
    "nmwateruse2015_usgs.xlsx": "https://catalog.newmexicowaterdata.org/dataset/dfc69325-b397-4cdd-a008-5e795d823704/resource/ead5464c-56e0-4c2c-b57f-d4112e4aff26/download/nmwateruse2015.xlsx",
}


@register
class OSEReports(Source):
    name = "ose_reports"
    agency = "NM OSE/ISC"
    description = "OSE/ISC report mirror: water use by categories 1975-2020, TR-7, RGCC annual reports"
    kinds = ("report", "rgcc", "wateruse")

    # ose.nm.gov presents an incomplete certificate chain; config/sources.yaml sets
    # `verify_ssl: false` for this source, which the core HTTP client honors.

    def discover(self) -> pd.DataFrame:
        return pd.DataFrame(columns=["native_id", "name", "lat", "lon", "site_type", "agency", "state", "raw_metadata"])

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        kinds = opts.get("kinds") or ["report", "rgcc", "wateruse"]
        if "report" in kinds:
            items = list(REPORTS.items())[: limit] if limit else list(REPORTS.items())

            def rep(item) -> int:
                title, url = item
                try:
                    art = self.get(url, kind="report", refresh=refresh, headers={"User-Agent": UA})
                    summ.n_requests += 1
                    if art.from_cache:
                        summ.n_cached += 1
                    return 1
                except Exception as e:  # noqa: BLE001
                    summ.notes.append(f"{title}: {str(e)[:80]}")
                    note_manual(self.name, url, url.rsplit("/", 1)[-1], f"{title}: {str(e)[:60]}")
                    return 0

            got = self.parallel(rep, items, desc="reports")
            summ.notes.append(f"reports mirrored: {int(sum(got))}/{len(items)}")
        if "rgcc" in kinds:
            years = list(range(int(self.opt("rgcc_start", 1985)), date.today().year + 1))
            if limit:
                years = years[-limit:]
            found = []

            def rg(y: int) -> int:
                try:
                    art = self.get(RGCC.format(year=y), kind="rgcc", refresh=refresh, headers={"User-Agent": UA})
                    summ.n_requests += 1
                    if art.from_cache:
                        summ.n_cached += 1
                    found.append(y)
                    return 1
                except Exception as e:  # noqa: BLE001
                    if "404" not in str(e):
                        summ.notes.append(f"RGCC {y}: {str(e)[:80]}")
                    return 0

            self.parallel(rg, years, desc="RGCC reports")
            summ.notes.append(f"RGCC reports available: {sorted(found)}")
        if "wateruse" in kinds:
            mdir = manual_dir(self.settings.data_dir, self.name)
            for fname, url in CKAN_XLSX.items():
                local = mdir / fname
                if not local.exists():
                    try:
                        art = self.get(url, kind="wateruse", refresh=refresh,
                                       headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
                        summ.n_requests += 1
                        raw = art.read_bytes()
                        if raw[:2] != b"PK" and not fname.endswith(".mdb"):
                            raise RuntimeError("not an xlsx (Cloudflare challenge page)")
                        local.write_bytes(raw)
                    except Exception as e:  # noqa: BLE001
                        note_manual(self.name, url, fname, "NM Water Data catalog blocks scripted downloads (Cloudflare)")
                        summ.notes.append(f"{fname}: needs manual download ({str(e)[:60]})")
                        continue
                if local.suffix.lower() == ".xlsx":
                    n = self._ingest_xlsx(local)
                    summ.n_rows += n
                    summ.notes.append(f"{fname}: {n} rows ingested")
                elif local.suffix.lower() == ".mdb":
                    summ.notes.append(f"{fname}: present; Access file not parsed (needs mdbtools)")
        return summ

    def _ingest_xlsx(self, path: Path) -> int:
        """Dump every sheet to the wateruse group; attempt a tidy long table when the layout is recognizable."""
        year = "".join(ch for ch in path.stem if ch.isdigit())[-4:] or "unknown"
        sheets = pd.read_excel(path, sheet_name=None, dtype=str)
        total = 0
        for sname, df in sheets.items():
            if df.empty:
                continue
            df = df.dropna(how="all").dropna(axis=1, how="all")
            df.columns = [str(c) for c in df.columns]
            self.store.write_table(df, "wateruse", self.name, f"wateruse_{year}_{_safe(sname)}")
            total += len(df)
            tidy = self._tidy(df, year, sname)
            if tidy is not None and len(tidy):
                self.store.write_table(tidy, "wateruse", self.name, f"wateruse_{year}_{_safe(sname)}_tidy")
        return total

    @staticmethod
    def _tidy(df: pd.DataFrame, year: str, sheet: str) -> pd.DataFrame | None:
        cols = {c.lower(): c for c in df.columns}
        key = next((cols[k] for k in cols if k in ("county", "basin", "river basin", "county name")), None)
        if not key:
            return None
        num_cols = [c for c in df.columns if c != key and pd.to_numeric(df[c], errors="coerce").notna().mean() > 0.5]
        if not num_cols:
            return None
        long = df.melt(id_vars=[key], value_vars=num_cols, var_name="measure", value_name="value")
        long["value"] = pd.to_numeric(long["value"], errors="coerce")
        long = long[long["value"].notna()]
        long["year"] = year
        long["sheet"] = sheet
        long = long.rename(columns={key: "area"})
        long["area_type"] = key.lower()
        return long


def _safe(s: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in s)[:40]
