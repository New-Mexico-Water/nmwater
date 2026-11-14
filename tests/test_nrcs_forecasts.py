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


def _normalize(payload):
    from types import SimpleNamespace

    from nmwater.sources.nrcs import NRCS as Nrcs

    src = Nrcs.__new__(Nrcs)
    src.name = "nrcs"
    src.xw = SimpleNamespace(apply=lambda df, name: df)
    src.uid = lambda trip: f"nrcs:{trip}"
    return src.normalize(SimpleNamespace(kind="hourly", read_json=lambda: payload))


def _block(code, values):
    return {"stationElement": {"elementCode": code, "durationName": "HOURLY", "storedUnitCode": "in"}, "values": values}


def test_an_element_whose_every_hour_is_missing_does_not_lose_the_others():
    """A sensor that reported nothing for the whole window comes back with no `value` key at all; the station's other elements must still be stored."""
    missing = [{"date": "2026-11-10 00:00", "qcFlag": "M", "qaFlag": "R"}, {"date": "2026-11-10 01:00", "qcFlag": "M", "qaFlag": "R"}]
    ok = [{"date": "2026-11-10 00:00", "value": 3.0, "qcFlag": "V"}, {"date": "2026-11-10 01:00", "qaFlag": "R"}]
    out = _normalize([{"stationTriplet": "1138:NM:SNTL", "data": [_block("SNWD", missing), _block("WTEQ", ok)]}])
    assert list(out["source_param"]) == ["WTEQ"] and list(out["value"]) == [3.0]
    assert out["datetime_utc"].iloc[0].hour == 7           # the station's midnight (UTC-7 all year)
