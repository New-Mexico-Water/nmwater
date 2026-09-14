# just: task runner for the nmwater archive. `just --list` shows targets.

set shell := ["bash", "-cu"]

default:
    @just --list

# Install/refresh the environment
sync:
    uv sync --all-extras

# Validate catalog files
check:
    uv run nmwater catalog check

# Discover sites for one source (or all)
discover src="all":
    uv run nmwater discover {{src}}

# Backfill one source (or all)
fetch src="all":
    uv run nmwater fetch {{src}}

# Incremental update since a date for one source (or all)
update since src="all":
    uv run nmwater fetch {{src}} --since {{since}}

# Phase 1: federal station backbone
phase1:
    uv run nmwater discover usgs usbr_hydrodata usace_cwms nrcs noaa_ghcnd noaa_isd uscrn cocorahs iem_dcp nwps wqp usbr_rise usbr_albuq
    uv run nmwater fetch usgs usbr_hydrodata usace_cwms nrcs noaa_ghcnd noaa_isd uscrn cocorahs iem_dcp nwps wqp usbr_rise usbr_albuq

# Phase 2: New Mexico state, regional, and neighbor sources
phase2:
    uv run nmwater discover nmwdi_st2 nmed_st2 nmbgmr ose_arcgis ose_meas isc_sevenrivers ose_reports ckan codwr ibwc twdb ziamet edi bemp synoptic
    uv run nmwater fetch nmwdi_st2 nmed_st2 nmbgmr ose_arcgis ose_meas isc_sevenrivers ose_reports ckan codwr ibwc twdb ziamet edi bemp synoptic

# Phase 3: gridded, drought, water use, reference layers
phase3:
    uv run nmwater discover usdm nclimdiv wbd resopsus usgs_wateruse nass
    uv run nmwater fetch usdm nclimdiv wbd resopsus usgs_wateruse nass gridmet prism nclimgrid ua_swe snodas

# Compact parquet partitions and rebuild the DuckDB catalog
catalog:
    uv run nmwater compact
    uv run nmwater catalog build

# Ledger summary
status:
    uv run nmwater status

# Tests (offline unit tests only)
test:
    uv run pytest -q -m "not live"

# Live smoke tests against real endpoints (small requests)
test-live:
    uv run pytest -q -m live

# Derived report: Elephant Butte annual fill vs the capacity table in force
report-elephant-butte:
    uv run python scripts/elephant_butte_fill.py
