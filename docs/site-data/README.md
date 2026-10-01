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
