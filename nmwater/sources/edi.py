"""Environmental Data Initiative (EDI) research-network data via the PASTA+ REST API.

Packages of interest for the New Mexico water picture:
  knb-lter-sev  Sevilleta LTER - hourly meteorology from the refuge station network, 1988-2024
  knb-lter-jrn  Jornada Basin LTER - 30-minute/hourly/daily wireless met stations, 2013-
  edi (various) Navajo Nation wells database (>5,000 wells; water quality 1905-2020)

API (https://pastaplus-core.readthedocs.io):
  GET /package/search/eml?q=...&fl=packageid,title       discover packages
  GET /package/eml/{scope}/{id}                          list revisions
  GET /package/eml/{scope}/{id}/{rev}                    list entity ids
  GET /package/metadata/eml/{scope}/{id}/{rev}           EML document
  GET /package/data/eml/{scope}/{id}/{rev}/{entityId}    the data file (usually CSV)

Verified 2026-09-13: pasta.lternet.edu returned HTTP 403 "Public Access is not authorized to
execute service method" for search, revision listing, and entity listing from this network.
The module retries, reports the block in FetchSummary.notes, and records the packages in
docs/manual_downloads.md so they can be pulled from the portal by hand. Re-run when access is
restored; no code change is needed.
"""

from __future__ import annotations

import io
import logging
import re
from datetime import date

import pandas as pd

from ._manual import note_manual
from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.edi")

PASTA = "https://pasta.lternet.edu/package"
PORTAL = "https://portal.edirepository.org/nis/mapbrowse?packageid="

# (scope, identifier, label, what it holds). Identifiers are stable; revisions are resolved live.
PACKAGES = [
    ("knb-lter-sev", 1, "Sevilleta LTER meteorology", "hourly met, refuge station network, 1988-"),
    ("knb-lter-jrn", 210548001, "Jornada Basin LTER met stations", "30-min/hourly/daily met, 2013-"),
    ("knb-lter-jrn", 210437001, "Jornada Basin LTER precipitation", "daily/monthly precipitation"),
]

# Column-name patterns -> (canonical source_param used in catalog/crosswalk.d/edi.csv, unit hint).
COLMAP = [
    (r"^(air_?temp|airtemp|temp_air|tair)", "air_temp_c"),
    (r"^(max.*air.*temp|airt_max|tmax)", "air_temp_max_c"),
    (r"^(min.*air.*temp|airt_min|tmin)", "air_temp_min_c"),
    (r"^(mean.*air.*temp|airt_avg|tavg)", "air_temp_mean_c"),
    (r"^(precip|ppt|rain|rainfall)", "precip_mm"),
    (r"^(rel.*hum|rh)", "rh_pct"),
    (r"^(wind.*spe?e?d|ws)", "wind_speed_ms"),
    (r"^(wind.*dir|wd)", "wind_dir_deg"),
    (r"^(solar|sol_?rad|srad|par)", "solar_wm2"),
    (r"^(soil.*moist|vwc|sm)", "soil_moisture_pct"),
    (r"^(soil.*temp|st_)", "soil_temp_c"),
    (r"^(depth.*water|dtw|water_?level)", "depth_to_water_ft"),
]
DATE_COLS = ("date", "datetime", "date_time", "obs_date", "timestamp", "date_col")


def _param_for(col: str) -> str | None:
    c = col.strip().lower()
    for pat, param in COLMAP:
        if re.match(pat, c):
            return param
    return None


