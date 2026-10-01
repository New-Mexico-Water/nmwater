#!/usr/bin/env bash
# Build the website's data bundle (dist/site-data) from the archive, and optionally publish it. For cron.
#
#   scripts/cron/site_data.sh                      export the bundle from the current catalog
#   scripts/cron/site_data.sh --update             refresh the archive first (`nmwater update`), then export
#   scripts/cron/site_data.sh --publish            after a good export, upload it and tell the site to rebuild
#                                                  (scripts/cron/publish_site_data.sh; needs R2 settings in .env)
#
# Example crontab (daily at 05:30; the river pages script can keep running beside it until the site replaces it):
#   30 5 * * *  /home/vance/projects/water_newmexico/scripts/cron/site_data.sh --update --publish
#
# - One run at a time (shares the lock with river_reports.sh); a second run exits 0.
# - Log: data/logs/site_data.log. Exit: 0 success, 1 a river, the update, validation or the upload failed, 2 bad arguments.
# - The bundle is swapped in only when complete and checked against docs/site-data/v1, so a failed run leaves the
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
