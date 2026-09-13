"""Shared OGC SensorThings API 1.1 (FROST-Server) client base for NM Water Data Initiative hubs.

Not registered as a source. Provides:
  - paged listing of Things (with Locations/Datastreams expanded) following @iot.nextLink
  - bulk Observations via keyset paging on @iot.id with $expand=Datastream($select=id)
  - per-datastream Observations for targeted pulls

Verified 2026-09-13 on both st2 and nmenv: $resultFormat=dataArray silently drops the `id` and
`Datastream` components even when $select asks for them, so a dataArray page cannot be keyset-paged
and its rows cannot be attributed to a datastream. Bulk paging therefore uses ordinary JSON with
$expand=Datastream($select=id), which returns @iot.id plus the datastream id per observation.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date
from typing import Any

import pandas as pd

from ..core.http import RawArtifact
from .base import Source

log = logging.getLogger("nmwater.sensorthings")


class SensorThingsSource(Source):
    page_size = 1000
    obs_page_size = 5000

    @property
    def base(self) -> str:
        return (self.cfg.base_url or "").rstrip("/")

    # -- generic paging ------------------------------------------------------
    def st_pages(self, path: str, params: dict[str, Any] | None, kind: str, refresh: bool = False,
                 max_pages: int = 100000) -> Iterator[tuple[RawArtifact, dict]]:
        url = f"{self.base}/{path.lstrip('/')}"
        n = 0
        while url and n < max_pages:
            art = self.get(url, params=params, kind=kind, refresh=refresh)
            doc = art.read_json()
            yield art, doc
            url = doc.get("@iot.nextLink")
            params = None  # nextLink carries the full query
            n += 1

    def list_things(self, expand: str, select: str | None = None, extra_filter: str | None = None,
                    refresh: bool = True, limit: int | None = None) -> list[dict]:
        params: dict[str, Any] = {"$top": self.page_size, "$expand": expand, "$orderby": "id asc"}
        if select:
            params["$select"] = select
        if extra_filter:
            params["$filter"] = extra_filter
        out: list[dict] = []
        for _, doc in self.st_pages("Things", params, kind="things", refresh=refresh):
            out.extend(doc.get("value", []))
            if limit and len(out) >= limit:
                return out[:limit]
        return out

    def list_datastreams(self, select: str, expand: str | None = None, refresh: bool = False,
                         page_size: int | None = None) -> list[dict]:
        params: dict[str, Any] = {"$top": page_size or self.obs_page_size, "$select": select, "$orderby": "id asc"}
        if expand:
            params["$expand"] = expand
        out: list[dict] = []
        for _, doc in self.st_pages("Datastreams", params, kind="datastreams", refresh=refresh):
            out.extend(doc.get("value", []))
        return out

    # -- observations --------------------------------------------------------
    @staticmethod
    def _ds_id_from_link(link: str) -> int | None:
        try:
            return int(link.rsplit("(", 1)[1].rstrip(")"))
        except (IndexError, ValueError):
            return None

    def parse_data_array(self, doc: dict, want_params: bool = False) -> pd.DataFrame:
        """dataArray response -> frame with datastream_id, obs_id, phenomenon_time, result[, parameters]."""
        recs = []
        for grp in doc.get("value", []):
            ds = self._ds_id_from_link(grp.get("Datastream@iot.navigationLink", ""))
            comps = grp.get("components", [])
            idx = {c: i for i, c in enumerate(comps)}
            for row in grp.get("dataArray", []):
                rec = {
                    "datastream_id": ds,
                    "obs_id": row[idx["id"]] if "id" in idx else None,
                    "phenomenon_time": row[idx["phenomenonTime"]] if "phenomenonTime" in idx else None,
                    "result": row[idx["result"]] if "result" in idx else None,
                }
                if want_params and "parameters" in idx:
                    rec["parameters"] = row[idx["parameters"]]
                recs.append(rec)
        return pd.DataFrame(recs)

    def iter_observations_global(self, since: date | None = None, refresh: bool = False,
                                 select: str = "id,phenomenonTime,result", max_pages: int | None = None,
                                 want_params: bool = False) -> Iterator[tuple[RawArtifact, pd.DataFrame]]:
        """Keyset-page all Observations ordered by id. Tail pages (partial) are always refetched so
        new data arrive on re-runs; full pages come from the archive."""
        last_id = 0
        top = self.obs_page_size
        pages = 0
        while True:
            filt = f"id gt {last_id}"
            if since:
                filt += f" and phenomenonTime ge {since.isoformat()}T00:00:00Z"
            params = {"$top": top, "$filter": filt, "$orderby": "id asc", "$resultFormat": "dataArray",
                      "$select": select}
            url = f"{self.base}/Observations"
            art = self.get(url, params=params, kind="obs", refresh=refresh)
            doc = art.read_json()
            df = self.parse_data_array(doc, want_params=want_params)
            if art.from_cache and len(df) < top:
                # partial page from a previous run: refetch to pick up new observations
                art = self.get(url, params=params, kind="obs", refresh=True)
                doc = art.read_json()
                df = self.parse_data_array(doc, want_params=want_params)
            yield art, df
            pages += 1
            if len(df) < top or df["obs_id"].isna().all():
                break
            last_id = int(df["obs_id"].max())
            if max_pages and pages >= max_pages:
                break

    def iter_observations_expanded(self, since: date | None = None, refresh: bool = False,
                                   max_pages: int | None = None, want_params: bool = False,
                                   ) -> Iterator[tuple[RawArtifact, pd.DataFrame]]:
        """Keyset-page all Observations as ordinary JSON, carrying the datastream id.

        Yields frames with columns obs_id, phenomenon_time, result, datastream_id (and parameters
        when want_params). Full pages come from the archive; a partial tail page is refetched so
        later runs pick up new observations.
        """
        select = "id,phenomenonTime,result" + (",parameters" if want_params else "")
        last_id = 0
        top = self.obs_page_size
        pages = 0
        while True:
            filt = f"id gt {last_id}"
            if since:
                filt += f" and phenomenonTime ge {since.isoformat()}T00:00:00Z"
            params = {"$top": top, "$filter": filt, "$orderby": "id asc", "$select": select,
                      "$expand": "Datastream($select=id)"}
            url = f"{self.base}/Observations"
            art = self.get(url, params=params, kind="obs", refresh=refresh)
            rows = art.read_json().get("value", [])
            if art.from_cache and len(rows) < top:
                art = self.get(url, params=params, kind="obs", refresh=True)
                rows = art.read_json().get("value", [])
            df = pd.DataFrame(
                {
                    "obs_id": [r.get("@iot.id") for r in rows],
                    "phenomenon_time": [r.get("phenomenonTime") for r in rows],
                    "result": [r.get("result") for r in rows],
                    "datastream_id": [(r.get("Datastream") or {}).get("@iot.id") for r in rows],
                    **({"parameters": [r.get("parameters") for r in rows]} if want_params else {}),
                }
            )
            yield art, df
            pages += 1
            if len(rows) < top or df["obs_id"].isna().all():
                break
            last_id = int(pd.to_numeric(df["obs_id"], errors="coerce").max())
            if max_pages and pages >= max_pages:
                break

    def observations_for_datastream(self, ds_id: int, since: date | None = None, refresh: bool = False,
                                    want_params: bool = False) -> Iterator[tuple[RawArtifact, pd.DataFrame]]:
        params: dict[str, Any] = {"$top": self.obs_page_size, "$orderby": "phenomenonTime asc",
                                  "$resultFormat": "dataArray", "$select": "id,phenomenonTime,result"
                                  + (",parameters" if want_params else "")}
        if since:
            params["$filter"] = f"phenomenonTime ge {since.isoformat()}T00:00:00Z"
        url = f"{self.base}/Datastreams({ds_id})/Observations"
        while url:
            art = self.get(url, params=params, kind="obs_ds", refresh=refresh, site_uid=None, variable=str(ds_id))
            doc = art.read_json()
            df = self.parse_data_array(doc, want_params=want_params)
            df["datastream_id"] = ds_id
            yield art, df
            url = doc.get("@iot.nextLink")
            params = None

    @staticmethod
    def clean_times(ts: pd.Series, lo_year: int = 1900, hi_year: int | None = None) -> pd.Series:
        """Parse ISO timestamps to UTC, nulling absurd years."""
        hi_year = hi_year or (date.today().year + 1)
        t = pd.to_datetime(ts, errors="coerce", utc=True, format="ISO8601")
        bad = t.notna() & ((t.dt.year < lo_year) | (t.dt.year > hi_year))
        return t.mask(bad)
