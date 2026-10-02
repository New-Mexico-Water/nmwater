"""Copy the data contract into the website repository: the JSON Schemas, a VERSION file, and (optionally) a fresh fixture bundle.

    uv run python scripts/sync_contract.py ../nmwater-web                 # schemas + VERSION only
    uv run python scripts/sync_contract.py ../nmwater-web --fixtures      # also rebuild fixtures/site-data from dist/site-data

The website validates every bundle it builds from against contract/schemas and CI fails if they drift from what nmwater's
docs/site-data/v2 says, so run this (and commit the result in the website repo) whenever a schema changes. nmwater owns the contract.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_RIVERS = ["rio-grande", "rio-tesuque", "pecos-river"]


def main(web: Path, fixtures: bool) -> int:
    schemas = sorted((ROOT / "docs" / "site-data" / "v2").glob("*.schema.json"))
    out = web / "contract"
    if (out / "schemas").exists():
        shutil.rmtree(out / "schemas")
    (out / "schemas").mkdir(parents=True)
    for s in schemas:
        shutil.copy2(s, out / "schemas" / s.name)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "docs/site-data"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
    version = {"schema_version": 2, "nmwater_git_sha": sha + ("+uncommitted" if dirty else ""),
               "schemas": {s.name: hashlib.sha256(s.read_bytes()).hexdigest()[:16] for s in schemas}}
    (out / "VERSION").write_text(json.dumps(version, indent=1) + "\n")
    print(f"{len(schemas)} schemas -> {out}")
    if fixtures:
        sys.path.insert(0, str(ROOT / "scripts"))
        import make_site_fixture

        return make_site_fixture.main(ROOT / "dist" / "site-data", web / "fixtures" / "site-data", FIXTURE_RIVERS)
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit(__doc__)
    sys.exit(main(Path(args[0]), "--fixtures" in sys.argv))
