#!/usr/bin/env bash
# Write a CycloneDX 1.6 SBOM (JSON) for Countersign Shadow to <tree>/sbom/countersign.cdx.json.
#   bash tools/sbom.sh [tree] [version]     # tree: default this repo; version: default today's UTC date (YYYY.MM.DD)
# The core is Python standard library only; the shipped third-party code is the two vendored packages in
# countersign_shadow/_vendor/ (pypdf, olefile). Their versions are read from the vendored files, installed at
# exactly those versions into a throw-away venv, and described by cyclonedx-py (CycloneDX project, Apache-2.0),
# so licences and package URLs come from the packages' own metadata. The optional `anthropic` package (Claude
# planner only, not shipped, not installed by default) is not listed.
# Output is reproducible: same tree + same version -> same file (no random serial number; timestamp = version date).
# Needs network once to fill the cache (PyPI); later runs reuse it. Cache: $COUNTERSIGN_SBOM_CACHE.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TREE="$(cd "${1:-$ROOT}" && pwd)"
VERSION="${2:-$(date -u +%Y.%m.%d)}"
CACHE="${COUNTERSIGN_SBOM_CACHE:-$HOME/.cache/countersign-sbom}"
TOOL_VERSION="8.0.1"
V="$TREE/countersign_shadow/_vendor"

PYPDF=$(sed -nE 's/^__version__ = "([^"]+)"/\1/p' "$V/pypdf/_version.py" | tr -d '\r')
OLEFILE=$(sed -nE "s/^__version__ = '([^']+)'/\1/p" "$V/olefile/olefile.py" | tr -d '\r')
[ -n "$PYPDF" ] && [ -n "$OLEFILE" ] || { echo "sbom: could not read the vendored versions" >&2; exit 1; }

mkdir -p "$CACHE"
if [ "$("$CACHE/tool/bin/cyclonedx-py" --version 2>/dev/null)" != "$TOOL_VERSION" ]; then
  rm -rf "$CACHE/tool"
  python3 -m venv "$CACHE/tool"
  "$CACHE/tool/bin/pip" -q install "cyclonedx-bom==$TOOL_VERSION"
fi
ENV="$CACHE/env-pypdf$PYPDF-olefile$OLEFILE"
if [ ! -x "$ENV/bin/python" ]; then
  rm -rf "$CACHE"/env-*
  python3 -m venv --without-pip "$ENV"
  "$CACHE/tool/bin/pip" -q --python "$ENV/bin/python" install --no-deps "pypdf==$PYPDF" "olefile==$OLEFILE"
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/pyproject.toml" <<TOML
[project]
name = "countersign-shadow"
version = "$VERSION"
description = "Offline payment-fraud checks on a mailbox and bank export; never moves money"
license = "Apache-2.0"
requires-python = ">=3.10"
dependencies = ["pypdf==$PYPDF", "olefile==$OLEFILE"]
[project.urls]
Homepage = "https://github.com/LeriderTechnologies/countersign-shadow"
TOML
EPOCH=$(date -u -d "${VERSION//./-}" +%s)
mkdir -p "$TREE/sbom"
SOURCE_DATE_EPOCH="$EPOCH" "$CACHE/tool/bin/cyclonedx-py" environment "$ENV/bin/python" \
  --pyproject "$TMP/pyproject.toml" --mc-type application --output-reproducible --sv 1.6 \
  -o "$TMP/raw.json"

# Mark where each component lives in this tree (vendored, unmodified). The tool writes no host-specific data
# (checked with 8.0.1); the check below fails loudly if a local path ever slips in.
python3 - "$TMP/raw.json" "$TREE/sbom/countersign.cdx.json" "$CACHE" "$HOME" <<'PY'
import json, sys
src, dst, cache, home = sys.argv[1:5]
bom = json.load(open(src))
paths = {"pypdf": "countersign_shadow/_vendor/pypdf", "olefile": "countersign_shadow/_vendor/olefile"}
for c in bom.get("components", []):
    if c.get("name") in paths:
        c.setdefault("properties", []).append(
            {"name": "countersign:vendored-path", "value": paths[c["name"]]})
        c["description"] = (c.get("description") or "").strip() + " (vendored, unmodified)"
# olefile only declares the generic "BSD License" classifier; its LICENSE.txt (shipped next to it) is BSD-2-Clause.
for c in bom.get("components", []):
    if c.get("name") == "olefile":
        c["licenses"].append({"license": {"acknowledgement": "concluded", "id": "BSD-2-Clause"}})
names = sorted(c.get("name") for c in bom.get("components", []))
if names != ["olefile", "pypdf"]:
    sys.exit(f"sbom: unexpected component set {names}")
text = json.dumps(bom, indent=2, ensure_ascii=False) + "\n"
def strings(o):
    if isinstance(o, dict):
        for v in o.values(): yield from strings(v)
    elif isinstance(o, list):
        for v in o: yield from strings(v)
    elif isinstance(o, str):
        yield o
local = [s for s in strings(bom) if s.startswith(("/", "~")) or s[1:3] == ":\\" or cache in s or home in s]
if local:
    sys.exit(f"sbom: output contains a local path ({local[0]!r}); refusing")
open(dst, "w").write(text)
print(f"sbom: wrote {dst} ({', '.join(names)})")
PY
