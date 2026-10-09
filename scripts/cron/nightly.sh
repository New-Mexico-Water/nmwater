#!/usr/bin/env bash
# The cron entry point for the nightly refresh: runs scripts/cron/site_data.sh --update --publish with the same limits the systemd unit has, then refreshes the dev
# server on success or raises a desktop notification on failure.
#
#   crontab:  0 2 * * *  /home/vance/projects/water_newmexico/scripts/cron/nightly.sh
#
# cron gives a job no memory cap, and this machine is also a desktop where systemd-oomd kills a user session under memory pressure, so the job runs as a transient user unit
# (low priority, 8 GB soft and 14 GB hard memory limit) when the user's systemd manager is reachable, and under nice/ionice alone when it is not (the owner logged out).
# NIGHTLY_COMMAND overrides the job (for testing the plumbing). Exit: the job's.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
JOB=("${NIGHTLY_COMMAND:-$ROOT/scripts/cron/site_data.sh}")
[ -z "${NIGHTLY_COMMAND:-}" ] && JOB+=(--update --publish)   # --publish: upload the new bundle to R2 (publish_site_data.sh); the website rebuilds from it

if [ -S "$XDG_RUNTIME_DIR/bus" ]; then
  USER_MANAGER=1
  systemd-run --user --wait --collect --quiet -p Nice=10 -p IOSchedulingClass=idle -p CPUWeight=50 -p MemoryHigh=8G -p MemoryMax=14G \
    --working-directory="$ROOT" "${JOB[@]}"
  rc=$?
else
  USER_MANAGER=0
  cd "$ROOT" && nice -n 10 ionice -c3 "${JOB[@]}"
  rc=$?
fi

if [ "$rc" = 0 ]; then
  # show the new data on the dev server the owner uses (nothing happens if it is not running)
  [ "$USER_MANAGER" = 1 ] && systemctl --user try-restart nmwater-web-dev.service >/dev/null 2>&1
else
  notify-send --urgency=normal --app-name=nmwater "Nightly water data refresh failed (exit $rc)" "See $ROOT/data/logs/site_data.log" 2>/dev/null
fi
exit "$rc"