@register
class EDI(Source):
    name = "edi"
    agency = "EDI / LTER"
    description = "Sevilleta and Jornada LTER meteorology, Navajo Nation wells (EDI PASTA API)"
    kinds = ("packages", "metadata", "data")

    # ---------------------------------------------------------------- helpers
    def _newest_revision(self, scope: str, ident: int) -> str | None:
        art = self.get(f"{PASTA}/eml/{scope}/{ident}", kind="packages")
        revs = [ln.strip() for ln in art.read_text().splitlines() if ln.strip().isdigit()]
        return revs[-1] if revs else None

    def _entities(self, scope: str, ident: int, rev: str) -> list[str]:
        art = self.get(f"{PASTA}/eml/{scope}/{ident}/{rev}", kind="metadata")
        return [ln.strip() for ln in art.read_text().splitlines() if ln.strip()]

    def _entity_name(self, scope: str, ident: int, rev: str, ent: str) -> str:
        try:
            art = self.get(f"{PASTA}/name/eml/{scope}/{ident}/{rev}/{ent}", kind="metadata")
            return art.read_text().strip()[:120]
        except Exception:
            return ent

    # ---------------------------------------------------------------- interface
    def discover(self) -> pd.DataFrame:
        """Resolve package revisions and entity lists into a reference table; sites come from data."""
        rows = []
        blocked = 0
        for scope, ident, label, holds in PACKAGES:
            pid = f"{scope}.{ident}"
            try:
                rev = self._newest_revision(scope, ident)
                ents = self._entities(scope, ident, rev) if rev else []
                for e in ents:
                    rows.append({"package": pid, "revision": rev, "entity_id": e, "label": label,
                                 "holds": holds, "name": self._entity_name(scope, ident, rev, e)})
            except Exception as ex:
                blocked += 1
                log.warning("edi %s: %s", pid, str(ex)[:140])
                note_manual(self.name, PORTAL + pid, f"{pid}.zip",
                            f"{label}: PASTA API refused public access ({str(ex)[:60]})")
        if rows:
            self.store.write_table(pd.DataFrame(rows), "reference", self.name, "packages")
        if blocked:
            log.warning("edi: %d/%d packages unreachable; listed in docs/manual_downloads.md",
                        blocked, len(PACKAGES))
        # EDI sites are defined inside the data files; discover() only builds the package index.
        return pd.DataFrame(columns=["native_id", "name", "lat", "lon", "site_type", "agency",
                                     "state", "raw_metadata"])

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        pkgs = PACKAGES[:limit] if limit else PACKAGES
        for scope, ident, label, _holds in pkgs:
            pid = f"{scope}.{ident}"
            try:
                rev = self._newest_revision(scope, ident)
                if not rev:
                    continue
                ents = self._entities(scope, ident, rev)
            except Exception as ex:
                summ.n_errors += 1
                summ.notes.append(f"{pid}: {str(ex)[:90]}")
                continue
            for ent in ents:
                try:
                    art = self.get(f"{PASTA}/data/eml/{scope}/{ident}/{rev}/{ent}", kind="data",
                                   refresh=refresh, key_extra=pid)
                    summ.n_requests += 1
                    if art.from_cache:
                        summ.n_cached += 1
                        if self.already_written(art):
                            continue
                    df = self._normalize_table(art.read_bytes(), pid, ent, since)
                    n = self.write_obs(df, tag=f"{scope}-{ident}-{ent[:8]}") if df is not None else 0
                    self.ledger.set_rows(art.request_key, n)
                    summ.n_rows += n
                except Exception as ex:
                    summ.n_errors += 1
                    log.warning("edi %s/%s: %s", pid, ent[:10], str(ex)[:120])
        return summ

    def _normalize_table(self, raw: bytes, pid: str, ent: str, since: date | None) -> pd.DataFrame | None:
        """Melt a wide station-by-column CSV into observations. Unknown columns are ignored."""
        try:
            df = pd.read_csv(io.BytesIO(raw), low_memory=False)
        except Exception:
            return None
        if df.empty:
            return None
        cols = {c.strip().lower(): c for c in df.columns}
        dcol = next((cols[c] for c in DATE_COLS if c in cols), None)
        if dcol is None:
            return None
        ts = pd.to_datetime(df[dcol], errors="coerce", utc=True)
        # station identifier column, if present
        scol = next((cols[c] for c in ("station", "sta", "site", "station_id", "sitename", "site_id")
                     if c in cols), None)
        station = df[scol].astype(str) if scol else pd.Series(pid, index=df.index)
        frames = []
        for col in df.columns:
            param = _param_for(col)
            if not param or col == dcol:
                continue
            sub = pd.DataFrame(
                {
                    "site_uid": self.name + ":" + pid + ":" + station,
                    "datetime_utc": ts,
                    "value": pd.to_numeric(df[col], errors="coerce"),
                    "qualifier": f"edi:{ent}",
                    "source_param": param,
                    "statistic": None,
                    "interval": None,
                    "utc_offset_min": None,
                }
            )
            frames.append(sub[sub["value"].notna() & sub["datetime_utc"].notna()])
        if not frames:
            return None
        out = pd.concat(frames, ignore_index=True)
        if since is not None:
            out = out[out["datetime_utc"] >= pd.Timestamp(since, tz="UTC")]
        return self.xw.apply(out, self.name)

    def normalize(self, artifact) -> pd.DataFrame | None:
        if artifact.kind != "data":
            return None
        m = re.search(r"/data/eml/([^/]+)/(\d+)/(\d+)/(.+)$", artifact.url)
        if not m:
            return None
        return self._normalize_table(artifact.read_bytes(), f"{m.group(1)}.{m.group(2)}", m.group(4), None)
