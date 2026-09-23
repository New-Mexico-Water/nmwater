# Follow-ups

Everything outstanding as of 2026-09-13, written so each item can become a GitHub issue with
little editing. Each says what is wrong or missing, what is already known, and what doing it
involves. Priority is my judgement of value against effort, not an instruction.

**Tracking moved to GitHub on 2026-09-23.** Every open item below is now an issue in
[New-Mexico-Water/nmwater](https://github.com/New-Mexico-Water/nmwater/issues), labelled by priority
and type, and linked from its heading. Items marked DONE or FIXED have no issue, and the duplicate
observation keys (A1) were resolved by compaction. This file remains the background narrative; keep
the issues, not this file, up to date.

Status of the archive when this was first written (2026-09-13): 701 million observations, 218,166
sites, 15 GB, 39 of 40 sources exercised.

---

## A. Data quality problems found by `nmwater report`

These came out of the QA report and are the most concrete work available.

### A1. 2.19 million duplicate observation keys — DONE (0 remaining on 2026-09-23)
`nmwater compact` has not been run since the large backfills. Duplicates arise because each
fetch writes its own part files and overlapping windows are legal. Compaction merges partitions
and keeps the newest ingest run.
**Do:** run `nmwater compact` then `nmwater catalog build`, and confirm the count drops to zero.
Consider making `catalog build` refuse to run against an uncompacted store, or compact
automatically.

### A2. 106,920 negative snow water equivalent values — DONE (2026-09-24)
**Issue:** [#1](https://github.com/New-Mexico-Water/nmwater/issues/1)
Snow water equivalent cannot be negative. Almost certainly NRCS sensor spikes and reset
artifacts; the fork that wrote that module noted unfiltered spikes such as air temperature of
218 °C and hourly snow water equivalent of 1,424 inches, carrying quality flags.
**Do:** decide the policy. Either filter on the provider's QC flag during normalize, or keep the
values and mark them, which suits an archive better. Whichever, the rule belongs in the source
module and the caveat belongs in the crosswalk. Also 8 negative reservoir storage values remain.

### A3. 158,726 observation sites missing from the sites table — HIGH
**Issue:** [#2](https://github.com/New-Mexico-Water/nmwater/issues/2)
Observations exist for site_uids that have no row in `sites`. Almost all are OSE points of
diversion, whose drilling-time water levels are written as observations while the site rows come
from a different code path.
**Do:** make `ose_arcgis` write its point-of-diversion sites into the sites table, or make
`catalog build` synthesise minimal site rows from observations so nothing is orphaned.

### A4. Landmark checks failing on identifier format — MEDIUM
**Issue:** [#3](https://github.com/New-Mexico-Water/nmwater/issues/3)
Several landmark checks report "not loaded" because the expected site_uid does not match what
the source actually writes: the NRCS entries use `nrcs:486:NM:SNTL` while the module writes a
different form, and `usgs:08364000` (Rio Grande at El Paso) has no daily data despite being on
the allow-list. Albuquerque's first record is 1942-04-24 against a threshold of 1942-01-01.
**Do:** correct the identifiers in `nmwater/catalog/qa.py`, verify El Paso is actually being
fetched, and adjust the Albuquerque threshold to the true start of record.

### A5. 9,211 located sites with no watershed assigned — MEDIUM
**Issue:** [#4](https://github.com/New-Mexico-Water/nmwater/issues/4)
Only region 13 was fully processed for HUC8-in-scope. Regions 11, 12, 14 and 15 were downloaded
but the sites falling in them did not all resolve.
**Do:** confirm all five WBD region GeoPackages are present and re-run `catalog build`;
investigate any sites still unresolved, which are likely outside the buffered box.

### A6. Water quality dissolved oxygen reaching 2,374 mg/L — MEDIUM
**Issue:** [#5](https://github.com/New-Mexico-Water/nmwater/issues/5)
Physically impossible; almost certainly percent saturation mislabelled as concentration in one
Water Quality Portal chunk.
**Do:** add a range check per variable to the QA report, and decide whether to drop or flag.
A general "plausible range" column in `variables.yaml` would catch this class of error across all
sources.

### A7. Sites without coordinates — LOW
**Issue:** [#6](https://github.com/New-Mexico-Water/nmwater/issues/6)
usdm=103, nclimdiv=79, nmwdi_st2=165 and others. For drought and climate-division areas this is
expected, since they are polygons rather than points, but they should carry a centroid so map
queries work.
**Do:** give area-type sites a representative centroid, or exclude them from the coordinate check.

---

## B. Incomplete data pulls

### B1. Water Quality Portal results — DONE
Full backfill finished 2026-09-13; the 85 county-by-decade chunks that had been exhausted by the
Portal's rate limiting were retried successfully on 2026-09-14, adding 1,552,940 rows for zero
errors. Compacted and folded into the catalog.

### B2. Colorado DWR — DONE
CDSS enforces a daily data quota even with a registered key, so this needed successive daily
runs. The 2026-09-14 run completed 2,019 requests for 1,562,128 rows with 5 errors, all HTTP 404
on structures that have no daily diversion record. No quota rejections remain.

### B3. Gridded products are at test-pull scale — HIGH
**Issue:** [#7](https://github.com/New-Mexico-Water/nmwater/issues/7)
Five gridMET year-files, eight PRISM files, two SNODAS days, one nClimGrid month. The full pass
is roughly 25 to 30 GB and is what makes basin-scale water balance possible.
**Do:** run the phase 3 backfills. Note PRISM's two-downloads-per-file-per-day limit and that
SNODAS requires downloading full CONUS tars and clipping.

### B4. ZiaMet finished with a 1.5% error rate — LOW
**Issue:** [#40](https://github.com/New-Mexico-Water/nmwater/issues/40)
Completed: 1,718,843 rows from 20,446 requests, 312 errors (the NMSU server returned HTTP 500
under sustained load; it recovered faster than the ~33 hour estimate made mid-run).
**Do:** optionally re-run to pick up the failed requests; not urgent, since ZiaMet's stations
also appear in Synoptic and its precipitation overlaps GHCN.

### B5. BEMP has only 10 of about 30 sites — MEDIUM
**Issue:** [#8](https://github.com/New-Mexico-Water/nmwater/issues/8)
7,112 rows spanning 1997 to 2018 across 10 sites, but the Bosque Ecosystem Monitoring Program
runs roughly 30 riparian sites with five wells each. The fetch returned nothing new because the
archived responses were already ingested, so this is a discovery gap rather than a fetch failure.
**Do:** re-examine what the BEMP data page publishes and whether more workbooks exist. This is
the only shallow-aquifer record in the Rio Grande bosque.

### B6. USGS 15-minute archive beyond the last year — MEDIUM
**Issue:** [#9](https://github.com/New-Mexico-Water/nmwater/issues/9)
Now defaults to one year by design. Decide which historical periods are worth pulling: flood
years, drought years, and periods being studied.
**Do:** `nmwater fetch usgs --kind continuous --since X --until Y` per period of interest.

### B7. Older USGS water-use compilations — LOW
**Issue:** [#10](https://github.com/New-Mexico-Water/nmwater/issues/10)
The 1985 to 2005 county compilations were not found on ScienceBase by search. 2010, 2015 and the
modelled 2000-2020 series are in.
**Do:** locate the earlier releases, which may live under different ScienceBase identifiers.

---

## C. Access requests and credentials

Each of these needs a human to ask someone for something.

### C1. NMBGMR aquifer mapping API — HIGH
**Issue:** [#11](https://github.com/New-Mexico-Water/nmwater/issues/11)
`waterdata.nmt.edu` does not respond from this network and the App Engine mirror returns 403
because its default location set is private. This is the state's own aquifer program and the
Healy collaborative well network.
**Do:** try from another network first, since it may be a firewall rather than a permission
problem. If it persists, request API access from nmbg-waterlevels@nmt.edu.

### C2. MRGCD and EBID telemetry — HIGH
**Issue:** [#12](https://github.com/New-Mexico-Water/nmwater/issues/12)
Both irrigation districts publish gauge and diversion data only behind a OneRain login. MRGCD
diversions are partly recoverable through the Reclamation Albuquerque files, but EBID's network
is not. These are the two largest irrigation districts in the state and the largest single
diversions from the Rio Grande.
**Do:** request read credentials or a data-sharing agreement from each district.

### C3. Nine Cloudflare-blocked catalog files — MEDIUM
**Issue:** [#13](https://github.com/New-Mexico-Water/nmwater/issues/13)
Listed in `manual_downloads.md`: the 2015 and 2020 water-use spreadsheets, the 2015 Access
database, and ABCWUA, Santa Fe and NMBGMR groundwater files. The New Mexico Water Data catalog
serves a browser challenge to scripted downloads.
**Do:** download them in a browser into `data/manual/<source>/` and re-run the fetch, which
ingests them.

### C4. EDI research data — MEDIUM
**Issue:** [#14](https://github.com/New-Mexico-Water/nmwater/issues/14)
`pasta.lternet.edu` returns 403 for all public API methods from this network, blocking Sevilleta
meteorology from 1988, Jornada, and the Navajo Nation wells database.
**Do:** retry later, since this looks like a temporary access policy. The module needs no change.

### C5. USGS sub-daily data before 2007 — MEDIUM
**Issue:** [#15](https://github.com/New-Mexico-Water/nmwater/issues/15)
The series catalogs show unit values back to 1987, but no public API serves them. Twenty years
of sub-daily record.
**Do:** ask the USGS New Mexico Water Science Center whether the Instantaneous Data Archive is
recoverable.

### C6. USACE reservoir records before 1993 — MEDIUM
**Issue:** [#16](https://github.com/New-Mexico-Water/nmwater/issues/16)
Cochiti, Abiquiu, Conchas and Santa Rosa have operational histories going back decades further
than the CWMS record, apparently only in paper or PDF annual reports.
**Do:** ask the Albuquerque District water management office.

### C7. Tokens not yet set — MEDIUM
**Issue:** [#17](https://github.com/New-Mexico-Water/nmwater/issues/17)
`SYNOPTIC_TOKEN` for RAWS fire-weather stations, `NASS_API_KEY` for irrigated acreage and applied
water, and NASA Earthdata credentials for UA snow water equivalent, SMAP, GRACE and MODIS.
**Do:** register and add to `.env`. All are free.

### C8. OpenET research tier — MEDIUM
**Issue:** [#18](https://github.com/New-Mexico-Water/nmwater/issues/18)
The free tier allows 100 queries a month with a 50,000-acre cap, which cannot cover the state.
Evapotranspiration is the largest loss term in New Mexico's water budget.
**Do:** ask OpenET about a research arrangement, or compute the models in Earth Engine using the
`openet-*` packages.

### C9. Reclamation ET Toolbox and BIA diversion records — LOW
**Issue:** [#19](https://github.com/New-Mexico-Water/nmwater/issues/19)
The Middle Rio Grande ET Toolbox has no documented API; NIIP and other tribal irrigation
diversion records are held by the Bureau of Indian Affairs with no public source.
**Do:** contact the Albuquerque Area Office and BIA respectively.

---

## D. Code and tooling

### D1. `fetch` runs its named sources sequentially — MEDIUM
**Issue:** [#20](https://github.com/New-Mexico-Water/nmwater/issues/20)
`nmwater fetch a b c` processes one source at a time inside a single process, which made the
phase recipes much slower than necessary. Sources are independent providers, so they can run
concurrently; only same-source parallelism would violate rate limits, since the limiter is
per-process.
**Do:** add a worker pool across sources with a `--jobs` flag, defaulting to something modest.

### D2. `ose_arcgis` has no `normalize()` — MEDIUM
**Issue:** [#21](https://github.com/New-Mexico-Water/nmwater/issues/21)
It cannot be reprocessed offline; `reprocess --replace` deleted its rows before a guard was
added. The guard now refuses, but the underlying gap remains.
**Do:** implement `normalize()` so its observations can be rebuilt from the archive like every
other source.

### D3. Publish the QA report and data dictionary — MEDIUM
**Issue:** [#22](https://github.com/New-Mexico-Water/nmwater/issues/22)
`docs/qa/` is gitignored, so coverage evidence is not visible to anyone reading the repository.
**Do:** decide whether to commit a generated QA report, or add a workflow that regenerates and
publishes it.

### D4. NHDPlus reach linking — DONE (2026-09-13)
**Issue:** [#23](https://github.com/New-Mexico-Water/nmwater/issues/23)
`pynhd` is installed and `nmwater/sources/nhdplus.py` fetches NHDPlus v2 flowlines per HUC8 and
snaps stream, canal, diversion and return-flow sites onto the nearest reach within 500 m.
`site_reaches` and `flowlines` are first-class tables in the catalog. Two follow-ups remain:
confirm NWM v3.0's hydrofabric COMIDs match v2 before joining (the fork that surveyed the
gridded sources flagged this as unverified), and spot-check snaps with a large `snap_distance_m`
near confluences, since nearest-neighbour matching can pick the wrong tributary there.

### D5. Human review of site links — MEDIUM
**Issue:** [#24](https://github.com/New-Mexico-Water/nmwater/issues/24)
1,331 proximity-based `colocated` links were generated automatically at a 250 m threshold, plus
829 exact-identifier matches. The automatic ones need eyes before any statewide total relies on
deduplication.
**Do:** review and promote confirmed pairs into `catalog/sites_manual.csv`.

### D6. Unverified provider semantics — LOW
**Issue:** [#25](https://github.com/New-Mexico-Water/nmwater/issues/25)
Carried from the source modules: USACE quality-code meanings, IBWC stage datum, NRCS hourly
timestamps assumed to be Mountain Standard year-round, TWDB records with dates in the future
which appear to come from the source, and Colorado DWR telemetry storage and elevation mappings
never exercised on a reservoir.
**Do:** confirm each against provider documentation and record the answer in the crosswalk
caveats.

### D6b. Reclamation sedimentation surveys — DONE, extensible
**Issues:** [#27](https://github.com/New-Mexico-Water/nmwater/issues/27), [#28](https://github.com/New-Mexico-Water/nmwater/issues/28)
`nmwater fetch usbr_rise --kind acap` now downloads and parses Reclamation's area-capacity (ACAP)
tables into `reservoir_acap`: 410 elevation rows across 7 New Mexico reservoirs (Elephant Butte
2017/2019, Brantley 2013, Lake Sumner 2013, El Vado 2007, Heron 2010, Avalon 2023, Nambe Falls
2013), each row carrying survey year, capacity, surface area, Reclamation's nonlinear
interpolation coefficients, and the vertical datum note.

Validated: interpolating the Elephant Butte table at each day's observed elevation reproduces
Reclamation's published surface area exactly and published storage to within 0.08%, so the
operational storage series is confirmed to use this table.

Built on this: `scripts/elephant_butte_fill.py` recovers every capacity vintage back to 1915
from the operational record and writes `reports/elephant_butte_annual_fill.csv`. Method and
caveats in [reports/elephant-butte-fill.md](reports/elephant-butte-fill.md).

Superseded for capacity by D6c: reports now use the operators' current tables from CWMS, with
these survey tables kept as corroboration.

**Still open, lower priority:**
- Parsing the pre-2007 survey PDFs (1957, 1969, 1980, 1988, 1999, 2007) would replace derived
  capacities with published ones. Ute 1992 and the pre-2007 Elephant Butte surveys exist only as
  PDF reports.
- `reservoir_acap` joins to observations by reservoir name, not by comid.

### D6c. Reservoir reports for 24 reservoirs — DONE, with open items
**Issues:** [#29](https://github.com/New-Mexico-Water/nmwater/issues/29), [#30](https://github.com/New-Mexico-Water/nmwater/issues/30), [#31](https://github.com/New-Mexico-Water/nmwater/issues/31), [#32](https://github.com/New-Mexico-Water/nmwater/issues/32), [#33](https://github.com/New-Mexico-Water/nmwater/issues/33), [#34](https://github.com/New-Mexico-Water/nmwater/issues/34), [#35](https://github.com/New-Mexico-Water/nmwater/issues/35), [#36](https://github.com/New-Mexico-Water/nmwater/issues/36), [#37](https://github.com/New-Mexico-Water/nmwater/issues/37), [#38](https://github.com/New-Mexico-Water/nmwater/issues/38)
`nmwater fetch usace_cwms --kind ratings --kind levels` brings in the operators' current
elevation-to-storage tables (49 reservoirs) and named pool levels (18 locations).
`catalog/reservoirs.yaml` registers 24 New Mexico reservoirs with their sites, capacity source,
pools, release gauges and hand-written usage guidance; `scripts/reservoir_fill.py` builds annual
storage, fill, flood-pool use, inflow and release for each, plus one generated notes file per
reservoir. Index at [reports/reservoirs/index.md](reports/reservoirs/index.md), method at
[reports/reservoir-fill.md](reports/reservoir-fill.md).

Resolved along the way: Heron is now covered (the CWMS table is complete where the RISE survey
table stops at 7,102 ft); El Vado's 1.45 ft discrepancy disappears against its 2021 table.

**Open:**
- **Lake Sumner before June 2012.** The conservation pool was lowered in 2012 and the earlier pool
  is not in CWMS. Find the pre-2012 top of conservation (Reclamation or the Corps) so earlier years
  are not measured against the reduced pool.
- **Eagle Nest since 2013.** The lake has not exceeded about 62% of full pool since elevation
  reporting began. Confirm with the NM Department of Game and Fish whether an operating
  restriction applies.
- **El Vado 1979-2009.** Negative departures from the current table suggest an elevation datum
  change in the older record. Confirm with Reclamation.
- **Jemez Canyon permanent pool.** Find when the Corps stopped keeping a sediment pool, so early
  flood-storage counts can be separated from ordinary storage.
- **Cochiti and Abiquiu spring deviations.** Obtain the Corps' list of deviation years for spring
  pulse releases, so they can be labelled in the reports rather than inferred.
- **Six reservoirs without a validated table** (Ute, Costilla, McClure, Nichols, Bluewater,
  Maloya): an elevation series would move Ute and Costilla, which have CWMS tables, to the
  validated tier. The others need a capacity table from their owners.
- **Conchas release.** Releases go mostly to the Conchas Canal; find a current canal record.
- **Full-pool elevations detected from the record** for Heron, El Vado, Nambe Falls, Elephant
  Butte, Caballo and Avalon should be replaced with published pool definitions and citations.
- **Water years.** Add an October-September option; flow volumes are conventionally by water year.
- **Colorado reservoirs** that shape New Mexico inflow (Platoro, Rio Grande Reservoir, Vallecito,
  Lemon) have CWMS tables and could be added to the registry.

### D8. Findings from the first `nmwater update` (2026-09-23) — HIGH to LOW
**Issues:** [#39](https://github.com/New-Mexico-Water/nmwater/issues/39), [#40](https://github.com/New-Mexico-Water/nmwater/issues/40), [#41](https://github.com/New-Mexico-Water/nmwater/issues/41), [#42](https://github.com/New-Mexico-Water/nmwater/issues/42), [#43](https://github.com/New-Mexico-Water/nmwater/issues/43)
The first incremental update surfaced these; costs are in `reports/update_log.csv`.
- **NOAA ISD-Lite has stopped (HIGH).** There is no 2026 directory and the 2025 files were last
  modified 2025-08-29, so the archive's hourly airport and AWOS record ends there. NOAA's successor
  is the hourly Global Historical Climatology Network (GHCNh). `noaa_isd` is now skipped by
  `update`. **Do:** add a `noaa_ghcnh` source.
- **ZiaMet hosts are unreliable (MEDIUM).** duststorm.nmsu.edu and ziamet.org refused connections;
  weather.nmsu.edu answered slowly and returned HTTP 500 for most one-minute feeds, so the update
  spent 2 h 11 min mostly retrying and was stopped by hand. weather.nmsu.edu is now the primary
  host and updates cover daily data only. **Do:** confirm the next update completes, and cap
  per-request retries for this source.
- **Seven Rivers has no date filter (LOW).** Its API returns full history per point and analyte, so
  every update re-requests all 2,701 chemistry series. **Do:** skip chemistry in routine updates, or
  update it monthly.
- **PRISM's revision window is the largest download (LOW).** About 190 days of daily grids are
  re-pulled each update, roughly 2.5 GB, by design. **Do:** consider a shorter `revision_days` for
  routine updates and a full window monthly.
- **USGS annual peaks were never in the archive (FIXED).** The peaks collection rejects any
  datetime filter ("datetime query not supported"), and all 175 peaks requests in the original
  backfill had failed unnoticed. Peaks are now pulled whole, one request of about 37,000 rows:
  25,385 discharge peaks at 710 sites and 23,941 stage peaks, water years 1884-2025.
- **Colorado counted empty answers as errors (FIXED).** CDSS returns HTTP 404 "zero records" for a
  station with nothing new; 1,716 of 1,734 errors were that. The module now treats them as empty.
- **Reclamation HydroData removed 32 series (LOW).** Those site-datatype files now return 404.
  **Do:** confirm with the metadata file and retire them from discovery.

### D9. Incremental-update defects found auditing every source (2026-09-23) — HIGH to LOW
**Issues:** [#44](https://github.com/New-Mexico-Water/nmwater/issues/44), [#45](https://github.com/New-Mexico-Water/nmwater/issues/45), [#46](https://github.com/New-Mexico-Water/nmwater/issues/46), [#47](https://github.com/New-Mexico-Water/nmwater/issues/47), [#48](https://github.com/New-Mexico-Water/nmwater/issues/48), [#49](https://github.com/New-Mexico-Water/nmwater/issues/49), [#50](https://github.com/New-Mexico-Water/nmwater/issues/50), [#51](https://github.com/New-Mexico-Water/nmwater/issues/51)
Delivery mechanics for every source are in [sources.md](sources.md#how-each-source-delivers-data).
- **USGS 15-minute data stops at the last discovery (FIXED 2026-09-23).** Windows ended at each
  series' end date as recorded by `discover`, which for an active gauge is just the day discovery
  ran, so updates froze at 2026-09-12. Series whose recorded end is within 30 days
  (`continuous_active_days`) of the catalog's newest end date are now treated as active and fetched
  to today. After the fix, 268 of 285 15-minute gauges are current; the other 17 had stopped
  reporting before the catalog was taken.
- **The 30-day margin was discarded in four modules (FIXED 2026-09-23).** iem_dcp, nrcs,
  usace_cwms and USGS 15-minute data skipped ahead to the last fetched window whenever a start
  date was given, so the margin never re-pulled anything. `update` now passes `revise=True` and
  those modules honour the start date exactly; a manual `nmwater fetch --since` keeps its
  catch-up behaviour.
- **Corps updates stopped at the catalog's stale end date (FIXED 2026-09-23).** Windows ended at
  the CWMS catalog's `latest-time` + 1 day, and CWMS updates extents infrequently (Cochiti's
  15-minute storage: catalog 2026-09-14, data through 2026-09-23). Series whose latest-time is
  within 30 days (`active_days`) of the catalog's newest are now fetched to today. After the fix,
  all 147 Corps locations reporting since August are current.
- **Reference tables accumulate duplicates (MEDIUM, confirmed).** Tables written with
  `append_table` get a new part file every run and compaction does not cover them: USGS peaks and
  field measurements, WQP results, NMED drinking-water results, Seven Rivers readings, TWDB
  quality. USGS peaks already has 44 part files from one fetch. **Do:** deduplicate these in
  `compact`, or replace instead of append for whole-table pulls such as peaks.
- **ose_arcgis update fetches nothing (MEDIUM, confirmed).** Points of diversion refresh only on
  `discover`. **Do:** make update re-run the layer pull, or give it a discover step.
- **GHCN-D stations will drop out in January (MEDIUM).** Stations are filtered on the inventory's
  last year from discovery (`noaa_ghcnd.py:100-107`); when since's year becomes 2027, stations
  recorded as ending 2026 are silently skipped. **Do:** refresh the inventory in update.
- **NRCS forecasts re-pull full history every run (LOW).** `nrcs.py:163`.
- **Rolling-window sources lose data if updates lapse (LOW).** usbr_albuq keeps about 7 days, nwps
  about 30. **Do:** schedule updates at least weekly, or at least note the gap.
- **SensorThings and WQP miss late-loaded history (LOW).** Filtering on observation time skips
  records loaded late with old timestamps.
- **USGS field-measurements can truncate silently (LOW).** Full pages split only three levels deep
  and are then returned without warning (`usgs.py:367`).
- **PRISM can exceed its two-downloads-a-day limit (LOW)** if update runs twice in a day.

### D7. Plausible-range metadata for variables — LOW
**Issue:** [#26](https://github.com/New-Mexico-Water/nmwater/issues/26)
Related to A2 and A6. A minimum and maximum per canonical variable would let the QA report catch
impossible values generically rather than through hand-written checks.
**Do:** add optional range fields to `variables.yaml` and a check to `qa.py`.

---

## E. Deferred by agreement
**Issues:** [#52](https://github.com/New-Mexico-Water/nmwater/issues/52), [#53](https://github.com/New-Mexico-Water/nmwater/issues/53), [#54](https://github.com/New-Mexico-Water/nmwater/issues/54), [#55](https://github.com/New-Mexico-Water/nmwater/issues/55), [#56](https://github.com/New-Mexico-Water/nmwater/issues/56)

Not problems; recorded so the decision is not forgotten. All were verified as reachable and
excluded on volume grounds for the first pass.

- National Water Model retrospective, roughly 0.5 to 1 TB for New Mexico reaches. Needs D4 first.
- AORC hourly 1 km forcing, roughly 6 TB.
- MRMS radar precipitation, 50 to 200 GB for the useful hourly products.
- Full Daymet at 1 km, and MODIS daily snow and evapotranspiration tiles.
- NEXRAD Level II, excluded outright at over 100 TB, since Stage IV and MRMS already provide the
  gauge-corrected product.

---

## F. Beyond the archive
**Issues:** [#57](https://github.com/New-Mexico-Water/nmwater/issues/57), [#58](https://github.com/New-Mexico-Water/nmwater/issues/58), [#59](https://github.com/New-Mexico-Water/nmwater/issues/59), [#60](https://github.com/New-Mexico-Water/nmwater/issues/60)

Larger directions implied by the original goal of publishing this for scientists and the public.

- **Water balance queries.** The pieces are present for a basin-scale balance: inflow at Otowi,
  diversions, returns, reservoir storage change, evapotranspiration, outflow at El Paso. Writing
  those as tested, documented queries would prove the archive does what it was built for.
- **PostgreSQL/TimescaleDB migration.** The schema was designed for it; nothing in the pipeline
  needs to change. Worth doing when a website needs a live backend.
- **Scheduled refresh.** Incremental pulls work; a timer would keep the archive current.
- **The website itself.** The stated goal, and the reason `interpretation.md` exists: a public
  presentation has to carry the caveats with the numbers.
