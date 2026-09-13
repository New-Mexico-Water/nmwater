"""Settings, paths, scope, and per-source configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
CATALOG_DIR = PROJECT_ROOT / "catalog"
DOCS_DIR = PROJECT_ROOT / "docs"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


@dataclass
class Scope:
    """Geographic scope: bounding box, buffer, HUC prefixes, out-of-state allow-lists."""

    bbox: tuple[float, float, float, float]  # west, south, east, north
    buffer_deg: float
    state_fips: str
    state_abbr: str
    huc2: list[str]
    huc4: list[str]
    huc8: list[str]
    neighbor_states: list[str]
    allow_sites: dict[str, list[str]]  # source -> native ids always included
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def bbox_buffered(self) -> tuple[float, float, float, float]:
        w, s, e, n = self.bbox
        b = self.buffer_deg
        return (w - b, s - b, e + b, n + b)

    def in_bbox(self, lat: float | None, lon: float | None, buffered: bool = True) -> bool:
        if lat is None or lon is None:
            return False
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            return False
        w, s, e, n = self.bbox_buffered if buffered else self.bbox
        return s <= lat <= n and w <= lon <= e

    @classmethod
    def load(cls, path: Path | None = None) -> "Scope":
        d = _load_yaml(path or CONFIG_DIR / "scope.yaml")
        return cls(
            bbox=tuple(d["bbox"]),
            buffer_deg=float(d.get("buffer_deg", 0.0)),
            state_fips=str(d.get("state_fips", "35")),
            state_abbr=str(d.get("state_abbr", "NM")),
            huc2=[str(x) for x in d.get("huc2", [])],
            huc4=[str(x) for x in d.get("huc4", [])],
            huc8=[str(x) for x in d.get("huc8", [])],
            neighbor_states=[str(x) for x in d.get("neighbor_states", [])],
            allow_sites={k: [str(x) for x in v] for k, v in (d.get("allow_sites") or {}).items()},
            raw=d,
        )


@dataclass
class SourceConfig:
    name: str
    enabled: bool = True
    concurrency: int = 2
    min_interval_s: float = 0.5
    timeout_s: float = 120.0
    max_retries: int = 5
    token_env: list[str] = field(default_factory=list)
    base_url: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Settings:
    data_dir: Path
    contact: str
    tokens: dict[str, str | None]
    scope: Scope
    sources: dict[str, SourceConfig]
    defaults: SourceConfig

    @classmethod
    def load(cls, data_dir: Path | None = None) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env")
        dd = data_dir or Path(os.environ.get("NMWATER_DATA_DIR") or (PROJECT_ROOT / "data"))
        dd = dd.expanduser().resolve()
        src_cfg = _load_yaml(CONFIG_DIR / "sources.yaml")
        defaults_d = src_cfg.get("defaults", {})
        defaults = SourceConfig(name="_defaults", **{k: v for k, v in defaults_d.items() if k != "name"})
        sources: dict[str, SourceConfig] = {}
        for name, d in (src_cfg.get("sources") or {}).items():
            d = dict(d or {})
            merged = {
                "enabled": d.pop("enabled", True),
                "concurrency": d.pop("concurrency", defaults.concurrency),
                "min_interval_s": d.pop("min_interval_s", defaults.min_interval_s),
                "timeout_s": d.pop("timeout_s", defaults.timeout_s),
                "max_retries": d.pop("max_retries", defaults.max_retries),
                "token_env": d.pop("token_env", []),
                "base_url": d.pop("base_url", None),
            }
            sources[name] = SourceConfig(name=name, options=d, **merged)
        token_names = [
            "USGS_API_KEY",
            "NOAA_NCEI_TOKEN",
            "EARTHDATA_USERNAME",
            "EARTHDATA_PASSWORD",
            "OPENET_API_KEY",
            "SYNOPTIC_TOKEN",
            "NASS_API_KEY",
            "CODWR_API_KEY",
        ]
        tokens = {k: (os.environ.get(k) or None) for k in token_names}
        return cls(
            data_dir=dd,
            contact=os.environ.get("NMWATER_CONTACT", "nmwater-project"),
            tokens=tokens,
            scope=Scope.load(),
            sources=sources,
            defaults=defaults,
        )

    def source_config(self, name: str) -> SourceConfig:
        if name in self.sources:
            return self.sources[name]
        return SourceConfig(
            name=name,
            concurrency=self.defaults.concurrency,
            min_interval_s=self.defaults.min_interval_s,
            timeout_s=self.defaults.timeout_s,
            max_retries=self.defaults.max_retries,
        )

    # Paths
    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def parquet_dir(self) -> Path:
        return self.data_dir / "parquet"

    @property
    def grids_dir(self) -> Path:
        return self.data_dir / "grids"

    @property
    def duckdb_path(self) -> Path:
        return self.data_dir / "duckdb" / "nmwater.duckdb"

    @property
    def ledger_path(self) -> Path:
        return self.data_dir / "ledger.sqlite"

    def ensure_dirs(self) -> None:
        for p in (self.raw_dir, self.parquet_dir, self.grids_dir, self.duckdb_path.parent):
            p.mkdir(parents=True, exist_ok=True)
