"""`nmwater fetch --jobs`: sources run side by side, each with its own run id, and the totals still add up."""

import threading
import time
from types import SimpleNamespace

from typer.testing import CliRunner

from nmwater import cli
from nmwater.sources import SOURCES, Context, FetchSummary, Source


class _Ledger:
    def __init__(self):
        self.n, self.finished, self.lock = 0, {}, threading.Lock()

    def start_run(self, source, command, args=None):
        with self.lock:
            self.n += 1
            return f"run-{source}-{self.n}"

    def finish_run(self, run_id, status="ok", notes=None):
        self.finished[run_id] = (status, notes)

    def close(self):
        pass


def _install(monkeypatch, names, seen, active, peak):
    ledger = _Ledger()
    settings = SimpleNamespace(source_config=lambda n: SimpleNamespace(enabled=True))
    ctx = Context(settings=settings, http=SimpleNamespace(close=lambda: None), ledger=ledger,
                  store=SimpleNamespace(has_observations_without_sites=lambda n: False), crosswalk=None)

    def make(name):
        class Fake(Source):
            def __init__(self, c):
                self.name_run = c.run_id
                self.ctx = c

            def check_tokens(self):
                pass

            def fetch(self, **kw):
                with threading.Lock():
                    active.append(name)
                    peak[0] = max(peak[0], len(active))
                time.sleep(0.15)
                seen[name] = self.name_run          # the run id this source was constructed with
                active.remove(name)
                return FetchSummary(name, n_rows=10)

        Fake.name = name
        return Fake

    for n in names:
        monkeypatch.setitem(SOURCES, n, make(n))
    monkeypatch.setattr(cli, "load_all", lambda: None)
    monkeypatch.setattr(cli, "_ctx", lambda data_dir=None, run_id="adhoc": ctx)
    return ledger


def test_jobs_run_sources_at_the_same_time_with_their_own_run_ids(monkeypatch):
    seen, active, peak = {}, [], [0]
    ledger = _install(monkeypatch, ["fa", "fb", "fc"], seen, active, peak)
    res = CliRunner().invoke(cli.app, ["fetch", "fa", "fb", "fc", "--jobs", "3"])
    assert res.exit_code == 0, res.output
    assert peak[0] >= 2                                          # they overlapped
    assert set(seen) == {"fa", "fb", "fc"}
    assert all(seen[n].startswith(f"run-{n}-") for n in seen)    # no source picked up another's run id
    assert len(ledger.finished) == 3 and all(v[0] == "ok" for v in ledger.finished.values())


def test_one_job_keeps_the_sequential_behaviour(monkeypatch):
    seen, active, peak = {}, [], [0]
    _install(monkeypatch, ["fa", "fb"], seen, active, peak)
    res = CliRunner().invoke(cli.app, ["fetch", "fa", "fb"])
    assert res.exit_code == 0, res.output
    assert peak[0] == 1


def test_a_failing_source_does_not_stop_the_others(monkeypatch):
    seen, active, peak = {}, [], [0]
    ledger = _install(monkeypatch, ["fa", "fb"], seen, active, peak)

    def boom(self, **kw):
        raise RuntimeError("provider down")
    monkeypatch.setattr(SOURCES["fa"], "fetch", boom)
    res = CliRunner().invoke(cli.app, ["fetch", "fa", "fb", "-j", "2"])
    assert res.exit_code == 0
    assert "fb" in seen
    assert sorted(v[0] for v in ledger.finished.values()) == ["error", "ok"]
