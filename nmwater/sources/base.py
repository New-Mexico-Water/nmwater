"""Source plug-in interface and registry.

A Source knows how to (1) discover its sites, (2) fetch raw data through the shared
HTTP client (which archives and ledgers every response), and (3) normalize raw
artifacts into the canonical observations schema. `reprocess()` re-normalizes from
the raw archive without network access.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from ..catalog.crosswalk import Crosswalk
from ..core.config import Settings, SourceConfig
from ..core.http import Http, RawArtifact
from ..core.ledger import Ledger
from ..core.store import Store

log = logging.getLogger("nmwater.source")


@dataclass
class Context:
    settings: Settings
    http: Http
    ledger: Ledger
    store: Store
    crosswalk: Crosswalk
    run_id: str = "adhoc"


@dataclass
class FetchSummary:
    source: str
    n_requests: int = 0
    n_cached: int = 0
    n_rows: int = 0
    n_errors: int = 0
    notes: list[str] = field(default_factory=list)

    def add(self, other: FetchSummary) -> None:
        self.n_requests += other.n_requests
        self.n_cached += other.n_cached
        self.n_rows += other.n_rows
        self.n_errors += other.n_errors
        self.notes.extend(other.notes)


class SourceUnavailable(Exception):
    """Raised when a required token or dependency is missing."""


class Source:
    name: str = "base"
    description: str = ""
    agency: str = ""
    requires_tokens: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ("sites", "data")

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.settings = ctx.settings
        self.http = ctx.http
        self.ledger = ctx.ledger
        self.store = ctx.store
        self.xw = ctx.crosswalk
        self.scope = ctx.settings.scope
        self.cfg: SourceConfig = ctx.settings.source_config(self.name)
        self.run_id = ctx.run_id

    # -- helpers ------------------------------------------------------------
    def check_tokens(self) -> None:
        missing = [t for t in self.requires_tokens if not self.settings.tokens.get(t)]
        if missing:
            raise SourceUnavailable(f"{self.name}: missing token(s) {', '.join(missing)} (set in .env)")

    def opt(self, key: str, default: Any = None) -> Any:
        return self.cfg.options.get(key, default)

    def get(self, url: str, **kw) -> RawArtifact:
        kw.setdefault("run_id", self.run_id)
        return self.http.fetch(self.name, url, **kw)

    def uid(self, native_id: str) -> str:
        return f"{self.name}:{native_id}"

    def already_written(self, art: RawArtifact) -> bool:
        """True if this artifact came from the archive and its rows were already normalized."""
        if not art.from_cache:
            return False
        rec = self.ledger.get(art.request_key)
        return rec is not None and rec.n_rows is not None

    def sites(self) -> pd.DataFrame:
        """Previously discovered sites for this source (empty frame if none)."""
        return self.store.read_sites(self.name)

    def parallel(self, fn: Callable[[Any], Any], items: Iterable[Any], workers: int | None = None,
                 desc: str = "") -> list[Any]:
        """Run fn over items with a thread pool sized by the source config; log errors, keep going."""
        items = list(items)
        workers = workers or max(1, self.cfg.concurrency)
        results: list[Any] = []
        errors = 0
        if not items:
            return results
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(fn, it): it for it in items}
            done = 0
            for fut in as_completed(futs):
                done += 1
                try:
                    results.append(fut.result())
                except Exception as e:
                    errors += 1
                    log.warning("%s %s failed for %r: %s", self.name, desc, futs[fut], str(e)[:300])
                if done % 50 == 0 or done == len(items):
                    log.info("%s %s: %d/%d done (%d errors)", self.name, desc, done, len(items), errors)
        return results

    def write_obs(self, df: pd.DataFrame, tag: str | None = None) -> int:
        return self.store.write_observations(df, self.name, run_id=self.run_id, tag=tag)

    # -- interface ----------------------------------------------------------
    def discover(self) -> pd.DataFrame:
        """Return a sites frame (SITES_SCHEMA columns; native_id required)."""
        raise NotImplementedError

    def fetch(self, since: date | None = None, limit: int | None = None,
              site_ids: list[str] | None = None, refresh: bool = False, **opts) -> FetchSummary:
        """Download (or reuse archived) raw data, normalize, and write observations."""
        raise NotImplementedError

    def normalize(self, artifact: RawArtifact) -> pd.DataFrame | None:
        """Turn one raw artifact into an observations frame. Optional; used by reprocess()."""
        return None

    def reprocess(self, kind: str | None = "data") -> int:
        """Re-normalize everything in the raw archive for this source (offline)."""
        n = 0
        for rec in self.ledger.iter_fetches(self.name, kind=kind):
            if not rec.raw_path:
                continue
            from pathlib import Path

            art = RawArtifact(
                request_key=rec.request_key, source=self.name, kind=rec.kind or "data", url=rec.url,
                params=rec.params, path=Path(rec.raw_path), sha256=rec.sha256 or "", bytes=rec.bytes or 0,
                http_status=rec.http_status or 200, content_type=rec.content_type, fetched_at=rec.fetched_at,
                from_cache=True,
            )
            try:
                df = self.normalize(art)
            except Exception as e:
                log.warning("%s reprocess failed for %s: %s", self.name, rec.url, e)
                continue
            if df is not None and len(df):
                n += self.write_obs(df)
        return n


# Registry ---------------------------------------------------------------
SOURCES: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    SOURCES[cls.name] = cls
    return cls
