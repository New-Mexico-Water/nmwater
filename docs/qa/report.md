# QA report

Catalog: `nmwater.duckdb`, built 2026-11-03. Regenerate with `nmwater report`.

## Coverage by source

| source | sites | variables | observations | first | last |
|---|---|---|---|---|---|
| bemp | 321 | 12 | 51,066 | 1997-04-01 | 2018-01-20 |
| cocorahs | 2,155 | 4 | 5,454,619 | 2005-01-01 | 2026-11-02 |
| codwr | 1,918 | 5 | 5,317,105 | 1889-01-01 | 2026-11-02 |
| gridmet | 1 | 0 | 0 | NaT | NaT |
| ibwc | 19 | 3 | 4,723 | 2026-04-17 | 2026-10-30 |
| iem_dcp | 402 | 13 | 297,832,711 | 2010-11-01 | 2026-11-03 |
| isc_sevenrivers | 73 | 1 | 12,226 | 2004-04-20 | 2026-10-01 |
| nclimdiv | 81 | 7 | 867,969 | 1895-01-01 | 2026-09-01 |
| nclimgrid | 1 | 0 | 0 | NaT | NaT |
| nhdplus | 113 | 0 | 0 | NaT | NaT |
| nid | 16,019 | 0 | 0 | NaT | NaT |
| nmbgmr | 619 | 1 | 35,924 | 1946-08-25 | 2025-06-06 |
| nmed_st2 | 54,616 | 9 | 87,021 | 1990-01-02 | 2026-10-20 |
| nmwdi_st2 | 1,174 | 1 | 1,323,792 | 1946-08-25 | 2025-06-24 |
| noaa_ghcnd | 2,989 | 6 | 61,853,475 | 1850-01-01 | 2026-10-31 |
| noaa_ghcnh | 188 | 6 | 121,132,358 | 1892-08-01 | 2026-10-31 |
| noaa_isd | 176 | 4 | 64,844,624 | 1941-04-01 | 2025-08-28 |
| nrcs | 240 | 10 | 88,670,703 | 1900-01-01 | 2026-11-03 |
| nwps | 160 | 2 | 2,066,919 | 2026-08-13 | 2026-11-03 |
| ose_arcgis | 283,680 | 1 | 158,029 | 1860-12-31 | 2026-08-28 |
| ose_meas | 296 | 4 | 1,511,107 | 2011-06-06 | 2026-11-03 |
| prism | 1 | 0 | 0 | NaT | NaT |
| resopsus | 22 | 4 | 207,057 | 1915-03-21 | 2021-01-28 |
| snodas | 1 | 0 | 0 | NaT | NaT |
| twdb | 11,619 | 2 | 333,634 | 1900-01-01 | 2026-09-30 |
| usace_cwms | 743 | 8 | 188,027,080 | 1982-10-02 | 2026-10-19 |
| usbr_albuq | 25 | 7 | 256,751 | 2026-09-05 | 2026-11-03 |
| usbr_hydrodata | 134 | 11 | 5,332,596 | 1899-10-01 | 2026-11-02 |
| usbr_rise | 57 | 14 | 65,669 | 2011-03-15 | 2025-10-14 |
| uscrn | 25 | 6 | 10,787,596 | 2004-01-01 | 2026-11-03 |
| usdm | 172 | 6 | 534,800 | 2000-01-04 | 2026-10-27 |
| usgs | 50,342 | 19 | 19,894,813 | 1884-05-01 | 2026-11-03 |
| usgs_wateruse | 31 | 0 | 0 | NaT | NaT |
| wbd | 5 | 0 | 0 | NaT | NaT |
| wqp | 63,639 | 12 | 1,332,698 | 1900-01-01 | 2026-10-08 |
| ziamet | 214 | 8 | 3,749,227 | 2020-01-15 | 2026-11-02 |

## Site count checks

