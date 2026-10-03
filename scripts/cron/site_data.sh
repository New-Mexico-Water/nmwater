#!/usr/bin/env bash
# Build the website's data bundle (dist/site-data) from the archive, and optionally publish it. For cron.
#
#   scripts/cron/site_data.sh                      export the bundle from the current catalog
#   scripts/cron/site_data.sh --update             refresh the archive first (`nmwater update`), then export
#   scripts/cron/site_data.sh --publish            after a good export, upload it and tell the site to rebuild
#                                                  (scripts/cron/publish_site_data.sh; needs R2 settings in .env)
#
# Example crontab (daily at 05:30):
#   30 5 * * *  /home/vance/projects/water_newmexico/scripts/cron/site_data.sh --update --publish
# On this machine there is no cron daemon: scripts/cron/systemd/ has a systemd user timer that runs `--update` nightly (see docs/usage.md).
#
# - One run at a time (the lock is shared with other cron jobs); a second run exits 0.
# - Log: data/logs/site_data.log. Exit: 0 success, 1 a river, the update, validation or the upload failed, 2 bad arguments.
# - The bundle is swapped in only when complete and checked against docs/site-data/v2, so a failed run leaves the
#   previous bundle in place, and nothing is published unless the export and the check passed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

UPDATE=0
PUBLISH=0
for arg in "$@"; do
  case "$arg" in
    --update) UPDATE=1 ;;
    --publish) PUBLISH=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
UV="${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}"

DATA_DIR="${NMWATER_DATA_DIR:-$ROOT/data}"
mkdir -p "$DATA_DIR/logs" "$DATA_DIR/locks"
LOG="$DATA_DIR/logs/site_data.log"
exec >>"$LOG" 2>&1

exec 9>"$DATA_DIR/locks/nmwater-cron.lock"
if ! flock -n 9; then
  echo "$(date -Is) another run holds the lock; skipping"
  exit 0
fi

echo "$(date -Is) start (update=$UPDATE publish=$PUBLISH)"
status=0
if [ "$UPDATE" = 1 ]; then
  if ! "$UV" run nmwater update; then
    echo "$(date -Is) update failed; exporting from the existing catalog"
    status=1
  fi
fi
# `nmwater update` records a failed catalog build instead of failing (it leaves a stub database), so check the catalog before exporting from it
# and rebuild it once if it is not healthy. The export reads only the catalog; a bad one would leave the website data stale with no clear reason.
catalog_ok() {
  "$UV" run python - <<'PY'
import sys
import duckdb
from nmwater.core.config import Settings
try:
    n = duckdb.connect(str(Settings.load().duckdb_path), read_only=True).sql("select count(*) from observations").fetchone()[0]
except Exception as e:
    print("catalog check failed:", e)
    sys.exit(1)
print(f"catalog ok: {n:,} observations")
sys.exit(0 if n > 1_000_000 else 1)
PY
}
if ! catalog_ok; then
  echo "$(date -Is) the catalog is not healthy; rebuilding it once"
  "$UV" run nmwater catalog build || status=1
  catalog_ok || status=1
fi
if "$UV" run nmwater export-site-data; then
  if [ "$PUBLISH" = 1 ]; then
    "$ROOT/scripts/cron/publish_site_data.sh" "$ROOT/dist/site-data" || status=1
  fi
else
  status=1
  echo "$(date -Is) export failed; nothing published"
fi
echo "$(date -Is) done (exit $status)"
exit "$status"
