"""Tests for the update log helpers."""

import csv

import pandas as pd

from nmwater.core.update import UpdateRow, append_log, parquet_stats


def test_append_log_writes_header_once(tmp_path):
    p = tmp_path / "log.csv"
    append_log(p, [UpdateRow(update_id="u1", source="a", policy="delta", rows_fetched=5)])
    append_log(p, [UpdateRow(update_id="u1", source="b", policy="skip")])
    rows = list(csv.DictReader(p.open()))
    assert [r["source"] for r in rows] == ["a", "b"]
    assert rows[0]["rows_fetched"] == "5"


def test_parquet_stats_counts_only_the_named_source(tmp_path):
    for src, n in (("usgs", 3), ("nrcs", 7)):
        d = tmp_path / "timeseries" / f"source={src}" / "variable=x" / "year=2026"
        d.mkdir(parents=True)
        pd.DataFrame({"v": range(n)}).to_parquet(d / "part.parquet")
    rows, size = parquet_stats(tmp_path, "usgs")
    assert rows == 3 and size > 0


# ---- routine nights and the weekly revision
import json
from datetime import date
from types import SimpleNamespace

from typer.testing import CliRunner

from nmwater import cli
from nmwater.core.update import record_revision, revision_due


def test_a_revision_is_due_when_none_was_recorded_or_the_last_was_a_week_ago(tmp_path):
    p = tmp_path / "update_state.json"
    assert revision_due(p, date(2026, 11, 3))                      # nothing recorded
    record_revision(p, date(2026, 11, 3))
    assert json.loads(p.read_text()) == {"last_revision": "2026-11-03"}
    assert not revision_due(p, date(2026, 11, 3)) and not revision_due(p, date(2026, 11, 9))
    assert revision_due(p, date(2026, 11, 10))                     # seven days on
    p.write_text("not json")
    assert revision_due(p, date(2026, 11, 4))                      # an unreadable state means "do one"


def _update_ctx(tmp_path):
    pol = {"isc": {"kinds": ["levels"], "revise_kinds": ["levels", "chemistry"]}, "plain": {}}
    settings = SimpleNamespace(data_dir=tmp_path, duckdb_path=tmp_path / "none.duckdb",
                               source_config=lambda n: SimpleNamespace(options={"update": pol.get(n, {})}, enabled=True))
    ledger = SimpleNamespace(last_successful_fetch=lambda: {"isc": "2026-11-02T10:00:00Z", "plain": "2026-11-02T10:00:00Z"})
    return SimpleNamespace(settings=settings, ledger=ledger)


def _plan(monkeypatch, tmp_path, *args):
    monkeypatch.setattr(cli, "_ctx", lambda data_dir=None, run_id="adhoc": _update_ctx(tmp_path))
    monkeypatch.setattr(cli, "_resolve", lambda names: ["isc", "plain"])
    res = CliRunner().invoke(cli.app, ["update", "--dry-run", *args])
    assert res.exit_code == 0, res.output
    return res.output


def test_a_routine_night_looks_back_two_days_and_skips_the_revision_only_kinds(monkeypatch, tmp_path):
    record_revision(tmp_path / "update_state.json", date.today())
    out = _plan(monkeypatch, tmp_path)
    assert "routine run" in out and "2026-10-31" in out and "2026-10-03" not in out and "chemistry" not in out


def test_a_revision_night_looks_back_thirty_days_and_adds_the_revision_only_kinds(monkeypatch, tmp_path):
    out = _plan(monkeypatch, tmp_path)                              # nothing recorded: due
    assert "revision run" in out and "2026-10-03" in out and "chemistry" in out


def test_the_flags_force_either_kind_of_run(monkeypatch, tmp_path):
    record_revision(tmp_path / "update_state.json", date.today())
    assert "revision run" in _plan(monkeypatch, tmp_path, "--revise")
    (tmp_path / "update_state.json").unlink()
    assert "routine run" in _plan(monkeypatch, tmp_path, "--routine")


def test_a_dry_run_does_not_count_as_a_revision(monkeypatch, tmp_path):
    _plan(monkeypatch, tmp_path)
    assert not (tmp_path / "update_state.json").exists()
