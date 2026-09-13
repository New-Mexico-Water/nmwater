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
from .sources import SOURCES, Context, FetchSummary, SourceUnavailable, load_all

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
        except Exception as e:  # noqa: BLE001
            ctx.ledger.finish_run(run_id, "error", notes=str(e)[:500])
            console.print(f"[red]{name}: {e}[/red]")
            if verbose:
                raise


@app.command()
def fetch(
    sources: list[str] = typer.Argument(..., help="source names or 'all'"),
    since: Optional[str] = typer.Option(None, "--since", help="YYYY-MM-DD; only pull data after this date"),
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
    total = FetchSummary("all")
    for name in names:
        cls = SOURCES[name]
        if not ctx.settings.source_config(name).enabled:
            console.print(f"[yellow]{name}: disabled in config, skipping[/yellow]")
            continue
        run_id = ctx.ledger.start_run(name, "fetch", {"since": since, "limit": limit, "site": site})
        ctx.run_id = run_id
        src = cls(ctx)
        try:
            src.check_tokens()
            opts = {"kinds": list(kind)} if kind else {}
            s = src.fetch(since=since_d, limit=limit, site_ids=site, refresh=refresh, **opts)
            ctx.ledger.finish_run(run_id, "ok" if s.n_errors == 0 else "partial",
                                  notes=f"{s.n_rows} rows, {s.n_requests} req, {s.n_errors} errors")
            total.add(s)
            console.print(
                f"[green]{name}[/green]: {s.n_rows:,} rows, {s.n_requests} requests "
                f"({s.n_cached} cached), {s.n_errors} errors"
            )
            for note in s.notes[:20]:
                console.print(f"  - {note}")
        except SourceUnavailable as e:
            ctx.ledger.finish_run(run_id, "skipped", notes=str(e))
            console.print(f"[yellow]{e}[/yellow]")
        except Exception as e:  # noqa: BLE001
            ctx.ledger.finish_run(run_id, "error", notes=str(e)[:500])
            console.print(f"[red]{name}: {e}[/red]")
            if verbose:
                raise
    ctx.http.close()
    ctx.ledger.close()


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
