#!/usr/bin/env bash
# Wait for running grid backfills, retry each once (the ledger skips finished files), rebuild the catalog.
cd "$(dirname "$0")/.."
L=docs/qa
while pgrep -f "bin/nmwater fetch (snodas|nclimgrid|prism)$" >/dev/null; do sleep 300; done
for s in snodas nclimgrid prism; do
  echo "== retry $s $(date -Is)"; uv run nmwater fetch "$s" 2>&1 | tail -3
done
echo "== catalog $(date -Is)"; uv run nmwater catalog build 2>&1 | tail -3
du -sh data/grids/* ; echo "done $(date -Is)"
