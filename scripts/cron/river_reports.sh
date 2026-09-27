#!/usr/bin/env bash
# Build the river flow pages (dist/rivers) from the archive. Written for cron; safe to run by hand.
#
#   scripts/cron/river_reports.sh             rebuild pages from the current catalog
#   scripts/cron/river_reports.sh --update    refresh the archive first (`nmwater update`, which also
#                                             rebuilds the catalog), then rebuild the pages
#
# Example crontab (daily at 05:30, data refresh first):
#   30 5 * * *  /home/vance/projects/water_newmexico/scripts/cron/river_reports.sh --update
#
# - Only one run at a time: a second run while one is going exits 0 without doing anything.
# - Log: data/logs/river_reports.log (appended; rotate with logrotate if it grows).
# - Exit status: 0 success, 1 a river or the update failed, 2 bad arguments.
# - Pages are swapped in only when complete, so a failed run leaves the previous pages in place.
# - Environment: UV (path to uv, default: found on PATH or ~/.local/bin/uv), NMWATER_DATA_DIR.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

UPDATE=0
for arg in "$@"; do
  case "$arg" in
    --update) UPDATE=1 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# cron runs with a minimal PATH
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
UV="${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}"

DATA_DIR="${NMWATER_DATA_DIR:-$ROOT/data}"
mkdir -p "$DATA_DIR/logs" "$DATA_DIR/locks"
LOG="$DATA_DIR/logs/river_reports.log"
exec >>"$LOG" 2>&1

exec 9>"$DATA_DIR/locks/nmwater-cron.lock"
if ! flock -n 9; then
  echo "$(date -Is) another run holds the lock; skipping"
  exit 0
fi

echo "$(date -Is) start (update=$UPDATE)"
status=0
if [ "$UPDATE" = 1 ]; then
  if ! "$UV" run nmwater update; then
    echo "$(date -Is) update failed; building pages from the existing catalog"
    status=1
  fi
fi
if ! "$UV" run nmwater report-rivers; then
  status=1
fi
echo "$(date -Is) done (exit $status)"
exit "$status"
