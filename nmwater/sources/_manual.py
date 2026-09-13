"""Shared helper: record URLs that must be downloaded by hand (Cloudflare challenges etc.) in
docs/manual_downloads.md, and locate files the user has dropped into data/manual/<source>/."""

from __future__ import annotations

import fcntl
from pathlib import Path

from ..core.config import DOCS_DIR

DOC = DOCS_DIR / "manual_downloads.md"
HEADER = ("# Manual downloads\n\nThese resources could not be fetched automatically (bot protection or login). "
          "Download them in a browser and drop the files into `data/manual/<source>/` keeping the file name; "
          "the next `nmwater fetch <source>` ingests them.\n\n| source | file | url | note |\n|---|---|---|---|\n")


def note_manual(source: str, url: str, filename: str, note: str = "") -> None:
    DOCS_DIR.mkdir(exist_ok=True)
    line = f"| {source} | `{filename}` | {url} | {note} |\n"
    with open(DOC, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        existing = f.read()
        if not existing:
            f.write(HEADER)
        if url not in existing:
            f.write(line)
        fcntl.flock(f, fcntl.LOCK_UN)


def manual_dir(data_dir: Path, source: str) -> Path:
    d = data_dir / "manual" / source
    d.mkdir(parents=True, exist_ok=True)
    return d
