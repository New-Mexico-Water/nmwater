# QA report

Catalog: `nmwater.duckdb`, built 2026-11-12. Regenerate with `nmwater report`.

## Coverage by source

| source | sites | variables | observations | first | last |
|---|---|---|---|---|---|
| bemp | 321 | 12 | 51,066 | 1997-04-01 | 2018-01-20 |
| cocorahs | 2,157 | 4 | 5,462,353 | 2005-01-01 | 2026-11-11 |
| codwr | 1,918 | 5 | 5,319,379 | 1889-01-01 | 2026-11-11 |
| gridmet | 1 | 0 | 0 | NaT | NaT |
| ibwc | 19 | 3 | 4,960 | 2026-04-17 | 2026-11-10 |
| iem_dcp | 402 | 13 | 298,409,898 | 2010-11-01 | 2026-11-12 |
| isc_sevenrivers | 73 | 1 | 12,282 | 2004-04-20 | 2026-10-28 |
| nclimdiv | 81 | 7 | 868,518 | 1895-01-01 | 2026-10-01 |
| nclimgrid | 1 | 0 | 0 | NaT | NaT |
| nhdplus | 113 | 0 | 0 | NaT | NaT |
| nid | 16,019 | 0 | 0 | NaT | NaT |
| nmbgmr | 619 | 1 | 35,924 | 1946-08-25 | 2025-06-06 |
| nmed_st2 | 54,616 | 9 | 87,063 | 1990-01-02 | 2026-11-03 |
| nmwdi_st2 | 1,174 | 1 | 1,323,792 | 1946-08-25 | 2025-06-24 |
| noaa_ghcnd | 2,989 | 6 | 61,870,226 | 1850-01-01 | 2026-11-10 |
| noaa_ghcnh | 188 | 6 | 121,183,281 | 1892-08-01 | 2026-11-11 |
| noaa_isd | 176 | 4 | 64,844,624 | 1941-04-01 | 2025-08-28 |
| nrcs | 240 | 10 | 88,793,247 | 1900-01-01 | 2026-11-12 |
| nwps | 160 | 2 | 2,318,572 | 2026-08-13 | 2026-11-12 |
| ose_arcgis | 241,690 | 1 | 71,349 | 1876-12-31 | 2026-08-28 |
| ose_meas | 296 | 4 | 1,514,017 | 2011-06-06 | 2026-11-12 |
| prism | 1 | 0 | 0 | NaT | NaT |
| resopsus | 22 | 4 | 207,057 | 1915-03-21 | 2021-01-28 |
| snodas | 1 | 0 | 0 | NaT | NaT |
| twdb | 11,619 | 2 | 334,564 | 1900-01-01 | 2026-10-31 |
| usace_cwms | 743 | 8 | 188,027,080 | 1982-10-02 | 2026-10-19 |
| usbr_albuq | 25 | 7 | 298,511 | 2026-09-05 | 2026-11-12 |
| usbr_hydrodata | 134 | 11 | 5,335,012 | 1899-10-01 | 2026-11-11 |
| usbr_rise | 57 | 14 | 65,669 | 2011-03-15 | 2025-10-14 |
| uscrn | 25 | 6 | 10,797,426 | 2004-01-01 | 2026-11-12 |
| usdm | 172 | 6 | 535,182 | 2000-01-04 | 2026-11-03 |
| usgs | 50,342 | 19 | 20,447,720 | 1884-05-01 | 2026-11-12 |
| usgs_wateruse | 31 | 0 | 0 | NaT | NaT |
| wbd | 5 | 0 | 0 | NaT | NaT |
| wqp | 63,639 | 12 | 1,332,782 | 1900-01-01 | 2026-10-22 |
| ziamet | 214 | 8 | 3,774,619 | 2020-01-15 | 2026-11-12 |

## Site count checks

