"""USGS NWIS via the OGC Water Data API (api.waterdata.usgs.gov/ogcapi/v1) and the legacy
WaterServices (retiring Feb 2027). Kinds:

  sites        expanded site metadata for NM (+ border strips, + allow-list)
  catalog      legacy series catalogs (dv/uv/gw) with period of record per site/parameter
  daily        daily values: statewide per parameter (legacy, one request each) or OGC (incremental)
  gwlevels     discrete groundwater levels
  peaks        annual peak flows (OGC)
  measurements field discharge measurements (OGC)
  continuous   15-minute unit values (OGC, 1,000-day windows per site) - opt in
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import date, timedelta

import pandas as pd

from ..core.geo import basin_from_huc
from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.usgs")

SITE_TYPE_MAP = {
    "ST": "stream", "ST-CA": "canal", "ST-DCH": "diversion", "ST-TS": "stream", "LK": "lake",
    "GW": "well", "GW-TH": "well", "GW-MW": "well", "GW-CR": "well", "GW-EX": "well", "GW-IW": "well",
    "GW-HZ": "well", "SP": "spring", "AT": "met", "FA-DV": "diversion", "FA-WWTP": "wwtp",
    "FA-OF": "outfall", "FA-WDS": "other", "FA-STS": "other", "LA-SH": "other", "AG": "area",
    "AS": "area", "AW": "area", "ES": "other", "OC": "other", "WE": "other", "FA": "other",
    "FA-CI": "other", "FA-CS": "other", "FA-FON": "other", "FA-GC": "other",
    "FA-HP": "other", "FA-LF": "other", "FA-PV": "other", "FA-QC": "other",
    "FA-SEW": "wwtp", "FA-SPS": "other", "FA-TEP": "other", "LA": "other", "LA-EX": "other",
    "LA-OU": "other", "LA-SNK": "other", "LA-SR": "other", "LA-VOL": "other", "LA-PLY": "area",
    "SB": "other", "SB-CV": "other", "SB-GWD": "other", "SB-TSM": "other", "SB-UZ": "other",
}
STAT_MAP = {"00001": "max", "00002": "min", "00003": "mean", "00006": "total", "00008": "median",
            "00011": "instantaneous", "00021": "other", "00024": "other", "00004": "other", "00010": "other"}
QUAL_RE = re.compile(r"^(\d+)_(\d{5})(?:_(\d{5}))?(_cd)?$")


def iter_rdb_blocks(text: str):
    """Yield one DataFrame per header block of a USGS RDB response.

    Multi-site dv/iv/gwlevels responses are a concatenation of per-site blocks, each with its
    own '# comment' lines, header line, and format line (5s 15s 20d ...), and the header width
    varies by site. Rows are attached to the most recent header.
    """
    header: list[str] | None = None
    expect_format = False
    rows: list[list[str]] = []
    for ln in text.splitlines():
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split("\t")
        if parts[0] in ("agency_cd", "site_no") or (header is None and parts[0].isalpha() and "_" in parts[0]):
            if header is not None and rows:
                yield pd.DataFrame(rows, columns=header, dtype=str)
            header = parts
            rows = []
            expect_format = True
            continue
        if expect_format:
            expect_format = False
            if parts and all(p[:1].isdigit() and p[-1:] in "sdn" for p in parts if p):
                continue  # format line
        if header is not None and len(parts) == len(header):
            rows.append(parts)
    if header is not None and rows:
        yield pd.DataFrame(rows, columns=header, dtype=str)


def parse_rdb(text: str) -> pd.DataFrame:
    """Parse a USGS RDB response into one frame (union of columns across header blocks)."""
    blocks = list(iter_rdb_blocks(text))
    if not blocks:
        return pd.DataFrame()
    if len(blocks) == 1:
        return blocks[0]
    return pd.concat(blocks, ignore_index=True)


@register
class USGS(Source):
    name = "usgs"
    agency = "USGS"
    description = "NWIS daily/continuous/groundwater/peaks/measurements for NM and basin gauges"
    kinds = ("sites", "catalog", "daily", "gwlevels", "peaks", "measurements", "continuous")

    @property
    def ogc(self) -> str:
        return self.opt("ogc_base", "https://api.waterdata.usgs.gov/ogcapi/v1")

    @property
    def legacy(self) -> str:
        return self.opt("legacy_base", "https://nwis.waterservices.usgs.gov/nwis")

    def _api_key(self) -> dict[str, str]:
        k = self.settings.tokens.get("USGS_API_KEY")
        return {"api_key": k} if k else {}

    # ------------------------------------------------------------------ sites
    def _border_strips(self) -> list[tuple[float, float, float, float]]:
        w, s, e, n = self.scope.bbox
        bw, bs, be, bn = self.scope.bbox_buffered
        return [
            (bw, n, be, bn),   # north strip (CO)
            (bw, bs, be, s),   # south strip (Mexico/TX)
            (bw, s, w, n),     # west strip (AZ)
            (e, s, be, n),     # east strip (TX/OK)
        ]

    def _site_rdb(self, params: dict, kind: str = "sites", refresh: bool = True) -> pd.DataFrame:
        base = {"format": "rdb", "siteStatus": "all"}
        base.update(params)
        art = self.get(f"{self.legacy}/site/", params=base, kind=kind, refresh=refresh)
        return parse_rdb(art.read_text())

    def discover(self) -> pd.DataFrame:
        frames = [self._site_rdb({"stateCd": "nm", "siteOutput": "expanded"})]
        for w, s, e, n in self._border_strips():
            try:
                frames.append(self._site_rdb({"bBox": f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}", "siteOutput": "expanded"}))
            except Exception as ex:
                log.warning("border strip %s failed: %s", (w, s, e, n), ex)
        allow = self.scope.allow_sites.get("usgs", [])
        for i in range(0, len(allow), 100):
            try:
                frames.append(self._site_rdb({"sites": ",".join(allow[i:i + 100]), "siteOutput": "expanded"}))
            except Exception as ex:
                log.warning("allow-list batch failed: %s", ex)
        df = pd.concat([f for f in frames if len(f)], ignore_index=True).drop_duplicates("site_no")
        # Series catalogs (period of record) -> reference tables + inventory json on the site
        cats = {}
        for dtype in ("dv", "uv", "gw", "iv"):
            try:
                cat = self._site_rdb({"stateCd": "nm", "seriesCatalogOutput": "true", "outputDataTypeCd": dtype},
                                     kind="catalog")
                if len(cat):
                    cats[dtype] = cat
                    self.store.write_table(cat, "reference", self.name, f"series_catalog_{dtype}")
            except Exception as ex:
                log.warning("series catalog %s failed: %s", dtype, ex)
        # allow-listed out-of-state sites: catalogs by site
        if allow:
            for dtype in ("dv", "uv"):
                try:
                    cat = self._site_rdb({"sites": ",".join(allow[:100]), "seriesCatalogOutput": "true",
                                          "outputDataTypeCd": dtype}, kind="catalog")
                    if len(cat):
                        self.store.write_table(cat, "reference", self.name, f"series_catalog_{dtype}_allowlist")
                        cats[dtype] = pd.concat([cats.get(dtype, pd.DataFrame()), cat], ignore_index=True)
                except Exception as ex:
                    log.warning("allow-list catalog %s failed: %s", dtype, ex)
        inv = {}
        for dtype, cat in cats.items():
            for sn, g in cat.groupby("site_no"):
                inv.setdefault(sn, {})[dtype] = [
                    {"parm": r.get("parm_cd"), "stat": r.get("stat_cd"), "begin": r.get("begin_date"),
                     "end": r.get("end_date"), "n": r.get("count_nu")}
                    for _, r in g.iterrows()
                ]

        def f(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        sites = pd.DataFrame(
            {
                "native_id": df["site_no"],
                "name": df["station_nm"],
                "lat": df["dec_lat_va"].map(f),
                "lon": df["dec_long_va"].map(f),
                "elevation_m": df.get("alt_va", pd.Series(index=df.index)).map(f).mul(0.3048),
                "site_type": df["site_tp_cd"].map(lambda t: SITE_TYPE_MAP.get(t, "other")),
                "agency": df["agency_cd"],
                "state": df["state_cd"].map(_state_abbr),
                "county_fips": df["state_cd"].str.zfill(2) + df["county_cd"].str.zfill(3),
                "huc8": df.get("huc_cd", pd.Series(index=df.index)),
                "basin": df.get("huc_cd", pd.Series(index=df.index)).map(basin_from_huc),
                "well_depth_m": df.get("well_depth_va", pd.Series(index=df.index)).map(f).mul(0.3048),
                "aquifer": df.get("aqfr_cd", pd.Series(index=df.index)),
                "drainage_area_km2": df.get("drain_area_va", pd.Series(index=df.index)).map(f).mul(2.589988),
                "active": None,
                "raw_metadata": [
                    json.dumps({**{k: v for k, v in r.items() if v not in ("", None)}, "inventory": inv.get(r["site_no"], {})},
                               default=str)
                    for r in df.to_dict("records")
                ],
            }
        )
        return sites

    # ------------------------------------------------------------------ fetch
    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        kinds = opts.get("kinds") or ["daily", "gwlevels", "peaks", "measurements"]
        if self.opt("include_continuous", False) and "continuous" not in kinds:
            kinds.append("continuous")
        summ = FetchSummary(self.name)
        if "daily" in kinds:
            summ.add(self.fetch_daily(since, site_ids, limit, refresh))
        if "gwlevels" in kinds or "measurements" in kinds:
            # OGC field-measurements holds discrete groundwater levels (72019/62610/62611) and
            # field discharge/stage measurements; the legacy gwlevels service now returns 503.
            summ.add(self.fetch_ogc_table("field-measurements", since, refresh, site_ids))
        if "peaks" in kinds:
            summ.add(self.fetch_ogc_table("peaks", since, refresh, site_ids))
        if "continuous" in kinds:
            summ.add(self.fetch_continuous(since, site_ids, limit, refresh, until=opts.get("until")))
        return summ

    # ---- daily values ------------------------------------------------------------
    def fetch_daily(self, since, site_ids, limit, refresh) -> FetchSummary:
        summ = FetchSummary(self.name)
        end = date.today().isoformat()
        start = since.isoformat() if since else "1880-01-01"
        params = list(self.opt("daily_params", ["00060"]))
        jobs: list[dict] = []
        if site_ids:
            for i in range(0, len(site_ids), 100):
                jobs.append({"sites": ",".join(site_ids[i:i + 100]), "startDT": start, "endDT": end,
                             "siteStatus": "all", "format": "rdb"})
        else:
            for p in params:
                jobs.append({"stateCd": "nm", "parameterCd": p, "startDT": start, "endDT": end,
                             "siteStatus": "all", "format": "rdb"})
            allow = self.scope.allow_sites.get("usgs", [])
            for i in range(0, len(allow), 100):
                jobs.append({"sites": ",".join(allow[i:i + 100]), "startDT": start, "endDT": end,
                             "siteStatus": "all", "format": "rdb"})
            # border-strip sites (not NM, not allow-listed) by bbox strips, all params
            for w, s, e, n in self._border_strips():
                jobs.append({"bBox": f"{w:.4f},{s:.4f},{e:.4f},{n:.4f}", "startDT": start, "endDT": end,
                             "siteStatus": "all", "format": "rdb"})
        if limit:
            jobs = jobs[:limit]

        def one(p: dict) -> int:
            tag = p.get("parameterCd") or p.get("bBox", "sites")[:20]
            art = self.get(f"{self.legacy}/dv/", params=p, kind="daily", refresh=refresh or bool(since),
                           variable=p.get("parameterCd"), window=(start, end),
                           key_extra=None if since is None else f"since={start}")
            summ.n_requests += 1
            if art.from_cache:
                summ.n_cached += 1
                if self.already_written(art):
                    return 0
            df = self.normalize(art)
            n = self.write_obs(df, tag=f"dv-{tag}") if df is not None else 0
            self.ledger.set_rows(art.request_key, n)
            return n

        res = self.parallel(one, jobs, desc="daily")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(jobs) - len(res)
        return summ

    def _normalize_dv_rdb(self, text: str) -> pd.DataFrame | None:
        frames = []
        for df in iter_rdb_blocks(text):
            if df.empty or "site_no" not in df.columns:
                continue
            value_cols = [c for c in df.columns if QUAL_RE.match(c) and not c.endswith("_cd")]
            for c in value_cols:
                m = QUAL_RE.match(c)
                _, parm, stat, _ = m.groups()
                qcol = c + "_cd"
                sub = pd.DataFrame(
                    {
                        "site_uid": "usgs:" + df["site_no"],
                        "datetime_utc": pd.to_datetime(df["datetime"], errors="coerce", utc=True),
                        "value": pd.to_numeric(df[c], errors="coerce"),
                        "qualifier": df[qcol] if qcol in df.columns else None,
                        "source_param": parm,
                        "statistic": STAT_MAP.get(stat or "", None),
                        "interval": "daily",
                        "utc_offset_min": None,
                    }
                )
                frames.append(sub[sub["value"].notna()])
        if not frames:
            return None
        out = pd.concat(frames, ignore_index=True)
        out = self.xw.apply(out, self.name)
        return out

    # ---- legacy groundwater levels RDB (kept for reprocessing any archived responses) -------
    def _normalize_gw_rdb(self, text: str) -> pd.DataFrame | None:
        df = parse_rdb(text)
        if df.empty or "site_no" not in df.columns:
            return None
        # Modern gwlevels RDB: parameter_cd, lev_va (depth) or sl_lev_va (elevation), lev_dt, lev_tm, lev_tz_cd,
        # lev_status_cd, lev_age_cd; older: lev_va only (= 72019)
        parm = df["parameter_cd"] if "parameter_cd" in df.columns else pd.Series("72019", index=df.index)
        val = df["lev_va"] if "lev_va" in df.columns else df.get("sl_lev_va")
        if val is None:
            return None
        if "sl_lev_va" in df.columns:
            val = val.where(val.astype(str).str.strip() != "", df["sl_lev_va"])
        dt = df["lev_dt"].astype(str)
        tm = df["lev_tm"].astype(str) if "lev_tm" in df.columns else ""
        ts = pd.to_datetime((dt + " " + tm).str.strip(), errors="coerce")
        ts = ts.fillna(pd.to_datetime(dt, errors="coerce"))
        out = pd.DataFrame(
            {
                "site_uid": "usgs:" + df["site_no"],
                "datetime_utc": ts.dt.tz_localize("UTC"),
                "value": pd.to_numeric(val, errors="coerce"),
                "qualifier": (df.get("lev_status_cd", "").astype(str) + "|" + df.get("lev_age_cd", "").astype(str)).str.strip("|"),
                "source_param": parm,
                "statistic": "instantaneous",
                "interval": "irregular",
                "utc_offset_min": None,
            }
        )
        out = out[out["value"].notna() & out["datetime_utc"].notna()]
        return self.xw.apply(out, self.name)

    # ---- OGC tables: peaks, field-measurements ---------------------------------------
    OGC_PAGE = 50000
    OGC_NO_DATETIME = frozenset({"peaks"})   # collections that reject a datetime filter
    OGC_FIRST_YEAR = 1850

    def _ogc_windows(self, since) -> list[tuple[date, date]]:
        """Yearly windows (decades before 1900) from first year or `since` to today."""
        today = date.today()
        if since:
            return [(since, today)]
        wins: list[tuple[date, date]] = []
        y = self.OGC_FIRST_YEAR
        while y <= today.year:
            step = 10 if y < 1900 else 1
            e = min(date(y + step - 1, 12, 31), today)
            wins.append((date(y, 1, 1), e))
            y += step
        return wins

    def _ogc_fetch_window(self, collection: str, base: dict, b: date, e: date, refresh: bool,
                          summ: FetchSummary, depth: int = 0) -> list[pd.DataFrame]:
        """Fetch one time window; if the page is full, split the window and recurse."""
        params = dict(base)
        if collection not in self.OGC_NO_DATETIME:
            params["datetime"] = f"{b.isoformat()}T00:00:00Z/{e.isoformat()}T23:59:59Z"
        art = self.get(f"{self.ogc}/collections/{collection}/items", params=params, kind=collection,
                       window=(b.isoformat(), e.isoformat()), refresh=refresh)
        summ.n_requests += 1
        if art.from_cache:
            summ.n_cached += 1
        text = art.read_text()
        if not text.strip():
            return []
        try:
            df = pd.read_csv(io.StringIO(text), dtype=str)
        except Exception:
            return []
        if collection in self.OGC_NO_DATETIME:
            if len(df) >= self.OGC_PAGE:
                log.warning("usgs %s returned a full page (%d rows) and cannot be split by time; "
                            "results are truncated", collection, len(df))
            return [df]
        if len(df) < self.OGC_PAGE or depth >= 3 or b == e:
            return [df]
        # Full page: split the window in halves (year -> ~6 months -> ~3 months -> ~45 days)
        mid = b + (e - b) / 2
        log.info("usgs %s window %s..%s full (%d rows); splitting", collection, b, e, len(df))
        return (self._ogc_fetch_window(collection, base, b, mid, refresh, summ, depth + 1)
                + self._ogc_fetch_window(collection, base, mid + timedelta(days=1), e, refresh, summ, depth + 1))

    def fetch_ogc_table(self, collection: str, since, refresh, site_ids: list[str] | None = None) -> FetchSummary:
        summ = FetchSummary(self.name)
        key = self._api_key()
        common = {"f": "csv", "limit": self.OGC_PAGE, **key}
        jobs: list[tuple[dict, date, date]] = []
        # peaks rejects any datetime filter ("datetime query not supported"); the whole New Mexico
        # table is one request of ~37,000 rows, so it is simply re-pulled whole every time.
        wins = [(date(self.OGC_FIRST_YEAR, 1, 1), date.today())] if collection in self.OGC_NO_DATETIME \
            else self._ogc_windows(since)
        if site_ids:
            for sid in site_ids:
                jobs.append(({**common, "monitoring_location_id": f"USGS-{sid}"}, wins[0][0], date.today()))
        else:
            for b, e in wins:
                jobs.append(({**common, "state_name": "New Mexico"}, b, e))
            # border strips by bbox, whole period (few rows)
            for w, s, e_, n in self._border_strips():
                jobs.append(({**common, "bbox": f"{w:.4f},{s:.4f},{e_:.4f},{n:.4f}"}, date(self.OGC_FIRST_YEAR, 1, 1) if not since else since, date.today()))
            for sid in self.scope.allow_sites.get("usgs", []):
                jobs.append(({**common, "monitoring_location_id": f"USGS-{sid}"}, date(self.OGC_FIRST_YEAR, 1, 1) if not since else since, date.today()))

        def one(job) -> int:
            base, b, e = job
            frames = self._ogc_fetch_window(collection, base, b, e, refresh or bool(since), summ)
            frames = [f for f in frames if len(f)]
            if not frames:
                return 0
            df = pd.concat(frames, ignore_index=True).drop_duplicates()
            tag = base.get("monitoring_location_id") or base.get("bbox", "nm")[:12]
            self.store.append_table(df, "reference", self.name, collection.replace("-", "_"),
                                    f"{self.run_id}-{_safe_tag(tag)}-{b.year}")
            obs = self._normalize_ogc_table(collection, df)
            if obs is not None and len(obs):
                self.write_obs(obs, tag=f"{collection}-{b.year}")
            return len(df)

        res = self.parallel(one, jobs, desc=collection)
        summ.n_rows = int(sum(res))
        summ.n_errors = len(jobs) - len(res)
        return summ

    def _normalize_ogc_table(self, collection: str, df: pd.DataFrame) -> pd.DataFrame | None:
        """Groundwater levels from field-measurements and annual peaks become observations."""
        if df.empty or "parameter_code" not in df.columns:
            return None
        ts = pd.to_datetime(df["time"], errors="coerce", utc=True)
        if collection == "field-measurements":
            keep = df["parameter_code"].isin(["72019", "62610", "62611", "72020", "62615", "72150"])
            sub = df[keep]
            if sub.empty:
                return None
            out = pd.DataFrame(
                {
                    "site_uid": "usgs:" + sub["monitoring_location_id"].str.replace(r"^[A-Z0-9]+-", "", regex=True),
                    "datetime_utc": ts[keep],
                    "value": pd.to_numeric(sub["value"], errors="coerce"),
                    "qualifier": (sub.get("approval_status", pd.Series("", index=sub.index)).fillna("").astype(str)
                                  + "|" + sub.get("qualifier", pd.Series("", index=sub.index)).fillna("").astype(str)
                                  + "|" + sub.get("observing_procedure_code", pd.Series("", index=sub.index)).fillna("").astype(str)).str.strip("|"),
                    "source_param": sub["parameter_code"],
                    "statistic": "instantaneous",
                    "interval": "irregular",
                    "utc_offset_min": None,
                }
            )
        elif collection == "peaks":
            out = pd.DataFrame(
                {
                    "site_uid": "usgs:" + df["monitoring_location_id"].str.replace(r"^[A-Z0-9]+-", "", regex=True),
                    "datetime_utc": ts,
                    "value": pd.to_numeric(df["value"], errors="coerce"),
                    "qualifier": ("peak|" + df.get("qualifier", pd.Series("", index=df.index)).fillna("").astype(str)).str.strip("|"),
                    "source_param": df["parameter_code"],
                    "statistic": "max",
                    "interval": "water_year",
                    "utc_offset_min": None,
                }
            )
        else:
            return None
        out = out[out["value"].notna() & out["datetime_utc"].notna()]
        return self.xw.apply(out, self.name)

    # ---- continuous (15-min) -------------------------------------------------------
    def fetch_continuous(self, since, site_ids, limit, refresh, until=None) -> FetchSummary:
        """15-minute unit values.

        The full public record (2007-10-01 onward) is 250-500 million rows and thousands of
        requests against an hourly rate limit, and most analysis never touches it. So the
        default window is the most recent `continuous_years` (config, default 1). Ask for more
        explicitly with a date range:

            nmwater fetch usgs --kind continuous                          # last year
            nmwater fetch usgs --kind continuous --since 2015-01-01       # 2015 to today
            nmwater fetch usgs --kind continuous --since 2011-01-01 --until 2011-12-31
            nmwater fetch usgs --kind continuous --site 08313000 --since 2007-10-01

        Windows already completed are recorded in the ledger, so overlapping requests are served
        from the archive and only genuinely new spans hit the network.
        """
        summ = FetchSummary(self.name)
        params_ok = set(self.opt("continuous_params", ["00060"]))
        win = int(self.opt("continuous_window_days", 1000))
        floor = date(2007, 10, 1)
        today = date.today()
        end_cap = min(until, today) if until else today
        # Default horizon when no explicit start was given.
        if since is None:
            years = float(self.opt("continuous_years", 1))
            default_start = end_cap - timedelta(days=round(years * 365.25))
            summ.notes.append(
                f"continuous: defaulting to the last {years:g} year(s) "
                f"({default_start} to {end_cap}); pass --since/--until for more"
            )
        else:
            default_start = since
        sites = self.sites()
        if sites.empty:
            summ.notes.append("run `nmwater discover usgs` first (needs series catalog)")
            return summ
        jobs: list[tuple[str, str, date, date]] = []
        for r in sites.itertuples(index=False):
            sid = r.native_id
            if site_ids and sid not in site_ids:
                continue
            try:
                inv = json.loads(r.raw_metadata).get("inventory", {})
            except Exception:
                inv = {}
            for ent in inv.get("uv", []) + inv.get("iv", []):
                p = ent.get("parm")
                if p not in params_ok or not ent.get("begin"):
                    continue
                # Start at the later of: the series' own start, the archive floor, the requested
                # start, and (for incremental runs) whatever has already been fetched.
                b = max(date.fromisoformat(ent["begin"][:10]), floor, default_start)
                # `--since X` alone means "catch up", so skip ahead to whatever has already been
                # fetched. An explicit `--since X --until Y` names a span the caller wants, so
                # honour it exactly; identical windows are still served from the archive.
                if since is not None and until is None:
                    last = self.ledger.last_window_end(self.name, self.uid(sid), p)
                    if last:
                        b = max(b, date.fromisoformat(last[:10]))
                series_end = date.fromisoformat(ent["end"][:10]) if ent.get("end") else today
                e = min(series_end, end_cap)
                cur = b
                while cur <= e:
                    stop = min(cur + timedelta(days=win - 1), e)
                    jobs.append((sid, p, cur, stop))
                    cur = stop + timedelta(days=1)
        # dedupe (uv and iv catalogs overlap)
        jobs = sorted(set(jobs))
        if limit:
            sids = sorted({j[0] for j in jobs})[:limit]
            jobs = [j for j in jobs if j[0] in sids]
        log.info("usgs continuous: %d windows over %d sites", len(jobs), len({j[0] for j in jobs}))

        def one(j) -> int:
            sid, p, b, e = j
            params = {"monitoring_location_id": f"USGS-{sid}", "parameter_code": p,
                      "datetime": f"{b.isoformat()}T00:00:00Z/{e.isoformat()}T23:59:59Z",
                      "f": "csv", "limit": 50000, **self._api_key()}
            url = f"{self.ogc}/collections/continuous/items"
            total = 0
            pages = 0
            while url and pages < 200:
                art = self.get(url, params=params, kind="continuous", site_uid=self.uid(sid), variable=p,
                               window=(b.isoformat(), e.isoformat()), refresh=refresh)
                summ.n_requests += 1
                if art.from_cache:
                    summ.n_cached += 1
                    if self.already_written(art):
                        break
                df = self._normalize_ogc_csv(art.read_text(), interval="15min")
                n = self.write_obs(df, tag=f"iv-{sid}-{p}") if df is not None else 0
                self.ledger.set_rows(art.request_key, n)
                total += n
                # page by time if the page was full
                if df is not None and len(df) >= 50000:
                    last = df["datetime_utc"].max()
                    params = {**params, "datetime": f"{last.strftime('%Y-%m-%dT%H:%M:%SZ')}/{e.isoformat()}T23:59:59Z"}
                    pages += 1
                else:
                    url = None
            return total

        res = self.parallel(one, jobs, desc="continuous")
        summ.n_rows = int(sum(res))
        summ.n_errors = len(jobs) - len(res)
        return summ

    def _normalize_ogc_csv(self, text: str, interval: str) -> pd.DataFrame | None:
        if not text.strip():
            return None
        df = pd.read_csv(io.StringIO(text), dtype=str)
        if df.empty:
            return None
        cols = {c.lower(): c for c in df.columns}
        loc = cols.get("monitoring_location_id")
        tcol = cols.get("time")
        vcol = cols.get("value")
        pcol = cols.get("parameter_code")
        if not (loc and tcol and vcol and pcol):
            return None
        ts = pd.to_datetime(df[tcol], errors="coerce", utc=True)
        out = pd.DataFrame(
            {
                "site_uid": "usgs:" + df[loc].str.replace(r"^[A-Z0-9]+-", "", regex=True),
                "datetime_utc": ts,
                "value": pd.to_numeric(df[vcol], errors="coerce"),
                "qualifier": (df[cols["approval_status"]] if "approval_status" in cols else pd.Series(None, index=df.index)),
                "source_param": df[pcol],
                "statistic": df[cols["statistic_id"]].map(STAT_MAP) if "statistic_id" in cols else "instantaneous",
                "interval": interval,
                "utc_offset_min": None,
            }
        )
        if "qualifier" in cols:
            q = df[cols["qualifier"]].fillna("")
            out["qualifier"] = (out["qualifier"].fillna("").astype(str) + "|" + q.astype(str)).str.strip("|")
        out = out[out["value"].notna() & out["datetime_utc"].notna()]
        return self.xw.apply(out, self.name)

    # ---- reprocess entry point ------------------------------------------------------
    def normalize(self, artifact) -> pd.DataFrame | None:
        if artifact.kind == "daily":
            return self._normalize_dv_rdb(artifact.read_text())
        if artifact.kind == "gwlevels":
            return self._normalize_gw_rdb(artifact.read_text())
        if artifact.kind == "continuous":
            return self._normalize_ogc_csv(artifact.read_text(), "15min")
        if artifact.kind in ("field-measurements", "peaks"):
            try:
                df = pd.read_csv(io.StringIO(artifact.read_text()), dtype=str)
            except Exception:
                return None
            return self._normalize_ogc_table(artifact.kind, df)
        return None

    def reprocess(self, kind: str | None = None) -> int:
        n = 0
        kinds = [kind] if kind else ["daily", "gwlevels", "continuous", "field-measurements", "peaks"]
        for k in kinds:
            n += super().reprocess(kind=k)
        return n


_STATES = {"04": "AZ", "08": "CO", "35": "NM", "40": "OK", "48": "TX", "49": "UT", "20": "KS"}


def _safe_tag(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.\-]+", "_", str(s))


def _state_abbr(code: str) -> str | None:
    return _STATES.get(str(code).zfill(2), str(code) if code else None)
