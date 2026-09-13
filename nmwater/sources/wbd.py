"""USGS Watershed Boundary Dataset: HU2 GeoPackages for regions 11, 12, 13, 14, 15.

  https://prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/WBD/HU2/GPKG/WBD_<RR>_HU2_GPKG.zip  (~117 MB each)
Unzipped into data/grids/wbd/ so nmwater.core.geo.assign_hucs can spatially join sites. Also writes
reference table `huc8_in_scope` (HUC8 polygons intersecting the buffered NM bbox) and `huc4_in_scope`.
"""

from __future__ import annotations

import json
import logging
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd

from ..core.grids import record_grid, stream_download
from ..core.http import request_key
from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.wbd")

BASE = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Hydrography/WBD/HU2/GPKG"
CITATION = "U.S. Geological Survey, Watershed Boundary Dataset (WBD), accessed 2026 via The National Map."


@register
class WBD(Source):
    name = "wbd"
    agency = "USGS"
    description = "Watershed Boundary Dataset HU2 GeoPackages (regions 11-15) + HUC8/HUC4 in scope"
    kinds = ("reference",)

    def _dir(self) -> Path:
        d = self.settings.grids_dir / "wbd"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def discover(self) -> pd.DataFrame:
        return pd.DataFrame([{"native_id": f"hu2:{r}", "name": f"WBD region {r}", "site_type": "area", "agency": "USGS",
                              "active": True, "raw_metadata": "{}"} for r in self.scope.huc2])

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        import geopandas as gpd
        from shapely.geometry import box

        summ = FetchSummary(self.name)
        regions = [r for r in self.scope.huc2 if not site_ids or r in site_ids]
        if limit:
            regions = regions[:limit]
        gpkgs = []
        for r in regions:
            url = f"{BASE}/WBD_{r}_HU2_GPKG.zip"
            key = request_key("GET", url, None)
            rec = self.ledger.get(key)
            existing = sorted(self._dir().glob(f"WBD_{r}_HU2_GPKG*.gpkg"))
            if existing and rec and rec.status == "ok" and not refresh:
                summ.n_cached += 1
                gpkgs.append(existing[0])
                continue
            zp = self._dir() / f"WBD_{r}_HU2_GPKG.zip"
            status, sha, nb = stream_download(self.http, self.name, url, zp)
            summ.n_requests += 1
            with zipfile.ZipFile(zp) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".gpkg")]
                for n in names:
                    z.extract(n, self._dir())
                    src = self._dir() / n
                    dest = self._dir() / Path(n).name
                    if src != dest:
                        src.replace(dest)
                    gpkgs.append(dest)
            zp.unlink(missing_ok=True)
            record_grid(self.ledger, self.run_id, self.name, url, None, gpkgs[-1], sha, nb, variable=f"hu2_{r}", key=key)
        # HUC8 / HUC4 in scope
        w, s, e, n = self.scope.bbox_buffered
        bb = box(w, s, e, n)
        h8, h4 = [], []
        for g in gpkgs:
            layers = {name for name, _ in _list_layers(g)}
            l8 = "WBDHU8" if "WBDHU8" in layers else next((x for x in layers if x.lower().endswith("hu8")), None)
            l4 = "WBDHU4" if "WBDHU4" in layers else next((x for x in layers if x.lower().endswith("hu4")), None)
            if l8:
                df = gpd.read_file(g, layer=l8, bbox=(w, s, e, n))
                df = df[df.intersects(bb)] if len(df) else df
                h8.append(pd.DataFrame({"huc8": df["huc8"].astype(str), "name": df["name"], "states": df.get("states"),
                                        "areasqkm": df.get("areasqkm"), "region": g.name}))
            if l4:
                df = gpd.read_file(g, layer=l4, bbox=(w, s, e, n))
                h4.append(pd.DataFrame({"huc4": df["huc4"].astype(str), "name": df["name"], "states": df.get("states")}))
        if h8:
            allh8 = pd.concat(h8, ignore_index=True).drop_duplicates("huc8").sort_values("huc8")
            self.store.write_table(allh8, "reference", self.name, "huc8_in_scope")
            summ.n_rows += len(allh8)
            summ.notes.append(f"{len(allh8)} HUC8 in scope")
        if h4:
            allh4 = pd.concat(h4, ignore_index=True).drop_duplicates("huc4").sort_values("huc4")
            self.store.write_table(allh4, "reference", self.name, "huc4_in_scope")
        return summ


def _list_layers(gpkg: Path) -> list[tuple[str, str]]:
    import pyogrio

    return [(str(name), str(geom)) for name, geom in pyogrio.list_layers(gpkg)]
