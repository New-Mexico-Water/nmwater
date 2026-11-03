"""NRCS forecasts: a pull from a start date is merged into the stored table, not written over it."""

import pandas as pd

from nmwater.sources.nrcs import merge_forecasts


def _row(pub, p50, issue="2026-02-01"):
    return {"station_triplet": "A:NM:SNTL", "element": "SRVO", "period_start": "2026-04-01", "period_end": "2026-07-31", "issue_date": issue,
            "publication_date": pub, "p50": p50}


def test_new_publications_are_added_and_the_history_is_kept():
    old = pd.DataFrame([_row("2026-01-05", 100.0), _row("2026-02-05", 110.0, "2026-02-01")])
    new = pd.DataFrame([_row("2026-03-05", 120.0, "2026-03-01")])
    out = merge_forecasts(old, new)
    assert len(out) == 3 and set(out.publication_date) == {"2026-01-05", "2026-02-05", "2026-03-05"}


def test_a_forecast_published_again_takes_the_new_values_once():
    old = pd.DataFrame([_row("2026-02-05", 110.0)])
    out = merge_forecasts(old, pd.DataFrame([_row("2026-02-05", 111.5)]))
    assert len(out) == 1 and out.p50.iloc[0] == 111.5
