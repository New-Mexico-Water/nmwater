"""International Boundary and Water Commission (US Section) - Rio Grande daily flow conditions and
the historical Water Bulletins.

Verified 2026-09-12:
  https://ibwcsftpstg.blob.core.windows.net/wad/DailyReports/flowdata.htm   (Excel-exported HTML, latin-1)
      rows: station | DATE (dd-Mon-yy) | STAGE (metres) | DISCHARGE (cms) | RAINFALL (mm) | USGS site number
  https://ibwcsftpstg.blob.core.windows.net/wad/DailyReports/flowprev.htm   -> links flowYYYYMMDD.htm (~5 months)
  https://ibwcsftpstg.blob.core.windows.net/wad/water_bulletins/Rio_Grande/{YYYY}.pdf  1931..2006 (4-25 MB each)
The AQUARIUS portal (waterdata.ibwc.gov/AQWebportal) redirects and was not usable anonymously.
"""

from __future__ import annotations

import io
import json
import logging
import re
from datetime import date

import pandas as pd

from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.ibwc")

BLOB = "https://ibwcsftpstg.blob.core.windows.net/wad"
DAILY = f"{BLOB}/DailyReports/flowdata.htm"
PREV = f"{BLOB}/DailyReports/flowprev.htm"
BULLETIN = f"{BLOB}/water_bulletins/Rio_Grande/{{year}}.pdf"
HREF_RE = re.compile(r'href="\./(flow\d{8}\.htm)"', re.IGNORECASE)


def _parse_flow_page(text: str) -> pd.DataFrame:
    """Return tidy rows (name, usgs, date, stage_m, discharge_cms, rain_mm) from a daily flow page."""
    tables = pd.read_html(io.StringIO(text))
    if not tables:
        return pd.DataFrame()
    t = tables[0]
    rows = []
    for _, r in t.iterrows():
        vals = [v for v in r.tolist() if str(v) != "nan"]
        vals = list(dict.fromkeys(str(v).strip() for v in vals))
        if len(vals) < 5:
            continue
        name = vals[0]
        m = re.match(r"^(\d{1,2}-[A-Za-z]{3}-\d{2})$", vals[1]) if len(vals) > 1 else None
        if not m:
            continue
        usgs = vals[-1]
        if not re.fullmatch(r"\d{7,8}", usgs):
            continue
        nums = vals[2:-1]
        if len(nums) < 3:
            nums = nums + [None] * (3 - len(nums))
        rows.append({"name": name, "usgs": usgs.zfill(8), "date": pd.to_datetime(vals[1], format="%d-%b-%y", errors="coerce"),
                     "stage_m": nums[0], "discharge_cms": nums[1], "rain_mm": nums[2]})
    return pd.DataFrame(rows)


@register
class IBWC(Source):
    name = "ibwc"
    agency = "IBWC"
    description = "IBWC daily Rio Grande flow at El Paso/Fort Quitman and Water Bulletins 1931-2006"
    kinds = ("sites", "daily", "bulletin")

    def discover(self) -> pd.DataFrame:
        art = self.get(DAILY, kind="daily", refresh=True, window=(date.today().isoformat(), date.today().isoformat()))
        df = _parse_flow_page(art.read_text("latin-1"))
        usgs = self.store.read_sites("usgs")
        usgs = usgs.set_index("native_id") if len(usgs) else None
        rows = []
        for r in df.drop_duplicates("usgs").itertuples(index=False):
            lat = lon = None
            state = "TX"
            if usgs is not None and r.usgs in usgs.index:
                lat, lon, state = usgs.loc[r.usgs, "lat"], usgs.loc[r.usgs, "lon"], usgs.loc[r.usgs, "state"]
            rows.append({"native_id": r.usgs, "name": f"{r.name} (IBWC)", "lat": lat, "lon": lon, "site_type": "stream",
                         "agency": "IBWC", "state": state, "basin": "Rio Grande", "active": True,
                         "raw_metadata": json.dumps({"ibwc_name": r.name, "usgs_site": r.usgs, "linked_usgs": lat is not None})})
        return pd.DataFrame(rows)

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        summ = FetchSummary(self.name)
        kinds = opts.get("kinds") or ["daily", "bulletin"]
        if "daily" in kinds:
            urls = [DAILY]
            try:
                prev = self.get(PREV, kind="index", refresh=True)
                names = HREF_RE.findall(prev.read_text("latin-1"))
                urls += [f"{BLOB}/DailyReports/{n}" for n in sorted(set(names))]
            except Exception as e:  # noqa: BLE001
                summ.notes.append(f"previous-reports index failed: {e}")
            if since:
                keep = []
                for u in urls:
                    m = re.search(r"flow(\d{8})\.htm", u)
                    if not m or date.fromisoformat(f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}") >= since:
                        keep.append(u)
                urls = keep
            if limit:
                urls = urls[:limit]

            def one(u: str) -> int:
                today = date.today().isoformat()
                # the live page changes daily: key it by date so the archive keeps each day's copy
                art = self.get(u, kind="daily", refresh=refresh, key_extra=today if u == DAILY else None,
                               window=(today, today))
                summ.n_requests += 1
                if art.from_cache:
                    summ.n_cached += 1
                    if self.already_written(art):
                        return 0
                df = self.normalize(art)
                if site_ids and df is not None:
                    df = df[df["site_uid"].isin([self.uid(s) for s in site_ids])]
                n = self.write_obs(df, tag="daily") if df is not None else 0
                self.ledger.set_rows(art.request_key, n)
                return n

            res = self.parallel(one, urls, desc="daily pages")
            summ.n_rows += int(sum(res))
            summ.n_errors += len(urls) - len(res)
        if "bulletin" in kinds:
            years = list(range(int(self.opt("bulletin_start", 1931)), int(self.opt("bulletin_end", 2006)) + 1))
            if limit:
                years = years[:limit]

            def bul(y: int) -> int:
                try:
                    art = self.get(BULLETIN.format(year=y), kind="bulletin", refresh=False)
                    summ.n_requests += 1
                    if art.from_cache:
                        summ.n_cached += 1
                    return 1
                except Exception as e:
                    if "404" in str(e):
                        summ.notes.append(f"bulletin {y}: not found")
                        return 0
                    raise

            res = self.parallel(bul, years, desc="bulletins")
            summ.n_errors += len(years) - len(res)
            summ.notes.append(f"bulletins mirrored: {int(sum(res))}/{len(years)}")
        return summ

    def normalize(self, artifact) -> pd.DataFrame | None:
        if artifact.kind != "daily":
            return None
        df = _parse_flow_page(artifact.read_text("latin-1"))
        if df.empty:
            return None
        frames = []
        for col, param in (("stage_m", "STAGE_M"), ("discharge_cms", "DISCHARGE_CMS"), ("rain_mm", "RAINFALL_MM")):
            frames.append(pd.DataFrame({
                "site_uid": "ibwc:" + df["usgs"],
                "datetime_utc": pd.to_datetime(df["date"], utc=True),
                "value": pd.to_numeric(df[col], errors="coerce"),
                "qualifier": "provisional",
                "source_param": param,
                "interval": "daily",
                "utc_offset_min": None,
            }))
        out = pd.concat(frames, ignore_index=True)
        out = out[out["value"].notna() & out["datetime_utc"].notna()]
        return self.xw.apply(out, self.name)
