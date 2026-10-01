"""The data bundle the website renders (`nmwater export-site-data`).

The pipeline writes JSON, SVG and CSV files with a versioned contract (docs/site-data/); the site (a separate
repository) builds every page from them. See bundle.py for the layout and river.py for what each file holds.
"""

SCHEMA_VERSION = 1
