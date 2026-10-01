"""Reviewed corrections to NHDPlus v2 river names (catalog/reach_name_fixes.csv).

NHDPlus leaves some main stems unnamed and misnames a few; a site on such a reach gets the wrong river
(or the next river downstream). Each row of the CSV is one reviewed rule with its evidence:
  rename             reaches with gnis_id=<id> (and drainage >= min_totdasqkm) get a new name and id;
  levelpath_unnamed  the unnamed reaches on the level path of site=<site_uid>'s reach (limited to the
                     listed HUC8s, separated by ';') get the new name and id.
Ids starting "fix:" are ours, for rivers NHDPlus has no GNIS id for.

resolve() turns the rules into one row per reach (comid, gnis_name, gnis_id, rule). The catalog build
applies it to the flowlines and site_reaches tables; map code reading flowlines.gpkg applies it with
apply() so maps and the catalog agree.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

import pandas as pd

from ..core.config import CATALOG_DIR

RULES = CATALOG_DIR / "reach_name_fixes.csv"


def rules(path: Path = RULES) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("rule") and not r["rule"].startswith("#")]


def resolve_frames(flow: pd.DataFrame, reaches: pd.DataFrame, rows: list[dict]) -> pd.DataFrame:
    """flow: flowline attributes (comid, gnis_id, gnis_name, totdasqkm, levelpathi, huc8);
    reaches: site_reaches (site_uid, comid). Returns comid, gnis_name, gnis_id, rule."""
    out = []
    gid = flow["gnis_id"].astype(str).str.strip()
    unnamed = flow["gnis_name"].isna() | flow["gnis_name"].astype(str).str.strip().isin(["", "nan", "None"])
    for i, r in enumerate(rows):
        key, _, val = r["match"].partition("=")
        if r["rule"] == "rename" and key == "gnis_id":
            m = gid == val.strip()
            if r.get("min_totdasqkm"):
                m &= flow["totdasqkm"] >= float(r["min_totdasqkm"])
        elif r["rule"] == "levelpath_unnamed" and key == "site":
            comid = reaches.loc[reaches["site_uid"] == val.strip(), "comid"]
            if comid.empty:
                continue
            lp = flow.loc[flow["comid"] == comid.iloc[0], "levelpathi"]
            if lp.empty:
                continue
            m = (flow["levelpathi"] == lp.iloc[0]) & unnamed
        else:
            raise ValueError(f"reach_name_fixes.csv row {i + 2}: unknown rule or match {r['rule']!r} {r['match']!r}")
        if r.get("huc8"):
            m &= flow["huc8"].astype(str).isin([h.strip() for h in r["huc8"].split(";")])
        out.append(pd.DataFrame({"comid": flow.loc[m, "comid"].astype("int64"), "gnis_name": r["new_name"],
                                 "gnis_id": r["new_gnis_id"], "rule": f"{r['rule']} {r['match']}"}))
    if not out:
        return pd.DataFrame(columns=["comid", "gnis_name", "gnis_id", "rule"])
    return pd.concat(out, ignore_index=True).drop_duplicates("comid", keep="last")


@lru_cache(maxsize=2)
def resolve(parquet_dir: str) -> pd.DataFrame:
    """The fixes, computed from the NHDPlus reference parquet files under parquet_dir."""
    ref = Path(parquet_dir) / "reference" / "source=nhdplus"
    fp, rp = ref / "flowline_attributes.parquet", ref / "site_reaches.parquet"
    if not fp.exists() or not rp.exists():
        return pd.DataFrame(columns=["comid", "gnis_name", "gnis_id", "rule"])
    flow = pd.read_parquet(fp, columns=["comid", "gnis_id", "gnis_name", "totdasqkm", "levelpathi", "huc8"])
    return resolve_frames(flow, pd.read_parquet(rp, columns=["site_uid", "comid"]), rules())


def apply(frame, parquet_dir: str | Path | None = None):
    """Rename reaches in a (Geo)DataFrame with comid, gnis_id and gnis_name columns."""
    if parquet_dir is None:
        from ..core.config import Settings

        parquet_dir = Settings.load().parquet_dir
    fx = resolve(str(parquet_dir))
    if frame is None or len(frame) == 0 or fx.empty:
        return frame
    m = fx.set_index("comid")
    hit = frame["comid"].astype("int64").isin(m.index)
    if hit.any():
        frame = frame.copy()
        c = frame.loc[hit, "comid"].astype("int64")
        frame.loc[hit, "gnis_name"] = c.map(m["gnis_name"]).values
        frame.loc[hit, "gnis_id"] = c.map(m["gnis_id"]).values
    return frame