| source | expected >= | actual | ok |
|---|---|---|---|
| usgs | 42,000 | 50,342 | yes |
| noaa_ghcnd | 2,300 | 2,989 | yes |
| nmwdi_st2 | 1,100 | 1,174 | yes |
| usace_cwms | 500 | 743 | yes |
| nrcs | 200 | 240 | yes |
| wqp | 48,000 | 63,639 | yes |
| ose_arcgis | 250,000 | 241,690 | NO |
| usbr_hydrodata | 120 | 134 | yes |
| codwr | 100 | 1,918 | yes |

## Landmark records

| check | first | rows | ok |
|---|---|---|---|
| Rio Grande at Embudo daily discharge from 1889 (`usgs:08279500`) | 1889-01-01 | 47,063 | yes |
| Rio Grande at Otowi Bridge daily discharge from 1895 (`usgs:08313000`) | 1895-02-01 | 45,391 | yes |
| Rio Grande at Albuquerque daily discharge from 1965 (`usgs:08330000`) | 1965-10-01 | 22,319 | yes |
| Rio Grande Floodway at San Marcial (`usgs:08358400`) | 1949-10-01 | 28,164 | yes |
| Pecos River near Red Bluff (`usgs:08407500`) | 1937-10-01 | 32,548 | yes |
| Rio Grande at El Paso annual peak flows (`usgs:08364000`) | 1884-05-01 | 90 | yes |
| Elephant Butte storage from 1915 (`usbr_hydrodata:1119`) | 1915-03-21 | 40,685 | yes |
| Otowi flow from 1899 (Reclamation) (`usbr_hydrodata:1095`) | 1899-10-01 | 43,689 | yes |
| Cochiti daily elevation from 1993 (`usace_cwms:Cochiti`) | 1993-01-02 | 805,767 | yes |
| Frisco Divide SNOTEL snow water equivalent from 1978 (`nrcs:486:NM:SNTL`) | 1978-11-01 | 180,868 | yes |
| Santa Fe SNOTEL from 1996 (`nrcs:922:NM:SNTL`) | 1996-10-01 | 193,503 | yes |
| La Jara Creek (CO DWR) from 1943 (`codwr:LAJCAPCO`) | 1943-03-01 | 28,929 | yes |

## Hygiene

- variables in data missing from variables.yaml: none
- sites without coordinates: ibwc=17, codwr=4, nrcs=1, nclimdiv=81, nmed_st2=9,134, nmwdi_st2=165, usbr_albuq=11, wqp=443, usace_cwms=4, nid=17, ose_arcgis=20, ose_meas=111, usgs=437, usdm=172, usgs_wateruse=31, wbd=5, bemp=321, cocorahs=1, usbr_rise=15, nhdplus=113
- located sites without huc8, in New Mexico (WBD regions missing or a bad coordinate): 27
- located sites without huc8, in another state (expected; outside the watershed layers): 1,411
- sites at latitude 0, longitude 0 (a missing location written as a point; should be none): 0
- observation sites missing from sites table: 86,693
- duplicate observation keys, by the store's key site_uid, variable, datetime_utc, interval, statistic, depth_cm (run `nmwater compact` if not zero): 0
- implausible values, excluded from observations_clean (bounds in variables.yaml; table below): soil_temp=69,685, air_temp=58,419, water_temp=35,969, relative_humidity=25,832, soil_moisture=21,816, wind_direction=7,887, stage=4,334, snow_depth=2,446, swe=2,157, gw_level_elevation=2,087, precip=738, dissolved_oxygen=514, reservoir_elevation=345, wind_speed=139, ph=103, turbidity=58, snowfall=30, gw_depth_to_water=19, suspended_sediment_conc=11, discharge=10, specific_conductance=7, et_reference=1, reservoir_storage=1, solar_radiation=1, suspended_sediment_discharge=1
- near-zero values within instrument noise, kept and flagged near_zero: snow_depth=118,869, swe=105,085, relative_humidity=1,792, turbidity=171, reservoir_storage=7, specific_conductance=7, dissolved_oxygen=4

## Plausible ranges

