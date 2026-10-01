"""Hash a site data bundle, ignoring what changes on every run (generated timestamps, producer SHA).

    uv run python scripts/bundle_hash.py dist/site-data [--rivers slug ...]   # prints one "hash  path" line per file and a total

Used to prove a refactor of the exporter left the published data unchanged: hash before, hash after, diff the lists.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

DROP = {"generated", "producer"}


def norm(o):
    if isinstance(o, dict):
        return {k: norm(v) for k, v in sorted(o.items()) if k not in DROP}
    if isinstance(o, list):
        return [norm(v) for v in o]
    return o


def file_hash(p: Path) -> str:
    if p.suffix == ".json":
        data = json.dumps(norm(json.loads(p.read_text())), sort_keys=True, separators=(",", ":")).encode()
    else:
        data = p.read_bytes()
    return hashlib.sha256(data).hexdigest()[:16]


def main(root: Path, only: set[str]) -> None:
    lines = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if only and rel.startswith("rivers/") and rel.split("/")[1] not in only:
            continue
        if rel == "manifest.json":
            continue                      # lists whatever rivers the run had; the per-river files are the comparison
        lines.append(f"{file_hash(p)}  {rel}")
    print("\n".join(lines))
    print(f"{hashlib.sha256(chr(10).join(lines).encode()).hexdigest()[:16]}  TOTAL ({len(lines)} files)")


if __name__ == "__main__":
    args = sys.argv[1:]
    only = set(args[args.index("--rivers") + 1:]) if "--rivers" in args else set()
    main(Path(args[0]), only)
