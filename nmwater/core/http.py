"""HTTP client with retry, per-source rate limiting, and an immutable raw archive.

Every response body is written to data/raw/<source>/<kind>/<hash>.<ext>.gz before
anything parses it, and a row goes in the ledger. Repeat requests are served from
the archive unless refresh=True.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .config import Settings, SourceConfig
from .ledger import FetchRecord, Ledger, now_iso

log = logging.getLogger("nmwater.http")

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class RetryableHTTP(Exception):
    def __init__(self, status: int, retry_after: float | None = None):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (RetryableHTTP, httpx.TransportError, httpx.TimeoutException))


def _wait_with_retry_after(base):
    def _wait(retry_state: RetryCallState) -> float:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        if isinstance(exc, RetryableHTTP) and exc.retry_after:
            return min(exc.retry_after, 300.0)
        return base(retry_state)

    return _wait


class RateLimiter:
    """Simple token-style limiter: at most `concurrency` in flight, spaced by min_interval."""

    def __init__(self, concurrency: int, min_interval_s: float):
        self._sem = threading.Semaphore(max(1, concurrency))
        self._min = max(0.0, min_interval_s)
        self._lock = threading.Lock()
        self._last = 0.0

    def __enter__(self):
        self._sem.acquire()
        with self._lock:
            wait = self._last + self._min - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        return self

    def __exit__(self, *exc):
        self._sem.release()


@dataclass
class RawArtifact:
    request_key: str
    source: str
    kind: str
    url: str
    params: dict[str, Any] | None
    path: Path
    sha256: str
    bytes: int
    http_status: int
    content_type: str | None
    fetched_at: str
    from_cache: bool

    def read_bytes(self) -> bytes:
        with gzip.open(self.path, "rb") as f:
            return f.read()

    def read_text(self, encoding: str = "utf-8", errors: str = "replace") -> str:
        return self.read_bytes().decode(encoding, errors=errors)

    def read_json(self) -> Any:
        return json.loads(self.read_bytes())


def request_key(method: str, url: str, params: dict[str, Any] | None, body: Any = None,
                extra: str | None = None) -> str:
    h = hashlib.sha256()
    h.update(method.upper().encode())
    h.update(b"\n")
    h.update(url.encode())
    h.update(b"\n")
    if params:
        h.update(json.dumps(params, sort_keys=True, default=str).encode())
    h.update(b"\n")
    if body is not None:
        h.update(body if isinstance(body, bytes) else json.dumps(body, sort_keys=True, default=str).encode())
    if extra:
        h.update(b"\n")
        h.update(extra.encode())
    return h.hexdigest()


_EXT_BY_TYPE = {
    "application/json": "json",
    "application/vnd.api+json": "json",
    "application/geo+json": "geojson",
    "text/csv": "csv",
    "text/plain": "txt",
    "text/html": "html",
    "application/xml": "xml",
    "text/xml": "xml",
    "application/zip": "zip",
    "application/x-netcdf": "nc",
    "application/octet-stream": "bin",
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
}


def _ext_for(content_type: str | None, url: str) -> str:
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if ct in _EXT_BY_TYPE:
            return _EXT_BY_TYPE[ct]
    tail = url.split("?")[0].rsplit(".", 1)
    if len(tail) == 2 and 1 <= len(tail[1]) <= 5 and tail[1].isalnum():
        return tail[1].lower()
    return "dat"


class Http:
    def __init__(self, settings: Settings, ledger: Ledger):
        self.settings = settings
        self.ledger = ledger
        self._limiters: dict[str, RateLimiter] = {}
        self._clients: dict[str, httpx.Client] = {}
        self._lock = threading.Lock()
        self.user_agent = f"nmwater/0.1 (New Mexico hydrologic archive; contact: {settings.contact})"

    def _cfg(self, source: str) -> SourceConfig:
        return self.settings.source_config(source)

    def limiter(self, source: str) -> RateLimiter:
        with self._lock:
            if source not in self._limiters:
                c = self._cfg(source)
                self._limiters[source] = RateLimiter(c.concurrency, c.min_interval_s)
            return self._limiters[source]

    def client(self, source: str) -> httpx.Client:
        with self._lock:
            if source not in self._clients:
                c = self._cfg(source)
                self._clients[source] = httpx.Client(
                    timeout=httpx.Timeout(c.timeout_s, connect=30.0),
                    headers={"User-Agent": self.user_agent},
                    follow_redirects=True,
                    http2=False,
                    verify=bool(c.options.get("verify_ssl", True)),
                )
            return self._clients[source]

    def raw_path(self, source: str, kind: str, key: str, ext: str) -> Path:
        d = self.settings.raw_dir / source / kind / key[:2]
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{key}.{ext}.gz"

    # ------------------------------------------------------------------
    def fetch(
        self,
        source: str,
        url: str,
        *,
        kind: str = "data",
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        method: str = "GET",
        body: Any = None,
        json_body: Any = None,
        data: dict[str, Any] | None = None,
        cookies: dict[str, str] | None = None,
        site_uid: str | None = None,
        variable: str | None = None,
        window: tuple[str, str] | None = None,
        run_id: str = "adhoc",
        refresh: bool = False,
        key_extra: str | None = None,
        ok_statuses: set[int] | None = None,
        encoding: str | None = None,
    ) -> RawArtifact:
        """Fetch a URL (or return the archived copy) and record it in the ledger."""
        key = request_key(method, url, params, body=json_body if json_body is not None else (body or data),
                          extra=key_extra)
        if not refresh:
            rec = self.ledger.get(key)
            if rec and rec.status == "ok" and rec.raw_path and Path(rec.raw_path).exists():
                return RawArtifact(
                    request_key=key, source=source, kind=kind, url=url, params=params,
                    path=Path(rec.raw_path), sha256=rec.sha256 or "", bytes=rec.bytes or 0,
                    http_status=rec.http_status or 200, content_type=rec.content_type,
                    fetched_at=rec.fetched_at, from_cache=True,
                )

        cfg = self._cfg(source)
        ok = ok_statuses or {200, 206}

        @retry(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(max(1, cfg.max_retries)),
            wait=_wait_with_retry_after(wait_exponential_jitter(initial=2, max=120)),
            reraise=True,
        )
        def _do() -> httpx.Response:
            with self.limiter(source):
                resp = self.client(source).request(
                    method, url, params=params, headers=headers, content=body, json=json_body,
                    data=data, cookies=cookies,
                )
            if resp.status_code in RETRYABLE_STATUS:
                ra = resp.headers.get("Retry-After")
                try:
                    ra_f = float(ra) if ra else None
                except ValueError:
                    ra_f = None
                log.warning("%s %s -> %s (retrying)", source, url, resp.status_code)
                raise RetryableHTTP(resp.status_code, ra_f)
            return resp

        fetched_at = now_iso()
        try:
            resp = _do()
        except Exception as e:
            self.ledger.record(FetchRecord(
                request_key=key, run_id=run_id, source=source, kind=kind, url=url, params=params,
                site_uid=site_uid, variable=variable,
                window_start=window[0] if window else None, window_end=window[1] if window else None,
                status="error", http_status=getattr(e, "status", None), sha256=None, bytes=None,
                raw_path=None, content_type=None, fetched_at=fetched_at, error=str(e)[:500],
            ))
            raise

        content = resp.content
        ctype = resp.headers.get("Content-Type")
        ext = _ext_for(ctype, url)
        sha = hashlib.sha256(content).hexdigest()
        status = "ok" if resp.status_code in ok else "error"
        path = self.raw_path(source, kind, key, ext)
        with gzip.open(path, "wb", compresslevel=6) as f:
            f.write(content)
        meta = {
            "request_key": key, "source": source, "kind": kind, "method": method, "url": url,
            "params": params, "headers": {k: v for k, v in (headers or {}).items() if k.lower() != "authorization"},
            "http_status": resp.status_code, "content_type": ctype, "bytes": len(content), "sha256": sha,
            "fetched_at": fetched_at, "site_uid": site_uid, "variable": variable, "window": window,
            "final_url": str(resp.url),
        }
        with open(path.with_suffix(".meta.json"), "w") as f:
            json.dump(meta, f, default=str)
        self.ledger.record(FetchRecord(
            request_key=key, run_id=run_id, source=source, kind=kind, url=url, params=params,
            site_uid=site_uid, variable=variable,
            window_start=window[0] if window else None, window_end=window[1] if window else None,
            status=status, http_status=resp.status_code, sha256=sha, bytes=len(content),
            raw_path=str(path), content_type=ctype, fetched_at=fetched_at,
            error=None if status == "ok" else content[:300].decode("utf-8", "replace"),
        ))
        if status != "ok":
            raise httpx.HTTPStatusError(
                f"{source}: HTTP {resp.status_code} for {url}", request=resp.request, response=resp
            )
        return RawArtifact(
            request_key=key, source=source, kind=kind, url=url, params=params, path=path, sha256=sha,
            bytes=len(content), http_status=resp.status_code, content_type=ctype, fetched_at=fetched_at,
            from_cache=False,
        )

    def close(self) -> None:
        for c in self._clients.values():
            c.close()
