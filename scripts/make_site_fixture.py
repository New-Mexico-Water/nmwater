"""Cut a small fixture bundle (a few rivers and all the precipitation watersheds, no CSVs) out of a full site data bundle.

    uv run python scripts/make_site_fixture.py dist/site-data ../nmwater-web/fixtures/site-data rio-grande rio-tesuque pecos-river

The site repository commits the result so it builds and tests without the archive. CSV downloads are dropped
(they are the bulk of the size) and the summaries and manifest are rewritten to match, so the fixture still
conforms to docs/site-data/v1.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from nmwater.site.bundle import write_files_index
from nmwater.site.schema import validate_bundle


def main(src: Path, dst: Path, slugs: list[str]) -> int:
    man = json.loads((src / "manifest.json").read_text())
    keep = [e for e in man["rivers"] if e["slug"] in slugs]
    missing = sorted(set(slugs) - {e["slug"] for e in keep})
    if missing:
        print("not in the bundle:", ", ".join(missing))
        return 2
    if dst.exists():
        shutil.rmtree(dst)
    for e in keep:
        d = dst / e["path"]
        shutil.copytree(src / e["path"], d, ignore=shutil.ignore_patterns("*.csv"))
        s = json.loads((d / "summary.json").read_text())
        s["files"]["csv"] = []
        (d / "summary.json").write_text(json.dumps(s, separators=(",", ":"), ensure_ascii=False))
    if man.get("geo"):                                 # the shared state and county layers
        shutil.copytree(src / "geo", dst / "geo")
    if man.get("precipitation"):                       # every watershed, no CSVs; river links are cut down to the rivers kept
        pre = man["precipitation"]
        shutil.copytree(src / pre["path"], dst / pre["path"], ignore=shutil.ignore_patterns("*.csv"))
        have = {e["slug"] for e in keep}
        for f in sorted((dst / pre["path"]).rglob("*.json")):
            o = json.loads(f.read_text())
            if f.name == "index.json":
                for w in o["watersheds"]:
                    w["rivers"] = [r for r in w["rivers"] if r["slug"] in have]
                o["files"]["csv"] = []
            else:
                o["rivers"] = [r for r in o["rivers"] if r["slug"] in have]
            f.write_text(json.dumps(o, separators=(",", ":"), ensure_ascii=False))
        pre["files"]["csv"] = []
    man["rivers"] = keep
    rated = [e for e in keep if e["segments_rated"]]
    man["headlines"] = {"rivers": {"total": len(keep), "rated": len(rated),
                                   "with_segment_below_normal": sum(1 for e in rated if e["segments_below_normal"])}}
    (dst / "manifest.json").write_text(json.dumps(man, separators=(",", ":"), ensure_ascii=False))
    write_files_index(dst)
    problems = validate_bundle(dst)
    for p in problems:
        print("problem:", p)
    size = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())
    print(f"{len(keep)} rivers, {size / 1e6:.1f} MB in {dst}; {'does not conform' if problems else 'conforms to the schema'}")
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:]))
