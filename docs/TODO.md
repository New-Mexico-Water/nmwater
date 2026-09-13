# Follow-ups

Everything outstanding as of 2026-09-13, written so each item can become a GitHub issue with
little editing. Each says what is wrong or missing, what is already known, and what doing it
involves. Priority is my judgement of value against effort, not an instruction.

Status of the archive when this was written: 701 million observations, 218,166 sites, 15 GB,
39 of 40 sources exercised.

---

## A. Data quality problems found by `nmwater report`

These came out of the QA report and are the most concrete work available.

### A1. 2.19 million duplicate observation keys — HIGH
`nmwater compact` has not been run since the large backfills. Duplicates arise because each
fetch writes its own part files and overlapping windows are legal. Compaction merges partitions
and keeps the newest ingest run.
**Do:** run `nmwater compact` then `nmwater catalog build`, and confirm the count drops to zero.
Consider making `catalog build` refuse to run against an uncompacted store, or compact
automatically.

### A2. 106,920 negative snow water equivalent values — HIGH
Snow water equivalent cannot be negative. Almost certainly NRCS sensor spikes and reset
artifacts; the fork that wrote that module noted unfiltered spikes such as air temperature of
218 °C and hourly snow water equivalent of 1,424 inches, carrying quality flags.
**Do:** decide the policy. Either filter on the provider's QC flag during normalize, or keep the
values and mark them, which suits an archive better. Whichever, the rule belongs in the source
module and the caveat belongs in the crosswalk. Also 8 negative reservoir storage values remain.

### A3. 158,726 observation sites missing from the sites table — HIGH
Observations exist for site_uids that have no row in `sites`. Almost all are OSE points of
diversion, whose drilling-time water levels are written as observations while the site rows come
from a different code path.
**Do:** make `ose_arcgis` write its point-of-diversion sites into the sites table, or make
`catalog build` synthesise minimal site rows from observations so nothing is orphaned.

### A4. Landmark checks failing on identifier format — MEDIUM
Several landmark checks report "not loaded" because the expected site_uid does not match what
the source actually writes: the NRCS entries use `nrcs:486:NM:SNTL` while the module writes a
different form, and `usgs:08364000` (Rio Grande at El Paso) has no daily data despite being on
the allow-list. Albuquerque's first record is 1942-04-24 against a threshold of 1942-01-01.
**Do:** correct the identifiers in `nmwater/catalog/qa.py`, verify El Paso is actually being
fetched, and adjust the Albuquerque threshold to the true start of record.

### A5. 9,211 located sites with no watershed assigned — MEDIUM
Only region 13 was fully processed for HUC8-in-scope. Regions 11, 12, 14 and 15 were downloaded
but the sites falling in them did not all resolve.
**Do:** confirm all five WBD region GeoPackages are present and re-run `catalog build`;
investigate any sites still unresolved, which are likely outside the buffered box.

### A6. Water quality dissolved oxygen reaching 2,374 mg/L — MEDIUM
Physically impossible; almost certainly percent saturation mislabelled as concentration in one
Water Quality Portal chunk.
**Do:** add a range check per variable to the QA report, and decide whether to drop or flag.
A general "plausible range" column in `variables.yaml` would catch this class of error across all
sources.

### A7. Sites without coordinates — LOW
usdm=103, nclimdiv=79, nmwdi_st2=165 and others. For drought and climate-division areas this is
expected, since they are polygons rather than points, but they should carry a centroid so map
queries work.
**Do:** give area-type sites a representative centroid, or exclude them from the coordinate check.

---

## B. Incomplete data pulls

### B1. Water Quality Portal results — HIGH
8 of roughly 700 county-by-decade chunks fetched. This is the largest remaining gap in the
station backbone and the main source of ambient surface and groundwater chemistry, including
NMED's own monitoring.
**Do:** run `nmwater fetch wqp` to completion. Expect 10 to 40 million rows and 5 to 20 GB.