Bounds are `valid_min` / `noise_floor` / `valid_max` in catalog/variables.yaml. A value below `valid_min` is impossible unless it is in the noise band; a value above `valid_max` is impossible. Impossible values are excluded from `observations_clean`; nothing is deleted. "Seen" is the raw lowest and highest value.

| variable | unit | allowed | seen | impossible below | impossible above | noise band |
|---|---|---|---|---|---|---|
| air_temp | degC | -60 to 60 | -4,857 to 6.955e+04 | 21,072 | 37,347 | 0 |
| arsenic | ug/L | 0 to 1e+09 | 0 to 2.58e+04 | 0 | 0 | 0 |
| chloride | mg/L | 0 to 1e+06 | 0 to 2.5e+05 | 0 | 0 | 0 |
| discharge | cfs | at most 1e+06 | -315.2 to 4.872e+06 | 0 | 10 | 0 |
| discharge_volume | af | no bounds | -980.1 to 1.031e+05 | 0 | 0 | 0 |
| dissolved_oxygen | mg/L | 0 to 25 | -99.99 to 9,029 | 22 | 492 | 4 |
| diversion | cfs | at least 0 | 0 to 5.439e+04 | 0 | 0 | 0 |
| diversion_volume | af | at least 0 | 0 to 322.4 | 0 | 0 | 0 |
| drought_d0_pct | percent | 0 to 100 | 0 to 100 | 0 | 0 | 0 |
| drought_d1_pct | percent | 0 to 100 | 0 to 100 | 0 | 0 | 0 |
| drought_d2_pct | percent | 0 to 100 | 0 to 100 | 0 | 0 | 0 |
| drought_d3_pct | percent | 0 to 100 | 0 to 100 | 0 | 0 | 0 |
| drought_d4_pct | percent | 0 to 100 | 0 to 100 | 0 | 0 | 0 |
| dsci | index | 0 to 500 | 0 to 500 | 0 | 0 | 0 |
| et_reference | mm | 0 to 60 | 0 to 2.284e+34 | 0 | 1 | 0 |
| fluoride | mg/L | 0 to 1e+06 | 0 to 1,200 | 0 | 0 | 0 |
| gw_depth_to_water | ft | -500 to 3,500 | -957.1 to 8,495 | 3 | 16 | 0 |
| gw_level_elevation | ft | 1,000 to 1.4e+04 | 0.04 to 1.008e+06 | 1,822 | 265 | 0 |
| nitrate | mg/L | 0 to 1e+06 | 0 to 2.18e+04 | 0 | 0 | 0 |
| palmer_z | index | no bounds | -6.77 to 19.74 | 0 | 0 | 0 |
| pdsi | index | no bounds | -9.3 to 13.85 | 0 | 0 | 0 |
| ph | pH | 0 to 14 | -4,954 to 8,035 | 3 | 100 | 0 |
| phdi | index | no bounds | -9.3 to 13.85 | 0 | 0 | 0 |
| pmdi | index | no bounds | -8.13 to 13.79 | 0 | 0 | 0 |
| precip | in | 0 to 150 | 0 to 1.287e+05 | 0 | 738 | 0 |
| precip_accumulated_wy | in | no bounds | -84.8 to 8.452e+04 | 0 | 0 | 0 |
| relative_humidity | percent | 0 to 105 | -3,451 to 9.655e+04 | 13,128 | 12,704 | 1,792 |
| reservoir_area | acre | at least 0 | 0 to 2.102e+07 | 0 | 0 | 0 |
| reservoir_delta_storage | af | no bounds | -1.419e+05 to 1.027e+05 | 0 | 0 | 0 |
| reservoir_elevation | ft | 1,500 to 1.2e+04 | -0.16 to 7.792e+06 | 343 | 2 | 0 |
| reservoir_evaporation | af | no bounds | -287.8 to 7.567e+04 | 0 | 0 | 0 |
| reservoir_inflow | cfs | no bounds | -9.31e+04 to 9.389e+04 | 0 | 0 | 0 |
| reservoir_inflow_volume | af | no bounds | -1.911e+04 to 1.032e+05 | 0 | 0 | 0 |
| reservoir_release | cfs | at least 0 | 0 to 6.855e+05 | 0 | 0 | 0 |
| reservoir_release_volume | af | at least 0 | 0 to 2.856e+04 | 0 | 0 | 0 |
| reservoir_storage | af | at least 0 | -2.933e+04 to 2.303e+06 | 1 | 0 | 7 |
| snow_density | percent | 0 to 100 | 0.7 to 100 | 0 | 0 | 0 |
| snow_depth | in | 0 to 300 | -739 to 999 | 2,425 | 21 | 118,869 |
| snowfall | in | 0 to 100 | -0.5118 to 999.9 | 1 | 29 | 0 |
| soil_moisture | percent | 0 to 100 | 0 to 4.058e+05 | 0 | 21,816 | 0 |
| soil_temp | degC | -50 to 80 | -273.3 to 1,401 | 24,205 | 45,480 | 0 |
| solar_radiation | W/m2 | -20 to 1,500 | -3.836 to 7,782 | 0 | 1 | 0 |
| specific_conductance | uS/cm | at least 0 | -1.31e+04 to 4.1e+05 | 7 | 0 | 7 |
| spi | index | no bounds | -3.09 to 3.09 | 0 | 0 | 0 |
| stage | ft | -100 to 2e+04 | -1,311 to 1e+07 | 4,284 | 50 | 0 |
| sulfate | mg/L | 0 to 1e+06 | 0 to 7.64e+04 | 0 | 0 | 0 |
| suspended_sediment_conc | mg/L | 0 to 1e+06 | -99 to 4.7e+05 | 11 | 0 | 0 |
| suspended_sediment_discharge | tons/day | at least 0 | -99 to 9.13e+06 | 1 | 0 | 0 |
| swe | in | 0 to 200 | -26.9 to 1,583 | 1,835 | 322 | 105,085 |
| tds | mg/L | 0 to 1e+06 | 0 to 4.05e+05 | 0 | 0 | 0 |
| turbidity | FNU | 0 to 1e+05 | -1.137e+05 to 9.858e+05 | 31 | 27 | 171 |
| uranium | ug/L | 0 to 1e+09 | 0 to 3.1e+04 | 0 | 0 | 0 |
| water_temp | degC | -5 to 60 | -1.31e+04 to 2.21e+04 | 3,455 | 32,514 | 0 |
| wind_direction | degree | 0 to 360 | -100 to 548 | 7,883 | 4 | 0 |
| wind_speed | mph | 0 to 150 | -49.95 to 1.789e+04 | 61 | 78 | 0 |

