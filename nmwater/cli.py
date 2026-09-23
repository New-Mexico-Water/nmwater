"""nmwater command-line interface."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .catalog.crosswalk import Crosswalk
from .core.config import Settings
from .core.http import Http
from .core.ledger import Ledger
from .core.store import Store
from .sources import SOURCES, Context, FetchSummary, Source, SourceUnavailable, load_all

app = typer.Typer(help="New Mexico hydrologic data archive", no_args_is_help=True)
catalog_app = typer.Typer(help="Catalog and DuckDB build")
app.add_typer(catalog_app, name="catalog")
console = Console()


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, show_path=False, rich_tracebacks=False)],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _ctx(data_dir: Optional[Path] = None, run_id: str = "adhoc") -> Context:
    settings = Settings.load(data_dir)
    settings.ensure_dirs()
    ledger = Ledger(settings.ledger_path)
    http = Http(settings, ledger)
    store = Store(settings.parquet_dir)
    xw = Crosswalk()
    return Context(settings=settings, http=http, ledger=ledger, store=store, crosswalk=xw, run_id=run_id)


def _resolve(names: list[str]) -> list[str]:
    load_all()
    if not names or names == ["all"]:
        return sorted(SOURCES)
    bad = [n for n in names if n not in SOURCES]
    if bad:
        raise typer.BadParameter(f"unknown source(s): {', '.join(bad)}. Known: {', '.join(sorted(SOURCES))}")
    return names


@app.command("list")
def list_sources():
    """List registered sources."""
    load_all()
    settings = Settings.load()
    t = Table("source", "agency", "tokens", "enabled", "description")
    for name in sorted(SOURCES):
        cls = SOURCES[name]
        cfg = settings.source_config(name)
        toks = ", ".join(cls.requires_tokens) or "-"
        missing = [x for x in cls.requires_tokens if not settings.tokens.get(x)]
        if missing:
            toks = f"[red]{toks}[/red]"
        t.add_row(name, cls.agency, toks, "yes" if cfg.enabled else "no", cls.description)
    console.print(t)


@app.command()
def discover(
    sources: list[str] = typer.Argument(..., help="source names or 'all'"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
    verbose: bool = typer.Option(False, "-v"),
):
    """Discover sites for the given source(s) and write the sites table."""
    _setup_logging(verbose)
    names = _resolve(sources)
    ctx = _ctx(data_dir)
    for name in names:
        cls = SOURCES[name]
        if not ctx.settings.source_config(name).enabled:
            console.print(f"[yellow]{name}: disabled in config, skipping[/yellow]")
            continue
        run_id = ctx.ledger.start_run(name, "discover")
        ctx.run_id = run_id
        src = cls(ctx)
        try:
            src.check_tokens()
            df = src.discover()
            n = ctx.store.write_sites(df, name)
            ctx.ledger.finish_run(run_id, "ok", notes=f"{n} sites")
            console.print(f"[green]{name}[/green]: {n} sites")
        except SourceUnavailable as e:
            ctx.ledger.finish_run(run_id, "skipped", notes=str(e))
            console.print(f"[yellow]{e}[/yellow]")
        except Exception as e:
            ctx.ledger.finish_run(run_id, "error", notes=str(e)[:500])
            console.print(f"[red]{name}: {e}[/red]")
            if verbose:
                raise


@app.command()
def fetch(
    sources: list[str] = typer.Argument(..., help="source names or 'all'"),
    since: Optional[str] = typer.Option(None, "--since", help="YYYY-MM-DD; only pull data after this date"),
    until: Optional[str] = typer.Option(None, "--until", help="YYYY-MM-DD; stop at this date (use with --since to pull a range)"),
    limit: Optional[int] = typer.Option(None, "--limit", help="max sites (for testing)"),
    site: Optional[list[str]] = typer.Option(None, "--site", help="native site id(s) to restrict to"),
    refresh: bool = typer.Option(False, "--refresh", help="ignore archived responses"),
    kind: Optional[list[str]] = typer.Option(None, "--kind", help="restrict to data kind(s) the source supports"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
    verbose: bool = typer.Option(False, "-v"),
):
    """Download raw data (archived + ledgered), normalize, and write observations."""
    _setup_logging(verbose)
    names = _resolve(sources)
    ctx = _ctx(data_dir)
    since_d = date.fromisoformat(since) if since else None
    until_d = date.fromisoformat(until) if until else None
    if since_d and until_d and until_d < since_d:
        raise typer.BadParameter("--until is before --since")
    total = FetchSummary("all")
    for name in names:
        _fetch_one(ctx, name, since_d, until_d, limit, site, refresh, list(kind) if kind else None, verbose, total)
    ctx.http.close()
    ctx.ledger.close()


def _fetch_one(ctx: Context, name: str, since_d, until_d, limit, site, refresh: bool,
               kinds: list[str] | None, verbose: bool, total: FetchSummary | None = None
               ) -> tuple[str, FetchSummary | None, str | None, str]:
    """Fetch one source through the ledger. Returns (status, summary, run_id, note)."""
    cls = SOURCES[name]
    if not ctx.settings.source_config(name).enabled:
        console.print(f"[yellow]{name}: disabled in config, skipping[/yellow]")
        return "skipped", None, None, "disabled in config"
    run_id = ctx.ledger.start_run(name, "fetch", {"since": since_d.isoformat() if since_d else None,
                                                 "until": until_d.isoformat() if until_d else None,
                                                 "limit": limit, "site": site, "kinds": kinds})
    ctx.run_id = run_id
    src = cls(ctx)
    try:
        src.check_tokens()
        opts = {"kinds": list(kinds)} if kinds else {}
        if until_d:
            opts["until"] = until_d
        s = src.fetch(since=since_d, limit=limit, site_ids=site, refresh=refresh, **opts)
        status = "ok" if s.n_errors == 0 else "partial"
        ctx.ledger.finish_run(run_id, status, notes=f"{s.n_rows} rows, {s.n_requests} req, {s.n_errors} errors")
        if total is not None:
            total.add(s)
        console.print(f"[green]{name}[/green]: {s.n_rows:,} rows, {s.n_requests} requests "
                      f"({s.n_cached} cached), {s.n_errors} errors")
        for note in s.notes[:20]:
            console.print(f"  - {note}")
        return status, s, run_id, "; ".join(s.notes[:3])
    except SourceUnavailable as e:
        ctx.ledger.finish_run(run_id, "skipped", notes=str(e))
        console.print(f"[yellow]{e}[/yellow]")
        return "skipped", None, run_id, str(e)[:300]
    except Exception as e:
        ctx.ledger.finish_run(run_id, "error", notes=str(e)[:500])
        console.print(f"[red]{name}: {e}[/red]")
        if verbose:
            raise
        return "error", None, run_id, str(e)[:300]


@app.command()
def reprocess(
    sources: list[str] = typer.Argument(...),
    kind: Optional[str] = typer.Option(None, "--kind", help="only this raw kind"),
    replace: bool = typer.Option(False, "--replace", help="delete the source's observation partitions first"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
    verbose: bool = typer.Option(False, "-v"),
):
    """Re-normalize observations from the raw archive (no network)."""
    _setup_logging(verbose)
    names = _resolve(sources)
    ctx = _ctx(data_dir)
    for name in names:
        src_cls = SOURCES[name]
        if src_cls.normalize is Source.normalize:
            console.print(
                f"[yellow]{name}: does not implement normalize(); reprocess cannot rebuild its rows. "
                f"Re-run `nmwater fetch {name}` instead, which re-reads the raw archive.[/yellow]"
            )
            continue
        if replace:
            import shutil

            d = ctx.settings.parquet_dir / "timeseries" / f"source={name}"
            if d.exists():
                shutil.rmtree(d)
                console.print(f"removed {d}")
        run_id = ctx.ledger.start_run(name, "reprocess", {"kind": kind, "replace": replace})
        ctx.run_id = run_id
        n = SOURCES[name](ctx).reprocess(kind=kind) if kind else SOURCES[name](ctx).reprocess()
        ctx.ledger.finish_run(run_id, "ok", notes=f"{n} rows")
        console.print(f"{name}: {n:,} rows re-written")


@app.command()
def compact(
    source: Optional[str] = typer.Argument(None),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
):
    """Merge part files per partition and drop duplicate observations."""
    _setup_logging(False)
    ctx = _ctx(data_dir)
    res = ctx.store.compact(source)
    removed = sum(res.values())
    console.print(f"compacted {len(res)} partitions, removed {removed:,} duplicate rows")


@app.command()
def update(
    sources: list[str] = typer.Argument(None, help="sources to update (default: all with an update policy)"),
    margin_days: int = typer.Option(30, "--margin-days",
                                    help="start this many days before each source's last successful fetch, "
                                         "to pick up revisions of provisional data"),
    since: Optional[str] = typer.Option(None, "--since", help="YYYY-MM-DD for every source, overriding the ledger"),
    catalog: bool = typer.Option(True, "--catalog/--no-catalog", help="rebuild the DuckDB catalog afterwards"),
    dry_run: bool = typer.Option(False, "--dry-run", help="show the plan and exit"),
    log: Path = typer.Option(Path("reports/update_log.csv"), "--log", help="append one row per source here"),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
    verbose: bool = typer.Option(False, "-v"),
):
    """Fetch everything new since each source's last successful fetch, compact, rebuild the catalog,
    and log rows, bytes and time per source to reports/update_log.csv.

    Per-source policy lives under `update:` in config/sources.yaml: `skip: true` with a reason for
    reference layers and blocked sources, `kinds:` to choose what a delta covers. Sources that have
    never completed a fetch are skipped; run them once by hand with `nmwater fetch`."""
    import time
    from datetime import UTC, datetime, timedelta

    from .catalog.build import build as build_catalog
    from .core.update import UpdateRow, append_log, dir_bytes, parquet_stats

    _setup_logging(verbose)
    names = _resolve(sources or ["all"])
    ctx = _ctx(data_dir)
    st = ctx.settings
    uid = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    fixed = date.fromisoformat(since) if since else None
    last = ctx.ledger.last_successful_fetch()
    # What each source actually holds. A fetch limited to some kinds (e.g. usace_cwms ratings) is a
    # successful run that says nothing about the time series, so the delta starts from the earlier
    # of the last run and the newest observation. Coverage is only trusted within 90 days of the
    # run date: some feeds publish with a long lag (NOAA ISD year files, the state SensorThings
    # groundwater server) and should not drag every update back a year.
    cover: dict[str, date] = {}
    if st.duckdb_path.exists():
        import duckdb
        try:
            con = duckdb.connect(str(st.duckdb_path), read_only=True)
            con.execute("SET TimeZone='UTC'")
            for src_name, d in con.execute(
                    "select source, max(last_datetime)::date from site_variables "
                    "where last_datetime <= now() + interval 2 day group by 1").fetchall():
                cover[src_name] = d
            con.close()
        except Exception as e:
            console.print(f"[yellow]catalog coverage unavailable ({e}); using the ledger only[/yellow]")

    plan = []
    for name in names:
        pol = st.source_config(name).options.get("update") or {}
        if pol.get("skip"):
            plan.append((name, "skip", None, None, pol.get("reason", "skipped by policy")))
            continue
        if fixed:
            plan.append((name, "delta", fixed, pol.get("kinds"), ""))
            continue
        if name not in last:
            plan.append((name, "skip", None, None, "never completed a fetch; run `nmwater fetch` once by hand"))
            continue
        ran = date.fromisoformat(last[name][:10])
        cov = cover.get(name)
        basis = f"last fetch {ran}"
        if cov and cov < ran and (ran - cov).days <= 90:
            ran, basis = cov, f"newest data {cov}"
        plan.append((name, "delta", ran - timedelta(days=margin_days), pol.get("kinds"), basis))

    t = Table("source", "policy", "since", "kinds", "note")
    for name, pol, sd, kinds, note in plan:
        t.add_row(name, pol, sd.isoformat() if sd else "", ",".join(kinds or []), note)
    console.print(t)
    if dry_run:
        return

    rows: list[UpdateRow] = []
    for name, pol, sd, kinds, note in plan:
        r = UpdateRow(update_id=uid, source=name, policy=pol, since=sd.isoformat() if sd else "", notes=note)
        if pol == "skip":
            r.status = "skipped"
            rows.append(r)
            append_log(log, [r])
            continue
        raw_dir, grid_dir = st.raw_dir / name, st.grids_dir / name
        r.raw_bytes_before, r.grid_bytes_before = dir_bytes(raw_dir), dir_bytes(grid_dir)
        r.rows_before, r.parquet_bytes_before = parquet_stats(st.parquet_dir, name)
        console.rule(f"{name} since {r.since}")
        t0 = time.monotonic()
        status, summ, run_id, fnote = _fetch_one(ctx, name, sd, None, None, None, False, kinds, verbose)
        r.fetch_seconds = round(time.monotonic() - t0, 1)
        r.status, r.notes = status, fnote or note
        if summ:
            r.n_requests, r.n_cached, r.n_errors, r.rows_fetched = (summ.n_requests, summ.n_cached,
                                                                    summ.n_errors, summ.n_rows)
        if run_id:
            r.bytes_downloaded = ctx.ledger.bytes_for_run(run_id)
        t1 = time.monotonic()
        try:
            r.duplicates_removed = int(sum(ctx.store.compact(name).values()))
        except Exception as e:
            r.notes = (r.notes + f"; compact failed: {e}")[:300]
        r.compact_seconds = round(time.monotonic() - t1, 1)
        r.raw_bytes_after, r.grid_bytes_after = dir_bytes(raw_dir), dir_bytes(grid_dir)
        r.rows_after, r.parquet_bytes_after = parquet_stats(st.parquet_dir, name)
        r.net_new_rows = r.rows_after - r.rows_before
        r.disk_bytes_delta = ((r.raw_bytes_after - r.raw_bytes_before) + (r.grid_bytes_after - r.grid_bytes_before)
                              + (r.parquet_bytes_after - r.parquet_bytes_before))
        console.print(f"  {r.fetch_seconds:,.0f} s fetch, {r.compact_seconds:,.0f} s compact, "
                      f"{_human(r.bytes_downloaded)} downloaded, {r.net_new_rows:+,} net rows, "
                      f"{_human(r.disk_bytes_delta)} on disk")
        rows.append(r)
        append_log(log, [r])        # append as we go, so an interrupted run still leaves a record

    extra = []
    if catalog:
        console.rule("catalog build")
        t0 = time.monotonic()
        cstat = "ok"
        try:
            build_catalog(st)
        except Exception as e:
            cstat = f"error: {e}"[:200]
        extra.append(UpdateRow(update_id=uid, source="_catalog_build", policy="summary", status=cstat,
                               fetch_seconds=round(time.monotonic() - t0, 1),
                               parquet_bytes_after=dir_bytes(st.duckdb_path.parent)))
    done = [r for r in rows if r.policy == "delta"]
    tot = UpdateRow(update_id=uid, source="_total", policy="summary",
                    status=f"{sum(r.status == 'ok' for r in done)} ok, {sum(r.status == 'partial' for r in done)} "
                           f"partial, {sum(r.status == 'error' for r in done)} error, "
                           f"{sum(r.policy == 'skip' for r in rows)} skipped")
    for f in ("fetch_seconds", "compact_seconds", "n_requests", "n_cached", "n_errors", "rows_fetched",
              "bytes_downloaded", "duplicates_removed", "rows_before", "rows_after", "net_new_rows",
              "raw_bytes_before", "raw_bytes_after", "parquet_bytes_before", "parquet_bytes_after",
              "grid_bytes_before", "grid_bytes_after", "disk_bytes_delta"):
        setattr(tot, f, round(sum(getattr(r, f) for r in done), 1))
    tot.fetch_seconds += sum(r.fetch_seconds for r in extra)
    append_log(log, [*extra, tot])

    t = Table("source", "status", "time", "requests", "downloaded", "rows fetched", "net new rows", "disk")
    for r in [*done, *extra, tot]:
        t.add_row(r.source, r.status, f"{r.fetch_seconds + r.compact_seconds:,.0f} s", f"{r.n_requests:,}",
                  _human(r.bytes_downloaded), f"{r.rows_fetched:,}", f"{r.net_new_rows:+,}", _human(r.disk_bytes_delta))
    console.print(t)
    console.print(f"logged to {log}")
    ctx.http.close()
    ctx.ledger.close()


@app.command()
def status(
    source: Optional[str] = typer.Argument(None),
    data_dir: Optional[Path] = typer.Option(None, "--data-dir"),
):
    """Ledger summary: requests, bytes, rows per source."""
    ctx = _ctx(data_dir)
    t = Table("source", "status", "requests", "bytes", "rows", "first", "last")
    tot_b = 0
    for r in ctx.ledger.stats(source):
        tot_b += r["bytes"] or 0
        t.add_row(r["source"], r["status"], f"{r['n']:,}", _human(r["bytes"]), f"{r['rows']:,}",
                  (r["first"] or "")[:16], (r["last"] or "")[:16])
    console.print(t)
    console.print(f"total raw bytes (uncompressed as received): {_human(tot_b)}")
    du = _dir_size(ctx.settings.data_dir)
    console.print(f"data dir on disk: {_human(du)}  ({ctx.settings.data_dir})")
    t2 = Table("run", "source", "cmd", "status", "started", "requests", "rows", "notes")
    for r in ctx.ledger.runs(source, limit=15):
        t2.add_row(r["run_id"], r["source"], r["command"], r["status"] or "", (r["started_at"] or "")[:16],
                   str(r["n_requests"]), f"{r['n_rows'] or 0:,}", (r["notes"] or "")[:60])
    console.print(t2)


@catalog_app.command("build")
def catalog_build(data_dir: Optional[Path] = typer.Option(None, "--data-dir"), verbose: bool = typer.Option(False, "-v")):
    """Build the DuckDB catalog and export the data dictionary."""
    _setup_logging(verbose)
    from .catalog.build import build

    settings = Settings.load(data_dir)
    p = build(settings)
    console.print(f"catalog written to {p}")


@catalog_app.command("check")
def catalog_check():
    """Validate variables.yaml and crosswalk.csv."""
    from .catalog.variables import VariableRegistry

    reg = VariableRegistry()
    problems = reg.validate()
    xw = Crosswalk(registry=reg)
    bad = [k for k, e in xw.entries.items() if e.variable and e.variable not in reg]
    n_unmapped = sum(1 for e in xw.entries.values() if not e.variable)
    console.print(f"{n_unmapped} crosswalk entries are documented as intentionally unmapped")
    for p in problems:
        console.print(f"[red]{p}[/red]")
    for k in bad:
        console.print(f"[red]crosswalk {k} -> unknown variable[/red]")
    console.print(f"{len(reg.vars)} variables, {len(xw.entries)} crosswalk entries, {len(problems) + len(bad)} problems")


@app.command()
def report(data_dir: Optional[Path] = typer.Option(None, "--data-dir")):
    """Write docs/qa/report.md: coverage, landmark checks, hygiene (requires catalog build)."""
    from .catalog.qa import run as qa_run

    p = qa_run(Settings.load(data_dir))
    console.print(p.read_text())
    console.print(f"[green]written to {p}[/green]")


@app.command()
def query(sql: str, data_dir: Optional[Path] = typer.Option(None, "--data-dir")):
    """Run a SQL query against the DuckDB catalog."""
    import duckdb

    settings = Settings.load(data_dir)
    con = duckdb.connect(str(settings.duckdb_path), read_only=True)
    con.execute("SET TimeZone='UTC'")
    df = con.execute(sql).fetchdf()
    console.print(df.to_string(max_rows=200))


def _human(n: float | None) -> str:
    n = float(n or 0)
    if n < 0:
        return "-" + _human(-n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


if __name__ == "__main__":
    app()
