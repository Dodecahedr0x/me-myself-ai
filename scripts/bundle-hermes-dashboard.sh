#!/bin/sh
# Build the dashboard of the app's pinned Hermes so `memyselfai-hermes dashboard` works without Node.js.
# Usage: scripts/bundle-hermes-dashboard.sh <cache-dir> <app Contents dir>. Needs Node.js and npm.
set -eu
cache=$1
contents=$2
root=$(cd "$(dirname "$0")/.." && pwd)
eval "$(cd "$root" && python3 -c 'import hermes_runtime as h; print(f"url={h.HERMES_URL} checksum={h.HERMES_SHA256} tag={h.HERMES_TAG}")')"
mkdir -p "$cache"
archive="$cache/hermes-$tag.tar.gz"
if ! echo "$checksum  $archive" | shasum -a 256 -c - >/dev/null 2>&1; then
  temp=$(mktemp "$cache/hermes.XXXXXX")
  trap 'rm -f "$temp"' EXIT
  curl -fsSL --retry 2 "$url" -o "$temp"
  echo "$checksum  $temp" | shasum -a 256 -c - >/dev/null
  mv "$temp" "$archive"
fi
work="$cache/hermes-dashboard-build"
rm -rf "$work"
mkdir -p "$work"
tar -xzf "$archive" -C "$work"
source=$(echo "$work"/hermes-agent-*)
# Locked dependencies, with package install scripts disabled.
(cd "$source/web" && npm ci --no-audit --no-fund --ignore-scripts >/dev/null && npm run build >/dev/null)
target="$contents/Resources/vendor/hermes-dashboard"
rm -rf "$target"
mkdir -p "$target"
cp -R "$source/hermes_cli/web_dist/." "$target/"
cp "$source/LICENSE" "$contents/Resources/vendor/hermes-dashboard-LICENSE"
rm -rf "$work"
