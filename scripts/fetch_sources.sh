#!/usr/bin/env bash
# fetch_sources.sh — download candidate command-reference docs for the
# multivendor-cli-configurator corpus.
#
# Run this LOCALLY (it needs outbound internet). It reads candidate_sources.json
# and pulls each file into ./sources/<id>.<ext>. These files are inputs to
# parse_vendor_docs.py and should live under the repo's gitignored scripts/sources/.
#
# Usage:
#   ./fetch_sources.sh                 # download all
#   ./fetch_sources.sh cisco_ios_mcl   # download one by id
#
# Requires: bash, curl, python3 (stdlib only). Optional: poppler (pdftotext) for parsing later.

set -euo pipefail
cd "$(dirname "$0")"

# Manifest may live next to this script or in the sibling docs/ folder.
if [[ -f "candidate_sources.json" ]]; then
  MANIFEST="candidate_sources.json"
elif [[ -f "../docs/candidate_sources.json" ]]; then
  MANIFEST="../docs/candidate_sources.json"
else
  echo "candidate_sources.json not found in ./ or ../docs/" >&2; exit 1
fi
OUTDIR="sources"
mkdir -p "$OUTDIR"

# Extract (id, url, format) tuples from the manifest with stdlib json.
mapfile -t ROWS < <(python3 - "$MANIFEST" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
for s in data["sources"]:
    print(f'{s["id"]}\t{s["url"]}\t{s["format"]}')
PY
)

WANT="${1:-}"
for row in "${ROWS[@]}"; do
  IFS=$'\t' read -r id url fmt <<<"$row"
  [[ -n "$WANT" && "$WANT" != "$id" ]] && continue
  out="$OUTDIR/${id}.${fmt}"
  if [[ -s "$out" ]]; then
    echo "✓ already have $out"
    continue
  fi
  echo "↓ $id  ->  $out"
  # -L follow redirects, -A browser UA (some vendor portals require it), --fail on 4xx/5xx
  curl -fL --retry 3 --retry-delay 2 \
    -A "Mozilla/5.0 (compatible; cli-corpus-fetch/1.0)" \
    -o "$out" "$url" \
    || echo "  ⚠ failed: $id  ($url) — fetch manually and save as $out"
done

echo
echo "Done. Files in ./$OUTDIR/ :"
ls -lh "$OUTDIR" 2>/dev/null || true
echo
echo "Next: python3 parse_vendor_docs.py --all   (or --source <id>)"
