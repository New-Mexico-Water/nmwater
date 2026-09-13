# Sources

The thirty-nine providers `nmwater` knows how to pull from, grouped by the role each plays in a
water balance.

The "reference run" column records what a full pull produced on the developer's machine on
2026-09-13, with several backfills still in flight. It is there so you can tell whether your own
run got what it should have, and it is a floor rather than a total. No data ship with this
repository; these are the results of running the tools.

Per-source licence, citation, refresh cadence, and caveats live in `catalog/sources.yaml` and
`catalog/sources.d/`, and are exported to `data_dictionary.md` at catalog build.

## Federal station networks

| Source | What it carries | Period | Reference run |
|---|---|---|---|
| `usgs` | Streamflow, stage, reservoir storage, groundwater levels, water quality, peaks, field measurements; 42,000 New Mexico sites | 1889– | 15.0M rows, 36,380 sites |
| `usbr_hydrodata` | Reclamation reservoir operations and gauge flows: storage, elevation, release, inflow, evaporation | 1899– | 5.3M rows, 130 sites |
| `usbr_rise` | Reclamation's newer catalog: sedimentation surveys, evaporation studies, desalination research wells | varies | small, supplements HydroData |
| `usbr_albuq` | Albuquerque Area Office daily operations files, including the only public route to Middle Rio Grande Conservancy District diversions | rolling | 35K rows, 25 sites |
| `usace_cwms` | Corps reservoirs (Abiquiu, Cochiti, Conchas, Santa Rosa and more) plus 281 MRGCD diversion gauges | 1993– | 73.2M rows |
| `nrcs` | SNOTEL snow water equivalent, snow courses, soil moisture, water-supply forecasts | 1971– | 737K rows |
| `noaa_ghcnd` | Daily precipitation, snowfall, snow depth, temperature from every cooperative station | 1850– | 61.8M rows, 2,989 stations |
| `noaa_isd` | Hourly surface weather from airport and automated stations | 1941– | 64.9M rows, 175 stations |
| `uscrn` | Climate Reference Network: the best-instrumented stations in the state, with weighing precipitation gauges and five soil depths | 2004– | 7.4M rows |
| `cocorahs` | Volunteer daily precipitation, filling the gaps between official gauges | 2005– | 5.4M rows, 2,141 stations |
| `iem_dcp` | The archived National Weather Service SHEF/DCP feed, including sensors no other archive keeps | 2010– | 59.2M rows |
| `nwps` | National Water Prediction Service river forecasts and the NWS-to-USGS gauge crosswalk | 30-day window | forecasts |
| `wqp` | Water Quality Portal: 63,639 New Mexico and border monitoring locations | 1900s– | partial; see resume_needed |

## New Mexico state, local, and tribal

| Source | What it carries | Period | Reference run |
|---|---|---|---|
| `nmwdi_st2` | New Mexico Water Data Initiative: groundwater levels federated from nine agencies | 1946– | 1.36M rows, 905 wells |
| `nmed_st2` | Environment Department drinking-water chemistry, 862 analytes across 54,616 sampling points | 2002– | 3.19M results |
| `ose_meas` | The State Engineer's own telemetry: 296 stream, ditch, acequia and well stations. The only source of acequia diversion flows | 2011– | 1.49M rows, 294 sites |
| `ose_arcgis` | Water rights and infrastructure: 279,991 points of diversion, real-time meters, conveyances, springs, dams, acequias, irrigation districts | registry | 158,042 sites |
| `ose_reports` | Water Use by Categories 1975–2020, Rio Grande Compact annual reports, the 1888–1954 streamflow compilation | 1888– | PDF and spreadsheet mirror |
| `isc_sevenrivers` | Interstate Stream Commission's Pecos monitoring network, 37 analytes | 2004– | 84K rows |
| `nmbgmr` | Bureau of Geology aquifer mapping and the Healy collaborative well network | 1946– | 36K rows (host unreachable; see below) |
| `bemp` | Bosque Ecosystem Monitoring: shallow riparian wells and paired precipitation gauges along the Rio Grande | 1997–2017 | 7K rows |
| `ziamet` | New Mexico State University's ZiaMet agricultural mesonet, 214 stations | varies | reference evapotranspiration and weather |
| `ckan` | The New Mexico Water Data catalog: 376 datasets, 1,012 resources | index | metadata harvested |

