#!/usr/bin/env bash
# The hourly flood status: fetch the latest National Weather Service readings, make flooding-now.json, upload it next to latest.json, and render the site's home page again
# only when what its flood band says has changed (nmwater/site/flooding_now.py explains when). The site is static: the band is in the page's HTML, not loaded after it.
#
#   scripts/cron/flood_hourly.sh              fetch, make, upload, and start the site's build when needed
#   scripts/cron/flood_hourly.sh --dry-run    fetch and make the file; upload nothing and start nothing
#
#   crontab:  7 * * * *  /home/vance/projects/water_newmexico/scripts/cron/flood_hourly.sh
#
# It shares the nightly job's lock, so it skips the hours the nightly refresh is running (about 02:00 to 07:00) and the nightly skips if this is. Publishing needs the R2
# settings in .env (as publish_site_data.sh); starting the build uses the `gh` login. Log: data/logs/flood_hourly.log (the last 3000 lines are kept).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

# cron gives a job no memory cap, and this machine is also a desktop where systemd-oomd kills a user session under memory pressure: run as a small capped unit.
if [ -z "${FLOOD_HOURLY_INNER:-}" ] && [ -S "$XDG_RUNTIME_DIR/bus" ]; then
  exec systemd-run --user --wait --collect --quiet -p Nice=10 -p IOSchedulingClass=idle -p MemoryHigh=2G -p MemoryMax=3G \
    --setenv=FLOOD_HOURLY_INNER=1 --working-directory="$ROOT" "${BASH_SOURCE[0]}" "$@"
fi
cd "$ROOT"

DRY=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done
UV="${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}"
DATA_DIR="${NMWATER_DATA_DIR:-$ROOT/data}"
mkdir -p "$DATA_DIR/logs" "$DATA_DIR/locks"
LOG="$DATA_DIR/logs/flood_hourly.log"
exec >>"$LOG" 2>&1
[ -f "$ROOT/.env" ] && { set -a; . "$ROOT/.env"; set +a; }

exec 9>"$DATA_DIR/locks/nmwater-cron.lock"
if ! flock -n 9; then
  echo "$(date -Is) the nightly refresh (or another run) holds the lock; skipping"
  exit 0
fi

echo "$(date -Is) start (dry run: $DRY)"
status=0
if ! timeout 900 "$UV" run nmwater fetch nwps; then
  echo "$(date -Is) fetching the NWS readings failed; nothing made or published"
  status=1
else
  # every fetch adds about 250 small files; merging them takes 3 seconds and keeps the store (and the next query) small
  timeout 300 "$UV" run nmwater compact nwps || echo "$(date -Is) compacting the NWS files failed; carrying on"
  ARGS=()
  [ "$DRY" = 0 ] && ARGS+=(--publish --redeploy)
  timeout 600 "$UV" run nmwater flood-hourly "${ARGS[@]}" || status=1
fi
echo "$(date -Is) done (exit $status)"
tail -n 3000 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
exit "$status"
