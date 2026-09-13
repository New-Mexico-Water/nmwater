# Adding a source

A source is one Python module in `nmwater/sources/`, a crosswalk fragment, and a provenance
entry. The module is discovered automatically at import; there is no registry to edit.

## The contract

```python
"""One paragraph on what this provider serves, with the endpoints and anything verified live.

Record dates you checked things and quirks you found. Future readers need to know whether a
gap is a bug or the provider's reality.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .base import FetchSummary, Source, register


@register
class MyAgency(Source):
    name = "myagency"                      # module name, used everywhere
    agency = "Agency of Water"
    description = "What it serves, one line"
    requires_tokens = ()                   # e.g. ("SYNOPTIC_TOKEN",)
    kinds = ("sites", "data")

    def discover(self) -> pd.DataFrame:
        """Return a frame of sites; `native_id` is required, everything else optional."""

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        """Download, normalize, write observations, return a summary."""

    def normalize(self, artifact) -> pd.DataFrame | None:
        """Turn one archived response into observation rows. Enables offline reprocess."""
```

## Rules that matter

**Fetch through `self.get()`.** It archives the response, writes a ledger row, applies the
source's rate limit, retries with backoff, and serves repeats from the archive. A bare `httpx`
call skips all of that and breaks resumability.

```python
art = self.get(url, params=params, kind="data", site_uid=self.uid(sid),
               variable=param, window=(start.isoformat(), end.isoformat()), refresh=refresh)
```

**Skip work already done.** After a cache hit, check whether the rows were written before:

```python
if art.from_cache:
    summ.n_cached += 1
    if self.already_written(art):
        return 0
```

**Map units through the crosswalk, never in code.** Build a frame with `source_param` holding
the provider's own code, then:

```python
out = self.xw.apply(out, self.name)
```

This fills `variable`, `unit`, `statistic`, `interval`, and converts values. Hard-coding a
conversion hides it from the data dictionary and from anyone auditing later.

**Respect the time convention.** Daily and coarser: local calendar date at 00:00Z,
`utc_offset_min` null. Sub-daily: true UTC instant with the offset recorded. See
[data-model.md](data-model.md).

**Support `since` and `refresh`.** `since` should translate into the provider's own filter
where one exists, and otherwise into a narrower window. `refresh` must bypass the archive.

**Chunk long records.** Fetch by year for daily data, by month for sub-daily, so a failure costs
one small request. If the provider times out on long windows, halve and retry rather than
giving up on the series.

**Write through the store.** `self.write_obs(df, tag=...)` for observations;
`self.store.write_table(df, group, source, name)` or `append_table(...)` for water quality,
water use, reference, and forecast tables.

## Files to add

```
nmwater/sources/myagency.py         the module
catalog/crosswalk.d/myagency.csv    field mappings, same header as catalog/crosswalk.csv
catalog/sources.d/myagency.yaml     provenance, same shape as catalog/sources.yaml
config/sources.yaml                 a block, only if the defaults are wrong
```

Do not edit `catalog/crosswalk.csv` or `catalog/sources.yaml` directly for a new source. The
fragment directories exist so sources stay self-contained, and fragments load after the base
file, so a fragment row wins on a key collision.

A crosswalk fragment row:

```csv
source,source_param,source_name,source_unit,variable,factor,offset,statistic,interval,equivalence,caveat
myagency,FLOW,Mean daily flow,cfs,discharge,1,0,mean,daily,identical,
myagency,PRECIP_ACC,Accumulated precipitation,in,precip_accumulated_wy,1,0,accumulated,daily,related_not_comparable,differencing gives incremental precip
```

If a field genuinely has no canonical home, leave `variable` empty and say why in `caveat`.
That records the decision. If the provider measures something the catalog lacks, add the
variable to `catalog/variables.yaml` with a definition, unit, kind, and medium.

## Developing one

Read the provider's own documentation, then verify every endpoint live with a small request
before writing a parser. Fetch once, then read the archived bytes rather than guessing at the
shape:

```bash
uv run nmwater fetch myagency --limit 1 -v
zcat data/raw/myagency/data/*/*.gz | head -40
```

Check what landed by querying the Parquet directly, which works while other jobs are running:

```bash
uv run python -c "
import duckdb; con = duckdb.connect(); con.execute(\"SET TimeZone='UTC'\")
print(con.execute('''
  SELECT variable, count(*) n, count(DISTINCT site_uid) sites,
         min(datetime_utc)::DATE t0, max(datetime_utc)::DATE t1
  FROM read_parquet(\"data/parquet/timeseries/source=myagency/*/*/*.parquet\", hive_partitioning=true)
  GROUP BY 1 ORDER BY 2 DESC''').fetchdf().to_string())"
```

Then validate the catalog and re-run the offline tests:

```bash
uv run nmwater catalog check
uv run pytest -q
```

## Things real providers do

Every one of these was hit while building the current 39 sources, and each is handled in the
module that met it.

- **Multi-block responses.** A USGS RDB file for many sites is a concatenation of blocks, each
  with its own header of a different width. Parsing only the first header silently drops most of
  the data.
- **Silently ignored parameters.** Several services accept a filter and return everything
  anyway. Verify that a filter narrows the result before trusting it.
- **Dropped fields in compact formats.** The SensorThings `dataArray` format omits `id` and the
  datastream link even when asked for them, which breaks both paging and attribution. Ordinary
  JSON with an expand works.
- **Undocumented quotas.** Colorado DWR returns HTTP 403 "Exceeded Daily Data Limit" partway
  through a backfill; the run must resume on a later day.
- **Encoding.** USACE responses are Latin-1, not UTF-8. Spanish place names crash a naive
  decode.
- **Units that are not what the docs say.** Request explicit units where the API allows it, and
  check a known value against a published figure.
- **Sentinel values.** Reclamation uses -901 for missing storage. Filter sentinels in
  `normalize()`, before the crosswalk.
- **Bot protection.** Cloudflare and WAFs block scripted downloads on some state portals. When
  that happens, record the URL in `docs/manual_downloads.md` via `note_manual()` so a human can
  fetch it once, and ingest from `data/manual/<source>/` on the next run.
