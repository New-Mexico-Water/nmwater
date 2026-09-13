# Sources that need repeated runs

These sources cannot be completed in a single pass because the provider meters access. Every
failed request stays in the ledger with `status = error`, so simply re-running the same command
on a later day resumes exactly where it stopped. Nothing is re-downloaded.

| source | limit | command to repeat | expected runs |
|---|---|---|---|
| codwr | CDSS daily data quota, HTTP 403 "Exceeded Daily Data Limit", applies even with a registered key | `uv run nmwater fetch codwr` | ~3 days for Divisions 3 and 7 |
| usgs (continuous) | 1,000 requests/hour on api.waterdata.usgs.gov | `uv run nmwater fetch usgs --kind continuous` | 1 run for the default last year; the full 2007- record is several days |
| wqp | no hard quota, but statewide results are ~700 county x decade chunks of up to 20 GB | `uv run nmwater fetch wqp` | 1 long run |

Check progress with:

```bash
uv run nmwater status codwr          # ok vs error request counts
uv run nmwater report                # coverage and landmark checks
```

## A note on the 15-minute data

`usgs --kind continuous` no longer tries to pull 2007 to the present by default. It takes the
most recent year, which is what operational questions usually need, and leaves the rest to be
requested by date range when a specific past period matters:

```bash
nmwater fetch usgs --kind continuous --since 2013-08-01 --until 2013-09-30
```

Ranges accumulate. Fetching 2011 and later 2013 leaves both on disk, and neither run re-fetches
what the other already stored.