### B2. Colorado DWR needs two or three more daily runs — HIGH
CDSS enforces a daily data quota even with a registered key. 1,465 requests succeeded, 2,578
remain. Documented in `resume_needed.md`.
**Do:** re-run `nmwater fetch codwr` on successive days until the error count reaches zero.

### B3. Gridded products are at test-pull scale — HIGH
Five gridMET year-files, eight PRISM files, two SNODAS days, one nClimGrid month. The full pass
is roughly 25 to 30 GB and is what makes basin-scale water balance possible.
**Do:** run the phase 3 backfills. Note PRISM's two-downloads-per-file-per-day limit and that
SNODAS requires downloading full CONUS tars and clipping.

### B4. ZiaMet is failing under load — MEDIUM
7,150 of 20,544 requests done, 182 failures, 946 retry events; the NMSU server returns HTTP 500
under sustained load, and at the current rate finishing takes about 33 hours.
**Do:** decide whether it is worth it. ZiaMet's stations also appear in Synoptic and its
precipitation overlaps GHCN, so the marginal value is modest. If continuing, lower concurrency
and raise the interval for that source.

### B5. BEMP has only 10 of about 30 sites — MEDIUM
7,112 rows spanning 1997 to 2018 across 10 sites, but the Bosque Ecosystem Monitoring Program
runs roughly 30 riparian sites with five wells each. The fetch returned nothing new because the
archived responses were already ingested, so this is a discovery gap rather than a fetch failure.
**Do:** re-examine what the BEMP data page publishes and whether more workbooks exist. This is
the only shallow-aquifer record in the Rio Grande bosque.

### B6. USGS 15-minute archive beyond the last year — MEDIUM
Now defaults to one year by design. Decide which historical periods are worth pulling: flood
years, drought years, and periods being studied.
**Do:** `nmwater fetch usgs --kind continuous --since X --until Y` per period of interest.

### B7. Older USGS water-use compilations — LOW
The 1985 to 2005 county compilations were not found on ScienceBase by search. 2010, 2015 and the
modelled 2000-2020 series are in.
**Do:** locate the earlier releases, which may live under different ScienceBase identifiers.

---

## C. Access requests and credentials

Each of these needs a human to ask someone for something.

### C1. NMBGMR aquifer mapping API — HIGH
`waterdata.nmt.edu` does not respond from this network and the App Engine mirror returns 403
because its default location set is private. This is the state's own aquifer program and the
Healy collaborative well network.
**Do:** try from another network first, since it may be a firewall rather than a permission
problem. If it persists, request API access from nmbg-waterlevels@nmt.edu.

### C2. MRGCD and EBID telemetry — HIGH
Both irrigation districts publish gauge and diversion data only behind a OneRain login. MRGCD
diversions are partly recoverable through the Reclamation Albuquerque files, but EBID's network
is not. These are the two largest irrigation districts in the state and the largest single
diversions from the Rio Grande.
**Do:** request read credentials or a data-sharing agreement from each district.

### C3. Nine Cloudflare-blocked catalog files — MEDIUM
Listed in `manual_downloads.md`: the 2015 and 2020 water-use spreadsheets, the 2015 Access
database, and ABCWUA, Santa Fe and NMBGMR groundwater files. The New Mexico Water Data catalog
serves a browser challenge to scripted downloads.
**Do:** download them in a browser into `data/manual/<source>/` and re-run the fetch, which
ingests them.

### C4. EDI research data — MEDIUM
`pasta.lternet.edu` returns 403 for all public API methods from this network, blocking Sevilleta
meteorology from 1988, Jornada, and the Navajo Nation wells database.
**Do:** retry later, since this looks like a temporary access policy. The module needs no change.

### C5. USGS sub-daily data before 2007 — MEDIUM
The series catalogs show unit values back to 1987, but no public API serves them. Twenty years
of sub-daily record.
**Do:** ask the USGS New Mexico Water Science Center whether the Instantaneous Data Archive is
recoverable.

### C6. USACE reservoir records before 1993 — MEDIUM
Cochiti, Abiquiu, Conchas and Santa Rosa have operational histories going back decades further
than the CWMS record, apparently only in paper or PDF annual reports.
**Do:** ask the Albuquerque District water management office.

