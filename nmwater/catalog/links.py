"""Cross-source site identity: build the site_links table.

Two mechanisms:
  1. exact ids  - any site whose raw_metadata carries a USGS site number (keys containing 'usgs',
                  or native ids that are USGS numbers for sources that key on them) links to the
                  USGS site: link_type 'same_sensor' when the provider mirrors the USGS record,
                  'same_location_different_sensor' otherwise.
  2. proximity  - sites of the same kind from different sources within `radius_m` link as
                  'colocated' (candidates for human review in catalog/sites_manual.csv).
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path

import pandas as pd

log = logging.getLogger("nmwater.links")

USGS_ID_RE = re.compile(r"^(?:USGS-)?(\d{8,15})$")
# sources whose native_id IS a USGS site number
NATIVE_IS_USGS = {"ibwc", "nwps_usgs"}
# providers that republish the USGS record for the linked id
MIRRORS_USGS = {"usbr_hydrodata", "nrcs", "usace_cwms", "codwr", "iem_dcp", "nwps"}

KIND_GROUPS = {
    "stream": "flow", "canal": "flow", "diversion": "flow", "return_flow": "flow",
    "reservoir": "reservoir", "lake": "reservoir",
    "well": "well", "spring": "spring",
    "snow": "met", "met": "met",
}


def _usgs_ids_from_metadata(meta: str | None) -> set[str]:
    out: set[str] = set()
    if not meta:
        return out
    try:
        d = json.loads(meta)
    except Exception:
        return out

    def walk(o, key=""):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
        elif isinstance(o, (str, int)) and "usgs" in key.lower():
            m = USGS_ID_RE.match(str(o).strip())
            if m:
                out.add(m.group(1))

    walk(d)
    return out


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def build_links(sites: pd.DataFrame, radius_m: float = 250.0, manual: Path | None = None) -> pd.DataFrame:
    rows: list[dict] = []
    usgs_ids = set(sites.loc[sites["source"] == "usgs", "native_id"].astype(str))

    # 1. exact USGS ids
    for r in sites.itertuples(index=False):
        if r.source == "usgs":
            continue
        ids = _usgs_ids_from_metadata(getattr(r, "raw_metadata", None))
        if r.source in NATIVE_IS_USGS:
            m = USGS_ID_RE.match(str(r.native_id))
            if m:
                ids.add(m.group(1))
        for uid in ids:
            if uid in usgs_ids:
                rows.append(
                    {
                        "site_uid_a": r.site_uid,
                        "site_uid_b": f"usgs:{uid}",
                        "link_type": "same_sensor" if r.source in MIRRORS_USGS else "same_location_different_sensor",
                        "evidence": "usgs id in provider metadata",
                        "distance_m": None,
                        "confidence": 0.95,
                    }
                )

    # 2. proximity within the same kind group, different sources
    pts = sites[sites["lat"].notna() & sites["lon"].notna()].copy()
    pts["grp"] = pts["site_type"].map(KIND_GROUPS)
    pts = pts[pts["grp"].notna()]
    # grid buckets of ~0.01 deg (~1.1 km) to limit comparisons
    pts["gx"] = (pts["lon"] * 100).round().astype(int)
    pts["gy"] = (pts["lat"] * 100).round().astype(int)
    buckets: dict[tuple[str, int, int], list] = {}
    for r in pts.itertuples(index=False):
        buckets.setdefault((r.grp, r.gx, r.gy), []).append(r)
    seen = set()
    for (grp, gx, gy), items in buckets.items():
        neigh = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neigh.extend(buckets.get((grp, gx + dx, gy + dy), []))
        for a in items:
            for b in neigh:
                if a.site_uid >= b.site_uid or a.source == b.source:
                    continue
                key = (a.site_uid, b.site_uid)
                if key in seen:
                    continue
                d = _haversine_m(a.lat, a.lon, b.lat, b.lon)
                if d <= radius_m:
                    seen.add(key)
                    rows.append(
                        {
                            "site_uid_a": a.site_uid,
                            "site_uid_b": b.site_uid,
                            "link_type": "colocated",
                            "evidence": f"same kind ({grp}) within {radius_m:.0f} m",
                            "distance_m": round(d, 1),
                            "confidence": round(max(0.3, 0.9 - d / radius_m * 0.5), 2),
                        }
                    )

    links = pd.DataFrame(rows, columns=["site_uid_a", "site_uid_b", "link_type", "evidence", "distance_m", "confidence"])

    # 3. manual overrides / additions
    if manual and manual.exists():
        try:
            man = pd.read_csv(manual, dtype=str)
            if {"site_uid_a", "site_uid_b", "link_type"}.issubset(man.columns):
                man["evidence"] = man.get("evidence", "manual")
                man["confidence"] = 1.0
                man["distance_m"] = None
                links = pd.concat([links, man[links.columns]], ignore_index=True)
        except Exception as e:
            log.warning("could not read %s: %s", manual, e)
    links = links.drop_duplicates(subset=["site_uid_a", "site_uid_b", "link_type"])
    log.info("site_links: %d exact, %d colocated", (links["link_type"] != "colocated").sum(), (links["link_type"] == "colocated").sum())
    return links
