"""Canonical variable registry loaded from catalog/variables.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..core.config import CATALOG_DIR


@dataclass
class Variable:
    name: str
    label: str
    definition: str
    unit: str
    quantity: str
    kind: str  # flux | stock | state | index | concentration
    medium: str  # surface_water | groundwater | snow | atmosphere | soil | reservoir | use
    sign: str | None = None
    intervals: list[str] = field(default_factory=list)
    comparability: str | None = None
    aliases: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


class VariableRegistry:
    def __init__(self, path: Path | None = None):
        self.path = path or CATALOG_DIR / "variables.yaml"
        with self.path.open() as f:
            doc = yaml.safe_load(f) or {}
        self.units: dict[str, dict[str, Any]] = doc.get("units", {})
        self.vars: dict[str, Variable] = {}
        for name, d in (doc.get("variables") or {}).items():
            d = dict(d)
            known = {k: d.pop(k) for k in list(d) if k in Variable.__dataclass_fields__ and k != "extra"}
            self.vars[name] = Variable(name=name, extra=d, **known)

    def __contains__(self, name: str) -> bool:
        return name in self.vars

    def __getitem__(self, name: str) -> Variable:
        return self.vars[name]

    def unit_of(self, name: str) -> str:
        return self.vars[name].unit

    def validate(self) -> list[str]:
        problems = []
        for v in self.vars.values():
            if v.unit not in self.units:
                problems.append(f"variable {v.name}: unit '{v.unit}' not declared in units")
        return problems

    def to_frame(self):
        import pandas as pd

        return pd.DataFrame(
            [
                {
                    "variable": v.name, "label": v.label, "unit": v.unit, "quantity": v.quantity,
                    "kind": v.kind, "medium": v.medium, "sign": v.sign, "definition": v.definition,
                    "comparability": v.comparability,
                }
                for v in self.vars.values()
            ]
        )
