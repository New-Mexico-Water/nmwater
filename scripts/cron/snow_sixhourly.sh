#!/usr/bin/env bash
# The six-hourly snow-station status: fetch the latest SNOTEL hourly readings, make snow-now.json, upload it next to latest.json, and render the site again only when a station's
# snow figures moved enough to see (nmwater/site/snow_now.py explains when). The site is static: the figures are in the page's HTML, not loaded after it.
#
#   scripts/cron/snow_sixhourly.sh              fetch, make, upload, and start the site's build when needed
#   scripts/cron/snow_sixhourly.sh --dry-run    fetch and make the file; upload nothing and start nothing
#
#   crontab:  17 */6 * * *  /home/vance/projects/water_newmexico/scripts/cron/snow_sixhourly.sh
#
# It shares the nightly job's lock (and the hourly flood job's), so it skips a run while one of them holds it. 00:17 local is the first run after the stations' midnight reading, so
# yesterday's new snow is out before the nightly refresh. Publishing needs the R2 settings in .env (as publish_site_data.sh); starting the build uses the `gh` login.
# Log: data/logs/snow_sixhourly.log (the last 3000 lines are kept).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

# cron gives a job no memory cap, and this machine is also a desktop where systemd-oomd kills a user session under memory pressure: run as a small capped unit.
if [ -z "${SNOW_SIXHOURLY_INNER:-}" ] && [ -S "$XDG_RUNTIME_DIR/bus" ]; then
  exec systemd-run --user --wait --collect --quiet -p Nice=10 -p IOSchedulingClass=idle -p MemoryHigh=3G -p MemoryMax=4G \
    --setenv=SNOW_SIXHOURLY_INNER=1 --working-directory="$ROOT" "${BASH_SOURCE[0]}" "$@"
fi
cd "$ROOT"

DRY=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done
UV="${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}"
DATA_DIR="${NMWATER_DATA_DIR:-$ROOT/data}"
mkdir -p "$DATA_DIR/logs" "$DATA_DIR/locks"
LOG="$DATA_DIR/logs/snow_sixhourly.log"
exec >>"$LOG" 2>&1
[ -f "$ROOT/.env" ] && { set -a; . "$ROOT/.env"; set +a; }

exec 9>"$DATA_DIR/locks/nmwater-cron.lock"
if ! flock -n 9; then
  echo "$(date -Is) another run (the nightly refresh or the flood job) holds the lock; skipping"
  exit 0
fi

echo "$(date -Is) start (dry run: $DRY)"
status=0
SITES=()
while read -r s; do [ -n "$s" ] && SITES+=(--site "$s"); done < <("$UV" run nmwater snow-now --list-sites)
if [ "${#SITES[@]}" -eq 0 ]; then
  echo "$(date -Is) no SNOTEL stations in the catalog; nothing made or published"
  status=1
# the hourly readings of the last four days: a missed run or a late station is caught up, and the files NRCS revises are read again
elif ! timeout 900 "$UV" run nmwater fetch nrcs --kind hourly --since "$(date -d '4 days ago' +%F)" "${SITES[@]}"; then
  echo "$(date -Is) fetching the SNOTEL readings failed; nothing made or published"
  status=1
else
  timeout 300 "$UV" run nmwater compact nrcs || echo "$(date -Is) compacting the NRCS files failed; carrying on"
  ARGS=()
  [ "$DRY" = 0 ] && ARGS+=(--publish --redeploy)
  timeout 600 "$UV" run nmwater snow-now "${ARGS[@]}" || status=1
fi
echo "$(date -Is) done (exit $status)"
tail -n 3000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
exit "$status"
