# Site data bundle

`nmwater export-site-data` writes the files the website renders. The contract is versioned (`schema_version`,
currently 1) with JSON Schemas in `v1/`. The site (a separate repository) validates what it reads and fails
the build on a mismatch, so a change here that the site cannot read is caught before it is published.

```
manifest.json                     producer, generated, data_through, rivers[], headlines
rivers/<slug>/summary.json        the Overview: status, description, cited background, acequias, reservoirs, users, sources, map, meta
rivers/<slug>/map.svg             the zoomable map (state and river views, two levels of detail; styled by the site)
rivers/<slug>/flow.json           weekly mean flow per segment, whole record, and its gauges
rivers/<slug>/normal.json         last 52 weeks against 1991-2020                (only where there is a baseline)
rivers/<slug>/drying.json         days a year below 0.1 cfs
rivers/<slug>/quality.json        water temperature and salinity                 (only where there is data)
rivers/<slug>/watershed.json      daily and monthly precipitation, temperature, snow, drought   (only where there is data)
rivers/<slug>/notes.json          data gaps and disparities
rivers/<slug>/*.csv, notes.md     downloads;  social.png  the 1200x630 share image
```

Conventions: dates are ISO 8601 (`YYYY-MM-DD`); flow is cfs, precipitation inches, temperature degrees C, storage
acre-feet. Series are parallel arrays (the same length as their `weeks`, `months` or `dates` array) with `null`
where there is no value; the exporter checks this. `summary.json` lists the files a river has in `files` and
its tabs in `tabs`; a tab whose file is absent is not offered. Numbers in `sources` are referenced by the
`sources` arrays of the statements and acequias that cite them. A PRISM day (daily precipitation) is the 24
hours ending at 12:00 UTC on its date.

Compatibility: adding fields is allowed within a version (schemas permit extra properties); renaming or
removing a field, or changing a unit, bumps `schema_version` and the folder (`v2/`). The exporter writes one
version at a time.


## Precipitation (`precipitation/`)

Written on every run (a `--river` run rebuilds it too, from the river summaries it keeps). Source: PRISM, area-weighted over each HUC8
(`nmwater watershed-precip`; see `docs/reports/watershed-precipitation.md`). Absent when the catalog has no `watershed_precip` view.

| File | Contents |
|---|---|
| `precipitation/index.json` | every watershed with data: recent rain over 7, 30 and 90 days against 1991-2020 (`total_in`, `normal_in`, `percent_of_normal`, `percentile`, `class`, the wettest day), coverage (`grid_fraction`, `partial`, `coverage_note`) and the rivers whose pages use the watershed |
| `precipitation/map.svg` | New Mexico's watersheds that reach into the state, clipped to it; `<path class="ws" data-huc8 data-name data-c7 data-c30 data-c90>`, the `data-c*` values being the rating slugs (`much-below-normal` ... `much-above-normal`, or `none`) |
| `precipitation/recent.csv` | the same numbers as a table |
| `precipitation/<huc8>/precip.json` | one watershed: 36 months of totals with the 1991-2020 median, the last 90 days, the windows, the rivers |
| `precipitation/<huc8>/precip_daily_last_365_days.csv` | the last 365 PRISM days with the exact period each covers |

Ratings use the same percentile classes as flow (`much below normal` under the 10th percentile ... `much above normal` over the 90th). A window with
fewer than 20 baseline years has no normal and no rating. A watershed wholly outside New Mexico (a Colorado or Texas HUC8 a river page uses) has a page
but is not on the map. A PRISM day ends at 12:00 UTC on its date. Schemas: `precip_index.schema.json`, `precip_watershed.schema.json`.
