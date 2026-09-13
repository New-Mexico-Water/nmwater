"""Canonical table schemas (pyarrow). Identical in Parquet now and TimescaleDB later."""

from __future__ import annotations

import pyarrow as pa

OBS_SCHEMA = pa.schema(
    [
        pa.field("site_uid", pa.string(), nullable=False),
        pa.field("variable", pa.string(), nullable=False),
        pa.field("datetime_utc", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("utc_offset_min", pa.int16()),
        pa.field("value", pa.float64()),
        pa.field("unit", pa.string()),
        pa.field("interval", pa.string()),
        pa.field("statistic", pa.string()),
        pa.field("qualifier", pa.string()),
        pa.field("source_param", pa.string()),
        pa.field("source_unit", pa.string()),
        pa.field("ingest_run_id", pa.string()),
    ]
)

OBS_KEY = ["site_uid", "variable", "datetime_utc", "interval", "statistic"]

SITES_SCHEMA = pa.schema(
    [
        pa.field("site_uid", pa.string(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("native_id", pa.string(), nullable=False),
        pa.field("name", pa.string()),
        pa.field("lat", pa.float64()),
        pa.field("lon", pa.float64()),
        pa.field("elevation_m", pa.float64()),
        pa.field("site_type", pa.string()),
        pa.field("agency", pa.string()),
        pa.field("state", pa.string()),
        pa.field("county_fips", pa.string()),
        pa.field("huc8", pa.string()),
        pa.field("huc12", pa.string()),
        pa.field("basin", pa.string()),
        pa.field("well_depth_m", pa.float64()),
        pa.field("aquifer", pa.string()),
        pa.field("drainage_area_km2", pa.float64()),
        pa.field("active", pa.bool_()),
        pa.field("raw_metadata", pa.string()),  # JSON
    ]
)

SITE_TYPES = {
    "stream", "reservoir", "lake", "well", "spring", "snow", "met", "diversion", "canal",
    "return_flow", "wwtp", "outfall", "grid_cell", "area", "other",
}

INTERVALS = {"instant", "1min", "5min", "15min", "30min", "hourly", "daily", "weekly", "semimonthly",
             "monthly", "annual", "water_year", "irregular"}
STATISTICS = {"instantaneous", "mean", "max", "min", "total", "accumulated", "median", "observed",
              "sample"}


def site_uid(source: str, native_id: str) -> str:
    return f"{source}:{native_id}"