- variables with data and no bounds at all, so no check: 11 of 55 (discharge_volume, palmer_z, pdsi, phdi, pmdi, precip_accumulated_wy, reservoir_delta_storage, reservoir_evaporation, reservoir_inflow, reservoir_inflow_volume, spi)
- no lower bound although negative values occur: discharge (9,455: negative values are kept until the state engineer confirms whether reverse flow in canals is real (#61)); discharge_volume (3,997: volumes computed from the same flows: negative values are kept for the same reason as discharge (#61)); palmer_z (34,784: an index: negative means dry, and that is real); pdsi (64,552: an index: negative means dry, and that is real); phdi (60,678: an index: negative means dry, and that is real); pmdi (32,358: an index: negative means dry, and that is real); precip_accumulated_wy (98,661: NRCS accumulated precipitation drifts below zero (to -84.8 in, 98,661 values); the drift band has not been set (#26)); reservoir_delta_storage (104,920: negative values expected (signed)); reservoir_evaporation (3: may be net of rain on the lake; three negative values; unconfirmed); reservoir_inflow (9,853: negative values expected (signed)); reservoir_inflow_volume (7,072: negative values expected (signed)); spi (30,906: an index: negative means drier than normal, and that is real)

## Crosswalk

- equivalent_method: 50, identical: 338, related_not_comparable: 47