### C7. Tokens not yet set — MEDIUM
`SYNOPTIC_TOKEN` for RAWS fire-weather stations, `NASS_API_KEY` for irrigated acreage and applied
water, and NASA Earthdata credentials for UA snow water equivalent, SMAP, GRACE and MODIS.
**Do:** register and add to `.env`. All are free.

### C8. OpenET research tier — MEDIUM
The free tier allows 100 queries a month with a 50,000-acre cap, which cannot cover the state.
Evapotranspiration is the largest loss term in New Mexico's water budget.
**Do:** ask OpenET about a research arrangement, or compute the models in Earth Engine using the
`openet-*` packages.

### C9. Reclamation ET Toolbox and BIA diversion records — LOW
The Middle Rio Grande ET Toolbox has no documented API; NIIP and other tribal irrigation
diversion records are held by the Bureau of Indian Affairs with no public source.
**Do:** contact the Albuquerque Area Office and BIA respectively.

---

## D. Code and tooling

### D1. `fetch` runs its named sources sequentially — MEDIUM
`nmwater fetch a b c` processes one source at a time inside a single process, which made the
phase recipes much slower than necessary. Sources are independent providers, so they can run
concurrently; only same-source parallelism would violate rate limits, since the limiter is
per-process.
**Do:** add a worker pool across sources with a `--jobs` flag, defaulting to something modest.

### D2. `ose_arcgis` has no `normalize()` — MEDIUM
It cannot be reprocessed offline; `reprocess --replace` deleted its rows before a guard was
added. The guard now refuses, but the underlying gap remains.
**Do:** implement `normalize()` so its observations can be rebuilt from the archive like every
other source.

### D3. Publish the QA report and data dictionary — MEDIUM
`docs/qa/` is gitignored, so coverage evidence is not visible to anyone reading the repository.
**Do:** decide whether to commit a generated QA report, or add a workflow that regenerates and
publishes it.

### D4. NHDPlus reach linking — MEDIUM
`pynhd` is not installed, so stream-network COMIDs were never attached to sites. This is the join
key for the National Water Model and for any upstream and downstream reasoning.
**Do:** add `pynhd` and attach COMIDs to stream sites during catalog build.

### D5. Human review of site links — MEDIUM
1,331 proximity-based `colocated` links were generated automatically at a 250 m threshold, plus
829 exact-identifier matches. The automatic ones need eyes before any statewide total relies on
deduplication.
**Do:** review and promote confirmed pairs into `catalog/sites_manual.csv`.

### D6. Unverified provider semantics — LOW
Carried from the source modules: USACE quality-code meanings, IBWC stage datum, NRCS hourly
timestamps assumed to be Mountain Standard year-round, TWDB records with dates in the future
which appear to come from the source, and Colorado DWR telemetry storage and elevation mappings
never exercised on a reservoir.
**Do:** confirm each against provider documentation and record the answer in the crosswalk
caveats.

### D7. Plausible-range metadata for variables — LOW
Related to A2 and A6. A minimum and maximum per canonical variable would let the QA report catch
impossible values generically rather than through hand-written checks.
**Do:** add optional range fields to `variables.yaml` and a check to `qa.py`.

---

## E. Deferred by agreement

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

Larger directions implied by the original goal of publishing this for scientists and the public.

- **Water balance queries.** The pieces are present for a basin-scale balance: inflow at Otowi,
  diversions, returns, reservoir storage change, evapotranspiration, outflow at El Paso. Writing
  those as tested, documented queries would prove the archive does what it was built for.
- **PostgreSQL/TimescaleDB migration.** The schema was designed for it; nothing in the pipeline
  needs to change. Worth doing when a website needs a live backend.
- **Scheduled refresh.** Incremental pulls work; a timer would keep the archive current.
- **The website itself.** The stated goal, and the reason `interpretation.md` exists: a public
  presentation has to carry the caveats with the numbers.
