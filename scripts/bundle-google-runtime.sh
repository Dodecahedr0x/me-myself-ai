#!/bin/sh
# Bundle the official standalone runtime; end users do not need a Node installation.
set -eu
cache=$1
contents=$2
version=v22.23.5
case "$(uname -m)" in
  arm64) arch=arm64; checksum=e7131995759b61a0170b646010d2ad35a5d37b4cb0d471f3e8317eac6f742f3d ;;
  x86_64) arch=x64; checksum=c53383238206b9ae4db23c5d8a63645e2e753bec8ab51a49cc81939aa64e9422 ;;
  *) echo "Unsupported macOS architecture" >&2; exit 1 ;;
esac
name="node-$version-darwin-$arch"
mkdir -p "$cache" "$contents/Resources/vendor/google-workspace" "$contents/Helpers"
archive="$cache/$name.tar.gz"
if ! echo "$checksum  $archive" | shasum -a 256 -c - >/dev/null 2>&1; then
  temp=$(mktemp "$cache/node.XXXXXX")
  trap 'rm -f "$temp"' EXIT
  curl -fsSL --retry 2 "https://nodejs.org/dist/$version/$name.tar.gz" -o "$temp"
  echo "$checksum  $temp" | shasum -a 256 -c - >/dev/null
  mv "$temp" "$archive"
fi
tar -xOzf "$archive" "$name/bin/node" > "$contents/Helpers/workspace-node"
chmod +x "$contents/Helpers/workspace-node"
tar -xOzf "$archive" "$name/LICENSE" > "$contents/Resources/vendor/google-workspace/NODE-LICENSE"
codesign --force --sign - "$contents/Helpers/workspace-node"
