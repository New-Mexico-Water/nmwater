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


## Geometry (`geo/`, `rivers/<slug>/geo/`, `precipitation/watersheds.geojson`)

nmwater prepares geometry (clipping, simplifying, joining; it needs the archive and the GIS libraries) and the website projects and draws it, so a
map is data in the bundle, not a picture. GeoJSON (RFC 7946), WGS84 longitude/latitude, exterior rings counter-clockwise, coordinates rounded
(3 decimals for state-scale layers, more for the fine river view).

| File | Contents |
|---|---|
| `geo/state.geojson`, `geo/county-lines.geojson` | the state outline and the county boundaries inside it (coarse: about 0.5 px on a 600 px wide map of the state) |
| `geo/main-rivers.geojson` | the state's main named rivers, one feature each with `name` and `gnis_id` (coarse) |
| `geo/cities.geojson` | the five cities every state map labels |
| `precipitation/watersheds.geojson` | the HUC8 watersheds that reach into New Mexico, clipped to it (`huc8`, `name`); ratings come from `index.json` |
| `rivers/<slug>/geo/state-view.geojson` | coarse, whole extent: `kind` `segment` (`index`, `segment`, `huc8`) and `river` |
| `rivers/<slug>/geo/river-view.geojson` | fine, clipped to the river frame plus a 15% margin: `kind` `segment`, `river`, `tributary`, `reservoir`, `state-boundary`, `county-lines`, `main-river`, and points `town`, `gauge`, `segment-label`, `reservoir-label` |
| `rivers/<slug>/geo/bounds.json` | `extent` (the river's lon/lat box), `state_bounds` (the state's exact bounds) and `segments` (names, numbered 1..n in this order) |

**The river map's frame** (the website must recompute it exactly; `nmwater/site/geo.py river_view_frame` and `src/lib/geo/rivermap.ts frame` agree, and a
test compares the result with `summary.map.view_state`, `view_river` and `scale`): project with an equirectangular projection of
`full` = the state bounds widened to include `extent`, plus 0.12 degrees on each side, 600 map units wide (x = (lon - west) * k * s,
y = (north - lat) * s, k = cos(mean latitude), s = 600 / ((east - west) * k)). The river view is `extent`'s box in those units, widened to the
full map's aspect ratio and centred.
