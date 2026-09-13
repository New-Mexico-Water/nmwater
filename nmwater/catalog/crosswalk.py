"""Source-native parameter -> canonical variable crosswalk (catalog/crosswalk.csv).

Columns: source, source_param, source_name, source_unit, variable, factor, offset,
statistic, interval, equivalence, caveat.
  value_canonical = value_source * factor + offset
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ..core.config import CATALOG_DIR
from .variables import VariableRegistry

log = logging.getLogger("nmwater.crosswalk")


@dataclass(frozen=True)
class XwEntry:
    source: str
    source_param: str
    source_name: str
    source_unit: str
    variable: str
    factor: float
    offset: float
    statistic: str | None
    interval: str | None
    equivalence: str
    caveat: str

    @property
    def unit(self) -> str:
        return _REG.unit_of(self.variable) if self.variable in _REG else ""


_REG: VariableRegistry | None = None


class Crosswalk:
    def __init__(self, path: Path | None = None, registry: VariableRegistry | None = None):
        global _REG
        self.path = path or CATALOG_DIR / "crosswalk.csv"
        self.registry = registry or VariableRegistry()
        _REG = self.registry
        self.entries: dict[tuple[str, str], XwEntry] = {}
        self._unmapped: set[tuple[str, str]] = set()
        files = [self.path] + sorted((self.path.parent / "crosswalk.d").glob("*.csv"))
        for fp in files:
            self._load_file(fp)

    def _load_file(self, fp: Path) -> None:
        with fp.open(newline="") as f:
            for row in csv.DictReader(f):
                if not row.get("source") or row["source"].startswith("#"):
                    continue
                e = XwEntry(
                    source=row["source"].strip(),
                    source_param=row["source_param"].strip(),
                    source_name=(row.get("source_name") or "").strip(),
                    source_unit=(row.get("source_unit") or "").strip(),
                    variable=row["variable"].strip(),
                    factor=float(row.get("factor") or 1.0),
                    offset=float(row.get("offset") or 0.0),
                    statistic=(row.get("statistic") or "").strip() or None,
                    interval=(row.get("interval") or "").strip() or None,
                    equivalence=(row.get("equivalence") or "identical").strip(),
                    caveat=(row.get("caveat") or "").strip(),
                )
                if e.variable and e.variable not in self.registry:
                    log.warning("crosswalk: %s/%s maps to unknown variable %s", e.source, e.source_param, e.variable)
                self.entries[(e.source, e.source_param)] = e

    def lookup(self, source: str, source_param: str) -> XwEntry | None:
        """Return the mapping, or None if absent or explicitly unmapped (empty variable)."""
        e = self.entries.get((source, str(source_param)))
        if e is not None and not e.variable:
            return None
        if e is None:
            key = (source, str(source_param))
            if key not in self._unmapped:
                self._unmapped.add(key)
                log.debug("crosswalk: no mapping for %s/%s", source, source_param)
        return e

    def unmapped(self) -> list[tuple[str, str]]:
        return sorted(self._unmapped)

    def apply(self, df: pd.DataFrame, source: str, param_col: str = "source_param",
              value_col: str = "value", keep_unmapped: bool = False) -> pd.DataFrame:
        """Vectorized mapping: fills variable/unit/statistic/interval and converts values.

        Rows whose source_param has no crosswalk entry are dropped unless keep_unmapped.
        Existing non-null statistic/interval columns in df take precedence over the crosswalk.
        """
        if df is None or len(df) == 0:
            return df
        params = df[param_col].astype(str)
        uniq = params.unique()
        maps = {p: self.lookup(source, p) for p in uniq}
        out = df.copy()
        out["variable"] = params.map(lambda p: maps[p].variable if maps[p] else None)
        out["unit"] = params.map(lambda p: maps[p].unit if maps[p] else None)
        factor = params.map(lambda p: maps[p].factor if maps[p] else 1.0).astype(float)
        offset = params.map(lambda p: maps[p].offset if maps[p] else 0.0).astype(float)
        out[value_col] = pd.to_numeric(out[value_col], errors="coerce") * factor.values + offset.values
        xs = params.map(lambda p: maps[p].statistic if maps[p] else None)
        xi = params.map(lambda p: maps[p].interval if maps[p] else None)
        if "statistic" in out.columns:
            out["statistic"] = out["statistic"].where(out["statistic"].notna(), xs)
        else:
            out["statistic"] = xs
        if "interval" in out.columns:
            out["interval"] = out["interval"].where(out["interval"].notna(), xi)
        else:
            out["interval"] = xi
        if "source_unit" not in out.columns:
            out["source_unit"] = params.map(lambda p: maps[p].source_unit if maps[p] else None)
        if not keep_unmapped:
            out = out[out["variable"].notna()]
        return out

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([e.__dict__ for e in self.entries.values()])
