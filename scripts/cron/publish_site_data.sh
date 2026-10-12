#!/usr/bin/env bash
# Upload a site data bundle to Cloudflare R2 and tell the website repository to rebuild.
#
#   scripts/cron/publish_site_data.sh [--dry-run] [BUNDLE_DIR]      (default BUNDLE_DIR: dist/site-data)
#
# Settings come from the environment or the repository's .env (never committed):
#   R2_ENDPOINT   https://<account id>.r2.cloudflarestorage.com
#   R2_BUCKET     bucket name
#   AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY    an R2 API token with write access to the bucket
#   WEB_REPO      optional, e.g. New-Mexico-Water/nmwater-web: a `data-updated` repository_dispatch is sent to it
#   GH_TOKEN      needed for WEB_REPO (a token that may create dispatch events on it)
#   KEEP          bundles to keep in the bucket (default 7)
#
# Layout in the bucket: bundles/<build id>/...   one folder per export, never changed after upload
#                       state-now.json           the home page's summary line (optional; see 1b below)
#                       latest.json              {"bundle": "<build id>", "generated": ..., "data_through": ...}
# A reader fetches latest.json, then that one folder, so it always sees one consistent snapshot. latest.json is
# written last, after the folder is complete. Old folders beyond KEEP are removed afterwards.
# The bundle is checked against docs/site-data/v1 first and nothing is uploaded if it does not conform.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:/usr/local/bin:/usr/bin:/bin:$PATH"
UV="${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}"

DRY=0
BUNDLE=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    -*) echo "unknown argument: $arg" >&2; exit 2 ;;
    *) BUNDLE="$arg" ;;
  esac
done
BUNDLE="${BUNDLE:-$ROOT/dist/site-data}"
[ -f "$ROOT/.env" ] && { set -a; . "$ROOT/.env"; set +a; }
KEEP="${KEEP:-7}"

[ -f "$BUNDLE/manifest.json" ] || { echo "no bundle at $BUNDLE" >&2; exit 2; }
"$UV" run python -m nmwater.site.schema "$BUNDLE" || { echo "bundle does not match the schema; not publishing" >&2; exit 1; }

ID="$("$UV" run python -c "import json,sys; m=json.load(open(sys.argv[1])); print(m['generated'].replace('-','').replace(':','')[:15].replace('T','T'))" "$BUNDLE/manifest.json")"
echo "bundle id $ID ($(du -sh "$BUNDLE" | cut -f1))"

run() { if [ "$DRY" = 1 ]; then echo "[dry run] $*"; else "$@"; fi; }
if [ "$DRY" = 0 ]; then
  : "${R2_ENDPOINT:?set R2_ENDPOINT}" "${R2_BUCKET:?set R2_BUCKET}" "${AWS_ACCESS_KEY_ID:?set AWS_ACCESS_KEY_ID}" "${AWS_SECRET_ACCESS_KEY:?set AWS_SECRET_ACCESS_KEY}"
else
  R2_ENDPOINT="${R2_ENDPOINT:-https://example.r2.cloudflarestorage.com}"; R2_BUCKET="${R2_BUCKET:-nmwater-site-data}"
fi
S3=(aws s3 --endpoint-url "$R2_ENDPOINT" --region auto)

# 1. the folder, complete and immutable
run "${S3[@]}" sync "$BUNDLE" "s3://$R2_BUCKET/bundles/$ID/" --only-show-errors
# 1b. the home page's summary line, beside the pointer: written here, on the machine that makes the data, from the new bundle, by the website repository's
#     scripts/summarize.ts (needs bun and ANTHROPIC_API_KEY in .env). The site checks it against the data and falls back to its own wording when it is
#     missing, so nothing in this step can stop the publish.
WEB_DIR="${WEB_DIR:-$HOME/projects/newmexicowater/nmwater-web}"
LINE="$WEB_DIR/.generated/state-now.json"
if [ "$DRY" = 1 ]; then
  echo "[dry run] would write the summary line with $WEB_DIR/scripts/summarize.ts and upload it as state-now.json"
elif [ -f "$WEB_DIR/scripts/summarize.ts" ] && [ -n "${ANTHROPIC_API_KEY:-}" ] && command -v bun >/dev/null 2>&1; then
  rm -f "$LINE"
  if (cd "$WEB_DIR" && NMWATER_DATA="$(cd "$BUNDLE" && pwd)" bun scripts/summarize.ts) && [ -f "$LINE" ]; then
    run "${S3[@]}" cp "$LINE" "s3://$R2_BUCKET/state-now.json" --content-type application/json --cache-control "max-age=60"
  else
    echo "no summary line was made this time; the site uses its own wording"
  fi
else
  echo "summary line skipped (it needs the website repository at $WEB_DIR, bun, and ANTHROPIC_API_KEY in .env)"
fi
# 2. the pointer, last
POINTER="$(mktemp)"; trap 'rm -f "$POINTER"' EXIT
"$UV" run python - "$BUNDLE/manifest.json" "$ID" >"$POINTER" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
print(json.dumps({"bundle": sys.argv[2], "generated": m["generated"], "data_through": m["data_through"], "schema_version": m["schema_version"]}))
PY
run "${S3[@]}" cp "$POINTER" "s3://$R2_BUCKET/latest.json" --content-type application/json --cache-control "max-age=60"
# 3. tell the site
if [ -n "${WEB_REPO:-}" ]; then
  run gh api "repos/$WEB_REPO/dispatches" -f event_type=data-updated -f "client_payload[bundle]=$ID"
else
  echo "WEB_REPO is not set: the site was not told to rebuild"
fi
# 4. prune
if [ "$DRY" = 0 ]; then
  mapfile -t ALL < <("${S3[@]}" ls "s3://$R2_BUCKET/bundles/" | awk '{print $2}' | sed 's#/$##' | sort)
  if [ "${#ALL[@]}" -gt "$KEEP" ]; then
    for old in "${ALL[@]:0:${#ALL[@]}-KEEP}"; do "${S3[@]}" rm "s3://$R2_BUCKET/bundles/$old/" --recursive --only-show-errors; done
  fi
else
  echo "[dry run] prune bundles/ to the newest $KEEP"
fi
echo "published $ID"
