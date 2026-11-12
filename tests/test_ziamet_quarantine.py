"""ZiaMet answers some station/product pairs with HTTP 500 every time; routine runs leave those alone for a week (#40)."""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd

from nmwater.sources.ziamet import QUARANTINE_DAYS, ZiaMet

NOW = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)


class _Ledger:
    def __init__(self, last):
        self.last = last                       # {(site_uid, product): {...}}

    def last_attempt(self, source, site_uid, variable, kind=None):
        return self.last.get((site_uid, variable))


def _att(days_ago, status="error", http=500):
    return {"status": status, "http_status": http, "fetched_at": (NOW - timedelta(days=days_ago)).isoformat()}


def _src(last, routine_requests=None):
    s = ZiaMet.__new__(ZiaMet)
    s.name = "ziamet"
    s.ledger = _Ledger(last)
    s.cfg = SimpleNamespace(options={"daily_products": ["sfwx", "dret"], "start_year": 2026}, concurrency=1)
    s.opt = lambda k, d=None: s.cfg.options.get(k, d)
    s.uid = lambda sid: f"ziamet:{sid}"
    return s


def test_only_a_recent_http_500_puts_a_pair_in_quarantine():
    s = _src({("ziamet:a", "dret"): _att(3), ("ziamet:b", "dret"): _att(QUARANTINE_DAYS + 1), ("ziamet:c", "dret"): _att(1, "ok", 200),
              ("ziamet:d", "dret"): _att(1, "error", 503)})
    assert s._quarantined("a", "dret", NOW)
    assert not s._quarantined("b", "dret", NOW)           # a week on: try again
    assert not s._quarantined("c", "dret", NOW)           # it worked last time
    assert not s._quarantined("d", "dret", NOW)           # a 503 is the host being down, not the server's answer for this series
    assert not s._quarantined("e", "dret", NOW)           # never requested


def _run(monkeypatch, routine, refresh=False):
    s = _src({("ziamet:a", "dret"): _att(0.1)})           # failed a few hours ago
    sent = []
    monkeypatch.setattr(s, "_quarantined", lambda sid, prod, now=None: (sid, prod) == ("a", "dret"))
    s._csrf = lambda path, sid=None, prod=None: "tok"
    s.hosts, s._host_idx = ["https://h"], 0

    def fake_get(path, **kw):
        sent.append(path)
        return SimpleNamespace(from_cache=False, read_text=lambda: "", request_key="k")
    s._get = fake_get
    s.already_written = lambda art: False
    s._parse_daily = lambda text, sid, prod: None
    s.ledger = SimpleNamespace(set_rows=lambda *a: None)
    s.write_obs = lambda *a, **k: 0
    summ = s._fetch_daily(["a", "b"], date(2026, 11, 8), refresh, routine=routine)
    return sent, summ


def test_a_routine_run_does_not_request_a_quarantined_pair_and_says_so(monkeypatch):
    sent, summ = _run(monkeypatch, routine=True)
    assert not any("/dret/dly/a/" in p for p in sent) and any("/dret/dly/b/" in p for p in sent) and any("/sfwx/dly/a/" in p for p in sent)
    assert summ.n_errors == 0 and any("not requested" in n for n in summ.notes)


def test_a_revision_run_or_a_manual_fetch_tries_every_pair(monkeypatch):
    sent, summ = _run(monkeypatch, routine=False)
    assert any("/dret/dly/a/" in p for p in sent) and not summ.notes
    sent, _ = _run(monkeypatch, routine=True, refresh=True)
    assert any("/dret/dly/a/" in p for p in sent)


def test_the_ledger_finds_the_latest_attempt_of_any_kind_so_a_failing_form_page_counts(tmp_path):
    from nmwater.core.ledger import FetchRecord, Ledger
    led = Ledger(tmp_path / "l.sqlite")
    def rec(key, kind, status, http, at):
        led.record(FetchRecord(request_key=key, run_id="r", source="ziamet", kind=kind, url="u", params=None, site_uid="ziamet:a", variable="dret", window_start=None,
                               window_end=None, status=status, http_status=http, sha256=None, bytes=None, raw_path=None, content_type=None, fetched_at=at, error=None))
    rec("k1", "daily", "ok", 200, "2026-11-10T09:00:00+00:00")
    rec("k2", "form", "error", 500, "2026-11-11T09:00:00+00:00")         # the data request was never made: the form page failed
    assert led.last_attempt("ziamet", "ziamet:a", "dret")["http_status"] == 500
    assert led.last_attempt("ziamet", "ziamet:a", "dret", "daily")["status"] == "ok"
    assert led.last_attempt("ziamet", "ziamet:zz", "dret") is None
