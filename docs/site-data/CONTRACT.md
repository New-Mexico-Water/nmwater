# The data contract between nmwater and the website

**nmwater** collects, aggregates and computes. **nmwater-web** presents. They are separate repositories, built and run as separate
tasks. The only thing that passes between them is a versioned **data bundle**. This page is the one-page agreement; the files and
fields are in [README.md](README.md) and the JSON Schemas in `v1/`.

## Who owns what

| | nmwater | nmwater-web |
|---|---|---|
| The archive, catalog, fetchers, derived datasets, analysis | yes | never |
| The bundle's files and the schemas that describe them (`docs/site-data/v1`) | owns | reads |
| Layout, design, charts, maps drawn on screen, wording, search/sharing tags, accessibility | no | owns |
| Fixture bundles for tests (`scripts/make_site_fixture.py`) | generates | commits a copy (`fixtures/site-data`) |
| A pinned copy of the schemas (`contract/schemas`, `contract/VERSION`) | `just sync-contract` writes it | validates every bundle against it |

nmwater never runs the website build. The website never imports nmwater. (A test, `tests/test_boundaries.py`, keeps the data
modules free of HTML.)

## The flow

1. nmwater host (cron): `scripts/cron/site_data.sh --update --publish` = `nmwater update`, then `nmwater export-site-data` (validates the
   bundle against the schemas and a set of consistency rules; a failed run leaves the previous bundle), then
   `scripts/cron/publish_site_data.sh`.
2. `publish_site_data.sh` uploads the bundle to Cloudflare R2 as `bundles/<id>/` (never changed after upload), then writes `latest.json`
   (`{"bundle", "generated", "data_through", "schema_version"}`) last, so a reader always sees one complete snapshot, then sends the website
   repository a `data-updated` `repository_dispatch` with `client_payload.bundle = <id>`.
3. nmwater-web CI: fetch `bundles/<id>/` (the id from the dispatch, else `latest.json`), check it against `files.json` (every file's size and
   SHA-256), validate every JSON file against `contract/schemas`, build, test, deploy. A bundle that fails validation is not deployed.
   A scheduled daily run covers a missed dispatch.

## Versioning

`schema_version` is 1. Changes inside version 1 are **additive** (new optional fields, new files, new sections such as `precipitation/`);
the website must ignore what it does not know and tolerate what is absent. A change that removes or renames something is version 2:
the website accepts both during the overlap, nmwater writes both until the website stops reading the old one. `contract/VERSION` in the
website records which nmwater commit its schemas came from.

## Changing the contract

1. Edit the schema (and the exporter, and `README.md`) in nmwater; add or update the test.
2. `just sync-contract ../nmwater-web --fixtures` copies the schemas and a fresh three-river fixture into the website repo.
3. Commit that in the website repo (CI fails if `contract/schemas` differs from the schemas the fixtures were made with only when the
   fixtures do not validate against them, so a forgotten sync is caught by the fixture check).
4. Merge the side that only adds fields first; the website tolerates absent fields, so either order is safe for additions.

## What is not (yet) pure data

The bundle still carries three kinds of presentation that nmwater draws and the website will take over: the river and precipitation
`map.svg` files, the `social.png` share images, and the auto-written descriptions (`description.auto`, `meta.description`). They are
being replaced by geometry (GeoJSON) and plain facts the website turns into drawings and sentences.
