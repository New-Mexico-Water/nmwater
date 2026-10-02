"""The data bundle the website renders (`nmwater export-site-data`).

The pipeline writes data (JSON, GeoJSON, CSV, and the share images) with a versioned contract (docs/site-data/); the site (a separate
repository) builds every page from it: layout, maps, wording and all. See bundle.py for the layout and river.py for what each file holds.
"""

SCHEMA_VERSION = 2