## Neighbouring states and the border

Basin inflows and outflows are not inside the state line.

| Source | What it carries | Period | Reference run |
|---|---|---|---|
| `codwr` | Colorado Division of Water Resources, Divisions 3 and 7: the Rio Grande and San Juan headwaters, with diversion records | 1889– | 4.6M rows, 1,358 sites |
| `ibwc` | International Boundary and Water Commission: Rio Grande at El Paso and Fort Quitman, plus annual water bulletins 1931–2006 | 1931– | daily feed and PDF archive |
| `twdb` | Texas Water Development Board groundwater database, border counties | 1900– | 333K rows, 7,771 wells |

## Gridded, remote sensing, and aggregate

| Source | What it carries | Period |
|---|---|---|
| `gridmet` | 4 km daily meteorology and reference evapotranspiration, the cheapest complete forcing set | 1979– |
| `prism` | 4 km precipitation and temperature; the long baseline | monthly 1895–, daily 1981– |
| `nclimgrid` | 1/24° daily temperature and precipitation, the longest daily grid | 1951– |
| `snodas` | 1 km daily snow model: snow water equivalent, melt, sublimation | 2003– |
| `ua_swe` | 4 km daily snow water equivalent, the longest consistent gridded snow record | 1981–2023 |
| `usdm` | US Drought Monitor by state, county, climate division, and watershed | 2000– |
| `nclimdiv` | Palmer indices, SPI, precipitation and temperature by climate division and county | 1895– |
| `usgs_wateruse` | County water-use compilations and the modelled monthly 2000–2020 estimates | 1985– |
| `nass` | Agricultural census and irrigation survey: irrigated acres and water applied | 5-yearly |
| `resopsus` | Harmonized daily reservoir operations for 22 New Mexico dams | 1930–2020 |
| `wbd` | Watershed boundaries; supplies the HUC assignment for every site | reference |
| `nhdplus` | NHDPlus v2 stream network: named reaches with drainage area and topology; snaps sites onto the channel | reference |
| `tiger` | Census places, counties, tracts, block groups, tribal and urban areas; the demand-side geography | 2025 vintage |
| `edi` | Sevilleta and Jornada research meteorology, Navajo Nation wells | 1988– |
| `synoptic` | RAWS fire-weather and other mesonets (token required) | 1997– |

## Blocked or partial

Honest accounting of what is not working, with what was tried.

| Source | Status |
|---|---|
| `nmbgmr` | The aquifer program API at `waterdata.nmt.edu` does not respond from this network and the App Engine mirror returns 403 because its default location set is private. The USGS national groundwater network serves site metadata but its water-level route sits behind the same web application firewall. Much of the record is reachable through `nmwdi_st2` and USGS groundwater levels. Retry from another network or request credentials. |
| `edi` | `pasta.lternet.edu` returns 403 "Public Access is not authorized" for search, revision listing, and entity listing. Packages are recorded in `manual_downloads.md` for download from the portal. No code change needed when access returns. |
| `ckan` resources | The New Mexico Water Data catalog serves a Cloudflare challenge to scripted downloads. Metadata harvests fine; nine data files need a browser. See `manual_downloads.md`. |
| `codwr` | Finishes across about three daily runs because of a provider quota. See `resume_needed.md`. |
| `usgs` continuous | The 15-minute archive is opt-in and takes days at 1,000 requests per hour. |
| MRGCD, EBID | Both districts publish telemetry only behind a login. MRGCD diversions are recoverable through `usbr_albuq`; EBID's own gauges would need a data-sharing agreement. |

## Deliberately deferred

Verified and understood, but excluded from this pass on volume grounds: the National Water Model
retrospective (0.5–1 TB for New Mexico reaches), AORC hourly 1 km forcing (~6 TB), MRMS radar
precipitation (50–200 GB), full Daymet, and MODIS daily snow and evapotranspiration tiles.
NEXRAD Level II is excluded outright at 100+ TB, since Stage IV and MRMS already provide the
gauge-corrected product.