| source | expected >= | actual | ok |
|---|---|---|---|
| usgs | 42,000 | 50,342 | yes |
| noaa_ghcnd | 2,300 | 2,989 | yes |
| nmwdi_st2 | 1,100 | 1,174 | yes |
| usace_cwms | 500 | 743 | yes |
| nrcs | 200 | 240 | yes |
| wqp | 48,000 | 63,639 | yes |
| ose_arcgis | 250,000 | 283,680 | yes |
| usbr_hydrodata | 120 | 134 | yes |
| codwr | 100 | 1,918 | yes |

## Landmark records

| check | first | rows | ok |
|---|---|---|---|
| Rio Grande at Embudo daily discharge from 1889 (`usgs:08279500`) | 1889-01-01 | 47,055 | yes |
| Rio Grande at Otowi Bridge daily discharge from 1895 (`usgs:08313000`) | 1895-02-01 | 45,383 | yes |
| Rio Grande at Albuquerque daily discharge from 1965 (`usgs:08330000`) | 1965-10-01 | 22,311 | yes |
| Rio Grande Floodway at San Marcial (`usgs:08358400`) | 1949-10-01 | 28,156 | yes |
| Pecos River near Red Bluff (`usgs:08407500`) | 1937-10-01 | 32,540 | yes |
| Rio Grande at El Paso annual peak flows (`usgs:08364000`) | 1884-05-01 | 90 | yes |
| Elephant Butte storage from 1915 (`usbr_hydrodata:1119`) | 1915-03-21 | 40,676 | yes |
| Otowi flow from 1899 (Reclamation) (`usbr_hydrodata:1095`) | 1899-10-01 | 43,680 | yes |
| Cochiti daily elevation from 1993 (`usace_cwms:Cochiti`) | 1993-01-02 | 805,767 | yes |
| Frisco Divide SNOTEL snow water equivalent from 1978 (`nrcs:486:NM:SNTL`) | 1978-11-01 | 180,652 | yes |
| Santa Fe SNOTEL from 1996 (`nrcs:922:NM:SNTL`) | 1996-10-01 | 193,278 | yes |
| La Jara Creek (CO DWR) from 1943 (`codwr:LAJCAPCO`) | 1943-03-01 | 28,920 | yes |

## Hygiene

- variables in data missing from variables.yaml: none
- sites without coordinates: nclimdiv=81, nmed_st2=9,134, nmwdi_st2=165, usbr_albuq=11, nhdplus=113, usace_cwms=4, nid=17, ose_arcgis=20, usgs=437, usdm=172, ose_meas=111, ibwc=17, codwr=4, nrcs=1, cocorahs=1, usbr_rise=15, bemp=321, usgs_wateruse=31, wbd=5, wqp=443
- located sites without huc8, in New Mexico (WBD regions missing or a bad coordinate): 27
- located sites without huc8, in another state (expected; outside the watershed layers): 1,411
- sites at latitude 0, longitude 0 (a missing location written as a point; should be none): 0
- observation sites missing from sites table: 10
- duplicate observation keys (run `nmwater compact`): 12,504,256
- implausible values, excluded from observations_clean (bounds in variables.yaml): air_temp=58,415, water_temp=35,987, relative_humidity=25,832, wind_direction=7,887, stage=4,332, snow_depth=2,446, swe=2,157, gw_level_elevation=2,087, precip=738, dissolved_oxygen=514, reservoir_elevation=345, wind_speed=139, ph=103, turbidity=58, gw_depth_to_water=19, discharge=10, specific_conductance=1, reservoir_storage=1
- near-zero values within instrument noise, kept and flagged near_zero: snow_depth=118,031, swe=105,085, relative_humidity=1,792, turbidity=171, specific_conductance=7, reservoir_storage=7, dissolved_oxygen=4
- negative discharge, deliberately unbounded pending confirmation that canal reverse flow is real: 9,447

## Crosswalk

- identical: 338, related_not_comparable: 47, equivalent_method: 50
