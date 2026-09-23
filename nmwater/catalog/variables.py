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
    # Plausibility (see docs/data-model.md, "Quality flags"). Values below valid_min are impossible
    # unless they fall in [noise_floor, valid_min), the instrument-noise band, which is kept and
    # flagged near_zero. Values above valid_max are impossible. No bound means no check.
    valid_min: float | None = None
    valid_max: float | None = None
    noise_floor: float | None = None
    # Provider "no data" markers specific to this variable, added to the registry default.
    missing_codes: list[float] = field(default_factory=list)
    # True where negative values of any size are legitimate (net change, computed inflow): the
    # default missing-value codes are not applied, because a real value could equal one.
    signed: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class VariableRegistry:
    def __init__(self, path: Path | None = None):
        self.path = path or CATALOG_DIR / "variables.yaml"
        with self.path.open() as f:
            doc = yaml.safe_load(f) or {}
        self.units: dict[str, dict[str, Any]] = doc.get("units", {})
        self.missing_codes_default: tuple[float, ...] = tuple(float(x) for x in doc.get("missing_codes_default", []))
        self.vars: dict[str, Variable] = {}
        for name, d in (doc.get("variables") or {}).items():
            d = dict(d)
            known = {k: d.pop(k) for k in list(d) if k in Variable.__dataclass_fields__ and k != "extra"}
            self.vars[name] = Variable(name=name, extra=d, **known)

    def __contains__(self, name: str) -> bool:
        return name in self.vars

    def __getitem__(self, name: str) -> Variable:
        return self.vars[name]

    def missing_codes_for(self, name: str) -> tuple[float, ...]:
        """Values that mean "no data" for this variable; empty if unknown or signed."""
        v = self.vars.get(name)
        if v is None or v.signed:
            return ()
        return tuple(dict.fromkeys([*self.missing_codes_default, *(float(x) for x in v.missing_codes)]))

    def unit_of(self, name: str) -> str:
        return self.vars[name].unit

    def validate(self) -> list[str]:
        problems = []
        for v in self.vars.values():
            if v.unit not in self.units:
                problems.append(f"variable {v.name}: unit '{v.unit}' not declared in units")
            if v.noise_floor is not None and v.valid_min is None:
                problems.append(f"variable {v.name}: noise_floor needs valid_min")
            if v.noise_floor is not None and v.valid_min is not None and v.noise_floor > v.valid_min:
                problems.append(f"variable {v.name}: noise_floor {v.noise_floor} is above valid_min {v.valid_min}")
            if v.valid_min is not None and v.valid_max is not None and v.valid_min > v.valid_max:
                problems.append(f"variable {v.name}: valid_min is above valid_max")
        return problems

    def to_frame(self):
        import pandas as pd

        return pd.DataFrame(
            [
                {
                    "variable": v.name, "label": v.label, "unit": v.unit, "quantity": v.quantity,
                    "kind": v.kind, "medium": v.medium, "sign": v.sign, "definition": v.definition,
                    "comparability": v.comparability, "valid_min": v.valid_min, "noise_floor": v.noise_floor,
                    "valid_max": v.valid_max,
                }
                for v in self.vars.values()
            ]
        )
