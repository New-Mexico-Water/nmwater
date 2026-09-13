"""US Census TIGER/Line boundaries: the administrative geography of water demand.

Watersheds organize where water comes from. Places, counties, tracts and tribal lands organize
who uses it, who is served by which utility, and who is counted in a census. Most hard questions
in New Mexico live where those two partitions disagree, so the archive carries both.

Layers pulled (vintage set by `tiger_year`, default 2025):

  place    incorporated cities, towns, villages and census designated places (528 in NM)
  cousub   county subdivisions
  tract    census tracts
  bg       block groups
  county   counties, filtered to New Mexico and the neighbouring border counties in scope
  aiannh   American Indian / Alaska Native / Native Hawaiian areas: reservations, off-reservation
           trust land, Oklahoma statistical areas; filtered to those intersecting the scope box
  uac      urban areas (national file, filtered to scope)

Shapefiles land in data/grids/tiger/ as GeoPackages so nmwater.core.geo can spatially join sites
against them; attribute tables (no geometry) also go to the reference group for SQL use.

Source: https://www2.census.gov/geo/tiger/TIGER<year>/ - public domain, no key, no rate limit.
Verified 2026-09-13: the 2025 vintage is published for every layer used here.
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import pandas as pd

from ..core.grids import record_grid, stream_download
from .base import FetchSummary, Source, register

log = logging.getLogger("nmwater.tiger")

BASE = "https://www2.census.gov/geo/tiger"
CITATION = ("U.S. Census Bureau, TIGER/Line Shapefiles, accessed 2026. "
            "Public domain; boundaries are for statistical and cartographic use.")

# layer -> (scope of the published file, filename template)
LAYERS = {
    "place":  ("state",    "PLACE/tl_{y}_{st}_place.zip"),
    "cousub": ("state",    "COUSUB/tl_{y}_{st}_cousub.zip"),
    "tract":  ("state",    "TRACT/tl_{y}_{st}_tract.zip"),
    "bg":     ("state",    "BG/tl_{y}_{st}_bg.zip"),
    "county": ("national", "COUNTY/tl_{y}_us_county.zip"),
    "aiannh": ("national", "AIANNH/tl_{y}_us_aiannh.zip"),
    "uac":    ("national", "UAC20/tl_{y}_us_uac20.zip"),
}
# LSAD codes that matter for reading place records
LSAD_LABEL = {
    "25": "city", "43": "town", "47": "village", "57": "census designated place",
    "21": "borough", "00": "unspecified",
}


@register
class Tiger(Source):
    name = "tiger"
    agency = "US Census Bureau"
    description = "TIGER/Line boundaries: places, tracts, counties, tribal and urban areas"
    kinds = ("boundary",)

    @property
    def year(self) -> int:
        return int(self.opt("tiger_year", 2025))

    def _dir(self) -> Path:
        d = self.settings.grids_dir / "tiger"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _states(self) -> list[str]:
        """State FIPS to pull state-scoped layers for: New Mexico plus neighbours in scope."""
        fips = {"NM": "35", "CO": "08", "TX": "48", "AZ": "04", "OK": "40", "UT": "49", "KS": "20"}
        want = [self.scope.state_abbr] + list(self.opt("tiger_neighbor_states", []))
        return [fips[s] for s in want if s in fips]

    def discover(self) -> pd.DataFrame:
        return pd.DataFrame(
            [{"native_id": f"layer:{k}", "name": f"TIGER {k}", "site_type": "area",
              "agency": "US Census Bureau", "state": "NM"} for k in LAYERS],
            columns=["native_id", "name", "lat", "lon", "site_type", "agency", "state", "raw_metadata"],
        )

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        """Boundaries are a vintage, not a time series: `since` is ignored, `--refresh` re-downloads."""
        import geopandas as gpd

        summ = FetchSummary(self.name)
        y = self.year
        layers = list(LAYERS)
        if site_ids:
            layers = [l for l in layers if l in site_ids or f"layer:{l}" in site_ids]
        if limit:
            layers = layers[:limit]
        w, s, e, n = self.scope.bbox_buffered

        for layer in layers:
            kind_scope, tmpl = LAYERS[layer]
            targets = [(st, tmpl.format(y=y, st=st)) for st in self._states()] if kind_scope == "state" \
                else [("us", tmpl.format(y=y, st="us"))]
            frames = []
            for st, rel in targets:
                url = f"{BASE}/TIGER{y}/{rel}"
                dest = self._dir() / f"{layer}_{st}_{y}.zip"
                try:
                    if refresh or not dest.exists():
                        _status, sha, nbytes = stream_download(self.http, self.name, url, dest)
                        record_grid(self.ledger, self.run_id, self.name, url, None, dest,
                                    sha256=sha, nbytes=nbytes, variable=layer)
                        summ.n_requests += 1
                    else:
                        summ.n_cached += 1
                    g = gpd.read_file(dest)
                    if g.crs is not None and g.crs.to_epsg() != 4326:
                        g = g.to_crs(4326)
                    frames.append(g)
                except Exception as ex:
                    summ.n_errors += 1
                    summ.notes.append(f"{layer}/{st}: {str(ex)[:90]}")
                    log.warning("tiger %s/%s failed: %s", layer, st, str(ex)[:160])
            if not frames:
                continue
            g = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
            g = gpd.GeoDataFrame(g, geometry="geometry", crs=4326)
            # National layers are clipped to the scope box; state layers are already regional.
            if kind_scope == "national":
                g = g.cx[w:e, s:n]
            if layer == "place" and "LSAD" in g.columns:
                g["PLACE_KIND"] = g["LSAD"].map(LSAD_LABEL).fillna("other")
            gpkg = self._dir() / f"{layer}.gpkg"
            g.to_file(gpkg, driver="GPKG", layer=layer)
            attrs = pd.DataFrame(g.drop(columns=["geometry"]))
            self.store.write_table(attrs, "reference", self.name, layer)
            summ.n_rows += len(g)
            summ.notes.append(f"{layer}: {len(g)} features -> {gpkg.name}")
            log.info("tiger %s: %d features", layer, len(g))
        summ.notes.append(f"vintage TIGER{y}; {CITATION}")
        return summ
